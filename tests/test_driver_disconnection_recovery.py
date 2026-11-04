"""Unit tests for driver liveness detection, session disconnection handling, and reporting recovery."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

from src.automation.driver import is_driver_alive, is_session_disconnected_error
from src.automation.email_reporter import EmailReporter, SessionReport
from src.automation.watching_worker import WatchingWorker
from src.models.account import Account


class TestDriverLiveness:
    def test_is_driver_alive_none(self):
        assert is_driver_alive(None) is False

    def test_is_driver_alive_no_session_id(self):
        driver = MagicMock()
        driver.session_id = None
        assert is_driver_alive(driver) is False

    def test_is_driver_alive_healthy(self):
        driver = MagicMock()
        driver.session_id = "test-session-123"
        driver.execute_script.return_value = 1
        assert is_driver_alive(driver) is True

    def test_is_driver_alive_invalid_session_id_exception(self):
        driver = MagicMock()
        driver.session_id = "test-session-123"
        driver.execute_script.side_effect = InvalidSessionIdException("session deleted as the browser has closed")
        assert is_driver_alive(driver) is False

    def test_is_driver_alive_disconnected_devtools(self):
        driver = MagicMock()
        driver.session_id = "test-session-123"
        driver.execute_script.side_effect = WebDriverException("disconnected: not connected to DevTools")
        assert is_driver_alive(driver) is False

    def test_is_session_disconnected_error(self):
        assert is_session_disconnected_error(None) is False
        assert is_session_disconnected_error(WebDriverException("invalid session id")) is True
        assert is_session_disconnected_error(WebDriverException("session deleted as the browser has closed")) is True
        assert is_session_disconnected_error(WebDriverException("disconnected: not connected to DevTools")) is True
        assert is_session_disconnected_error(WebDriverException("chrome not reachable")) is True
        assert is_session_disconnected_error(WebDriverException("no such window: target window already closed")) is True
        assert is_session_disconnected_error(ValueError("random value error")) is False


class TestReportingFallbackOnDeadDriver:
    @pytest.fixture
    def fake_report(self):
        report = MagicMock(spec=SessionReport)
        report.generate_subject.return_value = "Test Report"
        report.generate_plain_text.return_value = "Test Plain Text"
        report.thread_url = None
        return report

    def test_dispatch_report_falls_back_to_headless_when_driver_dead(self, fake_report):
        dead_driver = MagicMock()
        dead_driver.session_id = "dead-session"
        dead_driver.execute_script.side_effect = InvalidSessionIdException("session deleted")

        account = MagicMock(spec=Account)
        account.email = "test@example.com"
        account.password = "secret"

        config_mgr = MagicMock()
        config_mgr.get_setting.side_effect = lambda key, default="": {
            "ReportEmailMethod": "Auto",
            "ReportRecipientEmail": "admin@example.com",
            "SmtpHost": "smtp.gmail.com",
            "SmtpUsername": "test@example.com",
            "SmtpPassword": "pass",
            "SenderEmail": "test@example.com",
        }.get(key, default)
        config_mgr.get_int.return_value = 587

        with patch.object(EmailReporter, "send_via_browser_gmail") as mock_browser, \
             patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True) as mock_headless:
            ok = EmailReporter.dispatch_report(
                report=fake_report,
                config_mgr=config_mgr,
                driver=dead_driver,
                account=account,
                log_queue=queue.Queue(),
            )
            assert ok is True
            mock_browser.assert_not_called()
            mock_headless.assert_called_once()

    def test_dispatch_report_falls_back_to_headless_when_browser_send_fails(self, fake_report):
        alive_driver = MagicMock()
        alive_driver.session_id = "alive-session"
        alive_driver.execute_script.return_value = 1

        account = MagicMock(spec=Account)
        account.email = "test@example.com"
        account.password = "secret"

        config_mgr = MagicMock()
        config_mgr.get_setting.side_effect = lambda key, default="": {
            "ReportEmailMethod": "Auto",
            "ReportRecipientEmail": "admin@example.com",
        }.get(key, default)
        config_mgr.get_int.return_value = 587

        with patch.object(EmailReporter, "send_via_browser_gmail", return_value=False) as mock_browser, \
             patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True) as mock_headless:
            ok = EmailReporter.dispatch_report(
                report=fake_report,
                config_mgr=config_mgr,
                driver=alive_driver,
                account=account,
                log_queue=queue.Queue(),
            )
            assert ok is True
            mock_browser.assert_called_once()
            mock_headless.assert_called_once()


class TestWatchingWorkerDisconnectionHandling:
    @patch("time.sleep", return_value=None)
    def test_fetch_recommended_videos_raises_on_disconnected_session(self, mock_sleep):
        worker = WatchingWorker.__new__(WatchingWorker)
        worker.log_queue = queue.Queue()
        worker.stop_event = threading.Event()
        worker.driver = MagicMock()
        worker.driver.execute_script.side_effect = InvalidSessionIdException("session deleted as the browser has closed")

        with pytest.raises(InvalidSessionIdException):
            worker.fetch_recommended_videos()

    @patch("time.sleep", return_value=None)
    def test_fetch_home_videos_raises_on_disconnected_session(self, mock_sleep):
        worker = WatchingWorker.__new__(WatchingWorker)
        worker.log_queue = queue.Queue()
        worker.stop_event = threading.Event()
        worker.pause_event = threading.Event()
        worker.reporting_thread = None
        worker.driver = MagicMock()
        worker.driver.execute_script.side_effect = WebDriverException("disconnected: not connected to DevTools")

        with pytest.raises(WebDriverException):
            worker.fetch_home_videos()

    def test_send_session_report_replaces_dead_drivers_with_none(self):
        worker = WatchingWorker.__new__(WatchingWorker)
        worker.log_queue = queue.Queue()
        worker.event_queue = queue.Queue()
        worker.stop_event = threading.Event()
        worker.pause_event = threading.Event()
        worker.reporting_thread = None
        worker.config_mgr = MagicMock()
        worker.account = MagicMock(spec=Account)
        worker.account.email = "test@example.com"

        dead_reporting = MagicMock()
        dead_reporting.session_id = "dead-rep"
        dead_reporting.execute_script.side_effect = InvalidSessionIdException("dead reporting driver")
        worker.reporting_driver = dead_reporting

        dead_watch = MagicMock()
        dead_watch.session_id = "dead-watch"
        dead_watch.execute_script.side_effect = InvalidSessionIdException("dead watch driver")
        worker.driver = dead_watch

        tracker = MagicMock()
        tracker.get_session_report_data.return_value = {
            "start_time": MagicMock(),
            "end_time": MagicMock(),
            "duration_seconds": 120,
            "total_session_watch_seconds": 60,
            "channels": [],
        }
        worker.channel_tracker = tracker

        with patch.object(EmailReporter, "dispatch_report", return_value=True) as mock_dispatch:
            worker._send_session_report(status="Completed")
            assert mock_dispatch.called
            # The driver passed to dispatch_report must be None because both were dead
            passed_driver = mock_dispatch.call_args[1].get("driver")
            assert passed_driver is None
