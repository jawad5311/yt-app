"""Tests for ConfigManager: defaults, purging, credentials and queue persistence."""

import json

from src.config.manager import ConfigManager


def make_manager(tmp_path, data=None):
    config_file = tmp_path / "settings.json"
    if data is not None:
        config_file.write_text(json.dumps(data), encoding="utf-8")
    return ConfigManager(config_file=config_file)


def test_creates_default_file_when_missing(tmp_path):
    mgr = make_manager(tmp_path)
    assert mgr.config_file.exists()
    assert mgr.get_int("ParallelBrowsers", 1) == 1
    assert mgr.get_setting("WatchtimeDuration") == "5 Minutes"
    assert mgr.get_bool("DirectLinkFallback", True) is True


def test_defaults_do_not_contain_retired_keys(tmp_path):
    mgr = make_manager(tmp_path)
    assert "AccessKey" not in mgr.data["settings"]
    assert "OpenRouterKey" not in mgr.data["settings"]
    assert "FullVideoTimeoutMinutes" in mgr.data["settings"]


def test_purges_retired_settings_and_video_fields(tmp_path):
    seed = {
        "settings": {
            "AccessKey": "super-secret",
            "OpenRouterKey": "sk-abc",
            "CommentType": "funny",
            "DirectLinkFallback": "False",
        },
        "accounts": [],
        "videos": [{"url": "https://youtu.be/abcdefghijk", "like": True, "comment_type": "nice"}],
    }
    mgr = make_manager(tmp_path, seed)

    for key in ("AccessKey", "OpenRouterKey", "CommentType"):
        assert key not in mgr.data["settings"]
    assert "like" not in mgr.data["videos"][0]
    assert "comment_type" not in mgr.data["videos"][0]

    # Purging is persisted back to disk
    on_disk = json.loads(mgr.config_file.read_text(encoding="utf-8"))
    assert "AccessKey" not in on_disk["settings"]
    assert "like" not in on_disk["videos"][0]


def test_get_int_returns_default_for_bad_values(tmp_path):
    seed = {"settings": {"ParallelBrowsers": "not-a-number"}, "accounts": [], "videos": []}
    mgr = make_manager(tmp_path, seed)
    assert mgr.get_int("ParallelBrowsers", 7) == 7


def test_get_bool_parses_string_values(tmp_path):
    seed = {"settings": {"DirectLinkFallback": "false"}, "accounts": [], "videos": []}
    mgr = make_manager(tmp_path, seed)
    assert mgr.get_bool("DirectLinkFallback", True) is False


def test_settings_roundtrip(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.set_setting("ParallelBrowsers", "4")
    reloaded = ConfigManager(config_file=mgr.config_file)
    assert reloaded.get_int("ParallelBrowsers", 1) == 4


def test_video_queue_roundtrip(tmp_path):
    mgr = make_manager(tmp_path)
    tasks = [{"url": "https://youtu.be/abcdefghijk", "title": "T", "keywords": "k", "watchtime_duration": "1 Hour"}]
    mgr.set_video_queue(tasks)
    reloaded = ConfigManager(config_file=mgr.config_file)
    assert reloaded.get_video_queue() == tasks


def test_accounts_are_stored_in_credentials_file(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.set_accounts([{"email": "a@b.com", "password": "pw"}])

    creds = json.loads((tmp_path / "credentials.json").read_text(encoding="utf-8"))
    assert creds["accounts"][0]["email"] == "a@b.com"

    reloaded = ConfigManager(config_file=mgr.config_file)
    assert reloaded.get_accounts()[0]["email"] == "a@b.com"


def test_legacy_accounts_migrate_to_credentials_file(tmp_path):
    seed = {"settings": {}, "accounts": [{"email": "legacy@x.com", "password": "pw"}], "videos": []}
    mgr = make_manager(tmp_path, seed)

    accounts = mgr.get_accounts()
    assert accounts == [{"email": "legacy@x.com", "password": "pw"}]
    assert (tmp_path / "credentials.json").exists()
