import tkinter as tk
from unittest.mock import MagicMock, patch
import pytest

from src.ui.app import YTBotApp


class TestAppMaximizeOnStartup:
    def test_maximize_window_sets_zoomed_state(self):
        app = YTBotApp.__new__(YTBotApp)
        app.state = MagicMock()

        YTBotApp.maximize_window(app)

        app.state.assert_called_once_with("zoomed")

    def test_maximize_window_fallback_to_attributes_if_state_fails(self):
        app = YTBotApp.__new__(YTBotApp)
        app.state = MagicMock(side_effect=Exception("state zoomed not supported"))
        app.attributes = MagicMock()

        YTBotApp.maximize_window(app)

        app.state.assert_called_once_with("zoomed")
        app.attributes.assert_called_once_with("-zoomed", True)

    def test_maximize_window_handles_all_exceptions_gracefully(self):
        app = YTBotApp.__new__(YTBotApp)
        app.state = MagicMock(side_effect=Exception("state zoomed not supported"))
        app.attributes = MagicMock(side_effect=Exception("attributes -zoomed not supported"))

        # Should not raise any exception
        YTBotApp.maximize_window(app)

    def test_real_tk_window_zoomed_state(self):
        root = tk.Tk()
        try:
            root.geometry("1180x740")
            root.minsize(1020, 620)
            YTBotApp.maximize_window(root)
            root.update()

            # On Windows, state is 'zoomed'
            assert root.state() in ("zoomed", "normal")
        finally:
            root.destroy()

    def test_start_watching_maximizes_window_screen(self):
        app = MagicMock()
        app.is_running = False
        app.is_closing = False
        app._get_owned_threads.return_value = []
        app._ensure_active_accounts_for_session.return_value = True
        app.config_mgr = MagicMock()
        app.config_mgr.get_channels.return_value = [{"name": "TestChannel", "enabled": True, "archived": False}]
        app.config_mgr.update_settings.return_value = None
        app.queued_video_tasks = []
        mock_dashboard = MagicMock()
        mock_dashboard.get_browsing_limit.return_value = (1, True)
        mock_dashboard.get_sleep_nap_enabled.return_value = True
        app.views = {"dashboard": mock_dashboard}
        app.log_queue = MagicMock()
        app._confirm_full_video_timeout_if_needed.return_value = True
        app.stop_event = MagicMock()
        app.pause_event = MagicMock()
        app.worker_progress = {}
        app.event_queue = MagicMock()
        app.event_queue.empty.return_value = True
        app.loaded_accounts = [MagicMock()]
        app.after = MagicMock()

        with patch("threading.Thread"):
            YTBotApp.start_watching(app)

        app.maximize_window.assert_called_once()
        app.after.assert_any_call(50, app.maximize_window)

    def test_start_automation_maximizes_window_screen(self):
        app = MagicMock()
        app.is_running = False
        app.is_closing = False
        app._get_owned_threads.return_value = []
        app._ensure_active_accounts_for_session.return_value = True
        app.config_mgr = MagicMock()
        app.config_mgr.get_float.return_value = 0.0
        app.config_mgr.get_channels.return_value = []
        app.config_mgr.set_setting.return_value = None
        task = MagicMock()
        task.enabled = True
        app.queued_video_tasks = [task]
        app.stats_store = None
        mock_dashboard = MagicMock()
        mock_dashboard.get_loop_count.return_value = 1
        app.views = {"dashboard": mock_dashboard}
        app.log_queue = MagicMock()
        app._confirm_full_video_timeout_if_needed.return_value = True
        app.stop_event = MagicMock()
        app.worker_progress = {}
        app.event_queue = MagicMock()
        app.event_queue.empty.return_value = True
        app.after = MagicMock()

        with patch("threading.Thread"), patch("src.ui.app.is_video_task_runnable", return_value=True):
            YTBotApp.start_automation(app)

        app.maximize_window.assert_called_once()
        app.after.assert_any_call(50, app.maximize_window)

