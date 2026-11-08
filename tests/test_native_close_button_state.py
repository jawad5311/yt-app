import queue
import sys
import threading
from unittest.mock import MagicMock, patch
import pytest

from src.ui.app import YTBotApp


class TestNativeCloseButtonState:
    @pytest.fixture
    def fake_app(self):
        app = YTBotApp.__new__(YTBotApp)
        app.is_running = False
        app.is_closing = False
        app.current_mode = "watching"
        app.active_workers = []
        app.active_threads = []
        app.session_manager_thread = None
        app.report_modal = None
        app.background_jobs = None
        app.views = {"dashboard": MagicMock(), "logs": MagicMock()}
        app.log_queue = queue.Queue()
        app.event_queue = queue.Queue()
        app._close_button_enabled = None

        app.after = MagicMock(return_value="job_1")
        app.after_cancel = MagicMock()
        app.destroy = MagicMock()
        app.winfo_exists = MagicMock(return_value=True)
        app.winfo_id = MagicMock(return_value=12345)

        return app

    def test_is_any_task_running_states(self, fake_app):
        # 1. Idle state
        assert fake_app.is_any_task_running() is False

        # 2. Automation / browsing running
        fake_app.is_running = True
        assert fake_app.is_any_task_running() is True

        # 3. Thread alive
        fake_app.is_running = False
        t = MagicMock()
        t.is_alive.return_value = True
        fake_app.active_threads = [t]
        assert fake_app.is_any_task_running() is True

        # 4. Report modal generating
        fake_app.active_threads = []
        mock_modal = MagicMock()
        mock_modal.winfo_exists.return_value = True
        mock_modal.is_generating = True
        fake_app.report_modal = mock_modal
        assert fake_app.is_any_task_running() is True

        # 5. Report modal finished generating
        mock_modal.is_generating = False
        assert fake_app.is_any_task_running() is False

        # 6. Background job active
        bg = MagicMock()
        mock_job = MagicMock()
        mock_job.is_alive.return_value = True
        bg._jobs = {"job1": mock_job}
        fake_app.background_jobs = bg
        assert fake_app.is_any_task_running() is True

    def test_update_close_button_state_transitions(self, fake_app):
        with patch.object(fake_app, "_set_native_close_button_enabled") as mock_set_native:
            # When idle -> enables close button
            fake_app.is_running = False
            fake_app._update_close_button_state()
            assert fake_app._close_button_enabled is True
            mock_set_native.assert_called_once_with(True)

            mock_set_native.reset_mock()
            # Repeating while still idle does not call Win32 API repeatedly
            fake_app._update_close_button_state()
            mock_set_native.assert_not_called()

            # When task starts -> disables and grays out close button
            fake_app.is_running = True
            fake_app._update_close_button_state()
            assert fake_app._close_button_enabled is False
            mock_set_native.assert_called_once_with(False)

            mock_set_native.reset_mock()
            # When task stops -> re-enables close button
            fake_app.is_running = False
            fake_app._update_close_button_state()
            assert fake_app._close_button_enabled is True
            mock_set_native.assert_called_once_with(True)

    def test_set_native_close_button_enabled_win32_calls(self, fake_app):
        fake_user32 = MagicMock()
        fake_user32.GetParent.return_value = 54321
        fake_user32.GetSystemMenu.return_value = 99999

        with patch("sys.platform", "win32"), \
             patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.user32 = fake_user32

            # Disable / gray out
            fake_app._set_native_close_button_enabled(False)
            fake_user32.GetSystemMenu.assert_called_with(54321, False)
            # MF_DISABLED (2) | MF_GRAYED (1) = 3
            fake_user32.EnableMenuItem.assert_called_with(99999, 0xF060, 3)
            fake_user32.DrawMenuBar.assert_called_with(54321)
            fake_user32.SetWindowPos.assert_called()

            # Enable
            fake_user32.reset_mock()
            fake_app._set_native_close_button_enabled(True)
            fake_user32.GetSystemMenu.assert_called_with(54321, False)
            # MF_ENABLED = 0
            fake_user32.EnableMenuItem.assert_called_with(99999, 0xF060, 0)
            fake_user32.DrawMenuBar.assert_called_with(54321)

    def test_on_closing_immediate_when_idle(self, fake_app):
        fake_app.is_running = False
        fake_app.active_threads = []
        fake_app.session_manager_thread = None

        fake_app.on_closing()

        assert fake_app.is_closing is True
        fake_app.destroy.assert_called_once()
