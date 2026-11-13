import queue
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest
from selenium.webdriver.common.by import By

from src.automation.email_reporter import EmailReporter
from src.automation.watching_worker import WatchingWorker
from src.automation.worker import AutomationWorker
from src.config.manager import ConfigManager
from src.models.account import Account


class TestGmailComposeSignInRecovery:
    @patch("time.sleep", return_value=None)
    @patch("src.automation.login.perform_google_login", return_value=True)
    def test_deliver_auto_signin_when_redirected_to_google_login(self, mock_perform_login, mock_sleep):
        """When compose_url redirects to accounts.google.com, perform_google_login should be triggered."""
        mock_driver = MagicMock()
        # Initial URL is signin page; after login it redirects to compose
        mock_driver.current_url = "https://accounts.google.com/signin/v2/identifier"

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True
        mock_body.text = ""

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        mock_toast = MagicMock(is_displayed=lambda: True)

        call_count = 0
        def find_elements_mock(by, selector):
            nonlocal call_count
            call_count += 1
            # Before login (first call), no compose elements found
            if call_count <= 2:
                return []
            if "@role='dialog'" in selector:
                return []
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock
        mock_driver.find_element.return_value = MagicMock(get_attribute=lambda attr: "")

        log_q = queue.Queue()
        account = Account(email="auto_user@gmail.com", password="secret_password")
        mock_config = MagicMock()

        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Test Subject",
            plain_text="Test Body",
            recipient_email="recipient@gmail.com",
            log_queue=log_q,
            account=account,
            config_mgr=mock_config,
        )

        assert ok is True
        mock_perform_login.assert_called_once_with(
            mock_driver,
            account,
            log_queue=log_q,
            config_mgr=mock_config,
        )
        mock_send_btn.click.assert_called_once()

    @patch("time.sleep", return_value=None)
    @patch("src.automation.login.perform_google_login", return_value=False)
    def test_deliver_fails_gracefully_when_signin_fails(self, mock_perform_login, mock_sleep):
        """If perform_google_login fails, delivery should fail and log appropriate error."""
        mock_driver = MagicMock()
        mock_driver.current_url = "https://accounts.google.com/signin/v2/identifier"
        mock_driver.find_elements.return_value = []

        log_q = queue.Queue()
        account = Account(email="auto_user@gmail.com", password="bad_password")

        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Test Subject",
            plain_text="Test Body",
            recipient_email="recipient@gmail.com",
            log_queue=log_q,
            account=account,
        )

        assert ok is False
        mock_perform_login.assert_called_once()
        messages = []
        while not log_q.empty():
            messages.append(log_q.get())
        assert any("not signed in" in str(m).lower() for m in messages)


class TestWatchingWorkerStartNotificationRetry:
    @patch("time.sleep", return_value=None)
    def test_watching_worker_retries_on_failure_and_succeeds(self, mock_sleep, tmp_path):
        """WatchingWorker retries start notification and succeeds on attempt 2."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail",
        })

        account = Account(email="test@gmail.com", password="pwd")
        log_q = queue.Queue()
        stop_evt = threading.Event()

        worker = WatchingWorker(
            channels=[],
            account=account,
            config_mgr=cfg,
            log_queue=log_q,
            stop_event=stop_evt,
        )
        worker._start_notification_delays = [0.01, 0.01]

        mock_reporting_driver = MagicMock()
        worker.reporting_driver = mock_reporting_driver

        with patch.object(worker, "_send_start_notification", side_effect=[False, True]) as mock_send, \
             patch.object(EmailReporter, "create_headless_reporting_driver", return_value=MagicMock()) as mock_create:
            worker._async_init_and_send_start_notification()

            assert mock_send.call_count == 2
            # reporting_driver.quit() should have been called on cleanup after attempt 1
            mock_reporting_driver.quit.assert_called_once()
            # New reporting driver should be recreated for attempt 2
            mock_create.assert_called_once()

    @patch("time.sleep", return_value=None)
    def test_watching_worker_alerts_when_all_attempts_fail(self, mock_sleep, tmp_path):
        """WatchingWorker emits [Start Notification Alert] after max attempts fail."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail",
        })

        account = Account(email="test@gmail.com", password="pwd")
        log_q = queue.Queue()
        stop_evt = threading.Event()

        worker = WatchingWorker(
            channels=[],
            account=account,
            config_mgr=cfg,
            log_queue=log_q,
            stop_event=stop_evt,
        )
        worker._start_notification_delays = [0.01, 0.01]

        with patch.object(worker, "_send_start_notification", return_value=False) as mock_send, \
             patch.object(EmailReporter, "create_headless_reporting_driver", return_value=None):
            worker._async_init_and_send_start_notification()

            # 1 initial + 2 delays = 3 attempts
            assert mock_send.call_count == 3

            messages = []
            while not log_q.empty():
                messages.append(log_q.get())
            alert_logs = [m for m in messages if "[Start Notification Alert]" in str(m)]
            assert len(alert_logs) >= 1

    @patch("time.sleep", return_value=None)
    def test_watching_worker_retry_honors_stop_event(self, mock_sleep, tmp_path):
        """If stop_event is set, worker aborts start notification retry immediately."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail",
        })

        account = Account(email="test@gmail.com", password="pwd")
        log_q = queue.Queue()
        stop_evt = threading.Event()

        worker = WatchingWorker(
            channels=[],
            account=account,
            config_mgr=cfg,
            log_queue=log_q,
            stop_event=stop_evt,
        )
        worker._start_notification_delays = [0.01, 0.01]

        def send_side_effect():
            stop_evt.set()
            return False

        with patch.object(worker, "_send_start_notification", side_effect=send_side_effect) as mock_send:
            worker._async_init_and_send_start_notification()

            # Should abort after attempt 1 because stop_evt is set
            assert mock_send.call_count == 1


class TestAutomationWorkerStartNotificationRetry:
    @patch("time.sleep", return_value=None)
    def test_automation_worker_worker_id_not_zero_skips(self, mock_sleep, tmp_path):
        """Worker with worker_id != 0 should not send start notification."""
        cfg = ConfigManager(tmp_path / "settings.json")
        account = Account(email="test@gmail.com", password="pwd")
        worker = AutomationWorker(
            worker_id=1,
            video_tasks=[],
            account=account,
            config_mgr=cfg,
            log_queue=queue.Queue(),
            stop_event=threading.Event(),
        )

        with patch.object(worker, "_send_start_notification") as mock_send:
            worker._async_init_and_send_start_notification()
            mock_send.assert_not_called()

    @patch("time.sleep", return_value=None)
    def test_automation_worker_retries_on_failure(self, mock_sleep, tmp_path):
        """Worker with worker_id == 0 retries and succeeds."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail",
        })

        account = Account(email="test@gmail.com", password="pwd")
        log_q = queue.Queue()
        stop_evt = threading.Event()
        worker = AutomationWorker(
            worker_id=0,
            video_tasks=[],
            account=account,
            config_mgr=cfg,
            log_queue=log_q,
            stop_event=stop_evt,
        )
        worker._start_notification_delays = [0.01, 0.01]

        with patch.object(worker, "_send_start_notification", side_effect=[False, True]) as mock_send, \
             patch.object(EmailReporter, "create_headless_reporting_driver", return_value=MagicMock()):
            worker._async_init_and_send_start_notification()
            assert mock_send.call_count == 2
