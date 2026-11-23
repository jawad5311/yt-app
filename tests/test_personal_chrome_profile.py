import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.automation.driver import setup_driver
from src.automation.profile_manager import (
    ProfileBusyError,
    get_active_system_profile,
    get_system_chrome_profiles,
    get_system_chrome_user_data_dir,
    is_default_chrome_user_data_dir,
    is_system_chrome_running,
)
from src.config.manager import ConfigManager


def test_get_system_chrome_user_data_dir():
    user_data = get_system_chrome_user_data_dir()
    assert isinstance(user_data, Path)
    assert "Google" in str(user_data) or "chrome" in str(user_data).lower()


def test_get_system_chrome_profiles_from_mocked_local_state(tmp_path):
    mock_user_data = tmp_path / "Google" / "Chrome" / "User Data"
    mock_user_data.mkdir(parents=True)
    local_state = mock_user_data / "Local State"

    state_content = {
        "profile": {
            "info_cache": {
                "Default": {
                    "name": "Personal Test",
                    "user_name": "tester@gmail.com",
                    "gaia_name": "Test User",
                    "is_consented_primary_account": True,
                },
                "Profile 1": {
                    "name": "Work Account",
                    "user_name": "work@company.com",
                    "gaia_name": "Work User",
                    "is_consented_primary_account": False,
                },
            }
        }
    }
    local_state.write_text(json.dumps(state_content), encoding="utf-8")

    with patch("src.automation.profile_manager.get_system_chrome_user_data_dir", return_value=mock_user_data):
        profiles = get_system_chrome_profiles()
        assert len(profiles) == 2
        folders = [p["folder"] for p in profiles]
        assert "Default" in folders
        assert "Profile 1" in folders

        default_prof = next(p for p in profiles if p["folder"] == "Default")
        assert default_prof["email"] == "tester@gmail.com"
        assert default_prof["name"] == "Personal Test"
        assert default_prof["is_primary"] is True


def test_get_active_system_profile_fallback(tmp_path):
    mock_user_data = tmp_path / "Google" / "Chrome" / "User Data"
    mock_user_data.mkdir(parents=True)

    with patch("src.automation.profile_manager.get_system_chrome_user_data_dir", return_value=mock_user_data):
        active = get_active_system_profile("Default")
        assert active["folder"] == "Default"


def test_is_system_chrome_running_lockfile(tmp_path):
    mock_user_data = tmp_path / "Google" / "Chrome" / "User Data"
    mock_user_data.mkdir(parents=True)
    lock_file = mock_user_data / "lockfile"
    lock_file.touch()

    with patch("src.automation.profile_manager.get_system_chrome_user_data_dir", return_value=mock_user_data):
        with patch("builtins.open", side_effect=PermissionError("Locked by Chrome")):
            assert is_system_chrome_running() is True


def test_config_manager_system_profile_settings(tmp_path):
    cfg_file = tmp_path / "settings.json"
    cfg = ConfigManager(cfg_file)

    assert cfg.get_use_system_chrome_profile() is True
    assert cfg.get_system_chrome_profile_name() == "Default"

    cfg.set_use_system_chrome_profile(False)
    assert cfg.get_use_system_chrome_profile() is False

    cfg.set_system_chrome_profile_name("Profile 2")
    assert cfg.get_system_chrome_profile_name() == "Profile 2"


def test_setup_driver_busy_raises_error():
    with patch("src.automation.driver.is_system_chrome_running", return_value=True):
        with pytest.raises(ProfileBusyError) as exc_info:
            setup_driver(use_system_profile=True)
        assert "Google Chrome is currently open" in str(exc_info.value)


def test_setup_driver_options_system_profile_flags(tmp_path):
    mock_sys_dir = tmp_path / "User Data"
    mock_sys_dir.mkdir(parents=True)

    captured_options = []

    def mock_uc_chrome(options=None, **kwargs):
        captured_options.append(options)
        driver_mock = MagicMock()
        driver_mock.patcher.executable_path = str(tmp_path / "chromedriver.exe")
        return driver_mock

    with patch("src.automation.driver.is_system_chrome_running", return_value=False):
        with patch("src.automation.driver.get_system_chrome_user_data_dir", return_value=mock_sys_dir):
            with patch("src.automation.driver.ensure_chromedriver", return_value=None):
                with patch("undetected_chromedriver.Chrome", side_effect=mock_uc_chrome):
                    setup_driver(use_system_profile=True, system_profile_name="Default")

    assert len(captured_options) == 1
    opts = captured_options[0]
    args = opts.arguments

    # Verify user data dir points to system chrome directory
    user_data_arg = next((a for a in args if "--user-data-dir=" in a), None)
    assert user_data_arg is not None
    assert str(mock_sys_dir) in user_data_arg

    # Verify profile directory is passed
    assert "--profile-directory=Default" in args

    # Verify disable-gpu and headless flags are removed
    assert "--disable-gpu" not in args
    assert "--headless=new" not in args
    assert "--headless" not in args
    assert "--start-maximized" in args


def test_setup_driver_personal_profile_always_uses_existing_profile_directly(tmp_path):
    """Personal profile should always directly target the user data directory without creating an isolated copy."""
    system_dir = tmp_path / "User Data"
    system_dir.mkdir(parents=True)
    captured_options = []
    driver_mock = MagicMock()
    driver_mock.patcher.executable_path = str(tmp_path / "chromedriver.exe")

    def mock_uc_chrome(options=None, **kwargs):
        captured_options.append(options)
        return driver_mock

    with patch("src.automation.driver.is_system_chrome_running", return_value=False), \
         patch("src.automation.driver.get_system_chrome_user_data_dir", return_value=system_dir), \
         patch("src.automation.driver.cleanup_profile_locks") as cleanup_locks, \
         patch("src.automation.driver.ensure_chromedriver", return_value=None), \
         patch("src.automation.driver.sync_reporting_profile") as sync_profile, \
         patch("undetected_chromedriver.Chrome", side_effect=mock_uc_chrome):
        driver = setup_driver(use_system_profile=True, system_profile_name="Default")

    assert driver is driver_mock
    # Verify no profile sync or isolated copy was created
    sync_profile.assert_not_called()
    cleanup_locks.assert_called_once_with(system_dir)
    assert len(captured_options) == 1
    args = captured_options[0].arguments
    assert f"--user-data-dir={system_dir}" in args
    assert "--profile-directory=Default" in args
    assert "--password-store=basic" not in args
    assert driver._is_system_profile is True
    driver._profile_lock.release()


def test_app_ensure_active_accounts_populates_when_chrome_closed(tmp_path):
    from src.ui.app import YTBotApp

    mock_app = MagicMock(spec=YTBotApp)
    mock_app.config_mgr = ConfigManager(tmp_path / "settings.json")
    mock_app.config_mgr.set_use_system_chrome_profile(True)
    mock_app.loaded_accounts = []
    mock_app.log_queue = MagicMock()

    with patch("src.automation.profile_manager.is_system_chrome_running", return_value=False):
        with patch("src.automation.profile_manager.get_active_system_profile", return_value={"folder": "Default", "email": "auto_sys@gmail.com"}):
            ok = YTBotApp._ensure_active_accounts_for_session(mock_app)
            assert ok is True
            assert len(mock_app.loaded_accounts) == 1
            assert mock_app.loaded_accounts[0].email == "auto_sys@gmail.com"


def test_app_ensure_active_accounts_rejects_when_chrome_open(tmp_path):
    from src.ui.app import YTBotApp

    mock_app = MagicMock(spec=YTBotApp)
    mock_app.config_mgr = ConfigManager(tmp_path / "settings.json")
    mock_app.config_mgr.set_use_system_chrome_profile(True)
    mock_app.loaded_accounts = []
    mock_app.log_queue = MagicMock()

    with patch("src.automation.profile_manager.is_system_chrome_running", return_value=True):
        with patch("tkinter.messagebox.showwarning"):
            ok = YTBotApp._ensure_active_accounts_for_session(mock_app)
            assert ok is False
            assert len(mock_app.loaded_accounts) == 0



def test_warmup_manager_exempts_personal_chrome_profile_by_default(tmp_path):
    from src.automation.warmup_manager import WarmupManager

    cfg = ConfigManager(tmp_path / "settings.json")
    cfg.set_use_system_chrome_profile(True)

    with patch("src.automation.profile_manager.get_active_system_profile", return_value={"folder": "Default", "email": "personal_test@gmail.com"}):
        wm = WarmupManager(cfg)
        w_cfg = wm.get_account_config("personal_test@gmail.com")
        assert w_cfg.enabled is False


def test_accounts_view_has_personal_warmup_button(ctk_app, tmp_path):
    from src.ui.views.accounts_view import AccountsView

    controller = MagicMock()
    controller.is_closing = False
    controller.config_mgr = ConfigManager(tmp_path / "settings.json")
    controller.config_mgr.set_use_system_chrome_profile(True)
    controller.appearance_mode = "Dark"

    with patch("src.ui.views.accounts_view.get_active_system_profile", return_value={"folder": "Default", "email": "personal_test@gmail.com"}):
        with patch("src.ui.views.accounts_view.is_system_chrome_running", return_value=False):
            view = AccountsView(ctk_app, controller)
            assert hasattr(view, "personal_warmup_btn")
            assert hasattr(view, "personal_warmup_pill")
            assert view.personal_warmup_btn.cget("text") == "🔥 Warm Up"

            with patch("src.ui.views.accounts_view.WarmupDialog") as mock_dialog:
                view.open_personal_warmup_dialog()
                assert mock_dialog.called
                call_args = mock_dialog.call_args[1]
                assert call_args["account"].email == "personal_test@gmail.com"


def test_dashboard_view_chrome_status_pill_states(ctk_app, tmp_path):
    from src.ui.views.dashboard_view import DashboardView

    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr = ConfigManager(tmp_path / "settings.json")
    controller.config_mgr.set_use_system_chrome_profile(True)

    with patch("src.ui.views.dashboard_view.is_system_chrome_running", return_value=False):
        view = DashboardView(ctk_app, controller)
        assert hasattr(view, "chrome_status_pill")
        # Initially Chrome closed
        assert "Chrome Closed" in view.chrome_status_pill.cget("text")
        assert view.chrome_status_pill.cget("bg") == "#064e3b"

    # State: Chrome is open
    with patch("src.ui.views.dashboard_view.is_system_chrome_running", return_value=True):
        view._update_chrome_status_pill()
        assert "Chrome is Open" in view.chrome_status_pill.cget("text")
        assert view.chrome_status_pill.cget("bg") == "#7f1d1d"

    # State: Automation is actively running
    controller.is_running = True
    view.set_running_state(True)
    assert "Chrome In Use" in view.chrome_status_pill.cget("text")
    assert view.chrome_status_pill.cget("bg") == "#064e3b"

    # State: Dedicated profiles mode active
    controller.is_running = False
    view.set_running_state(False)
    controller.config_mgr.set_use_system_chrome_profile(False)
    view._update_chrome_status_pill()
    assert "Dedicated Profiles Active" in view.chrome_status_pill.cget("text")
    assert view.chrome_status_pill.cget("bg") == "#334155"

    view.destroy()
    assert view._chrome_poll_job is None


def test_app_show_view_refreshes_dashboard_chrome_status_pill():
    from src.ui.app import YTBotApp

    mock_app = MagicMock(spec=YTBotApp)
    mock_app.current_view_name = ""
    mock_app.nav_buttons = {}
    mock_app.queued_video_tasks = []

    mock_dashboard = MagicMock()
    mock_app.views = {"dashboard": mock_dashboard}

    YTBotApp.show_view(mock_app, "dashboard")

    mock_dashboard.tkraise.assert_called_once()
    mock_dashboard.refresh_channel_stats.assert_called_once()
    mock_dashboard._update_chrome_status_pill.assert_called_once()


def test_is_default_chrome_user_data_dir(tmp_path):
    default_dir = get_system_chrome_user_data_dir()
    assert is_default_chrome_user_data_dir(default_dir) is True
    assert is_default_chrome_user_data_dir(tmp_path / "custom_chrome_data") is False


def test_watching_worker_personal_profile_bypasses_automated_login():
    """WatchingWorker should immediately verify the session and bypass automated credential entry for personal profile."""
    from src.automation.watching_worker import WatchingWorker
    from src.models.account import Account

    worker = MagicMock(spec=WatchingWorker)
    worker.account = Account(email="personal@gmail.com", password="")
    worker.stop_event = MagicMock()
    worker.stop_event.is_set.return_value = False
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_use_system_chrome_profile.return_value = True
    worker.driver = MagicMock()
    worker.driver._is_system_profile = True
    worker.driver.current_url = "https://www.youtube.com/"
    worker.log_queue = MagicMock()

    ok = WatchingWorker._ensure_signed_in_and_wait(worker)
    assert ok is True
    # Verify no attempt was made to enter email or password
    worker.driver.find_elements.assert_not_called()


def test_perform_google_login_personal_profile_bypasses_credentials():
    """perform_google_login should immediately return True without credential entry for personal profile."""
    from src.automation.login import perform_google_login
    from src.models.account import Account

    driver_mock = MagicMock()
    driver_mock._is_system_profile = True
    driver_mock.current_url = "https://www.youtube.com/"
    account = Account(email="personal@gmail.com", password="")
    log_queue = MagicMock()

    ok = perform_google_login(driver_mock, account, log_queue=log_queue)
    assert ok is True
    # Verify driver did not search for identifier input or attempt typing
    driver_mock.find_elements.assert_not_called()


def test_setup_driver_personal_profile_cleans_up_stale_locks(tmp_path):
    """setup_driver should clean up stale lock files on the system user data directory."""
    system_dir = tmp_path / "User Data"
    system_dir.mkdir(parents=True)
    driver_mock = MagicMock()
    driver_mock.patcher.executable_path = str(tmp_path / "chromedriver.exe")

    with patch("src.automation.driver.is_system_chrome_running", return_value=False), \
         patch("src.automation.driver.get_system_chrome_user_data_dir", return_value=system_dir), \
         patch("src.automation.driver.cleanup_profile_locks") as cleanup_locks, \
         patch("src.automation.driver.ensure_chromedriver", return_value=None), \
         patch("undetected_chromedriver.Chrome", return_value=driver_mock):
        driver = setup_driver(use_system_profile=True, system_profile_name="Default")

    cleanup_locks.assert_called_once_with(system_dir)
    assert driver is driver_mock
    driver._profile_lock.release()





