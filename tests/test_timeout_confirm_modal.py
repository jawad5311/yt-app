from unittest.mock import MagicMock, patch
import queue
import pytest
import customtkinter as ctk

from src.ui.app import YTBotApp
from src.ui.views.timeout_confirm_modal import FullVideoTimeoutConfirmModal
from src.ui.views.dashboard_view import DashboardView
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask


def test_timeout_modal_confirm(ctk_app):
    """Test that clicking Confirm sets confirmed to True and closes modal."""
    modal = FullVideoTimeoutConfirmModal(ctk_app, timeout_minutes=5)
    assert modal.timeout_minutes == 5
    assert modal.confirmed is False

    # Simulate confirm
    modal._on_confirm()
    assert modal.confirmed is True


def test_timeout_modal_cancel(ctk_app):
    """Test that clicking Cancel sets confirmed to False and closes modal."""
    modal = FullVideoTimeoutConfirmModal(ctk_app, timeout_minutes=2)
    assert modal.timeout_minutes == 2

    # Simulate cancel
    modal._on_cancel()
    assert modal.confirmed is False


def test_app_timeout_check_zero_minutes(ctk_app, tmp_path):
    """When FullVideoTimeoutMinutes is 0, no confirmation popup is displayed."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 0)

    app = ctk_app
    app.config_mgr = mgr
    app.log_queue = queue.Queue()

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal") as mock_modal_cls:
        result = YTBotApp._confirm_full_video_timeout_if_needed(app)
        assert result is True
        mock_modal_cls.assert_not_called()


def test_app_timeout_check_non_zero_user_confirms(ctk_app, tmp_path):
    """When FullVideoTimeoutMinutes is non-zero (e.g. 2), modal appears and user confirms."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 2)

    app = ctk_app
    app.config_mgr = mgr
    app.log_queue = queue.Queue()

    mock_modal_instance = MagicMock()
    mock_modal_instance.show.return_value = True

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal", return_value=mock_modal_instance) as mock_modal_cls:
        result = YTBotApp._confirm_full_video_timeout_if_needed(app)
        assert result is True
        mock_modal_cls.assert_called_once_with(app, timeout_minutes=2)
        mock_modal_instance.show.assert_called_once()


def test_app_timeout_check_non_zero_user_cancels(ctk_app, tmp_path):
    """When FullVideoTimeoutMinutes is non-zero (e.g. 10), modal appears and user cancels."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 10)

    app = ctk_app
    app.config_mgr = mgr
    app.log_queue = queue.Queue()

    mock_modal_instance = MagicMock()
    mock_modal_instance.show.return_value = False

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal", return_value=mock_modal_instance) as mock_modal_cls:
        result = YTBotApp._confirm_full_video_timeout_if_needed(app)
        assert result is False
        mock_modal_cls.assert_called_once_with(app, timeout_minutes=10)
        mock_modal_instance.show.assert_called_once()


def test_start_browsing_timeout_cancelled_reverts_button_and_nothing_runs(ctk_app, tmp_path):
    """When user cancels timeout popup, Start Browsing button reverts to idle text and nothing runs in background."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 15)
    mgr.set_channels([{"name": "TestChannel", "url": "https://youtube.com/@test", "enabled": True}])
    mgr.set_video_queue([{"title": "Video 1", "url": "https://youtube.com/watch?v=123", "channel": "TestChannel"}])

    app = MagicMock()
    app.config_mgr = mgr
    app.log_queue = queue.Queue()
    app.loaded_accounts = [Account(email="test@gmail.com", password="pwd")]
    app.queued_video_tasks = [VideoTask(title="Video 1", url="https://youtube.com/watch?v=123", channel="TestChannel")]
    app.is_running = False
    app.session_manager_thread = None
    app.is_closing = False
    app._get_owned_threads.return_value = []

    dashboard = DashboardView(ctk_app, app)
    app.views = {"dashboard": dashboard}

    # Bind real start_watching logic onto mock app
    app.start_watching = lambda: YTBotApp.start_watching(app)
    app._confirm_full_video_timeout_if_needed = lambda: YTBotApp._confirm_full_video_timeout_if_needed(app)

    assert dashboard.start_watching_btn.cget("text") == "▶ Start Browsing"

    mock_modal_instance = MagicMock()
    mock_modal_instance.show.return_value = False

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal", return_value=mock_modal_instance):
        # User clicks Start Browsing button
        dashboard.start_watching_btn.invoke()

    # Modal was invoked and user clicked Cancel
    mock_modal_instance.show.assert_called_once()

    # Verify button reverted to its idle text and state
    assert dashboard.start_watching_btn.cget("text") == "▶ Start Browsing"
    assert str(dashboard.start_watching_btn.cget("state")) == "normal"
    assert dashboard.start_watching_btn.cget("cursor") == "hand2"

    # Verify nothing is running in the background
    assert app.is_running is False
    assert app.session_manager_thread is None
    assert dashboard.is_running is False

    dashboard.destroy()


def test_start_browsing_timeout_confirmed_starts_session(ctk_app, tmp_path):
    """When user confirms timeout popup, browsing session starts and button transitions to running state."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 15)
    mgr.set_channels([{"name": "TestChannel", "url": "https://youtube.com/@test", "enabled": True}])
    mgr.set_video_queue([{"title": "Video 1", "url": "https://youtube.com/watch?v=123", "channel": "TestChannel"}])

    app = MagicMock()
    app.config_mgr = mgr
    app.log_queue = queue.Queue()
    app.event_queue = queue.Queue()
    app.stop_event = MagicMock()
    app.pause_event = MagicMock()
    app.worker_progress = {}
    app.loaded_accounts = [Account(email="test@gmail.com", password="pwd")]
    app.queued_video_tasks = [VideoTask(title="Video 1", url="https://youtube.com/watch?v=123", channel="TestChannel")]
    app.is_running = False
    app.session_manager_thread = None
    app.is_closing = False
    app._get_owned_threads.return_value = []

    dashboard = DashboardView(ctk_app, app)
    app.views = {"dashboard": dashboard}

    app.start_watching = lambda: YTBotApp.start_watching(app)
    app._confirm_full_video_timeout_if_needed = lambda: YTBotApp._confirm_full_video_timeout_if_needed(app)

    mock_modal_instance = MagicMock()
    mock_modal_instance.show.return_value = True

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal", return_value=mock_modal_instance), \
         patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        dashboard.start_watching_btn.invoke()

    mock_modal_instance.show.assert_called_once()
    assert app.is_running is True
    assert dashboard.start_watching_btn.cget("text") == "Browsing started"
    assert str(dashboard.start_watching_btn.cget("state")) == "disabled"
    assert dashboard.is_running is True
    mock_thread.start.assert_called_once()

    dashboard.destroy()
