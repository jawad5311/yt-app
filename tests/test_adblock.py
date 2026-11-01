"""Tests confirming ad blocker extension has been removed and disabled."""

from pathlib import Path

from src.automation.adblock import ensure_adblock, find_extension_root, load_extension_cdp


def test_ensure_adblock_returns_none():
    assert ensure_adblock() is None


def test_load_extension_cdp_returns_false():
    assert load_extension_cdp(None, Path("mock")) is False


def test_find_extension_root(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert find_extension_root(tmp_path) == tmp_path

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert find_extension_root(empty_dir) is None
