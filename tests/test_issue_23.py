import json
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from src.config.manager import ConfigManager, CorruptConfigurationError, CorruptCredentialsError
from src.models.channel import Channel
from src.models.video_task import VideoTask


def test_missing_settings_and_credentials_follow_first_run(tmp_path):
    """Missing settings and credentials create default configuration on first run."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    assert not settings_file.exists()
    assert not credentials_file.exists()

    mgr = ConfigManager(settings_file)
    assert settings_file.exists()
    assert mgr.get_setting("AppearanceMode") == "Dark"
    assert mgr.get_int("ParallelBrowsers") == 1
    assert mgr.is_settings_corrupt is False

    # First run credentials
    accounts = mgr.get_accounts()
    assert isinstance(accounts, list)
    assert len(accounts) == 0
    assert credentials_file.exists()
    assert mgr.is_credentials_corrupt is False


def test_malformed_settings_preserved_in_backup_before_recovery(tmp_path):
    """Malformed settings file is preserved in a unique local backup before recovery defaults load."""
    settings_file = tmp_path / "settings.json"
    corrupt_content = '{"settings": { "broken": true, '  # Unclosed JSON
    settings_file.write_text(corrupt_content, encoding="utf-8")

    mgr = ConfigManager(settings_file)

    assert mgr.is_settings_corrupt is True
    assert mgr.settings_backup_path is not None
    assert mgr.settings_backup_path.exists()
    assert mgr.settings_backup_path.read_text(encoding="utf-8") == corrupt_content
    # Original settings.json on disk must not have been overwritten by recovery defaults
    assert settings_file.read_text(encoding="utf-8") == corrupt_content


def test_malformed_credentials_remain_byte_for_byte_unchanged(tmp_path):
    """Malformed credentials file is never overwritten by get_accounts() and remains byte-for-byte intact."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"settings": {}, "accounts": []}), encoding="utf-8")
    credentials_file = tmp_path / "credentials.json"
    bad_creds = '{"accounts": [{"email": "canary@test.com", "password": "SuperSecretPassword123"'  # malformed
    credentials_file.write_text(bad_creds, encoding="utf-8")

    mgr = ConfigManager(settings_file)

    with pytest.raises(CorruptCredentialsError):
        mgr.get_accounts()

    assert mgr.is_credentials_corrupt is True
    # Credentials file must remain byte-for-byte unchanged
    assert credentials_file.read_text(encoding="utf-8") == bad_creds


def test_automatic_appearance_persistence_does_not_overwrite_invalid_original(tmp_path):
    """Automatic appearance persistence during startup does not overwrite an invalid settings file."""
    settings_file = tmp_path / "settings.json"
    corrupt_content = '{"broken_json": 123,'
    settings_file.write_text(corrupt_content, encoding="utf-8")

    mgr = ConfigManager(settings_file)
    assert mgr.is_settings_corrupt is True

    # Simulate UI applying appearance mode (calling set_setting)
    mgr.set_setting("AppearanceMode", "Light")

    # File on disk must STILL be the corrupt original, not overwritten
    assert settings_file.read_text(encoding="utf-8") == corrupt_content


def test_existing_backups_are_not_overwritten(tmp_path):
    """Multiple backups create uniquely named files and never overwrite existing backups."""
    settings_file = tmp_path / "settings.json"
    corrupt1 = '{"first_corrupt": true,'
    settings_file.write_text(corrupt1, encoding="utf-8")

    mgr1 = ConfigManager(settings_file)
    backup1 = mgr1.settings_backup_path
    assert backup1 is not None and backup1.exists()

    corrupt2 = '{"second_corrupt": true,'
    settings_file.write_text(corrupt2, encoding="utf-8")

    mgr2 = ConfigManager(settings_file)
    backup2 = mgr2.settings_backup_path
    assert backup2 is not None and backup2.exists()
    assert backup1 != backup2
    assert backup1.read_text(encoding="utf-8") == corrupt1
    assert backup2.read_text(encoding="utf-8") == corrupt2


def test_backup_failure_prevents_destructive_replacement(tmp_path):
    """If backup creation fails, settings file is never replaced with defaults."""
    settings_file = tmp_path / "settings.json"
    corrupt_content = '{"broken": 999,'
    settings_file.write_text(corrupt_content, encoding="utf-8")

    with patch.object(ConfigManager, "_create_unique_backup", side_effect=OSError("Disk write protected")):
        with pytest.raises(CorruptConfigurationError):
            ConfigManager(settings_file)

    # settings.json must remain byte-for-byte unchanged
    assert settings_file.read_text(encoding="utf-8") == corrupt_content


def test_invalid_root_types_handled(tmp_path):
    """Root structures that are not JSON objects are handled as corrupt without crashing."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('["item1", "item2"]', encoding="utf-8")  # list instead of dict

    mgr = ConfigManager(settings_file)
    assert mgr.is_settings_corrupt is True
    assert "Root of settings file" in mgr.settings_load_error

    # Credentials with invalid root
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(CorruptCredentialsError):
        mgr.get_accounts()


def test_malformed_channel_numbers_do_not_crash_startup(tmp_path):
    """Malformed numeric fields in channels do not crash startup or Channel deserialization."""
    raw_channel = {
        "name": "TechChannel",
        "url": "https://youtube.com/@tech",
        "watch_seconds": "invalid_string_watch",
        "target_watch_seconds": None,
    }
    ch = Channel.from_dict(raw_channel)
    assert ch.name == "TechChannel"
    assert ch.watch_seconds == 0.0
    assert ch.target_watch_seconds == 0.0

    settings_file = tmp_path / "settings.json"
    data = {
        "settings": {},
        "channels": [raw_channel],
        "videos": [],
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    channels = mgr.get_channels()
    assert len(channels) == 1
    assert channels[0]["name"] == "TechChannel"
    assert channels[0].get("watch_seconds", 0.0) == 0.0


def test_valid_entries_survive_alongside_invalid_entries(tmp_path):
    """Valid channel and video entries survive alongside malformed or corrupted entries."""
    settings_file = tmp_path / "settings.json"
    data = {
        "settings": {},
        "channels": [
            None,
            "not a dict",
            {"name": "", "url": "https://youtube.com/@blank"},
            {"name": "ValidChannel", "url": "https://youtube.com/@valid"},
        ],
        "videos": [
            None,
            "invalid video",
            {"url": "https://youtube.com/watch?v=valid1", "title": "Good Video"},
        ],
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    assert len(mgr.get_channels()) == 1
    assert mgr.get_channels()[0]["name"] == "ValidChannel"

    assert len(mgr.get_video_queue()) == 1
    assert mgr.get_video_queue()[0]["title"] == "Good Video"


def test_non_finite_values_rejected_safely(tmp_path):
    """NaN and Infinity in numeric fields are safely replaced with defaults without unlimited execution."""
    # Video task with NaN / Inf
    task_dict = {
        "url": "https://youtube.com/watch?v=123",
        "title": "Video",
        "target_multiplier": float("nan"),
        "max_runs": float("inf"),
        "duration_seconds": float("-inf"),
    }
    task = VideoTask.from_dict(task_dict)
    assert task.target_multiplier == 0.0
    assert task.duration_seconds == 0
    assert task.max_runs == 0

    # Channel with Inf
    ch = Channel.from_dict({"name": "C", "url": "https://youtube.com/@c", "watch_seconds": float("inf")})
    assert ch.watch_seconds == 0.0

    # Safety limits in settings: invalid or non-finite values must not become 0 (unlimited execution)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "settings": {
            "FullVideoTimeoutMinutes": float("nan"),
            "VideoCooldownHours": -5,
            "ParallelBrowsers": 0,
        }
    }), encoding="utf-8")
    mgr = ConfigManager(settings_file)
    assert mgr.get_int("FullVideoTimeoutMinutes", 5) == 5
    assert mgr.get_float("VideoCooldownHours", 12.0) == 12.0
    assert mgr.get_int("ParallelBrowsers", 1) == 1


def test_simulated_write_failure_preserves_usable_state(tmp_path):
    """Simulated write failure rolls back in-memory changes and does not pretend write succeeded."""
    settings_file = tmp_path / "settings.json"
    initial_data = {"settings": {"WatchtimeDuration": 100}, "accounts": [], "videos": [], "channels": []}
    settings_file.write_text(json.dumps(initial_data), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    assert mgr.get_int("WatchtimeDuration") == 100

    with patch("pathlib.Path.replace", side_effect=PermissionError("Locked by another process")):
        with pytest.raises(PermissionError):
            mgr.set_setting("WatchtimeDuration", 999)

    # In-memory value must have rolled back to 100
    assert mgr.get_int("WatchtimeDuration") == 100


def test_logs_and_errors_do_not_contain_password_canaries(tmp_path, caplog):
    """Error logs and user-facing messages never expose credentials or passwords."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"
    canary = "P@ssw0rdCanary_DoNotLeak!"
    # Corrupt credentials containing password canary
    credentials_file.write_text(f'{{"accounts": [{{"email": "user@test.com", "password": "{canary}"}},', encoding="utf-8")

    mgr = ConfigManager(settings_file)

    with pytest.raises(CorruptCredentialsError) as exc_info:
        mgr.get_accounts()

    # Neither exception message nor logs should contain the password canary
    assert canary not in str(exc_info.value)
    assert canary not in caplog.text
