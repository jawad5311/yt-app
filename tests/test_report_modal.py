"""Unit tests for ReportProgressModal and session report pop-up notification."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.ui.app import YTBotApp
from src.ui.views.report_modal import ReportProgressModal


class TestReportProgressModal:
    def test_modal_initial_state_is_generating(self):
        parent = MagicMock()
        with patch.object(ReportProgressModal, "__init__", lambda self, *args, **kwargs: None):
            modal = ReportProgressModal(parent)
            modal.is_generating = True
            modal.parent_window = parent
            modal.on_close_callback = None
            modal.title_label = MagicMock()
            modal.msg_label = MagicMock()
            modal.progress_bar = MagicMock()
            modal.action_btn = MagicMock()

            assert modal.is_generating is True

            # When window close attempted while generating and parent not closing, show warning
            parent.is_closing = False
            with patch("tkinter.messagebox.showwarning") as mock_warn, \
                 patch.object(modal, "close_modal") as mock_close:
                modal._on_window_delete()
                mock_warn.assert_called_once()
                mock_close.assert_not_called()

    def test_modal_set_completed_success(self):
        parent = MagicMock()
        with patch.object(ReportProgressModal, "__init__", lambda self, *args, **kwargs: None):
            modal = ReportProgressModal(parent)
            modal.is_generating = True
            modal.parent_window = parent
            modal.on_close_callback = MagicMock()
            modal.title_label = MagicMock()
            modal.msg_label = MagicMock()
            modal.progress_bar = MagicMock()
            modal.action_btn = MagicMock()
            modal.destroy = MagicMock()
            modal.grab_release = MagicMock()

            # Mark completed with success
            modal.set_completed(success=True, title="Report Sent Successfully!", message="Delivered to test@test.com")

            assert modal.is_generating is False
            modal.progress_bar.stop.assert_called_once()
            modal.progress_bar.pack_forget.assert_called_once()
            modal.action_btn.pack.assert_called_once()
            assert "Report Sent Successfully!" in modal.title_label.configure.call_args[1].get("text", "")

            # Now close_modal allows window destruction and invokes callback
            modal.close_modal()
            modal.destroy.assert_called_once()
            modal.on_close_callback.assert_called_once()


class TestAppReportModalIntegration:
    @pytest.fixture
    def fake_app(self):
        app = YTBotApp.__new__(YTBotApp)
        app.stop_event = threading.Event()
        app.pause_event = threading.Event()
        app.is_running = True
        app.is_closing = False
        app.current_mode = "watching"
        app.active_workers = []
        app.active_threads = []
        app.views = {"dashboard": MagicMock()}
        app.log_queue = queue.Queue()
        app.event_queue = queue.Queue()
        app.report_modal = None
        app._close_start_time = 0.0
        app._close_timeout = 20.0
        return app

    def test_stop_automation_triggers_show_report_modal(self, fake_app):
        with patch.object(fake_app, "show_report_modal") as mock_show:
            fake_app.stop_automation()
            mock_show.assert_called_once()
            assert fake_app.stop_event.is_set()

    def test_report_generating_event_triggers_show_report_modal(self, fake_app):
        with patch.object(fake_app, "show_report_modal") as mock_show:
            fake_app._handle_worker_event(("report_generating", "Generating session report... Please do not close the app."))
            mock_show.assert_called_once_with("Generating session report... Please do not close the app.")

    def test_report_sent_event_triggers_update_report_modal_completed(self, fake_app):
        with patch.object(fake_app, "update_report_modal_completed") as mock_update:
            fake_app._handle_worker_event(("report_sent", True, "Delivered"))
            mock_update.assert_called_once_with(True, "Delivered")

    def test_on_closing_during_report_generation_transitions_to_shutdown(self, fake_app):
        mock_modal = MagicMock()
        mock_modal.winfo_exists.return_value = True
        mock_modal.is_generating = True
        fake_app.report_modal = mock_modal

        with patch.object(fake_app, "_check_shutdown_progress") as mock_check:
            fake_app.on_closing()
            assert fake_app.is_closing is True
            mock_check.assert_called_once()
            mock_modal.title.assert_called_with("Closing Application")

    def test_on_closing_allowed_when_report_modal_is_completed(self, fake_app):
        mock_modal = MagicMock()
        mock_modal.winfo_exists.return_value = True
        mock_modal.is_generating = False
        fake_app.report_modal = mock_modal
        fake_app.is_running = False
        fake_app._get_owned_threads = MagicMock(return_value=[])
        fake_app._cancel_scheduled_callbacks = MagicMock()
        fake_app.destroy = MagicMock()

        fake_app.on_closing()
        fake_app.destroy.assert_called_once()
