import json
from unittest.mock import patch

import pytest

from src.config.manager import ConfigManager, ConfigurationError, CorruptCredentialsError, CredentialConflictError


def test_missing_credentials_plus_valid_legacy_migrates_and_removes(tmp_path):
    """Missing credentials file with valid legacy accounts in settings migrates and clears legacy copy."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    data = {
        "settings": {"AppearanceMode": "Dark"},
        "accounts": [{"email": "user1@test.com", "password": "SecretPassword1"}],
        "channels": [{"name": "C1", "url": "https://youtube.com/@c1"}],
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    accounts = mgr.get_accounts()

    # Credentials file created with legacy accounts
    assert credentials_file.exists()
    assert len(accounts) == 1
    assert accounts[0]["email"] == "user1@test.com"
    assert accounts[0]["password"] == "SecretPassword1"

    # settings.json on disk must have accounts cleared!
    saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved_settings["accounts"] == []
    # Unrelated channel preserved
    assert len(saved_settings["channels"]) == 1


def test_empty_credentials_plus_legacy_accounts(tmp_path):
    """Empty credentials file plus legacy accounts in settings migrates and clears legacy copy."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    settings_file.write_text(json.dumps({
        "settings": {},
        "accounts": [{"email": "user2@test.com", "password": "SecretPassword2"}],
    }), encoding="utf-8")
    credentials_file.write_text(json.dumps({"accounts": []}), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    accounts = mgr.get_accounts()

    assert len(accounts) == 1
    assert accounts[0]["email"] == "user2@test.com"

    saved_creds = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert len(saved_creds["accounts"]) == 1
    assert saved_creds["accounts"][0]["email"] == "user2@test.com"

    saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved_settings["accounts"] == []


def test_nonempty_credentials_plus_nonconflicting_legacy(tmp_path):
    """Nonempty credentials plus nonconflicting legacy accounts preserves both sources correctly."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    settings_file.write_text(json.dumps({
        "settings": {},
        "accounts": [{"email": "new@test.com", "password": "NewPassword"}],
    }), encoding="utf-8")
    credentials_file.write_text(json.dumps({
        "accounts": [{"email": "existing@test.com", "password": "ExistingPassword"}],
    }), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    accounts = mgr.get_accounts()

    assert len(accounts) == 2
    emails = {a["email"] for a in accounts}
    assert emails == {"existing@test.com", "new@test.com"}

    saved_creds = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert len(saved_creds["accounts"]) == 2

    saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved_settings["accounts"] == []


def test_identical_entries_are_not_duplicated(tmp_path):
    """Identical account entries across legacy and credentials files are deduplicated."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    settings_file.write_text(json.dumps({
        "settings": {},
        "accounts": [{"email": "same@test.com", "password": "SamePassword"}],
    }), encoding="utf-8")
    credentials_file.write_text(json.dumps({
        "accounts": [{"email": "same@test.com", "password": "SamePassword"}],
    }), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    accounts = mgr.get_accounts()

    assert len(accounts) == 1
    assert accounts[0]["email"] == "same@test.com"

    saved_creds = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert len(saved_creds["accounts"]) == 1

    saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved_settings["accounts"] == []


def test_conflicting_passwords_produce_explicit_conflict_without_data_loss(tmp_path):
    """Conflicting passwords for the same email produce CredentialConflictError without data loss."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    settings_data = {
        "settings": {},
        "accounts": [{"email": "conflict@test.com", "password": "PasswordVersionA"}],
    }
    creds_data = {
        "accounts": [{"email": "conflict@test.com", "password": "PasswordVersionB"}],
    }
    settings_file.write_text(json.dumps(settings_data), encoding="utf-8")
    credentials_file.write_text(json.dumps(creds_data), encoding="utf-8")

    mgr = ConfigManager(settings_file)

    with pytest.raises(CredentialConflictError) as exc_info:
        mgr.get_accounts()

    assert "conflict@test.com" in str(exc_info.value)
    # Neither password should be in the error message
    assert "PasswordVersionA" not in str(exc_info.value)
    assert "PasswordVersionB" not in str(exc_info.value)

    # Both files must remain completely unchanged (no data loss)
    assert json.loads(settings_file.read_text(encoding="utf-8"))["accounts"] == settings_data["accounts"]
    assert json.loads(credentials_file.read_text(encoding="utf-8")) == creds_data


def test_invalid_credentials_block_migration_without_overwriting(tmp_path):
    """Invalid credentials file blocks migration and is not overwritten."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    settings_data = {
        "settings": {},
        "accounts": [{"email": "legacy@test.com", "password": "LegacyPassword"}],
    }
    bad_creds_raw = "broken JSON { [ } <corrupt>"
    settings_file.write_text(json.dumps(settings_data), encoding="utf-8")
    credentials_file.write_text(bad_creds_raw, encoding="utf-8")

    mgr = ConfigManager(settings_file)

    with pytest.raises(CorruptCredentialsError):
        mgr.get_accounts()

    # Neither file modified
    assert credentials_file.read_text(encoding="utf-8") == bad_creds_raw
    assert json.loads(settings_file.read_text(encoding="utf-8"))["accounts"] == settings_data["accounts"]


def test_destination_write_failure_preserves_legacy_accounts(tmp_path):
    """If writing destination credentials fails, legacy settings accounts are left untouched."""
    settings_file = tmp_path / "settings.json"
    settings_data = {
        "settings": {},
        "accounts": [{"email": "keep_me@test.com", "password": "KeepMePassword"}],
    }
    settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    mgr = ConfigManager(settings_file)

    with patch.object(mgr, "_save_credentials_unlocked", side_effect=OSError("Disk write error")):
        with pytest.raises(OSError):
            mgr.get_accounts()

    # Legacy settings accounts must still be present
    assert json.loads(settings_file.read_text(encoding="utf-8"))["accounts"] == settings_data["accounts"]


def test_settings_cleanup_failure_leaves_recoverable_migration_state(tmp_path):
    """If cleaning settings fails after credentials were saved, error is reported and credentials file is retained."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"
    settings_data = {
        "settings": {},
        "accounts": [{"email": "mig@test.com", "password": "MigPassword"}],
    }
    settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    mgr = ConfigManager(settings_file)

    # Simulate settings write error during cleanup
    with patch.object(mgr, "_save_unlocked", side_effect=OSError("Settings locked")):
        with pytest.raises(ConfigurationError) as exc_info:
            mgr.migrate_credentials()
        assert "Migration incomplete" in str(exc_info.value)

    # Valid credentials file was written
    assert credentials_file.exists()
    saved_creds = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert saved_creds["accounts"][0]["email"] == "mig@test.com"


def test_second_migration_attempt_completes_without_duplicates(tmp_path):
    """A subsequent migration run completes cleanup without duplicating accounts."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"
    settings_data = {
        "settings": {},
        "accounts": [{"email": "mig@test.com", "password": "MigPassword"}],
    }
    settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    mgr = ConfigManager(settings_file)

    # First attempt fails at settings cleanup
    with patch.object(mgr, "_save_unlocked", side_effect=OSError("Settings locked")):
        with pytest.raises(ConfigurationError):
            mgr.migrate_credentials()

    # Second attempt succeeds without mocking failure
    mgr.migrate_credentials()

    saved_creds = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert len(saved_creds["accounts"]) == 1
    assert saved_creds["accounts"][0]["email"] == "mig@test.com"

    saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved_settings["accounts"] == []


def test_unrelated_settings_and_counters_remain_unchanged(tmp_path):
    """Migration preserves all unrelated settings, channels, watch times, and counters."""
    settings_file = tmp_path / "settings.json"
    settings_data = {
        "settings": {
            "AppearanceMode": "System",
            "FullVideoTimeoutMinutes": 7,
            "VideoCooldownHours": 18.0,
        },
        "accounts": [{"email": "preserve@test.com", "password": "Password123"}],
        "channels": [
            {"name": "Gaming", "url": "https://youtube.com/@gaming", "watch_seconds": 1234.5, "target_watch_seconds": 3600.0}
        ],
        "videos": [
            {"url": "https://youtube.com/watch?v=game1", "title": "Game 1", "max_runs": 2}
        ],
    }
    settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    mgr.get_accounts()

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["accounts"] == []
    assert saved["settings"]["AppearanceMode"] == "System"
    assert saved["settings"]["FullVideoTimeoutMinutes"] == 7
    assert saved["settings"]["VideoCooldownHours"] == 18.0
    assert saved["channels"][0]["watch_seconds"] == 1234.5
    assert saved["channels"][0]["target_watch_seconds"] == 3600.0
    assert saved["videos"][0]["max_runs"] == 2


def test_clearing_accounts_and_restarting_does_not_resurrect(tmp_path):
    """Intentionally clearing accounts via set_accounts([]) persists across restart without resurrection."""
    settings_file = tmp_path / "settings.json"
    credentials_file = tmp_path / "credentials.json"

    # Start with legacy accounts
    settings_file.write_text(json.dumps({
        "settings": {},
        "accounts": [{"email": "old@test.com", "password": "OldPassword"}],
    }), encoding="utf-8")

    mgr1 = ConfigManager(settings_file)
    assert len(mgr1.get_accounts()) == 1

    # User intentionally clears accounts
    mgr1.set_accounts([])
    assert mgr1.get_accounts() == []

    # App restarts: initialize new manager
    mgr2 = ConfigManager(settings_file)
    assert mgr2.get_accounts() == []
    assert json.loads(credentials_file.read_text(encoding="utf-8"))["accounts"] == []
    assert json.loads(settings_file.read_text(encoding="utf-8"))["accounts"] == []


def test_logs_contain_no_password_canaries(tmp_path, caplog):
    """Migration summaries and log messages never contain password canaries."""
    settings_file = tmp_path / "settings.json"
    canary = "UltraSecretPassword999!"

    settings_file.write_text(json.dumps({
        "settings": {},
        "accounts": [{"email": "canary@test.com", "password": canary}],
    }), encoding="utf-8")

    mgr = ConfigManager(settings_file)
    mgr.get_accounts()

    # Check all captured log output
    assert canary not in caplog.text
