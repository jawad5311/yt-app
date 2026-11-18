"""Tests for standalone account profile authentication and AccountsView Authenticate button."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.automation.account_auth import (
    AuthResult,
    AuthStatus,
    authenticate_account_profile,
    has_existing_profile_data,
)
from src.automation.login import LoginState
from src.automation.profile_manager import ProfileBusyError, resolve_profile_dir
from src.models.account import Account
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.accounts_view import AccountsView


def _create_fake_profile(runtime_dir: Path, account: Account) -> Path:
    profile_dir = resolve_profile_dir(account, known_accounts=[account], runtime_dir=runtime_dir)
    (profile_dir / "Default" / "Network").mkdir(parents=True, exist_ok=True)
    (profile_dir / "Local State").write_text("{}", encoding="utf-8")
    (profile_dir / "Default" / "Network" / "Cookies").write_bytes(b"sqlite")
    return profile_dir


def test_has_existing_profile_data(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    assert has_existing_profile_data(empty_dir) is False

    empty_dir.mkdir()
    assert has_existing_profile_data(empty_dir) is False

    (empty_dir / "Default").mkdir()
    assert has_existing_profile_data(empty_dir) is False

    (empty_dir / "Default" / "Preferences").write_text("{}", encoding="utf-8")
    assert has_existing_profile_data(empty_dir) is True


def test_authenticate_first_time_opens_visible_chrome_and_stores_profile(tmp_path: Path):
    """First-time account skips headless check, opens visible Chrome, logs in, and closes cleanly."""
    account = Account(email="newuser@gmail.com", password="secret")
    fake_driver = MagicMock()
    stages = []
    driver_events = []

    with patch("src.automation.account_auth.setup_driver", return_value=fake_driver) as mock_setup, \
         patch("src.automation.account_auth.perform_google_login", return_value=True) as mock_login, \
         patch("src.automation.account_auth.close_driver") as mock_close, \
         patch("src.automation.account_auth._wait_interruptible", return_value=False):
        result = authenticate_account_profile(
            account=account,
            known_accounts=[account],
            runtime_dir=tmp_path,
            progress_callback=stages.append,
            active_driver_callback=driver_events.append,
        )

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.is_logged_in is True
    assert result.profile_dir == resolve_profile_dir(account, known_accounts=[account], runtime_dir=tmp_path)
    assert "Start Browsing" in result.message

    # Must launch Chrome ONCE in visible mode (no headless pre-check when profile does not exist)
    mock_setup.assert_called_once()
    assert mock_setup.call_args.kwargs["headless"] is False
    assert mock_setup.call_args.kwargs["is_reporting"] is False

    # perform_google_login must not send email alerts during manual account setup
    mock_login.assert_called_once()
    assert mock_login.call_args.kwargs["config_mgr"] is None
    assert mock_login.call_args.kwargs["alert_callback"] is None
    assert mock_login.call_args.kwargs["wait_for_challenge_resolution"] is True

    mock_close.assert_called_once_with(fake_driver, log_queue=None)
    assert driver_events == [fake_driver, None]
    assert "Opening Chrome..." in stages
    assert "Logging In..." in stages


def test_authenticate_existing_profile_already_logged_in_reports_without_visible_browser(tmp_path: Path):
    """Existing profile that is already signed in verifies headlessly and returns ALREADY_LOGGED_IN."""
    account = Account(email="existing@gmail.com", password="secret")
    profile_dir = _create_fake_profile(tmp_path, account)

    headless_driver = MagicMock()
    headless_driver.current_url = "https://myaccount.google.com/"
    stages = []

    with patch("src.automation.account_auth.setup_driver", return_value=headless_driver) as mock_setup, \
         patch("src.automation.account_auth.safe_get") as mock_get, \
         patch("src.automation.account_auth.check_login_state", return_value=LoginState.SIGNED_IN_MATCH), \
         patch("src.automation.account_auth.perform_google_login") as mock_login, \
         patch("src.automation.account_auth.close_driver") as mock_close:
        result = authenticate_account_profile(
            account=account,
            known_accounts=[account],
            runtime_dir=tmp_path,
            progress_callback=stages.append,
        )

    assert result.status == AuthStatus.ALREADY_LOGGED_IN
    assert result.is_logged_in is True
    assert result.profile_dir == profile_dir
    assert "already logged in" in result.message.lower()

    # Only headless check was run; visible browser & perform_google_login were NOT needed
    mock_setup.assert_called_once()
    assert mock_setup.call_args.kwargs["headless"] is True
    mock_get.assert_called_once()
    mock_login.assert_not_called()
    mock_close.assert_called_once_with(headless_driver, log_queue=None)
    assert stages == ["Checking..."]


def test_authenticate_existing_profile_signed_out_opens_visible_browser_and_logs_in(tmp_path: Path):
    """Existing profile whose session expired closes headless check and opens visible Chrome to log in."""
    account = Account(email="expired@gmail.com", password="secret")
    _create_fake_profile(tmp_path, account)

    headless_driver = MagicMock()
    headless_driver.current_url = "https://accounts.google.com/v3/signin/identifier"
    visible_driver = MagicMock()
    stages = []

    with patch("src.automation.account_auth.setup_driver", side_effect=[headless_driver, visible_driver]) as mock_setup, \
         patch("src.automation.account_auth.safe_get"), \
         patch("src.automation.account_auth._has_visible_email_input", return_value=True), \
         patch("src.automation.account_auth.check_login_state", return_value=LoginState.SIGNED_OUT), \
         patch("src.automation.account_auth.perform_google_login", return_value=True) as mock_login, \
         patch("src.automation.account_auth.close_driver") as mock_close, \
         patch("src.automation.account_auth._wait_interruptible", return_value=False):
        result = authenticate_account_profile(
            account=account,
            known_accounts=[account],
            runtime_dir=tmp_path,
            progress_callback=stages.append,
            lock_settle_seconds=0.0,
        )

    assert result.status == AuthStatus.AUTHENTICATED
    assert mock_setup.call_count == 2
    assert mock_setup.call_args_list[0].kwargs["headless"] is True
    assert mock_setup.call_args_list[1].kwargs["headless"] is False
    mock_login.assert_called_once()
    assert mock_close.call_count == 2
    assert stages == ["Checking...", "Opening Chrome...", "Logging In..."]


def test_authenticate_existing_profile_myaccount_intro_treated_as_signed_out(tmp_path: Path):
    """myaccount.google.com/intro is Google's signed-out landing page and must trigger visible login."""
    account = Account(email="intro@gmail.com", password="secret")
    _create_fake_profile(tmp_path, account)

    headless_driver = MagicMock()
    headless_driver.current_url = "https://myaccount.google.com/intro"
    visible_driver = MagicMock()

    with patch("src.automation.account_auth.setup_driver", side_effect=[headless_driver, visible_driver]), \
         patch("src.automation.account_auth.safe_get"), \
         patch("src.automation.account_auth.perform_google_login", return_value=True) as mock_login, \
         patch("src.automation.account_auth.close_driver"), \
         patch("src.automation.account_auth._wait_interruptible", return_value=False):
        result = authenticate_account_profile(
            account=account,
            known_accounts=[account],
            runtime_dir=tmp_path,
            lock_settle_seconds=0.0,
        )

    assert result.status == AuthStatus.AUTHENTICATED
    mock_login.assert_called_once()


def test_authenticate_existing_profile_wrong_account(tmp_path: Path):
    """Profile signed into a different Google account reports WRONG_ACCOUNT."""
    account = Account(email="expected@gmail.com", password="secret")
    _create_fake_profile(tmp_path, account)

    headless_driver = MagicMock()
    headless_driver.current_url = "https://myaccount.google.com/"

    with patch("src.automation.account_auth.setup_driver", return_value=headless_driver) as mock_setup, \
         patch("src.automation.account_auth.safe_get"), \
         patch("src.automation.account_auth.check_login_state", return_value=LoginState.SIGNED_IN_OTHER), \
         patch("src.automation.account_auth.close_driver") as mock_close:
        result = authenticate_account_profile(
            account=account,
            known_accounts=[account],
            runtime_dir=tmp_path,
        )

    assert result.status == AuthStatus.WRONG_ACCOUNT
    assert result.is_logged_in is False
    mock_setup.assert_called_once()
    mock_close.assert_called_once_with(headless_driver, log_queue=None)


def test_authenticate_headless_fallback_to_visible_still_detects_already_logged_in(tmp_path: Path):
    """If headless check errors out, visible fallback still detects an already signed-in profile."""
    account = Account(email="fallback@gmail.com", password="secret")
    _create_fake_profile(tmp_path, account)

    visible_driver = MagicMock()
    visible_driver.current_url = "https://myaccount.google.com/"

    with patch(
        "src.automation.account_auth.setup_driver",
        side_effect=[RuntimeError("headless failed"), visible_driver],
    ) as mock_setup, \
         patch("src.automation.account_auth.safe_get"), \
         patch("src.automation.account_auth.check_login_state", return_value=LoginState.SIGNED_IN_MATCH), \
         patch("src.automation.account_auth.perform_google_login") as mock_login, \
         patch("src.automation.account_auth.close_driver") as mock_close:
        result = authenticate_account_profile(
            account=account,
            known_accounts=[account],
            runtime_dir=tmp_path,
            lock_settle_seconds=0.0,
        )

    assert result.status == AuthStatus.ALREADY_LOGGED_IN
    assert mock_setup.call_count == 2
    mock_login.assert_not_called()
    mock_close.assert_called_once_with(visible_driver, log_queue=None)


def test_authenticate_profile_busy_returns_busy_status(tmp_path: Path):
    """ProfileBusyError or active Chrome lock returns PROFILE_BUSY gracefully."""
    account = Account(email="busy@gmail.com", password="secret")

    with patch("src.automation.account_auth.is_chrome_lock_active", return_value=True):
        result = authenticate_account_profile(account=account, known_accounts=[account], runtime_dir=tmp_path)
    assert result.status == AuthStatus.PROFILE_BUSY

    with patch("src.automation.account_auth.is_chrome_lock_active", return_value=False), \
         patch("src.automation.account_auth.setup_driver", side_effect=ProfileBusyError("locked")):
        result2 = authenticate_account_profile(account=account, known_accounts=[account], runtime_dir=tmp_path)
    assert result2.status == AuthStatus.PROFILE_BUSY


def test_authenticate_browser_closed_by_user_returns_cancelled(tmp_path: Path):
    """If user closes Chrome window during sign-in, result is CANCELLED and driver is closed."""
    account = Account(email="closed@gmail.com", password="secret")
    visible_driver = MagicMock()

    with patch("src.automation.account_auth.setup_driver", return_value=visible_driver), \
         patch(
             "src.automation.account_auth.perform_google_login",
             side_effect=RuntimeError("target window already closed"),
         ), \
         patch("src.automation.account_auth.close_driver") as mock_close:
        result = authenticate_account_profile(account=account, known_accounts=[account], runtime_dir=tmp_path)

    assert result.status == AuthStatus.CANCELLED
    mock_close.assert_called_once_with(visible_driver, log_queue=None)


def test_authenticate_login_failure_returns_failed(tmp_path: Path):
    account = Account(email="fail@gmail.com", password="secret")
    visible_driver = MagicMock()

    with patch("src.automation.account_auth.setup_driver", return_value=visible_driver), \
         patch("src.automation.account_auth.perform_google_login", return_value=False), \
         patch("src.automation.account_auth.check_login_state", return_value=LoginState.SIGNED_OUT), \
         patch("src.automation.account_auth.close_driver") as mock_close:
        result = authenticate_account_profile(account=account, known_accounts=[account], runtime_dir=tmp_path)

    assert result.status == AuthStatus.FAILED
    mock_close.assert_called_once_with(visible_driver, log_queue=None)


# ---------------------------------------------------------------------------
# UI Tests for AccountsView Authenticate Button & Badges
# ---------------------------------------------------------------------------


def test_accounts_view_renders_authenticate_button_and_profile_badge(ctk_app, tmp_path: Path):
    controller = MagicMock()
    controller.is_closing = False
    controller.config_mgr.get_accounts.return_value = [
        {"email": "alpha@gmail.com", "password": "pw1"},
        {"email": "beta@gmail.com", "password": "pw2"},
    ]
    controller.config_mgr.get_setting.return_value = {}

    alpha = Account(email="alpha@gmail.com", password="pw1")
    beta = Account(email="beta@gmail.com", password="pw2")
    _create_fake_profile(tmp_path, alpha)

    with patch(
        "src.ui.views.accounts_view.resolve_profile_dir",
        side_effect=lambda acc, known_accounts=None: resolve_profile_dir(acc, known_accounts=known_accounts, runtime_dir=tmp_path),
    ):
        view = AccountsView(ctk_app, controller)
        try:
            assert "alpha@gmail.com" in view._auth_buttons
            assert "beta@gmail.com" in view._auth_buttons
            assert "Authenticate" in view._auth_buttons["alpha@gmail.com"].cget("text")
            assert "Authenticate" in view._auth_buttons["beta@gmail.com"].cget("text")

            assert "Profile Saved" in view._auth_badges["alpha@gmail.com"].cget("text")
            assert "Not Logged In" in view._auth_badges["beta@gmail.com"].cget("text")
        finally:
            view.destroy()


def test_accounts_view_authenticate_already_logged_in_flow(ctk_app):
    """Clicking Authenticate runs via BackgroundJobs, updates badge to Logged In, and shows Already Logged In dialog."""
    controller = MagicMock()
    controller.is_closing = False
    controller.background_jobs = BackgroundJobs()
    controller.log_queue = None
    controller.config_mgr.get_accounts.return_value = [
        {"email": "ready@gmail.com", "password": "pw1"},
    ]
    controller.config_mgr.get_setting.return_value = {}

    done_event = threading.Event()

    def fake_auth(account, **kwargs):
        if kwargs.get("progress_callback"):
            kwargs["progress_callback"]("Checking...")
        done_event.set()
        return AuthResult(
            status=AuthStatus.ALREADY_LOGGED_IN,
            message=f"Account '{account.email}' is already logged in and authenticated.",
        )

    with patch("src.ui.views.accounts_view.authenticate_account_profile", side_effect=fake_auth), \
         patch("src.ui.views.accounts_view.has_existing_profile_data", return_value=True), \
         patch("src.ui.views.accounts_view.messagebox.showinfo") as mock_info:
        view = AccountsView(ctk_app, controller)
        try:
            acc = view.accounts[0]
            view.authenticate_account(acc)

            # Button disabled immediately while running
            assert str(view._auth_buttons[acc.email].cget("state")) == "disabled"
            assert view.is_authenticating() is True

            assert done_event.wait(timeout=3.0)
            # Drain BackgroundJobs on main thread until job finishes
            for _ in range(20):
                controller.background_jobs.drain()
                if not view.is_authenticating():
                    break
                ctk_app.update()
                threading.Event().wait(0.02)

            assert view.is_authenticating() is False
            assert str(view._auth_buttons[acc.email].cget("state")) == "normal"
            assert "Authenticate" in view._auth_buttons[acc.email].cget("text")
            assert "Logged In" in view._auth_badges[acc.email].cget("text")
            mock_info.assert_called_once()
            assert mock_info.call_args.args[0] == "Already Logged In"
        finally:
            view.destroy()


def test_accounts_view_authenticate_new_login_and_busy_popups(ctk_app):
    """Verify AUTHENTICATED shows info popup and PROFILE_BUSY shows warning popup."""
    controller = MagicMock()
    controller.is_closing = False
    controller.background_jobs = BackgroundJobs()
    controller.log_queue = None
    controller.config_mgr.get_accounts.return_value = [
        {"email": "user1@gmail.com", "password": "pw1"},
        {"email": "user2@gmail.com", "password": "pw2"},
    ]
    controller.config_mgr.get_setting.return_value = {}

    ev1 = threading.Event()
    ev2 = threading.Event()

    def fake_auth(account, **kwargs):
        if account.email == "user1@gmail.com":
            ev1.set()
            return AuthResult(status=AuthStatus.AUTHENTICATED, message="Authenticated user1")
        ev2.set()
        return AuthResult(status=AuthStatus.PROFILE_BUSY, message="Profile busy for user2")

    with patch("src.ui.views.accounts_view.authenticate_account_profile", side_effect=fake_auth), \
         patch("src.ui.views.accounts_view.has_existing_profile_data", return_value=False), \
         patch("src.ui.views.accounts_view.messagebox.showinfo") as mock_info, \
         patch("src.ui.views.accounts_view.messagebox.showwarning") as mock_warn:
        view = AccountsView(ctk_app, controller)
        try:
            view.authenticate_account(view.accounts[0])
            view.authenticate_account(view.accounts[1])
            assert ev1.wait(timeout=3.0)
            assert ev2.wait(timeout=3.0)

            for _ in range(30):
                controller.background_jobs.drain()
                if not view.is_authenticating():
                    break
                ctk_app.update()
                threading.Event().wait(0.02)

            assert not view.is_authenticating()
            assert "Logged In" in view._auth_badges["user1@gmail.com"].cget("text")
            assert "Not Logged In" in view._auth_badges["user2@gmail.com"].cget("text")
            mock_info.assert_called_once()
            assert mock_info.call_args.args[0] == "Account Authenticated"
            mock_warn.assert_called_once()
            assert mock_warn.call_args.args[0] == "Profile In Use"
        finally:
            view.destroy()


def test_accounts_view_stop_all_authentications_closes_drivers(ctk_app):
    controller = MagicMock()
    controller.config_mgr.get_accounts.return_value = []
    view = AccountsView(ctk_app, controller)
    try:
        evt = threading.Event()
        fake_drv = MagicMock()
        view._auth_stop_events["a@b.com"] = evt
        view._auth_drivers["a@b.com"] = fake_drv

        with patch("src.ui.views.accounts_view.close_driver") as mock_close:
            view.stop_all_authentications()

        assert evt.is_set() is True
        mock_close.assert_called_once_with(fake_drv)
        assert view._auth_drivers == {}
    finally:
        view.destroy()
