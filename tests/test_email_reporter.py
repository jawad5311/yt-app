"""Unit and integration tests for Session Watch Time Email Reporting."""

from datetime import datetime, timedelta
import queue
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import (
    DEFAULT_ADMIN_RECIPIENT_EMAIL,
    EmailReporter,
    SessionReport,
    resolve_report_recipients,
)
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
    def test_subject_generation_threads_with_session_stamp(self, sample_report):
        subject = sample_report.generate_subject()
        assert "Views App Session" in subject
        assert "30 Oct 2026" in subject
        assert "tester@gmail.com" in subject

    def test_custom_subject_generation(self, sample_report):
        sample_report.custom_subject = "Views App Session [Thread-123] - tester@gmail.com"
        assert sample_report.generate_subject() == "Views App Session [Thread-123] - tester@gmail.com"

    def test_plain_text_report(self, sample_report):
        text = sample_report.generate_plain_text()
        assert "YOUTUBE VIEWS APP - SESSION REPORT" in text
        assert "COMBINED WATCH TIME GENERATED: 40m 00s (0.7 hours)" in text
        assert "Tech Channel" in text
        assert "30m 00s" in text
        assert "Science Channel" in text
        assert "10m 00s" in text
        # Channels with 0s watch time in session must be excluded
        assert "Idle Channel" not in text
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
        # Channels with 0s watch time in session must be excluded
        assert "Idle Channel" not in html_code
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

    def test_dispatch_report_no_recipient(self, tmp_path, sample_report):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({"ReportRecipientEmail": ""})

        assert EmailReporter.dispatch_report(sample_report, cfg) is False

    @patch.object(EmailReporter, "send_via_smtp", return_value=True)
    def test_dispatch_report_smtp_mode(self, mock_smtp_send, tmp_path, sample_report):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
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
            "ReportRecipientEmail": "dest@example.com",
            "ReportEmailMethod": "Active Browser Gmail",
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
        assert "Views App Session" in report_arg.custom_subject

    @patch.object(EmailReporter, "dispatch_report")
    def test_automation_worker_dispatches_report_on_completion(self, mock_dispatch, tmp_path):
        from src.automation.worker import AutomationWorker

        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
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
        assert "Views App Session" in report_arg.custom_subject

    @patch.object(EmailReporter, "send_start_notification")
    def test_watching_worker_sends_start_notification_at_launch(self, mock_start, tmp_path):
        from src.automation.watching_worker import WatchingWorker

        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "boss@company.com",
        })
        account = Account(email="worker.account@gmail.com", password="pwd")
        ch = Channel(name="TestChannel", url="https://youtube.com/@test")

        worker = WatchingWorker(
            account=account,
            channels=[ch],
            config_mgr=cfg,
        )

        worker._send_start_notification()
        mock_start.assert_called_once()
        assert mock_start.call_args[1]["account"] == account
        assert mock_start.call_args[1]["session_mode"] == "Continuous Watching / Browsing"
        assert "Views App Session" in mock_start.call_args[1]["custom_subject"]

    @patch.object(EmailReporter, "send_start_notification")
    def test_automation_worker_sends_start_notification_at_launch(self, mock_start, tmp_path):
        from src.automation.worker import AutomationWorker

        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "boss@company.com",
        })
        account = Account(email="auto.account@gmail.com", password="pwd")

        worker = AutomationWorker(
            account=account,
            video_tasks=[],
            config_mgr=cfg,
            worker_id=0,
        )

        worker._send_start_notification()
        mock_start.assert_called_once()
        assert mock_start.call_args[1]["account"] == account
        assert mock_start.call_args[1]["session_mode"] == "Automation Queue"
        assert "Views App Session" in mock_start.call_args[1]["custom_subject"]


class TestHeadlessEmailDelivery:
    @patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True)
    def test_send_start_notification_simple_format_and_threading(self, mock_headless, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "user@example.com",
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
        })
        account = Account(email="active@gmail.com", password="123")

        start_dt = datetime(2026, 10, 31, 4, 15, 0)
        custom_subj = "Views App Session [31 Oct 2026 - 04:15 AM] - active@gmail.com"

        ok = EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Watching / Browsing",
            config_mgr=cfg,
            start_time=start_dt,
            custom_subject=custom_subj,
            total_app_watch_seconds=7200.0,
        )

        assert ok is True
        mock_headless.assert_called_once()
        args = mock_headless.call_args[1]
        assert args["account"] == account
        assert args["subject"] == custom_subj
        assert args["recipient_email"] == "user@example.com"
        assert "Automation has started." in args["body_text"]
        assert "Account: active@gmail.com" in args["body_text"]
        assert "Started at 31 Oct 2026" in args["body_text"]
        assert "Total Watch Time Generated for All Channels: 2.0 hours (2h 00m)" in args["body_text"]
        # Verify log_queue is None to keep start notification silent in the UI logs
        assert args["log_queue"] is None

    @patch("src.automation.driver.setup_driver")
    @patch("src.automation.driver.close_driver")
    @patch.object(EmailReporter, "_deliver_via_gmail_compose", return_value=True)
    def test_send_via_headless_browser_gmail_flow(self, mock_deliver, mock_close, mock_setup):
        account = Account(email="test@gmail.com", password="pwd")
        mock_driver = MagicMock()
        mock_setup.return_value = mock_driver

        log_q = queue.Queue()
        ok = EmailReporter.send_via_headless_browser_gmail(
            account=account,
            subject="Test Subject",
            body_text="Test Body",
            recipient_email="recipient@gmail.com",
            log_queue=log_q,
        )

        assert ok is True
        mock_setup.assert_called_once_with(account=account, log_queue=log_q, headless=True)
        mock_deliver.assert_called_once()
        mock_close.assert_called_once_with(mock_driver, log_queue=log_q)


class TestRecipientResolutionAndBcc:
    def test_default_recipient_when_unconfigured(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({"ReportRecipientEmail": ""})
        to_email, bcc_email = resolve_report_recipients(cfg)
        assert to_email == DEFAULT_ADMIN_RECIPIENT_EMAIL
        assert bcc_email is None

    def test_default_recipient_when_none_config(self):
        to_email, bcc_email = resolve_report_recipients(None)
        assert to_email == DEFAULT_ADMIN_RECIPIENT_EMAIL
        assert bcc_email is None

    def test_recipient_equals_admin_no_bcc(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({"ReportRecipientEmail": DEFAULT_ADMIN_RECIPIENT_EMAIL})
        to_email, bcc_email = resolve_report_recipients(cfg)
        assert to_email == DEFAULT_ADMIN_RECIPIENT_EMAIL
        assert bcc_email is None

    def test_custom_recipient_bcc_admin(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({"ReportRecipientEmail": "client@example.com"})
        to_email, bcc_email = resolve_report_recipients(cfg)
        assert to_email == "client@example.com"
        assert bcc_email == DEFAULT_ADMIN_RECIPIENT_EMAIL

    @patch("smtplib.SMTP")
    def test_smtp_sends_to_recipient_and_bcc(self, mock_smtp, sample_report):
        mock_instance = MagicMock()
        mock_smtp.return_value = mock_instance

        ok = EmailReporter.send_via_smtp(
            report=sample_report,
            recipient_email="client@example.com",
            bcc_email=DEFAULT_ADMIN_RECIPIENT_EMAIL,
            password="test-password",
            sender_email="sender@gmail.com",
        )
        assert ok is True
        called_args = mock_instance.sendmail.call_args[0]
        sender_arg = called_args[0]
        envelope_recipients = called_args[1]
        msg_str = called_args[2]

        assert sender_arg == "sender@gmail.com"
        assert envelope_recipients == ["client@example.com", DEFAULT_ADMIN_RECIPIENT_EMAIL]
        assert "To: client@example.com" in msg_str
        assert "Bcc:" not in msg_str

    @patch("time.sleep", return_value=None)
    def test_gmail_compose_url_includes_bcc(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"
        mock_body = MagicMock(is_displayed=lambda: True)
        mock_send_btn = MagicMock(is_displayed=lambda: True)

        def find_elements_mock(by, selector):
            if "role='textbox'" in selector:
                return [mock_body]
            if "contains(@aria-label, 'Send')" in selector or "Send" in selector:
                return [mock_send_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock
        mock_driver.find_element.return_value = MagicMock(get_attribute=lambda attr: "")

        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Thread Subject",
            plain_text="Body content",
            recipient_email="client@example.com",
            bcc_email=DEFAULT_ADMIN_RECIPIENT_EMAIL,
        )
        assert ok is True
        compose_url = mock_driver.get.call_args[0][0]
        assert "to=client%40example.com" in compose_url
        assert "bcc=jawad%40ytgrowthexpert.com" in compose_url



class TestReportEnhancedMetricsAndAlerts:
    def test_videos_and_shorts_counts_in_report_text_and_html(self, sample_report):
        sample_report.total_videos_watched = 15
        sample_report.regular_videos_count = 10
        sample_report.shorts_count = 5
        sample_report.interruption_reason = "Completed normally (all targets achieved)"

        # Check plain text report
        text = sample_report.generate_plain_text()
        assert "Videos Watched:       15 (10 regular, 5 Shorts)" in text
        assert "Interruption Reason:  Completed normally (all targets achieved)" in text

        # Check HTML report
        html = sample_report.generate_html()
        assert "15" in html
        assert "10 regular, 5 Shorts" in html
        assert "Total Videos Watched:" in html

    def test_target_goal_progress_in_report_text_and_html(self, sample_report):
        sample_report.channel_reports = [
            {
                "name": "TargetChannel",
                "session_watch_seconds": 3600.0,
                "initial_watch_seconds": 7200.0,
                "final_watch_seconds": 10800.0,
                "target_watch_seconds": 18000.0,  # 5 hours
                "target_goal_text": "3.0h / 5.0h (60%)",
                "target_achieved": False,
                "regular_videos_count": 4,
                "shorts_count": 2,
            },
            {
                "name": "AchievedChannel",
                "session_watch_seconds": 1800.0,
                "initial_watch_seconds": 3600.0,
                "final_watch_seconds": 5400.0,
                "target_watch_seconds": 3600.0,
                "target_goal_text": "1.5h / 1.0h (100%) [ACHIEVED]",
                "target_achieved": True,
                "regular_videos_count": 1,
                "shorts_count": 0,
            },
        ]

        text = sample_report.generate_plain_text()
        assert "3.0h / 5.0h (60%)" in text
        assert "Target Progress" in text

        html = sample_report.generate_html()
        assert "3.0h / 5.0h (60%)" in html
        assert "Target Goal Progress" in html
        assert "4 vids, 2 shorts" in html

    def test_interruption_reason_display_on_error_and_bot_challenge(self, sample_report):
        sample_report.status = "Bot Challenge / CAPTCHA Alert"
        sample_report.interruption_reason = "Google presented a CAPTCHA verification challenge"

        text = sample_report.generate_plain_text()
        assert "Interruption Reason:  Google presented a CAPTCHA verification challenge" in text

        html = sample_report.generate_html()
        assert "Interruption / Exit Reason:" in html
        assert "Google presented a CAPTCHA verification challenge" in html

    @patch("smtplib.SMTP")
    def test_send_alert_notification_smtp(self, mock_smtp, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "client@example.com",
            "SmtpPassword": "test-password",
            "SmtpUsername": "sender@gmail.com",
        })
        account = Account(email="active@gmail.com", password="123")
        start_dt = datetime(2026, 10, 31, 4, 30, 0)
        custom_subj = "Views App Session [31 Oct 2026 - 04:30 AM] - active@gmail.com"

        ok = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Bot Challenge / CAPTCHA Alert",
            message_details="A CAPTCHA verification challenge appeared.",
            config_mgr=cfg,
            start_time=start_dt,
            custom_subject=custom_subj,
        )

        assert ok is True
        mock_smtp.assert_called_once()
        instance = mock_smtp.return_value
        assert instance.sendmail.called
        call_args = instance.sendmail.call_args[0]
        # Recipients must include To (client) and BCC (admin)
        assert call_args[1] == ["client@example.com", DEFAULT_ADMIN_RECIPIENT_EMAIL]
        # Message content must contain the alert details
        import email as em
        parsed_msg = em.message_from_string(call_args[2])
        payload = parsed_msg.get_payload(decode=True).decode("utf-8")
        assert "URGENT ALERT: BOT CHALLENGE / CAPTCHA ALERT" in payload
        assert "active@gmail.com" in payload

    @patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True)
    def test_send_alert_notification_headless(self, mock_headless, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "user@example.com",
            "SmtpPassword": "",  # No SMTP password -> uses headless browser
        })
        account = Account(email="acc@gmail.com", password="123")
        custom_subj = "Views App Session [31 Oct 2026 - 04:30 AM] - acc@gmail.com"

        ok = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Proxy / Network Failure",
            message_details="Connection to YouTube timed out.",
            config_mgr=cfg,
            custom_subject=custom_subj,
        )

        assert ok is True
        mock_headless.assert_called_once()
        args = mock_headless.call_args[1]
        assert args["subject"] == custom_subj
        assert args["recipient_email"] == "user@example.com"
        assert args["bcc_email"] == DEFAULT_ADMIN_RECIPIENT_EMAIL
        assert "URGENT ALERT: PROXY / NETWORK FAILURE" in args["body_text"]
        assert args["log_queue"] is None

    def test_tracker_records_videos_shorts_and_target_goal_progress(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        ch1 = Channel(name="Alpha", watch_seconds=3600.0, target_watch_seconds=7200.0)
        ch2 = Channel(name="Beta", watch_seconds=0.0, target_watch_seconds=3600.0)

        tracker = SessionChannelTracker(config_mgr=cfg, channels=[ch1, ch2])
        tracker.set_active_channel("Alpha")
        tracker.record_video_watched(is_short=False, channel_name="Alpha")
        tracker.record_video_watched(is_short=True, channel_name="Alpha")
        tracker.add_watch_delta("Alpha", 1800.0)

        tracker.set_active_channel("Beta")
        tracker.record_video_watched(is_short=True, channel_name="Beta")
        tracker.add_watch_delta("Beta", 3600.0)

        data = tracker.get_session_report_data()
        assert data["total_videos_watched"] == 3
        assert data["regular_videos_count"] == 1
        assert data["shorts_count"] == 2

        alpha_data = next(c for c in data["channels"] if c["name"] == "Alpha")
        assert alpha_data["regular_videos_count"] == 1
        assert alpha_data["shorts_count"] == 1
        assert alpha_data["target_achieved"] is False
        assert "1.5h / 2.0h (75%)" in alpha_data["target_goal_text"]

        beta_data = next(c for c in data["channels"] if c["name"] == "Beta")
        assert beta_data["regular_videos_count"] == 0
        assert beta_data["shorts_count"] == 1
        assert beta_data["target_achieved"] is True
        assert "[ACHIEVED]" in beta_data["target_goal_text"]
