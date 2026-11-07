import os
import queue
import time
import threading
from unittest.mock import MagicMock, patch
import pytest

from src.ui.app import YTBotApp


class DummyWorker:
    def __init__(self):
        self.stop_called = False
        self.driver = MagicMock()
        self.reporting_driver = MagicMock()
        self.reporting_thread = None

    def stop(self):
        self.stop_called = True


class MockYTBotApp:
    """Test harness binding YTBotApp's shutdown methods for unit testing."""
    def __init__(self):
        self.is_closing = False
        self.is_running = False
        self._close_start_time = 0.0
        self._close_timeout = 30.0
        self._report_finish_time = None
        self.event_queue = queue.Queue()
        self.log_queue = queue.Queue()
        self.active_workers = []
        self.active_threads = []
        self.session_manager_thread = None
        self.report_modal = None
        self.views = {"dashboard": MagicMock()}
        self._close_check_job = None
        self._poll_job = None

    _get_owned_threads = YTBotApp._get_owned_threads
    _cancel_scheduled_callbacks = YTBotApp._cancel_scheduled_callbacks
    _safe_exit = YTBotApp._safe_exit
    on_closing = YTBotApp.on_closing
    _check_shutdown_progress = YTBotApp._check_shutdown_progress
    _handle_worker_event = YTBotApp._handle_worker_event

    def show_report_modal(self, message=""):
        pass

    def update_report_modal_completed(self, success=True, message=""):
        if self.report_modal is not None:
            self.report_modal.is_generating = False

    def stop_automation(self):
        self.is_running = False
        for w in self.active_workers:
            w.stop()

    def after(self, ms, callback):
        return "job_123"

    def after_cancel(self, job_id):
        pass

    def quit(self):
        pass

    def destroy(self):
        pass


@pytest.fixture
def mock_app():
    return MockYTBotApp()


def test_on_closing_when_idle_exits_immediately(mock_app):
    """When the app is idle, clicking close should call _safe_exit immediately."""
    with patch.object(mock_app, "_safe_exit") as mock_exit:
        mock_app.on_closing()
        mock_exit.assert_called_once()
        assert mock_app.is_closing is True


def test_on_closing_second_click_forces_exit(mock_app):
    """If the close button is clicked again while already closing, it immediately forces exit."""
    mock_app.is_closing = True
    with patch.object(mock_app, "_safe_exit") as mock_exit:
        mock_app.on_closing()
        mock_exit.assert_called_once_with(force=True)


def test_on_closing_when_running_stops_automation_and_checks_progress(mock_app):
    """When a session is running, on_closing stops automation and begins monitoring report progress."""
    worker = DummyWorker()
    mock_app.is_running = True
    mock_app.active_workers = [worker]

    with patch.object(mock_app, "_check_shutdown_progress") as mock_check, \
         patch.object(mock_app, "_safe_exit") as mock_exit:
        mock_app.on_closing()
        assert worker.stop_called is True
        mock_check.assert_called_once()
        mock_exit.assert_not_called()
        assert mock_app.is_closing is True


def test_handle_worker_event_during_closing_processes_report_events(mock_app):
    """During shutdown, report_generating, report_sent, and finished must be processed, not dropped."""
    mock_app.is_closing = True

    mock_modal = MagicMock()
    mock_modal.winfo_exists.return_value = True
    mock_modal.is_generating = True
    mock_app.report_modal = mock_modal

    # 1. report_generating event
    with patch.object(mock_app, "show_report_modal") as mock_show:
        mock_app._handle_worker_event(("report_generating", "Preparing report..."))
        mock_show.assert_called_once_with("Preparing report...")

    # 2. report_sent event
    with patch.object(mock_app, "update_report_modal_completed") as mock_completed:
        mock_app._handle_worker_event(("report_sent", True, "Delivered"))
        mock_completed.assert_called_once_with(True, "Delivered")


def test_check_shutdown_progress_waits_for_report_and_then_exits(mock_app):
    """Shutdown must wait while report is in progress, and exit once report finishes."""
    mock_app.is_closing = True
    mock_app._close_start_time = time.monotonic()
    mock_app._report_finish_time = None

    # Simulate active report modal
    mock_modal = MagicMock()
    mock_modal.winfo_exists.return_value = True
    mock_modal.is_generating = True
    mock_app.report_modal = mock_modal

    with patch.object(mock_app, "_safe_exit") as mock_exit, \
         patch.object(mock_app, "after", return_value="job_check") as mock_after:

        # Turn 1: report is generating -> does not exit, schedules next check
        mock_app._check_shutdown_progress()
        mock_exit.assert_not_called()
        mock_after.assert_called_once()

        # Turn 2: Worker delivers report_sent event into queue
        mock_app.event_queue.put(("report_sent", True, "Delivered successfully!"))

        # Check progress again: event is drained and report finished -> exits!
        mock_app._check_shutdown_progress()
        mock_exit.assert_called_once()


def test_shutdown_timeout_forces_exit_without_workers_busy_popup(mock_app):
    """When shutdown timeout expires, it forces exit cleanly without showing any 'Workers Busy' popup."""
    mock_app.is_closing = True
    mock_app._close_start_time = time.monotonic() - 35.0

    mock_modal = MagicMock()
    mock_modal.winfo_exists.return_value = True
    mock_modal.is_generating = True
    mock_app.report_modal = mock_modal

    with patch.object(mock_app, "_safe_exit") as mock_exit, \
         patch("tkinter.messagebox.askyesno") as mock_msgbox:

        mock_app._check_shutdown_progress()

        # Must NOT show any messagebox popup
        mock_msgbox.assert_not_called()
        # Must force exit
        mock_exit.assert_called_once_with(force=True)


def test_safe_exit_closes_active_worker_drivers(mock_app):
    """_safe_exit closes active worker drivers cleanly and spawns background process terminator."""
    worker = DummyWorker()
    mock_app.active_workers = [worker]

    with patch("src.ui.app.close_driver") as mock_close_driver, \
         patch.object(mock_app, "quit") as mock_quit, \
         patch.object(mock_app, "destroy") as mock_destroy, \
         patch("threading.Thread") as mock_thread:

        mock_app._safe_exit()

        assert mock_close_driver.call_count == 2
        assert worker.driver is None
        assert worker.reporting_driver is None
        mock_quit.assert_called_once()
        mock_destroy.assert_called_once()
        assert mock_thread.called
