"""Unit tests for unified email threading across an entire session (from start to app close)."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.automation.email_reporter import EmailReporter, SessionReport
from src.models.account import Account


class DummyConfigMgr:
    def __init__(self, settings=None):
        self._settings = settings or {}

    def get_setting(self, key, default=""):
        return self._settings.get(key, default)

    def get_int(self, key, default=0):
        val = self._settings.get(key, default)
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_channels(self):
        return []


@pytest.fixture
def account():
    return Account(email="testuser@example.com", password="dummy_password")


@pytest.fixture
def config_mgr():
    return DummyConfigMgr({
        "SystemName": "Server-Alpha",
        "SenderEmail": "bot@example.com",
        "AdminRecipientEmail": "admin@example.com",
        "ReportEmailMethod": "SMTP Connection",
        "SmtpHost": "smtp.example.com",
        "SmtpPort": "587",
        "SmtpUsername": "bot@example.com",
        "SmtpPassword": "botpassword",
    })


def test_full_session_email_lifecycle_threading_chain(account, config_mgr):
    """Verify that Start -> Nap Sleep -> Night Sleep -> Critical Alert -> Final Session Report
    all deliver under the exact same thread with consistent Subject, In-Reply-To, and cumulative References."""
    session_thread_info = {}
    EmailReporter.reset_session_thread()

    session_start = datetime(2026, 11, 8, 8, 0, 0)
    session_subject = f"Server-Alpha - Entertainment Hub Session [08 Nov 2026 - 08:00 AM] - {account.email}"

    sent_messages = []

    def mock_sendmail(sender, recipients, msg_str):
        sent_messages.append(msg_str)

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value = mock_smtp
        mock_smtp.sendmail.side_effect = mock_sendmail

        # 1. Start Notification
        ok_start = EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
            config_mgr=config_mgr,
            start_time=session_start,
            custom_subject=session_subject,
            total_app_watch_seconds=1200.0,
            thread_info=session_thread_info,
        )
        assert ok_start is True
        assert len(sent_messages) == 1
        msg1 = sent_messages[0]
        assert f"Subject: {session_subject}" in msg1
        assert "In-Reply-To" not in msg1  # Root email has no In-Reply-To

        root_id = session_thread_info.get("root_message_id")
        assert root_id is not None
        assert session_thread_info.get("message_id") == root_id
        assert session_thread_info.get("references") == [root_id]

        # 2. Short Sleep / Nap Notification (No target videos found)
        nap_report = SessionReport(
            account_email=account.email,
            session_mode="Continuous Browsing",
            start_time=session_start,
            end_time=session_start + timedelta(hours=1),
            duration_seconds=3600.0,
            total_session_watch_seconds=1800.0,
            status="Short Sleep (Nap Mode)",
            custom_subject=session_subject,
            thread_message_id=session_thread_info.get("message_id"),
            thread_references=list(session_thread_info.get("references", [])),
            root_message_id=session_thread_info.get("root_message_id"),
            system_name="Server-Alpha",
            sleep_cycle_number=1,
            sleep_duration_hours=2.0,
            wake_up_time=session_start + timedelta(hours=3),
            sleep_mode_type="nap",
        )
        ok_nap = EmailReporter.dispatch_report(
            report=nap_report,
            config_mgr=config_mgr,
            account=account,
            thread_info=session_thread_info,
        )
        assert ok_nap is True
        assert len(sent_messages) == 2
        msg2 = sent_messages[1]
        assert f"Subject: {session_subject}" in msg2
        assert f"In-Reply-To: {root_id}" in msg2
        assert f"References: {root_id}" in msg2

        msg2_id = session_thread_info.get("message_id")
        assert msg2_id is not None and msg2_id != root_id
        assert session_thread_info.get("references") == [root_id, msg2_id]

        # 3. Critical Alert Notification (Mid-session issue)
        ok_alert = EmailReporter.send_alert_notification(
            account=account,
            alert_type="CAPTCHA Challenge",
            message_details="Bot verification prompted by YouTube.",
            config_mgr=config_mgr,
            start_time=session_start,
            custom_subject=session_subject,
            thread_message_id=session_thread_info.get("message_id"),
            thread_info=session_thread_info,
        )
        assert ok_alert is True
        assert len(sent_messages) == 3
        msg3 = sent_messages[2]
        assert f"Subject: {session_subject}" in msg3
        assert f"In-Reply-To: {msg2_id}" in msg3
        assert f"References: {root_id} {msg2_id}" in msg3

        msg3_id = session_thread_info.get("message_id")
        assert msg3_id is not None and msg3_id != msg2_id
        assert session_thread_info.get("references") == [root_id, msg2_id, msg3_id]

        # 4. Long Sleep / Night Mode Notification
        night_report = SessionReport(
            account_email=account.email,
            session_mode="Continuous Browsing",
            start_time=session_start,
            end_time=session_start + timedelta(hours=8),
            duration_seconds=28800.0,
            total_session_watch_seconds=14400.0,
            status="Long Sleep (Night Mode)",
            custom_subject=session_subject,
            thread_message_id=session_thread_info.get("message_id"),
            thread_references=list(session_thread_info.get("references", [])),
            root_message_id=session_thread_info.get("root_message_id"),
            system_name="Server-Alpha",
            sleep_duration_hours=6.5,
            wake_up_time=session_start + timedelta(hours=14, minutes=30),
            sleep_mode_type="night",
        )
        ok_night = EmailReporter.dispatch_report(
            report=night_report,
            config_mgr=config_mgr,
            account=account,
            thread_info=session_thread_info,
        )
        assert ok_night is True
        assert len(sent_messages) == 4
        msg4 = sent_messages[3]
        assert f"Subject: {session_subject}" in msg4
        assert f"In-Reply-To: {msg3_id}" in msg4
        assert f"References: {root_id} {msg2_id} {msg3_id}" in msg4

        msg4_id = session_thread_info.get("message_id")
        assert msg4_id is not None and msg4_id != msg3_id
        assert session_thread_info.get("references") == [root_id, msg2_id, msg3_id, msg4_id]

        # 5. Final Session Report (App stopped by user)
        final_report = SessionReport(
            account_email=account.email,
            session_mode="Continuous Browsing",
            start_time=session_start,
            end_time=session_start + timedelta(hours=15),
            duration_seconds=54000.0,
            total_session_watch_seconds=25000.0,
            status="Stopped by user",
            custom_subject=session_subject,
            thread_message_id=session_thread_info.get("message_id"),
            thread_references=list(session_thread_info.get("references", [])),
            root_message_id=session_thread_info.get("root_message_id"),
            system_name="Server-Alpha",
        )
        ok_final = EmailReporter.dispatch_report(
            report=final_report,
            config_mgr=config_mgr,
            account=account,
            thread_info=session_thread_info,
        )
        assert ok_final is True
        assert len(sent_messages) == 5
        msg5 = sent_messages[4]
        assert f"Subject: {session_subject}" in msg5
        assert f"In-Reply-To: {msg4_id}" in msg5
        assert f"References: {root_id} {msg2_id} {msg3_id} {msg4_id}" in msg5

        # All 5 messages shared the exact same Subject!
        for raw in sent_messages:
            assert f"Subject: {session_subject}" in raw


def test_new_session_resets_thread_cache(account, config_mgr):
    """Verify that starting a brand new session resets the cached class-level IDs so it does not chain to an old session."""
    session1_info = {}
    session2_info = {}
    sent_messages = []

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value = mock_smtp
        mock_smtp.sendmail.side_effect = lambda s, r, m: sent_messages.append(m)

        # Session 1: Start and end
        EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
            config_mgr=config_mgr,
            custom_subject="Session 1 Subject",
            thread_info=session1_info,
        )
        sess1_root = session1_info.get("root_message_id")
        assert sess1_root is not None

        # Session 2: Fresh Start
        EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
            config_mgr=config_mgr,
            custom_subject="Session 2 Subject",
            thread_info=session2_info,
        )
        sess2_root = session2_info.get("root_message_id")
        assert sess2_root is not None
        assert sess2_root != sess1_root

        # Session 2 start email must NOT have In-Reply-To from Session 1
        msg_sess2_start = sent_messages[1]
        assert "In-Reply-To" not in msg_sess2_start
        assert "Session 2 Subject" in msg_sess2_start


def test_browser_gmail_thread_url_propagation(account):
    """Verify that browser-based Gmail delivery properly captures and reuses thread_url."""
    mock_driver = MagicMock()
    mock_driver.current_url = "https://mail.google.com/mail/u/0/#sent/18d84920ab4c12ef"
    mock_driver.find_elements.return_value = []

    thread_info = {}

    with patch.object(EmailReporter, "_deliver_via_gmail_compose", return_value=True) as mock_compose, \
         patch.object(EmailReporter, "_deliver_via_gmail_thread_reply", return_value=True) as mock_reply:

        # 1. Start notification via browser compose
        mock_compose.side_effect = lambda **kwargs: kwargs.get("thread_info", {}).update({
            "thread_url": "https://mail.google.com/mail/u/0/#sent/18d84920ab4c12ef"
        }) or True

        EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
            config_mgr=DummyConfigMgr({"ReportEmailMethod": "Active Browser Gmail"}),
            driver=mock_driver,
            thread_info=thread_info,
            custom_subject="Session Test Browser",
        )

        assert thread_info.get("thread_url") == "https://mail.google.com/mail/u/0/#sent/18d84920ab4c12ef"

        # 2. Critical Alert via browser reply
        EmailReporter.send_alert_notification(
            account=account,
            alert_type="Browser Disconnect",
            message_details="Connection dropped",
            config_mgr=DummyConfigMgr({"ReportEmailMethod": "Active Browser Gmail"}),
            driver=mock_driver,
            thread_info=thread_info,
            custom_subject="Session Test Browser",
        )

        # Thread reply must have been called with the captured thread URL
        mock_reply.assert_called_once()
        call_kwargs = mock_reply.call_args[1]
        assert call_kwargs["thread_url"] == "https://mail.google.com/mail/u/0/#sent/18d84920ab4c12ef"
        assert call_kwargs["subject"] == "Session Test Browser"
