"""Unit and integration tests for Session Watch Time Email Reporting."""

import queue
from datetime import datetime, timedelta
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
        session_mode="Continuous Browsing",
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
        assert "Entertainment Hub Session" in subject
        assert "30 Oct 2026" in subject
        assert "tester@gmail.com" in subject

    def test_custom_subject_generation(self, sample_report):
        sample_report.custom_subject = "Views App Session [Thread-123] - tester@gmail.com"
        assert sample_report.generate_subject() == "Views App Session [Thread-123] - tester@gmail.com"

    def test_plain_text_report(self, sample_report):
        text = sample_report.generate_plain_text()
        assert "ENTERTAINMENT HUB - SESSION REPORT" in text
        assert "COMBINED ENTERTAINMENT TIME: 40m 00s (0.7 hours)" in text
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
        assert "Session Entertainment Report" in html_code
        assert "Combined Entertainment Time" in html_code
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

        mock_toast = MagicMock(is_displayed=lambda: True)

        def find_elements_mock(by, selector):
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
        assert report_arg.session_mode == "Continuous Browsing"
        assert report_arg.status == "Stopped by user"
        assert "Entertainment Hub Session" in report_arg.custom_subject

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
        assert "Entertainment Hub Session" in report_arg.custom_subject

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
        assert mock_start.call_args[1]["session_mode"] == "Continuous Browsing"
        assert "Entertainment Hub Session" in mock_start.call_args[1]["custom_subject"]

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
        assert "Entertainment Hub Session" in mock_start.call_args[1]["custom_subject"]


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
        custom_subj = "Entertainment Hub Session [31 Oct 2026 - 04:15 AM] - active@gmail.com"

        ok = EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
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
        assert "Entertainment session has started." in args["body_text"]
        assert "Account: active@gmail.com" in args["body_text"]
        assert "Started at 31 Oct 2026" in args["body_text"]
        assert "Total Entertainment Time for All Brands: 2.0 hours (2h 00m)" in args["body_text"]
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

        mock_toast = MagicMock(is_displayed=lambda: True)

        def find_elements_mock(by, selector):
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
        assert "Videos Watched" in text
        assert "6 video(s)" in text
        assert "1 video(s)" in text

        # Verify exact column header ordering: Channel Name -> This Session -> Target Progress -> Videos Watched
        header_line = [line for line in text.splitlines() if "Channel Name" in line][0]
        col_names = [c.strip() for c in header_line.split("|")]
        assert col_names == ["Channel Name", "This Session", "Target Progress", "Videos Watched"]
        assert "Lifetime Total" not in header_line

        html = sample_report.generate_html()
        assert "3.0h / 5.0h (60%)" in html
        assert "Target Progress" in html
        assert "Videos Watched" in html
        assert "4 vids, 2 shorts" in html
        assert "Lifetime Total" not in html

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


    def test_discovery_sources_breakdown_in_report_text_and_html(self, sample_report):
        sample_report.total_videos_watched = 8
        sample_report.regular_videos_count = 6
        sample_report.shorts_count = 2
        sample_report.suggested_videos_count = 3
        sample_report.search_videos_count = 2
        sample_report.home_videos_count = 2
        sample_report.shorts_count = 4
        sample_report.random_videos_count = 1
        sample_report.search_queries_count = 5

        plain_text = sample_report.generate_plain_text()
        # Verify strict ordering: Suggested -> Search -> Home -> Shorts -> Random
        idx_sugg = plain_text.index("Suggested Section:")
        idx_search = plain_text.index("YouTube Search:")
        idx_home = plain_text.index("Home Page Feed:")
        idx_shorts = plain_text.index("YouTube Shorts:")
        idx_rand = plain_text.index("Random / Other:")

        assert idx_sugg < idx_search < idx_home < idx_shorts < idx_rand
        assert "Suggested Section:   3 video(s)" in plain_text
        assert "YouTube Search:      2 video(s) (5 searches performed)" in plain_text
        assert "Home Page Feed:      2 video(s)" in plain_text
        assert "YouTube Shorts:      4 short(s)" in plain_text
        assert "Random / Other:      1 video(s) (non-target channels)" in plain_text

        html_text = sample_report.generate_html()
        idx_h_sugg = html_text.index("Suggested Section")
        idx_h_search = html_text.index("YouTube Search")
        idx_h_home = html_text.index("Homepage Feed")
        idx_h_shorts = html_text.index("YouTube Shorts")
        idx_h_rand = html_text.index("Random / Other")
        assert idx_h_sugg < idx_h_search < idx_h_home < idx_h_shorts < idx_h_rand
        assert "5 searches" in html_text

    def test_channels_sorted_by_session_watch_time_descending(self, sample_report):
        """Verify channels in report are sorted by most watch time generated in current session."""
        sample_report.channel_reports = [
            {"name": "Low Channel", "session_watch_seconds": 60.0, "final_watch_seconds": 60.0},
            {"name": "High Channel", "session_watch_seconds": 3600.0, "final_watch_seconds": 3600.0},
            {"name": "Mid Channel", "session_watch_seconds": 600.0, "final_watch_seconds": 600.0},
        ]

        text = sample_report.generate_plain_text()
        idx_high = text.index("High Channel")
        idx_mid = text.index("Mid Channel")
        idx_low = text.index("Low Channel")
        assert idx_high < idx_mid < idx_low

        html_text = sample_report.generate_html()
        idx_h_high = html_text.index("High Channel")
        idx_h_mid = html_text.index("Mid Channel")
        idx_h_low = html_text.index("Low Channel")
        assert idx_h_high < idx_h_mid < idx_h_low

    def test_tracker_records_discovery_sources_and_search_queries(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        tracker = SessionChannelTracker(config_mgr=cfg)

        tracker.record_video_watched(is_short=False, source="suggested")
        tracker.record_video_watched(is_short=True, source="suggested", is_random=True)
        tracker.record_video_watched(is_short=False, source="search")
        tracker.record_video_watched(is_short=False, source="home")
        tracker.record_video_watched(is_short=False, source="home")

        tracker.record_search_query()
        tracker.record_search_query()
        tracker.record_search_query()

        data = tracker.get_session_report_data()
        assert data["suggested_videos_count"] == 2
        assert data["search_videos_count"] == 1
        assert data["home_videos_count"] == 2
        assert data["random_videos_count"] == 1
        assert data["search_queries_count"] == 3
        assert data["total_videos_watched"] == 5


class TestSessionEmailThreading:
    """Tests for single session email thread persistence across start, alerts, and reports."""

    @patch("time.sleep", return_value=None)
    def test_deliver_via_gmail_thread_reply_success(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox/FMfcgz123"

        mock_reply_btn = MagicMock()
        mock_reply_btn.is_displayed.return_value = True

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        mock_toast = MagicMock(is_displayed=lambda: True)

        def find_elements_mock(by, selector):
            if "@role='dialog'" in selector:
                return []
            if "contains(@data-tooltip, 'Reply')" in selector or "text()='Reply'" in selector or "contains(., 'Reply')" in selector:
                return [mock_reply_btn]
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        thread_info = {}
        ok = EmailReporter._deliver_via_gmail_thread_reply(
            driver=mock_driver,
            plain_text="Final session watch report content",
            thread_url="https://mail.google.com/mail/u/0/#inbox/FMfcgz123",
            email_description="session watch time report",
            thread_info=thread_info,
        )

        assert ok is True
        mock_driver.get.assert_called_once_with("https://mail.google.com/mail/u/0/#inbox/FMfcgz123")
        mock_reply_btn.click.assert_called()
        mock_send_btn.click.assert_called()
        assert thread_info.get("thread_url") == "https://mail.google.com/mail/u/0/#inbox/FMfcgz123"

    @patch("time.sleep", return_value=None)
    def test_deliver_via_gmail_thread_reply_search_fallback(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#search/subject"

        mock_row = MagicMock()
        mock_row.is_displayed.return_value = True

        mock_reply_btn = MagicMock()
        mock_reply_btn.is_displayed.return_value = True

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        mock_toast = MagicMock(is_displayed=lambda: True)

        def find_elements_mock(by, selector):
            if "@role='dialog'" in selector:
                return []
            if "contains(@class, 'zA')" in selector:
                return [mock_row]
            if "contains(@data-tooltip, 'Reply')" in selector or "text()='Reply'" in selector:
                return [mock_reply_btn]
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        ok = EmailReporter._deliver_via_gmail_thread_reply(
            driver=mock_driver,
            plain_text="Final report text",
            thread_url=None,
            subject="Views App Session [01 Nov 2026 - 03:00 AM] - test@gmail.com",
            email_description="session watch time report",
        )

        assert ok is True
        assert "subject%3A" in mock_driver.get.call_args[0][0]
        mock_row.click.assert_called()

    @patch("smtplib.SMTP")
    def test_smtp_threading_headers_in_reply_to_and_references(self, mock_smtp, sample_report):
        mock_instance = MagicMock()
        mock_smtp.return_value = mock_instance

        sample_report.thread_message_id = "<initial_msg_12345@yttool.app>"
        thread_info = {}

        ok = EmailReporter.send_via_smtp(
            report=sample_report,
            recipient_email="reports@example.com",
            host="smtp.gmail.com",
            port=587,
            username="sender@gmail.com",
            password="fake-password",
            sender_email="sender@gmail.com",
            thread_info=thread_info,
        )

        assert ok is True
        sent_raw = mock_instance.sendmail.call_args[0][2]
        assert "In-Reply-To: <initial_msg_12345@yttool.app>" in sent_raw
        assert "References: <initial_msg_12345@yttool.app>" in sent_raw
        assert "Message-ID:" in sent_raw

    @patch("time.sleep", return_value=None)
    def test_compose_captures_view_message_toast_link(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"

        mock_body = MagicMock(is_displayed=lambda: True)
        mock_send_btn = MagicMock(is_displayed=lambda: True)
        mock_toast = MagicMock(is_displayed=lambda: True)

        mock_view_link = MagicMock()
        mock_view_link.is_displayed.return_value = True
        mock_view_link.get_attribute.return_value = "#sent/FMfcgzGsmWxyz"
        def on_link_click():
            mock_driver.current_url = "https://mail.google.com/mail/u/0/#sent/FMfcgzGsmWxyz"
        mock_view_link.click.side_effect = on_link_click

        def find_elements_mock(by, selector):
            if "@role='dialog'" in selector:
                return []
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "link_vmsg" in selector or "View message" in selector:
                return [mock_view_link]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        thread_info = {}
        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Views App Session - test@gmail.com",
            plain_text="Starting session",
            recipient_email="jawad@ytgrowthexpert.com",
            email_description="start notification email",
            thread_info=thread_info,
        )

        assert ok is True
        assert "FMfcgzGsmWxyz" in thread_info.get("thread_url", "")
        assert EmailReporter.last_thread_url == thread_info["thread_url"]


class TestPersistentHeadlessReportingBrowser:
    @patch("src.automation.driver.setup_driver")
    def test_create_headless_reporting_driver_passes_is_reporting(self, mock_setup):
        account = Account(email="tester@gmail.com", password="pwd")
        mock_driver = MagicMock()
        mock_setup.return_value = mock_driver

        log_q = queue.Queue()
        drv = EmailReporter.create_headless_reporting_driver(account, log_queue=log_q)

        assert drv is mock_driver
        mock_setup.assert_called_once_with(
            account=account,
            log_queue=log_q,
            headless=True,
            is_reporting=True,
        )

    @patch.object(EmailReporter, "_deliver_via_gmail_compose", return_value=True)
    def test_send_start_notification_uses_persistent_driver(self, mock_deliver, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "SessionStartNotification": True,
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
            "ReportRecipientEmail": "admin@test.com",
        })
        account = Account(email="bot@gmail.com", password="p")
        mock_driver = MagicMock()

        ok = EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
            config_mgr=cfg,
            driver=mock_driver,
        )

        assert ok is True
        mock_deliver.assert_called_once()
        args, kwargs = mock_deliver.call_args
        assert kwargs.get("driver") is mock_driver or args[0] is mock_driver

    @patch.object(EmailReporter, "send_via_browser_gmail", return_value=True)
    @patch.object(EmailReporter, "send_via_headless_browser_gmail")
    def test_dispatch_report_reuses_persistent_driver_for_headless_method(
        self, mock_send_headless, mock_send_browser, sample_report, tmp_path
    ):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
            "ReportRecipientEmail": "admin@test.com",
        })
        account = Account(email="bot@gmail.com", password="p")
        mock_persistent_driver = MagicMock()

        ok = EmailReporter.dispatch_report(
            report=sample_report,
            config_mgr=cfg,
            account=account,
            driver=mock_persistent_driver,
        )

        assert ok is True
        mock_send_browser.assert_called_once()
        mock_send_headless.assert_not_called()
        assert mock_send_browser.call_args[0][0] is mock_persistent_driver

    @patch("time.sleep", return_value=None)
    def test_compose_start_notification_navigates_to_sent_and_parks_on_thread(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"

        mock_body = MagicMock(is_displayed=lambda: True)
        mock_send_btn = MagicMock(is_displayed=lambda: True)
        mock_toast = MagicMock(is_displayed=lambda: True)
        mock_sent_row = MagicMock(is_displayed=lambda: True)

        def on_sent_row_click():
            mock_driver.current_url = "https://mail.google.com/mail/u/0/#sent/FMfcgzGsmSent123"

        mock_sent_row.click.side_effect = on_sent_row_click

        def find_elements_mock(by, selector):
            if "@role='dialog'" in selector:
                return []
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "link_vmsg" in selector or "View message" in selector:
                return []
            if "contains(@class, 'zA')" in selector:
                return [mock_sent_row]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        thread_info = {}
        log_q = queue.Queue()
        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Views App Session - test@gmail.com",
            plain_text="Starting session",
            recipient_email="jawad@ytgrowthexpert.com",
            email_description="start notification email",
            thread_info=thread_info,
            log_queue=log_q,
        )

        assert ok is True
        assert mock_sent_row.click.called
        assert "FMfcgzGsmSent123" in thread_info.get("thread_url", "")
        assert EmailReporter.last_thread_url == "https://mail.google.com/mail/u/0/#sent/FMfcgzGsmSent123"

    @patch("time.sleep", return_value=None)
    def test_thread_reply_parks_on_thread_and_expands_trimmed_quote(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#sent/FMfcgzGsmSent123"

        mock_reply_btn = MagicMock(is_displayed=lambda: True)
        mock_trimmed_btn = MagicMock(is_displayed=lambda: True)
        mock_body = MagicMock(is_displayed=lambda: True)
        mock_send_btn = MagicMock(is_displayed=lambda: True)
        mock_toast = MagicMock(is_displayed=lambda: True)

        def find_elements_mock(by, selector):
            if "@role='dialog'" in selector:
                return []
            if "Trimmed content" in selector or "ajR" in selector:
                return [mock_trimmed_btn]
            if "contains(@data-tooltip, 'Reply')" in selector or "text()='Reply'" in selector or "contains(., 'Reply')" in selector:
                return [mock_reply_btn]
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        thread_info = {}
        log_q = queue.Queue()
        ok = EmailReporter._deliver_via_gmail_thread_reply(
            driver=mock_driver,
            plain_text="Final report content",
            thread_url="https://mail.google.com/mail/u/0/#sent/FMfcgzGsmSent123",
            email_description="session watch time report",
            thread_info=thread_info,
            log_queue=log_q,
        )

        assert ok is True
        mock_reply_btn.click.assert_called()
        mock_trimmed_btn.click.assert_called()
        mock_send_btn.click.assert_called()

    @patch.object(EmailReporter, "_deliver_via_gmail_compose", return_value=True)
    @patch.object(EmailReporter, "_deliver_via_gmail_thread_reply")
    def test_send_via_browser_gmail_retries_sent_folder_before_compose(
        self, mock_reply, mock_compose, sample_report
    ):
        mock_driver = MagicMock()
        sample_report.thread_url = "https://mail.google.com/mail/u/0/#inbox/stale123"

        # First reply fails, retry with #sent succeeds
        mock_reply.side_effect = [False, True]

        log_q = queue.Queue()
        ok = EmailReporter.send_via_browser_gmail(
            driver=mock_driver,
            report=sample_report,
            recipient_email="reports@example.com",
            log_queue=log_q,
        )

        assert ok is True
        assert mock_reply.call_count == 2
        # Second call target url is #sent
        assert mock_reply.call_args_list[1].kwargs.get("thread_url") == "https://mail.google.com/mail/u/0/#sent"
        mock_compose.assert_not_called()

    def test_start_notification_suppresses_intermediate_logs_and_emits_notified_successfully(self):
        log_q = queue.Queue()
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"
        mock_driver.find_elements.return_value = []
        mock_driver.switch_to.alert = MagicMock()

        t_counter = [100.0]
        def fake_time():
            t_counter[0] += 0.5
            return t_counter[0]

        # Mock successful send and delivery confirmation
        with patch("time.sleep"), patch("time.time", side_effect=fake_time):
            # When find_elements called for toasts or buttons
            mock_send_btn = MagicMock()
            mock_body = MagicMock(text="Some body")
            mock_body.is_displayed.side_effect = [True, True, True, False, False, False]

            def mock_fe(by, val):
                if "role='dialog'" in val:
                    return []
                if "Message sent" in val:
                    return [MagicMock(is_displayed=lambda: True)]
                if "role='textbox'" in val:
                    return [mock_body]
                if "role='button'" in val:
                    return [mock_send_btn]
                return []

            mock_driver.find_elements.side_effect = mock_fe

            ok = EmailReporter._deliver_via_gmail_compose(
                driver=mock_driver,
                subject="Test Start",
                plain_text="Session started",
                recipient_email="user@test.com",
                log_queue=log_q,
                email_description="start notification email",
            )

            assert ok is True
            messages = []
            while not log_q.empty():
                messages.append(log_q.get_nowait())

            # All intermediate logs should be suppressed
            for msg in messages:
                assert "Navigating to Gmail web compose" not in str(msg)
                assert "Navigating to Gmail Sent folder" not in str(msg)
                assert "Parked on session" not in str(msg)
                assert "Successfully sent start notification" not in str(msg)

            # Exactly "Notified successfully" should be emitted
            assert any("Notified successfully" in str(msg) for msg in messages)



def test_html_dashboard_generation_includes_progress_bars_and_buttons(sample_report):
    """Verify that HTML report includes progress bars, channel links, and CTA action buttons."""
    sample_report.channel_reports = [
        {
            "name": "Alpha Channel",
            "url": "https://youtube.com/@alphachannel",
            "session_watch_seconds": 1800.0,
            "final_watch_seconds": 3600.0,
            "target_watch_seconds": 7200.0,
            "target_goal_text": "1.0h / 2.0h (50%)",
            "target_achieved": False,
            "regular_videos_count": 2,
            "shorts_count": 1,
        }
    ]
    html = sample_report.generate_html()
    assert "Open YouTube" in html
    assert "YouTube Studio" in html
    assert "Visit Channel" in html
    assert "https://youtube.com/@alphachannel" in html
    assert "background-color: #0284c7; width: 50.0%;" in html

    body = sample_report.generate_html_body()
    assert "Open YouTube" in body
    assert "<!DOCTYPE html>" not in body


class TestHeadlessFirstEmailDeliveryAndMainWindowFallback:
    def test_start_notification_uses_headless_and_falls_back_to_main_window(self, tmp_path):
        """Verify that start notification tries headless browser first, and falls back to main window if headless fails."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
        })
        account = Account(email="user@gmail.com", password="pwd")
        mock_main_driver = MagicMock()
        mock_main_driver.session_id = "main-window-session"
        mock_main_driver.execute_script.return_value = 1

        # Case 1: Headless succeeds
        with patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True) as mock_headless, \
             patch.object(EmailReporter, "_deliver_via_gmail_compose") as mock_compose:
            ok = EmailReporter.send_start_notification(
                account=account,
                session_mode="Continuous Browsing",
                config_mgr=cfg,
                fallback_driver=mock_main_driver,
            )
            assert ok is True
            mock_headless.assert_called_once()
            assert mock_headless.call_args.kwargs.get("headless") is True
            mock_compose.assert_not_called()

        # Case 2: Headless fails -> falls back to main window driver
        with patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=False) as mock_headless, \
             patch.object(EmailReporter, "_deliver_via_gmail_compose", return_value=True) as mock_compose:
            ok = EmailReporter.send_start_notification(
                account=account,
                session_mode="Continuous Browsing",
                config_mgr=cfg,
                fallback_driver=mock_main_driver,
            )
            assert ok is True
            mock_headless.assert_called_once()
            assert mock_headless.call_args.kwargs.get("headless") is True
            mock_compose.assert_called_once()
            assert mock_compose.call_args.kwargs.get("driver") is mock_main_driver

    def test_dispatch_report_uses_headless_first_and_falls_back_to_main_window(self, tmp_path, sample_report):
        """Verify that dispatch_report attempts headless browser first, and falls back to main window if headless fails."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
        })
        account = Account(email="user@gmail.com", password="pwd")
        mock_main_driver = MagicMock()
        mock_main_driver.session_id = "main-window-session"
        mock_main_driver.execute_script.return_value = 1

        # Case 1: Headless delivery succeeds
        with patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True) as mock_headless, \
             patch.object(EmailReporter, "send_via_browser_gmail") as mock_browser:
            ok = EmailReporter.dispatch_report(
                report=sample_report,
                config_mgr=cfg,
                account=account,
                main_driver=mock_main_driver,
            )
            assert ok is True
            mock_headless.assert_called_once()
            assert mock_headless.call_args.kwargs.get("headless") is True
            mock_browser.assert_not_called()

        # Case 2: Headless fails -> falls back to main window (send_via_browser_gmail)
        with patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=False) as mock_headless, \
             patch.object(EmailReporter, "send_via_browser_gmail", return_value=True) as mock_browser:
            ok = EmailReporter.dispatch_report(
                report=sample_report,
                config_mgr=cfg,
                account=account,
                main_driver=mock_main_driver,
            )
            assert ok is True
            assert mock_headless.call_args_list[0].kwargs.get("headless") is True
            mock_browser.assert_called_once()
            assert mock_browser.call_args[0][0] is mock_main_driver

        # Case 3: No main window open (main_driver is None) and headless fails -> opens headed browser
        with patch.object(EmailReporter, "send_via_headless_browser_gmail", side_effect=[False, True]) as mock_headless, \
             patch.object(EmailReporter, "send_via_browser_gmail") as mock_browser:
            ok = EmailReporter.dispatch_report(
                report=sample_report,
                config_mgr=cfg,
                account=account,
                main_driver=None,
            )
            assert ok is True
            assert mock_headless.call_count == 2
            # 1st attempt: headless=True
            assert mock_headless.call_args_list[0].kwargs.get("headless") is True
            # 2nd attempt (fallback): headless=False (headed main window)
            assert mock_headless.call_args_list[1].kwargs.get("headless") is False


def test_channel_watch_time_displays_only_hours_and_minutes_without_decimal_hours():
    """Verify that in email reports, channel watch time is displayed in hours and minutes only
    (e.g. '1h 51m' or '6m 29s') without trailing decimal hour annotations like '(1.9 hours)' or '(0.1 hours)'."""
    now = datetime(2026, 11, 16, 8, 0, 0)
    report = SessionReport(
        account_email="test@gmail.com",
        session_mode="Continuous Browsing",
        status="Completed",
        channel_reports=[
            {
                "name": "Lifestyle Mapping",
                "session_watch_seconds": 6660.0,  # 1h 51m -> formerly '1h 51m (1.9 hours)'
                "total_videos_count": 2,
                "target_goal_text": "2h 00m (92.5%)",
            },
            {
                "name": "The Kidney Nutrition Institute",
                "session_watch_seconds": 389.0,  # 6m 29s -> formerly '6m 29s (0.1 hours)'
                "total_videos_count": 1,
                "target_goal_text": "30m 00s (21.6%)",
            },
        ],
        start_time=now - timedelta(hours=2),
        end_time=now,
        duration_seconds=7200.0,
        total_session_watch_seconds=7049.0,
    )

    # 1. Plain text verification
    plain = report.generate_plain_text()
    assert "1h 51m" in plain
    assert "6m 29s" in plain
    assert "(1.9 hours)" not in plain
    assert "(0.1 hours)" not in plain

    # 2. HTML body verification
    html_body = report.generate_html_body()
    assert "1h 51m" in html_body
    assert "6m 29s" in html_body
    assert "(1.9 hours)" not in html_body
    assert "(0.1 hours)" not in html_body


