"""Tests for manual action required email alerts and login check on video/short play."""
import queue
import socket
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import EmailReporter
from src.automation.login import (
    check_account_logged_in,
    detect_manual_challenge,
    perform_google_login,
)
from src.config.manager import ConfigManager
from src.models.account import Account


@pytest.fixture(autouse=True)
def reset_alert_cache():
    EmailReporter.reset_session_thread()
    yield
    EmailReporter.reset_session_thread()


class TestDetectManualChallenge:
    def test_detects_captcha_url(self):
        driver = MagicMock()
        driver.current_url = "https://www.google.com/sorry/index?continue=https://www.youtube.com/"
        driver.page_source = "<html><body>Please solve the captcha</body></html>"

        res = detect_manual_challenge(driver)
        assert res is not None
        alert_type, details = res
        assert "CAPTCHA" in alert_type
        assert "Manual completion in the browser is required" in details

    def test_detects_2step_verification_url_and_source(self):
        driver = MagicMock()
        driver.current_url = "https://accounts.google.com/signin/v2/challenge/totp"
        driver.page_source = "<html><body>2-Step Verification: Enter the 2-digit number</body></html>"

        res = detect_manual_challenge(driver)
        assert res is not None
        alert_type, details = res
        assert "2-Step Verification" in alert_type or "Security Challenge" in alert_type
        assert "verify" in details.lower()

    def test_detects_google_asking_again_for_signin(self):
        driver = MagicMock()
        driver.current_url = "https://accounts.google.com/ServiceLogin?continue=https://mail.google.com/"
        driver.page_source = "<html><body>Your session has expired. Sign in again to continue.</body></html>"

        res = detect_manual_challenge(driver)
        assert res is not None
        alert_type, details = res
        assert "Re-Authentication" in alert_type
        assert "password confirmation again" in details

    def test_returns_none_on_normal_page(self):
        driver = MagicMock()
        driver.current_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        driver.page_source = "<html><body>Welcome to YouTube</body></html>"

        assert detect_manual_challenge(driver) is None


class TestAlertEmailContentAndMachineInfo:
    @patch("smtplib.SMTP")
    def test_email_alert_includes_pc_hostname_and_system_info(self, mock_smtp, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "SmtpPassword": "test-password",
            "SmtpUsername": "sender@gmail.com",
            "SystemName": "Render-Node-01",
        })
        account = Account(email="active.worker@gmail.com", password="pwd")

        ok = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Manual Action Required - Google CAPTCHA",
            message_details="Google presented a CAPTCHA challenge before playing video.",
            config_mgr=cfg,
        )
        assert ok is True

        mock_smtp.assert_called_once()
        instance = mock_smtp.return_value
        call_args = instance.sendmail.call_args[0]
        import email as em
        parsed_msg = em.message_from_string(call_args[2])
        payload = parsed_msg.get_payload(decode=True).decode("utf-8")

        # Verify PC and machine information is included
        assert "- System Name:  Render-Node-01" in payload
        assert "- PC Hostname:  " in payload
        assert socket.gethostname() in payload
        assert "- Local IP:     " in payload
        assert "- Account:      active.worker@gmail.com" in payload
        assert "Manual action is required on this PC" in payload

    @patch("smtplib.SMTP")
    def test_email_alert_auto_detects_hostname_when_system_name_empty(self, mock_smtp, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "SmtpPassword": "test-password",
            "SmtpUsername": "sender@gmail.com",
            "SystemName": "",  # Empty system name
        })
        account = Account(email="worker2@gmail.com", password="pwd")

        ok = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Manual Action Required - Account Logged Out",
            message_details="Account was logged out and re-login failed.",
            config_mgr=cfg,
        )
        assert ok is True

        instance = mock_smtp.return_value
        call_args = instance.sendmail.call_args[0]
        import email as em
        parsed_msg = em.message_from_string(call_args[2])
        payload = parsed_msg.get_payload(decode=True).decode("utf-8")

        # Must auto-detect hostname instead of showing N/A
        host = socket.gethostname()
        assert f"- System Name:  {host} (Auto-detected Hostname)" in payload
        assert f"- PC Hostname:  {host}" in payload

    @patch("smtplib.SMTP")
    def test_alert_throttling_prevents_duplicate_spam(self, mock_smtp, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "ReportRecipientEmail": "admin@example.com",
            "SmtpPassword": "test-password",
            "SmtpUsername": "sender@gmail.com",
        })
        account = Account(email="spam.test@gmail.com", password="pwd")

        # First alert sends email
        ok1 = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Bot Challenge",
            message_details="First challenge",
            config_mgr=cfg,
        )
        assert ok1 is True
        assert mock_smtp.call_count == 1

        # Second identical alert within throttle window should be throttled without calling sendmail again
        ok2 = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Bot Challenge",
            message_details="Duplicate challenge immediately after",
            config_mgr=cfg,
        )
        assert ok2 is True
        assert mock_smtp.call_count == 1  # not incremented


class TestCheckAccountLoggedInBeforePlay:
    def test_returns_true_for_none_or_empty_account(self):
        driver = MagicMock()
        assert check_account_logged_in(driver, None) is True
        assert check_account_logged_in(driver, Account(email="", password="")) is True

    def test_returns_true_when_ytcfg_logged_in_is_true(self):
        driver = MagicMock()
        driver.current_url = "https://www.youtube.com/watch?v=123"
        driver.execute_script.return_value = True  # window.ytcfg.get('LOGGED_IN') == True
        account = Account(email="active@gmail.com", password="123")

        assert check_account_logged_in(driver, account, content_type="video", content_title="Test Video") is True

    def test_detects_captcha_before_play_and_alerts(self):
        driver = MagicMock()
        driver.current_url = "https://www.google.com/sorry/index"
        driver.page_source = "<html><body>Please solve the captcha</body></html>"
        account = Account(email="blocked@gmail.com", password="123")

        mock_alert_callback = MagicMock()
        result = check_account_logged_in(
            driver,
            account,
            alert_callback=mock_alert_callback,
            content_type="short",
            content_title="Cool Short",
        )
        assert result is False
        mock_alert_callback.assert_called_once()
        alert_type, details = mock_alert_callback.call_args[0]
        assert "CAPTCHA" in alert_type
        assert "Cool Short" in details
        assert "blocked@gmail.com" in details

    @patch("src.automation.login.perform_google_login", return_value=True)
    def test_logged_out_auto_relogin_success_resumes_playback(self, mock_login):
        driver = MagicMock()
        driver.current_url = "https://www.youtube.com/watch?v=123"
        # ytcfg returns False (signed out)
        driver.execute_script.return_value = False
        account = Account(email="user@gmail.com", password="pwd")

        result = check_account_logged_in(
            driver,
            account,
            content_type="video",
            content_title="Awesome Vid",
        )
        assert result is True
        mock_login.assert_called_once()

    @patch("src.automation.login.perform_google_login", return_value=False)
    def test_logged_out_auto_relogin_failure_triggers_alert(self, mock_login):
        driver = MagicMock()
        driver.current_url = "https://www.youtube.com/watch?v=123"
        driver.execute_script.return_value = False
        account = Account(email="user@gmail.com", password="pwd")

        mock_alert_callback = MagicMock()
        result = check_account_logged_in(
            driver,
            account,
            alert_callback=mock_alert_callback,
            content_type="video",
            content_title="Awesome Vid",
        )
        assert result is False
        mock_alert_callback.assert_called_once()
        alert_type, details = mock_alert_callback.call_args[0]
        assert "Account Logged Out" in alert_type
        assert "user@gmail.com" in details
        assert "Awesome Vid" in details


class TestPerformGoogleLoginAlerts:
    @patch("src.automation.login.safe_get")
    def test_perform_google_login_dispatches_alert_on_challenge_detected(self, mock_safe_get):
        driver = MagicMock()
        driver.current_url = "https://accounts.google.com/signin/v2/challenge/pwd"
        driver.page_source = "<html><body>Google 2-Step Verification: check your phone</body></html>"

        account = Account(email="challenge.user@gmail.com", password="pwd")
        mock_alert_cb = MagicMock()
        stop_event = threading.Event()

        def on_alert(alert_type, details):
            stop_event.set()

        mock_alert_cb.side_effect = on_alert

        fake_times = [0.0, 15.0] + [20.0 + i * 2.0 for i in range(50)]
        time_idx = 0
        def fake_time():
            nonlocal time_idx
            val = fake_times[min(time_idx, len(fake_times) - 1)]
            time_idx += 1
            return val

        with patch("src.automation.login.time.time", side_effect=fake_time), \
             patch("src.automation.login.time.sleep"), \
             patch("src.automation.login.human_like_delay"):
            res = perform_google_login(
                driver,
                account,
                stop_event=stop_event,
                alert_callback=mock_alert_cb,
            )
        assert res is False
        mock_alert_cb.assert_called_once()
        alert_type, details = mock_alert_cb.call_args[0]
        assert "2-Step Verification" in alert_type or "Security Challenge" in alert_type
        assert "challenge.user@gmail.com" in details
        alert_type, details = mock_alert_cb.call_args[0]
        assert "2-Step Verification" in alert_type or "Security Challenge" in alert_type
        assert "challenge.user@gmail.com" in details
