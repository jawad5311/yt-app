"""Tests for ad blocker provisioning helpers (no network access)."""

import json
import sys
from pathlib import Path

import pytest

from src.automation.adblock import find_extension_root


def test_find_extension_root_at_folder_root(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert find_extension_root(tmp_path) == tmp_path


def test_find_extension_root_inside_wrapper_folder(tmp_path):
    wrapped = tmp_path / "uBOLite-2026.930.1227"
    wrapped.mkdir()
    (wrapped / "manifest.json").write_text("{}", encoding="utf-8")
    assert find_extension_root(tmp_path) == wrapped


def test_find_extension_root_missing(tmp_path):
    assert find_extension_root(tmp_path) is None


def test_installed_extension_is_usable():
    """If the extension has been provisioned locally, it must be loadable."""
    from src.automation.adblock import RELEASE_TAG, extensions_dir

    install_dir = extensions_dir() / f"uBOLite-{RELEASE_TAG}"
    if not (install_dir / "manifest.json").is_file():
        pytest.skip("ad blocker not provisioned in this checkout")
    root = find_extension_root(Path(install_dir))
    assert root is not None and (root / "manifest.json").is_file()


class _FakeWs:
    """Minimal websocket stand-in fed with scripted CDP responses."""

    def __init__(self, responses):
        self._responses = [json.dumps(r) for r in responses]
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        return self._responses.pop(0)

    def close(self):
        self.closed = True


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_cdp_request_returns_result_and_skips_unrelated_events():
    from src.automation.adblock import _cdp_request

    ws = _FakeWs(
        [
            {"method": "Network.event", "params": {}},  # unsolicited event
            {"id": 1, "result": {"extensions": []}},
        ]
    )
    assert _cdp_request(ws, "Extensions.getExtensions", {}, 1) == {"extensions": []}
    assert ws.sent[0]["method"] == "Extensions.getExtensions"


def test_cdp_request_raises_on_error_response():
    from src.automation.adblock import _cdp_request

    ws = _FakeWs([{"id": 2, "error": {"message": "Method not available."}}])
    with pytest.raises(RuntimeError, match="Method not available"):
        _cdp_request(ws, "Extensions.loadUnpacked", {"path": "x"}, 2)


def test_load_extension_cdp_happy_path(monkeypatch, tmp_path):
    import src.automation.adblock as adblock_mod
    from src.automation.adblock import load_extension_cdp

    ext_dir = tmp_path / "uBOLite-test"
    ext_dir.mkdir()
    fake_ws = _FakeWs(
        [
            {"id": 1, "result": {"extensions": []}},
            {"id": 2, "result": {"id": "abcdef0123456789"}},
        ]
    )

    class _FakeModule:
        @staticmethod
        def create_connection(*args, **kwargs):
            return fake_ws

    monkeypatch.setitem(sys.modules, "websocket", _FakeModule)
    monkeypatch.setattr(
        adblock_mod.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResp({"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/x"}),
    )

    driver = type("Driver", (), {"capabilities": {"goog:chromeOptions": {"debuggerAddress": "127.0.0.1:1"}}})()
    result = load_extension_cdp(driver, ext_dir)

    assert result == "abcdef0123456789"
    assert fake_ws.closed is True
    methods = [m["method"] for m in fake_ws.sent]
    assert methods == ["Extensions.getExtensions", "Extensions.loadUnpacked"]
    assert fake_ws.sent[1]["params"]["path"] == str(ext_dir)


def test_load_extension_cdp_reports_existing_install(monkeypatch, tmp_path):
    import src.automation.adblock as adblock_mod
    from src.automation.adblock import load_extension_cdp

    ext_dir = tmp_path / "uBOLite-test"
    ext_dir.mkdir()
    fake_ws = _FakeWs(
        [
            {
                "id": 1,
                "result": {"extensions": [{"id": "ext123", "path": str(ext_dir) + "\\"}]},
            }
        ]
    )

    class _FakeModule:
        @staticmethod
        def create_connection(*args, **kwargs):
            return fake_ws

    monkeypatch.setitem(sys.modules, "websocket", _FakeModule)
    monkeypatch.setattr(
        adblock_mod.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResp({"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/x"}),
    )

    driver = type("Driver", (), {"capabilities": {"goog:chromeOptions": {"debuggerAddress": "127.0.0.1:1"}}})()
    assert load_extension_cdp(driver, ext_dir) == "ext123"
    # no install attempt for an extension that is already active
    assert [m["method"] for m in fake_ws.sent] == ["Extensions.getExtensions"]


def test_load_extension_cdp_graceful_without_debugger_address():
    from src.automation.adblock import load_extension_cdp

    driver = type("Driver", (), {"capabilities": {}})()
    assert load_extension_cdp(driver, Path("whatever")) is None
