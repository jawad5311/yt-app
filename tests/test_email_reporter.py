"""Unit and integration tests for Session Watch Time Email Reporting."""

from datetime import datetime, timedelta
import queue
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import EmailReporter, SessionReport
from src.automation.session_tracker import SessionChannelTracker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel


@pytest.fixture
def sample_report():
    start = datetime(2026, 10, 30, 10, 0, 0)
    end = start + timedelta(minutes=45, seconds=30)
    return SessionReport(
        account_email="tester@gmail.com",
        session_mode="Continuous Watching / Browsing",
        start_time=start,
        end_time=end,
        duration_seconds=2730.0,
        total_session_watch_seconds=2400.0,  # 40 mins
        channel_reports=[
            {
                "name": "Tech Channel",
                "session_watch_seconds": 1800.0,  # 30 mins
                "initial_watch_seconds": 3600.0,  # 1 hour
                "final_watch_seconds": 5400.0,    # 1.5 hours
            },
            {
                "name": "Science Channel",
                "session_watch_seconds": 600.0,   # 10 mins
                "initial_watch_seconds": 7200.0,  # 2 hours
                "final_watch_seconds": 7800.0,
            },
            {
                "name": "Idle Channel",
                "session_watch_seconds": 0.0,
                "initial_watch_seconds": 1800.0,
                "final_watch_seconds": 1800.0,
            },
        ],
        status="Completed",
    )


class TestSessionReportFormatting:
    def test_subject_generation(self, sample_report):
        subject = sample_report.generate_subject()
        assert "Browsing Report" in subject
        assert "40m 00s" in subject
        assert "0.7 hours" in subject
        assert "tester@gmail.com" in subject

    def test_plain_text_report(self, sample_report):
        text = sample_report.generate_plain_text()
        assert "YOUTUBE VIEWS APP - SESSION REPORT" in text
        assert "COMBINED WATCH TIME GENERATED: 40m 00s (0.7 hours)" in text
        assert "Tech Channel" in text
        assert "30m 00s" in text
        assert "Science Channel" in text
        assert "10m 00s" in text
        assert "Idle Channel" in text
        assert "Account:              tester@gmail.com" in text
        assert "Status:               Completed" in text

    def test_html_report(self, sample_report):
        html_code = sample_report.generate_html()
        assert "<!DOCTYPE html>" in html_code
        assert "Session Watch Time Report" in html_code
        assert "Combined Watch Time Generated" in html_code
        assert "40m 00s" in html_code
        assert "Tech Channel" in html_code
        assert "Science Channel" in html_code
        assert "Idle Channel" in html_code
        assert "tester@gmail.com" in html_code


class TestSessionTrackerWatchTimeAccumulation:
    def test_baseline_and_per_channel_session_calculation(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.set_channels([
            {"name": "Alpha Channel", "watch_seconds": 3600.0},
            {"name": "Beta Channel", "watch_seconds": 1200.0},
        ])
        ch_a = Channel(name="Alpha Channel", watch_seconds=3600.0)
        ch_b = Channel(name="Beta Channel", watch_seconds=1200.0)

        tracker = SessionChannelTracker(config_mgr=cfg, channels=[ch_a, ch_b])

        canon_a = SessionChannelTracker._canon_name("Alpha Channel")
        canon_b = SessionChannelTracker._canon_name("Beta Channel")

        # Baseline snapshots must be noted separately for each channel at start
        assert tracker.initial_channel_watch_seconds[canon_a] == 3600.0
        assert tracker.initial_channel_watch_seconds[canon_b] == 1200.0

        # Initially 0s accumulated in this session
        assert tracker.get_session_channel_watch_seconds("Alpha Channel") == 0.0
        assert tracker.get_session_channel_watch_seconds("Beta Channel") == 0.0
        assert tracker.get_session_total_watch_seconds() == 0.0

        # Play video for Alpha Channel
        tracker.set_active_channel("Alpha Channel")
        tracker.add_watch_delta("Alpha Channel", 300.0)  # 5 minutes
        tracker.add_watch_delta("Alpha Channel", 150.0)  # 2.5 minutes

        # Alpha Channel should have 450s, Beta Channel 0s
        assert tracker.get_session_channel_watch_seconds("Alpha Channel") == 450.0
        assert tracker.get_session_channel_watch_seconds("Beta Channel") == 0.0
        assert tracker.get_session_total_watch_seconds() == 450.0

        # Switch to Beta Channel
        tracker.set_active_channel("Beta Channel")
        tracker.add_watch_delta("Beta Channel", 600.0)  # 10 minutes

        assert tracker.get_session_channel_watch_seconds("Alpha Channel") == 450.0
        assert tracker.get_session_channel_watch_seconds("Beta Channel") == 600.0
        # Combined watch time
        assert tracker.get_session_total_watch_seconds() == 1050.0

        # Check full report data
        data = tracker.get_session_report_data()
        assert data["total_session_watch_seconds"] == 1050.0
        assert len(data["channels"]) == 2

        alpha_item = next(c for c in data["channels"] if c["name"] == "Alpha Channel")
        assert alpha_item["initial_watch_seconds"] == 3600.0
        assert alpha_item["session_watch_seconds"] == 450.0
        assert alpha_item["final_watch_seconds"] == 4050.0

        beta_item = next(c for c in data["channels"] if c["name"] == "Beta Channel")
        assert beta_item["initial_watch_seconds"] == 1200.0
        assert beta_item["session_watch_seconds"] == 600.0
        assert beta_item["final_watch_seconds"] == 1800.0


class TestEmailReporterDelivery:
    @patch("smtplib.SMTP")
    def test_send_via_smtp_success(self, mock_smtp, sample_report):
        mock_instance = MagicMock()
        mock_smtp.return_value = mock_instance

        log_q = queue.Queue()
        ok = EmailReporter.send_via_smtp(
            report=sample_report,
            recipient_email="reports@example.com",
            host="smtp.gmail.com",
            port=587,
            username="sender@gmail.com",
            password="fake-app-password",
            sender_email="sender@gmail.com",
            log_queue=log_q,
        )

        assert ok is True
        mock_smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=25)
        mock_instance.starttls.assert_called_once()
        mock_instance.login.assert_called_once_with("sender@gmail.com", "fake-app-password")
        mock_instance.sendmail.assert_called_once()

    def test_send_via_smtp_missing_password_fails(self, sample_report):
        log_q = queue.Queue()
        ok = EmailReporter.send_via_smtp(
            report=sample_report,
            recipient_email="reports@example.com",
            password="",
            log_queue=log_q,
        )
        assert ok is False

    @patch("time.sleep", return_value=None)
    def test_send_via_browser_gmail_success(self, mock_sleep, sample_report):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        def find_elements_mock(by, selector):
            if "role='textbox'" in selector:
                return [mock_body]
            if "contains(@aria-label, 'Send')" in selector or "Send" in selector:
                return [mock_send_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock
        mock_driver.find_element.return_value = MagicMock(get_attribute=lambda attr: "")

        log_q = queue.Queue()
        ok = EmailReporter.send_via_browser_gmail(
            driver=mock_driver,
            report=sample_report,
            recipient_email="reports@example.com",
            log_queue=log_q,
        )

        assert ok is True
        mock_driver.get.assert_called_once()
        assert "mail.google.com" in mock_driver.get.call_args[0][0]
        assert "reports%40example.com" in mock_driver.get.call_args[0][0] or "reports@example.com" in mock_driver.get.call_args[0][0]
        mock_send_btn.click.assert_called_once()

    @patch("time.sleep", return_value=None)
    def test_send_via_browser_gmail_signed_out_fails(self, mock_sleep, sample_report):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://accounts.google.com/signin/v2/identifier"

        log_q = queue.Queue()
        ok = EmailReporter.send_via_browser_gmail(
            driver=mock_driver,
            report=sample_report,
            recipient_email="reports@example.com",
            log_queue=log_q,
        )

        assert ok is False

    def test_dispatch_report_disabled(self, tmp_path, sample_report):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({"EmailReportingEnabled": False, "ReportRecipientEmail": "user@example.com"})

        assert EmailReporter.dispatch_report(sample_report, cfg) is False

    def test_dispatch_report_no_recipient(self, tmp_path, sample_report):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({"EmailReportingEnabled": True, "ReportRecipientEmail": ""})

        assert EmailReporter.dispatch_report(sample_report, cfg) is False

    @patch.object(EmailReporter, "send_via_smtp", return_value=True)
    def test_dispatch_report_smtp_mode(self, mock_smtp_send, tmp_path, sample_report):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": True,
            "ReportRecipientEmail": "dest@example.com",
            "ReportEmailMethod": "SMTP Connection",
            "SmtpPassword": "test-password",
        })

        ok = EmailReporter.dispatch_report(sample_report, cfg)
        assert ok is True
        mock_smtp_send.assert_called_once()

    @patch.object(EmailReporter, "send_via_browser_gmail", return_value=True)
    def test_dispatch_report_browser_mode(self, mock_browser_send, tmp_path, sample_report):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": True,
            "ReportRecipientEmail": "dest@example.com",
            "ReportEmailMethod": "Browser Gmail (Logged-in Account)",
        })

        mock_driver = MagicMock()
        ok = EmailReporter.dispatch_report(sample_report, cfg, driver=mock_driver)
        assert ok is True
        mock_browser_send.assert_called_once()



class TestWorkerReportIntegration:
    @patch.object(EmailReporter, "dispatch_report")
    def test_watching_worker_dispatches_report_on_completion(self, mock_dispatch, tmp_path):
        from src.automation.watching_worker import WatchingWorker

        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": True,
            "ReportRecipientEmail": "boss@company.com",
        })
        account = Account(email="worker.account@gmail.com", password="pwd")
        ch = Channel(name="TestChannel", url="https://youtube.com/@test")

        stop_ev = MagicMock()
        stop_ev.is_set.return_value = True  # Immediately stop

        worker = WatchingWorker(
            account=account,
            channels=[ch],
            config_mgr=cfg,
            stop_event=stop_ev,
        )
        worker.driver = MagicMock()

        # Directly call _send_session_report as called in worker finally block
        worker._send_session_report(status="Stopped by user")

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1] if mock_dispatch.call_args[1] else {}
        if not call_kwargs and mock_dispatch.call_args[0]:
            report_arg = mock_dispatch.call_args[0][0]
        else:
            report_arg = call_kwargs.get("report")

        assert report_arg.account_email == "worker.account@gmail.com"
        assert report_arg.session_mode == "Continuous Watching / Browsing"
        assert report_arg.status == "Stopped by user"

    @patch.object(EmailReporter, "dispatch_report")
    def test_automation_worker_dispatches_report_on_completion(self, mock_dispatch, tmp_path):
        from src.automation.worker import AutomationWorker

        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": True,
            "ReportRecipientEmail": "boss@company.com",
        })
        account = Account(email="auto.account@gmail.com", password="pwd")

        worker = AutomationWorker(
            account=account,
            video_tasks=[],
            config_mgr=cfg,
        )
        worker.driver = MagicMock()

        worker._send_session_report(status="Completed")

        mock_dispatch.assert_called_once()
        if mock_dispatch.call_args[1]:
            report_arg = mock_dispatch.call_args[1].get("report")
        else:
            report_arg = mock_dispatch.call_args[0][0]

        assert report_arg.account_email == "auto.account@gmail.com"
        assert report_arg.session_mode == "Automation Queue"
        assert report_arg.status == "Completed"
