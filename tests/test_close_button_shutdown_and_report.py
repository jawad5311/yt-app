import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.ui.app import YTBotApp


class TestCloseButtonShutdownAndReport:
    """Tests verifying the top-right cross button stops the session, sends the report, and exits."""

    @pytest.fixture
    def app_instance(self):
        app = YTBotApp.__new__(YTBotApp)
        app.stop_event = threading.Event()
        app.pause_event = threading.Event()
        app.is_running = True
        app.is_closing = False
        app.current_mode = "watching"
        app.active_workers = []
        app.active_threads = []
        app.session_manager_thread = None
        app.views = {"dashboard": MagicMock(), "logs": MagicMock()}
        app.log_queue = queue.Queue()
        app.event_queue = queue.Queue()
        app.report_modal = None
        app._close_start_time = 0.0
        app._close_timeout = 20.0
        app._poll_job = "poll_job_1"
        app._close_check_job = None
        app._quota_check_job = None
        app.quota_tracker = None

        app.after = MagicMock(return_value="check_job_1")
        app.after_cancel = MagicMock()
        app.destroy = MagicMock()
        app.winfo_exists = MagicMock(return_value=True)

        return app

    def test_press_cross_button_stops_session_shows_closing_modal_and_waits(self, app_instance):
        """Pressing the cross button while running stops workers and shows closing progress modal."""
        worker = MagicMock()
        app_instance.active_workers = [worker]
        thread = threading.Thread(target=lambda: app_instance.stop_event.wait(timeout=1.0))
        app_instance.active_threads = [thread]
        thread.start()

        with patch.object(app_instance, "show_report_modal") as mock_show_modal:
            app_instance.on_closing()

            # Must request session stop
            assert app_instance.is_closing is True
            assert app_instance.stop_event.is_set()
            worker.stop.assert_called_once()
            mock_show_modal.assert_called_once()
            # Window must not be destroyed yet while waiting for report and thread
            assert not app_instance.destroy.called
            assert app_instance._close_check_job is not None

        thread.join(timeout=1.0)

    def test_shutdown_progress_drains_events_during_closing_so_report_sent_is_processed(self, app_instance):
        """_check_shutdown_progress drains events during shutdown so report_sent is not dropped."""
        app_instance.is_closing = True
        app_instance.event_queue.put_nowait(("report_sent", True, "Delivered"))

        with patch.object(app_instance, "_handle_worker_event") as mock_handler:
            app_instance._check_shutdown_progress()
            mock_handler.assert_called_once_with(("report_sent", True, "Delivered"))

    def test_session_report_completion_destroys_modal_and_exits_app(self, app_instance):
        """When report dispatch completes and worker thread finishes, app destroys modal and closes."""
        mock_modal = MagicMock()
        mock_modal.winfo_exists.return_value = True
        mock_modal.is_generating = True
        app_instance.report_modal = mock_modal
        app_instance.is_closing = True
        app_instance.is_running = False
        app_instance.active_threads = []
        app_instance.session_manager_thread = None

        # 1. While modal is generating, it does not destroy yet
        app_instance._check_shutdown_progress()
        assert not app_instance.destroy.called

        # 2. Worker emits report_sent event
        app_instance.update_report_modal_completed(success=True, message="Report delivered successfully")
        assert mock_modal.set_completed.called
        # auto_close=True must be passed when is_closing is True so no OK button blocks the exit
        _, kwargs = mock_modal.set_completed.call_args
        assert kwargs.get("auto_close") is True

        mock_modal.is_generating = False

        # 3. Next shutdown check destroys modal and destroys app window
        app_instance._check_shutdown_progress()
        mock_modal.destroy.assert_called_once()
        assert app_instance.report_modal is None
        app_instance.destroy.assert_called_once()

    def test_press_cross_button_when_idle_exits_immediately(self, app_instance):
        """Pressing the cross button when idle destroys app cleanly without waiting."""
        app_instance.is_running = False
        app_instance.active_threads = []
        app_instance.session_manager_thread = None

        app_instance.on_closing()

        assert app_instance.is_closing is True
        app_instance.destroy.assert_called_once()
