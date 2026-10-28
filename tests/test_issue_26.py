"""Regression tests for Issue 26: Duplicate accounts and profile collisions."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.models.account import Account, normalize_account_id
from src.config.manager import ConfigManager, CredentialConflictError
from src.automation.profile_manager import (
    resolve_profile_dir,
    ProfileLock,
    ProfileBusyError,
    is_chrome_lock_active,
)
from src.automation.driver import cleanup_profile_locks, setup_driver, close_driver


def test_normalization_policy():
    """Account normalization trims whitespace, treats casing consistently, and keeps special characters."""
    assert normalize_account_id("  User.Name+Tag@Gmail.COM  ") == "user.name+tag@gmail.com"
    acc = Account(email="  My.Account+123@Domain.ORG  ", password="secret_password")
    assert acc.email == "my.account+123@domain.org"


def test_duplicate_manual_account_entry_is_rejected():
    """Duplicate manual entry with same password is rejected as duplicate; conflicting password as conflict."""
    from src.ui.views.accounts_view import AccountsView

    mock_app = MagicMock()
    mock_app.config_mgr.get_accounts.return_value = []
    view = AccountsView.__new__(AccountsView)
    view.app_controller = mock_app
    view.accounts = [Account(email="test@gmail.com", password="password123")]
    view.save_accounts = MagicMock()
    view.refresh_list = MagicMock()

    mock_dialog_inst = MagicMock()
    with patch("customtkinter.CTkInputDialog", return_value=mock_dialog_inst), \
         patch("tkinter.messagebox.showwarning") as mock_warn:
        mock_dialog_inst.get_input.return_value = "TEST@gmail.com:password123"
        view.add_account_dialog()
        mock_warn.assert_called_once()
        assert len(view.accounts) == 1
        view.save_accounts.assert_not_called()

    with patch("customtkinter.CTkInputDialog", return_value=mock_dialog_inst), \
         patch("tkinter.messagebox.showerror") as mock_err:
        mock_dialog_inst.get_input.return_value = "test@gmail.com:DIFFERENT_PW"
        view.add_account_dialog()
        mock_err.assert_called_once()
        assert len(view.accounts) == 1
        assert view.accounts[0].password == "password123"
        view.save_accounts.assert_not_called()


def test_duplicate_imported_accounts_do_not_launch_duplicate_sessions():
    """Duplicate imported accounts are deduplicated so duplicate sessions are never launched."""
    from src.ui.app import YTBotApp

    app = YTBotApp.__new__(YTBotApp)
    app.log_queue = MagicMock()
    app.update_dashboard_stats = MagicMock()

    # Pass duplicates with different casing
    input_accounts = [
        Account(email="user1@gmail.com", password="p1"),
        Account(email="USER1@gmail.com", password="p1"),
        Account(email="user2@gmail.com", password="p2"),
    ]
    app.on_accounts_changed(input_accounts)

    assert len(app.loaded_accounts) == 2
    assert [a.email for a in app.loaded_accounts] == ["user1@gmail.com", "user2@gmail.com"]


def test_conflicting_passwords_are_reported_without_data_loss(tmp_path):
    """Credentials loading raises CredentialConflictError on duplicate conflicting passwords without data loss."""
    creds_file = tmp_path / "credentials.json"
    conflicting_data = {
        "accounts": [
            {"email": "same@gmail.com", "password": "password_A"},
            {"email": "same@gmail.com", "password": "password_B"},
        ]
    }
    creds_file.write_text(json.dumps(conflicting_data), encoding="utf-8")

    mgr = ConfigManager(tmp_path / "settings.json")
    with pytest.raises(CredentialConflictError):
        mgr.get_accounts()

    # Verify original file content was preserved
    assert json.loads(creds_file.read_text(encoding="utf-8")) == conflicting_data


def test_two_distinct_addresses_that_previously_collided_receive_distinct_profile_identities():
    """Two addresses that produced the same sanitized filename receive distinct profile identities."""
    acc1 = Account(email="user.name@gmail.com", password="pw1")
    acc2 = Account(email="user_name@gmail.com", password="pw2")

    # Under old logic:
    assert acc1.get_legacy_profile_dir_name() == acc2.get_legacy_profile_dir_name()

    # Under new collision-resistant logic:
    assert acc1.get_profile_dir_name() != acc2.get_profile_dir_name()


def test_existing_unambiguous_profile_remains_reusable(tmp_path):
    """An existing unambiguous legacy profile is reused to preserve session cookies."""
    runtime_dir = tmp_path / "chrome_profiles"
    runtime_dir.mkdir()

    acc = Account(email="single.user@gmail.com", password="pw")
    legacy_folder = runtime_dir / acc.get_legacy_profile_dir_name()
    legacy_folder.mkdir()
    (legacy_folder / "Cookies.sqlite").write_text("cookie_data", encoding="utf-8")

    resolved = resolve_profile_dir(acc, known_accounts=[acc], runtime_dir=runtime_dir)
    assert resolved == legacy_folder
    assert (resolved / "Cookies.sqlite").exists()


def test_ambiguous_legacy_profile_ownership_does_not_copy_sessions_across_accounts(tmp_path):
    """Ambiguous legacy profile ownership does not copy sessions across accounts."""
    runtime_dir = tmp_path / "chrome_profiles"
    runtime_dir.mkdir()

    acc1 = Account(email="shared.name@gmail.com", password="pw1")
    acc2 = Account(email="shared_name@gmail.com", password="pw2")

    # Shared legacy directory on disk
    legacy_folder = runtime_dir / acc1.get_legacy_profile_dir_name()
    legacy_folder.mkdir()
    (legacy_folder / "canary.txt").write_text("private_session_canary", encoding="utf-8")

    known = [acc1, acc2]
    res1 = resolve_profile_dir(acc1, known_accounts=known, runtime_dir=runtime_dir)
    res2 = resolve_profile_dir(acc2, known_accounts=known, runtime_dir=runtime_dir)

    # Distinct profiles assigned
    assert res1 != res2
    assert res1 != legacy_folder
    assert res2 != legacy_folder

    # Canary was NOT copied over
    assert not (res1 / "canary.txt").exists()
    assert not (res2 / "canary.txt").exists()


def test_concurrent_launches_for_same_profile_cannot_both_proceed(tmp_path):
    """Concurrent launches for the same profile cannot both proceed."""
    profile_dir = tmp_path / "profile_test"
    profile_dir.mkdir()

    lock1 = ProfileLock(profile_dir)
    assert lock1.acquire() is True

    lock2 = ProfileLock(profile_dir)
    with pytest.raises(ProfileBusyError):
        lock2.acquire()

    lock1.release()
    # Now lock2 can acquire
    assert lock2.acquire() is True
    lock2.release()


def test_different_profiles_remain_independent(tmp_path):
    """Different profiles can be acquired concurrently without interference."""
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    p1.mkdir()
    p2.mkdir()

    lock1 = ProfileLock(p1)
    lock2 = ProfileLock(p2)

    assert lock1.acquire() is True
    assert lock2.acquire() is True

    lock1.release()
    lock2.release()


def test_startup_failure_releases_application_profile_ownership(tmp_path):
    """Browser startup failure releases application profile ownership."""
    acc = Account(email="fail@gmail.com", password="pw")
    with patch("src.automation.driver.resolve_profile_dir", return_value=tmp_path / "fail_prof"), \
         patch("src.automation.driver.get_chrome_version", return_value="120.0.0.0"), \
         patch("undetected_chromedriver.Chrome", side_effect=RuntimeError("Chrome crashed on boot")):
        with pytest.raises(RuntimeError, match="Chrome crashed on boot"):
            setup_driver(acc)

    # Profile lock must be completely released
    lock = ProfileLock(tmp_path / "fail_prof")
    assert lock.acquire() is True
    lock.release()


def test_cleanup_never_targets_unrelated_chrome_process(tmp_path):
    """Cleanup does not run shell kill commands and raises if profile is actively in use."""
    profile_dir = tmp_path / "active_profile"
    profile_dir.mkdir()
    singleton = profile_dir / "SingletonLock"
    singleton.write_text("lock_content", encoding="utf-8")

    # Simulate an actively locked SingletonLock
    with patch("src.automation.driver.is_chrome_lock_active", return_value=True), \
         patch("subprocess.run") as mock_subproc:
        with pytest.raises(ProfileBusyError, match="actively running"):
            cleanup_profile_locks(profile_dir)
        mock_subproc.assert_not_called()
        assert singleton.exists()
