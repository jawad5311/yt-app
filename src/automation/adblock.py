"""Optional MV3 ad blocker (uBlock Origin Lite) provisioning.

When the ``SkipAds`` setting is on, the running session gets uBlock Origin Lite
installed as an unpacked extension via the browser's DevTools socket
(``Extensions.loadUnpacked``), so ads never render. Branded Chrome 137+ ignores
``--load-extension`` and hides the ``Extensions`` domain from ChromeDriver, but
the browser-level socket still exposes it, which is what
:func:`load_extension_cdp` uses. The package is downloaded once into
``extensions/`` (git-ignored) and verified against a pinned SHA-256. Any
failure falls back to ``None`` and the click-based skip logic in
:mod:`src.automation.youtube_actions` keeps working.
"""

import hashlib
import json
import logging
import queue
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..utils.logger import emit

# uBlock Origin Lite (MV3) - pinned release for reproducible sessions.
RELEASE_TAG = "2026.930.1227"
ARCHIVE_NAME = f"uBOLite_{RELEASE_TAG}.chromium.zip"
DOWNLOAD_URL = (
    f"https://github.com/uBlockOrigin/uBOL-home/releases/download/"
    f"{RELEASE_TAG}/{ARCHIVE_NAME}"
)
ARCHIVE_SHA256 = "13dd17bcc9720abbf5006793b0a4f7b672d34b394db2e6198daa7ef282b20361"

DOWNLOAD_TIMEOUT_SECONDS = 180

_resolved_path: Optional[Path] = None
_probed = False


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def extensions_dir() -> Path:
    path = project_root() / "extensions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_extension_root(folder: Path) -> Optional[Path]:
    """Return the directory containing ``manifest.json`` (handles a wrapper folder)."""
    manifest = folder / "manifest.json"
    if manifest.is_file():
        return folder
    candidates = sorted(folder.rglob("manifest.json"))
    if not candidates:
        return None
    return candidates[0].parent


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "yt-tool/2.1"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        with open(dest, "wb") as handle:
            shutil.copyfileobj(response, handle)


def ensure_adblock(log_queue: Optional[queue.Queue] = None, force: bool = False) -> Optional[Path]:
    """Return the unpacked extension directory, downloading it on first use.

    Returns ``None`` when the blocker is unavailable (offline, checksum mismatch,
    corrupt archive) so callers can fall back to click-based ad skipping.
    """
    global _resolved_path, _probed
    if _probed and not force:
        return _resolved_path

    install_dir = extensions_dir() / f"uBOLite-{RELEASE_TAG}"
    existing = find_extension_root(install_dir)
    if existing is not None:
        _resolved_path = existing
        _probed = True
        return existing

    temp_dir = Path(tempfile.mkdtemp(prefix="uBOLite-"))
    archive = temp_dir / ARCHIVE_NAME
    try:
        emit(log_queue, f"Downloading ad blocker ({ARCHIVE_NAME}, ~9 MB)...")
        _download(DOWNLOAD_URL, archive)

        checksum = _sha256(archive)
        if checksum != ARCHIVE_SHA256:
            emit(
                log_queue,
                f"Ad blocker checksum mismatch (got {checksum[:12]}…). Skipping install.",
                logging.ERROR,
            )
            _resolved_path = None
            return None

        extract_dir = temp_dir / "unpacked"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract_dir)

        root = find_extension_root(extract_dir)
        if root is None:
            emit(log_queue, "Ad blocker archive has no manifest.json. Skipping install.", logging.ERROR)
            _resolved_path = None
            return None

        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        shutil.copytree(root, install_dir)

        installed = find_extension_root(install_dir)
        _resolved_path = installed
        if installed is not None:
            emit(log_queue, f"Ad blocker installed: {installed}")
        return installed

    except Exception as exc:  # network, disk, zip errors -> degrade gracefully
        emit(log_queue, f"Ad blocker unavailable ({exc}). Using click-based skip only.", logging.WARNING)
        _resolved_path = None
        return None
    finally:
        _probed = True
        shutil.rmtree(temp_dir, ignore_errors=True)


def _cdp_request(ws: Any, method: str, params: Dict[str, Any], request_id: int) -> Dict[str, Any]:
    """Send one DevTools command over a websocket and await its response."""
    ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(message["error"].get("message", "CDP error"))
            return message.get("result", {})


def load_extension_cdp(driver: Any, extension_path: Path, log_queue: Optional[queue.Queue] = None) -> Optional[str]:
    """Install the unpacked blocker into a running session; return its id.

    Why not ``--load-extension``: branded Chrome 137+ ignores that switch, and
    ChromeDriver's CDP bridge refuses the ``Extensions`` domain (it forwards to
    the page target, which does not expose it). Chrome's *browser* DevTools
    socket still exposes ``Extensions.loadUnpacked``, so we talk to it directly.
    The extension is session-scoped, so this runs once per browser start.
    """
    try:
        import websocket
    except ImportError:  # pragma: no cover - selenium always ships it
        emit(log_queue, "websocket-client missing; ad blocker not loaded.", logging.WARNING)
        return None

    try:
        debugger = driver.capabilities.get("goog:chromeOptions", {}).get("debuggerAddress")
        if not debugger:
            emit(log_queue, "No debugger address; ad blocker not loaded.", logging.WARNING)
            return None

        with urllib.request.urlopen(f"http://{debugger}/json/version", timeout=5) as response:
            ws_url = json.loads(response.read().decode("utf-8")).get("webSocketDebuggerUrl")
        if not ws_url:
            emit(log_queue, "No browser DevTools endpoint; ad blocker not loaded.", logging.WARNING)
            return None

        # suppress_origin: Chrome rejects unknown WebSocket origins with 403.
        ws = websocket.create_connection(ws_url, timeout=15, suppress_origin=True)
        try:
            wanted = str(extension_path).rstrip("\\/").lower()

            def find_loaded() -> Optional[str]:
                known = _cdp_request(ws, "Extensions.getExtensions", {}, 1).get("extensions", [])
                for info in known:
                    if str(info.get("path", "")).rstrip("\\/").lower() == wanted:
                        return str(info.get("id", ""))
                return None

            existing = find_loaded()
            if existing:
                emit(log_queue, f"Ad blocker already active ({existing}).")
                return existing

            try:
                result = _cdp_request(ws, "Extensions.loadUnpacked", {"path": str(extension_path)}, 2)
            except RuntimeError as exc:
                # Chrome rejects a duplicate install -> it is present after all.
                existing = find_loaded()
                if existing:
                    emit(log_queue, f"Ad blocker already active ({existing}).")
                    return existing
                raise RuntimeError(exc)

            extension_id = str(result.get("id", ""))
            if extension_id:
                emit(log_queue, f"Ad blocker loaded ({extension_id}).")
                return extension_id
            emit(log_queue, "Ad blocker load returned no id.", logging.WARNING)
            return None
        finally:
            ws.close()
    except Exception as exc:
        emit(log_queue, f"Ad blocker could not be loaded: {exc}", logging.WARNING)
        return None


if __name__ == "__main__":  # pragma: no cover - manual provisioning helper
    result = ensure_adblock(force=True)
    print(f"ADBLOCK_PATH={result}")
