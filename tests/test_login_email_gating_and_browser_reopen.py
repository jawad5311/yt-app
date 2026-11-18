"""Tests for email-page CAPTCHA false-positive fix, login-gated emails,
YouTube-URL-first start notification, external browser close auto-reopen,
and SystemName on_save callback regression check.
"""
import queue
import threading
from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import InvalidSessionIdException

from src.automation.email_reporter import EmailReporter
from src.automation.login import (
    _perform_automated_login_steps,
    detect_manual_challenge,
    perform_google_login,
)
from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.models.watch_stats import WatchResult


@pytest.fixture(autouse=True)
def reset_reporter():
    EmailReporter.reset_session_thread()
    yield
    EmailReporter.reset_session_thread()


# ---------------------------------------------------------------------------
# 1. Email Entry Page & Hidden CAPTCHA False-Positive Fix
# ---------------------------------------------------------------------------

class TestEmailPageHiddenCaptchaFix:
    def test_detect_manual_challenge_ignores_hidden_captchaimg_and_script_strings(self):
        """Hidden captchaimg templates or JS strings on Google's email page must NOT trigger CAPTCHA."""
        driver = MagicMock()
        driver.current_url = "https://accounts.google.com/v3/signin/identifier?continue=https://www.youtube.com/"
        driver.page_source = (
            '<html><head><script>var msg="type the characters you see in the picture";</script></head>'
            '<body><input id="identifierId" type="email">'
            '<div style="display:none"><img id="captchaimg" src=""></div>'
            '</body></html>'
        )
        driver.execute_script.return_value = "Sign in\nUse your Google Account\nEmail or phone\nNext"

        email_box = MagicMock()
        email_box.is_displayed.return_value = True

        hidden_captcha = MagicMock()
        hidden_captcha.is_displayed.return_value = False
        hidden_captcha.tag_name = "img"
        hidden_captcha.get_attribute.return_value = ""

        def find_elements_side_effect(by, value):
            if "captchaimg" in value:
                return [hidden_captcha]
            if "identifierId" in value:
                return [email_box]
            return []

        driver.find_elements.side_effect = find_elements_side_effect

        assert detect_manual_challenge(driver) is None

    def test_detect_manual_challenge_detects_actually_visible_captchaimg(self):
        """A genuinely visible captchaimg with non-empty src MUST still be detected."""
        driver = MagicMock()
        driver.current_url = "https://accounts.google.com/v3/signin/identifier"
        driver.page_source = '<html><body><img id="captchaimg" src="https://accounts.google.com/Captcha?ctoken=abc"></body></html>'
        driver.execute_script.return_value = "Type the text you hear or see"

        visible_captcha = MagicMock()
        visible_captcha.is_displayed.return_value = True
        visible_captcha.tag_name = "img"
        visible_captcha.get_attribute.return_value = "https://accounts.google.com/Captcha?ctoken=abc"

        def find_elements_side_effect(by, value):
            if "captchaimg" in value:
                return [visible_captcha]
            return []

        driver.find_elements.side_effect = find_elements_side_effect

        res = detect_manual_challenge(driver)
        assert res is not None
        assert "CAPTCHA" in res[0]

    @patch("src.automation.login.human_like_delay")
    @patch("src.automation.login.slow_type")
    def test_automated_login_enters_email_despite_hidden_captcha_in_page_source(
        self, mock_slow_type, mock_delay
    ):
        """_perform_automated_login_steps enters email instead of breaking on hidden captchaimg."""
        driver = MagicMock()
        driver.current_url = "https://accounts.google.com/v3/signin/identifier"
        driver.page_source = (
            '<html><body><input id="identifierId" type="email">'
            '<div style="display:none"><img id="captchaimg" src=""></div></body></html>'
        )
        driver.execute_script.return_value = "Sign in\nEmail or phone\nNext"

        email_box = MagicMock()
        email_box.is_displayed.return_value = True
        email_box.get_attribute.return_value = "user@gmail.com"

        next_btn = MagicMock()
        next_btn.is_displayed.return_value = True

        def on_next_click():
            driver.current_url = "https://myaccount.google.com/"
            driver.page_source = "<html><body>user@gmail.com</body></html>"
            driver.execute_script.return_value = "Welcome user@gmail.com"

        next_btn.click.side_effect = on_next_click

        hidden_captcha = MagicMock()
        hidden_captcha.is_displayed.return_value = False
        hidden_captcha.tag_name = "img"
        hidden_captcha.get_attribute.return_value = ""

        def find_elements_side_effect(by, value):
            if "captchaimg" in value:
                return [hidden_captcha]
            if "identifierId" in value and "accounts.google.com" in driver.current_url:
                return [email_box]
            return []

        driver.find_elements.side_effect = find_elements_side_effect
        driver.find_element.return_value = next_btn

        account = Account(email="user@gmail.com", password="secret")
        res = _perform_automated_login_steps(driver, account)

        assert res is True
        mock_slow_type.assert_called_once_with(email_box, "user@gmail.com")
        next_btn.click.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Login-Gated Email Delivery & YouTube-URL-First Start Notification
# ---------------------------------------------------------------------------

class TestLoginGatedEmailAndYouTubeStartNotification:
    def _make_worker(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": True,
            "SessionStartNotification": True,
            "ReportEmailMethod": "Auto",
            "ReportRecipientEmail": "admin@example.com",
            "SignInWaitSeconds": 0.01,
            "SignInMaxRetries": 1,
        })
        channels = [Channel(name="Test Channel", url="https://youtube.com/@testchannel")]
        cfg.set_channels([c.to_dict() for c in channels])
        account = Account(email="yt.office.002@gmail.com", password="pwd")
        return WatchingWorker(
            account=account,
            channels=channels,
            config_mgr=cfg,
            log_queue=queue.Queue(),
            stop_event=threading.Event(),
            event_queue=queue.Queue(),
            stats_store=StatsStore(tmp_path / "stats.json"),
        )

    def test_no_alert_or_final_report_email_sent_when_not_logged_in(self, tmp_path):
        """App must NOT send alert or session report email if account is not logged in."""
        worker = self._make_worker(tmp_path)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://accounts.google.com/v3/signin/identifier"
        mock_driver.page_source = "<html><body>Sign in</body></html>"

        def fake_login(drv, acc, alert_callback=None, **kwargs):
            # Even if login triggers alert_callback during wait, worker must skip it while unauthenticated
            if alert_callback:
                alert_callback("Manual Action Required - Google CAPTCHA / Bot Challenge", "CAPTCHA")
            return False

        with patch("src.automation.watching_worker.setup_driver", return_value=mock_driver), \
             patch("src.automation.watching_worker.perform_google_login", side_effect=fake_login), \
             patch("src.automation.watching_worker.close_driver"), \
             patch.object(EmailReporter, "send_alert_notification") as mock_alert, \
             patch.object(worker, "_send_session_report") as mock_report, \
             patch.object(worker, "_async_init_and_send_start_notification") as mock_start:
            worker.run()

            mock_alert.assert_not_called()
            mock_report.assert_not_called()
            mock_start.assert_not_called()
            assert worker.has_started_browsing is False

    def test_reaches_youtube_url_before_sending_session_started_email(self, tmp_path):
        """After login completes on myaccount.google.com, app navigates to YouTube first, then sends start email."""
        worker = self._make_worker(tmp_path)
        mock_driver = MagicMock()
        mock_driver.session_id = "sess-1"
        mock_driver.current_url = "https://myaccount.google.com/"
        mock_driver.execute_script.return_value = 1

        url_at_start_email = []

        def fake_safe_get(drv, url, **kwargs):
            drv.current_url = url
            return True

        def fake_start_notification():
            url_at_start_email.append(mock_driver.current_url)
            worker.stop_event.set()

        with patch("src.automation.watching_worker.setup_driver", return_value=mock_driver), \
             patch("src.automation.watching_worker.perform_google_login", return_value=True), \
             patch("src.automation.watching_worker.safe_get", side_effect=fake_safe_get), \
             patch("src.automation.watching_worker.human_like_delay"), \
             patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
             patch("src.automation.watching_worker.close_driver"), \
             patch.object(worker, "_send_session_report"), \
             patch.object(worker, "_async_init_and_send_start_notification", side_effect=fake_start_notification):
            worker.run()

        assert len(url_at_start_email) == 1
        assert "youtube.com" in url_at_start_email[0].lower()

    def test_email_reporter_skips_headless_alert_when_active_driver_unauthenticated(self, tmp_path):
        """EmailReporter.send_alert_notification must not launch headless Chrome when active driver is on accounts.google.com."""
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": True,
            "ReportEmailMethod": "Auto",
            "ReportRecipientEmail": "admin@example.com",
            "SmtpPassword": "",
        })
        account = Account(email="notloggedin@gmail.com", password="pwd")
        active_driver = MagicMock()
        active_driver._is_reporting = False
        active_driver.current_url = "https://accounts.google.com/v3/signin/identifier"

        with patch.object(EmailReporter, "send_via_headless_browser_gmail") as mock_headless:
            ok = EmailReporter.send_alert_notification(
                account=account,
                alert_type="Manual Action Required - Google CAPTCHA / Bot Challenge",
                message_details="CAPTCHA challenge",
                config_mgr=cfg,
                driver=active_driver,
            )
            assert ok is False
            mock_headless.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Auto-Reopen Main Browser on External/Accidental Close
# ---------------------------------------------------------------------------

class TestExternalBrowserCloseAutoReopen:
    def _make_worker(self, tmp_path):
        cfg = ConfigManager(tmp_path / "settings.json")
        cfg.update_settings({
            "EmailReportingEnabled": False,
            "SessionStartNotification": False,
            "ReportEmailMethod": "Disabled",
            "SignInWaitSeconds": 0.05,
        })
        channels = [Channel(name="Channel A", url="https://youtube.com/@channelA")]
        cfg.set_channels([c.to_dict() for c in channels])
        account = Account(email="reopen@gmail.com", password="pwd")
        return WatchingWorker(
            account=account,
            channels=channels,
            config_mgr=cfg,
            log_queue=queue.Queue(),
            stop_event=threading.Event(),
            event_queue=queue.Queue(),
            stats_store=StatsStore(tmp_path / "stats.json"),
        )

    def test_reopens_browser_when_closed_during_signin_wait(self, tmp_path):
        """If user closes browser while waiting for sign-in, app reopens browser and completes sign-in."""
        worker = self._make_worker(tmp_path)
        driver1 = MagicMock(name="driver1")
        driver1.session_id = "s1"
        driver2 = MagicMock(name="driver2")
        driver2.session_id = "s2"
        driver2.current_url = "https://www.youtube.com/"
        driver2.execute_script.return_value = 1

        worker.driver = driver1
        login_calls = {"count": 0}

        def fake_login(drv, acc, **kwargs):
            login_calls["count"] += 1
            if login_calls["count"] == 1:
                raise InvalidSessionIdException("invalid session id")
            return True

        with patch("src.automation.watching_worker.perform_google_login", side_effect=fake_login), \
             patch("src.automation.watching_worker.setup_driver", return_value=driver2) as mock_setup, \
             patch("src.automation.watching_worker.close_driver") as mock_close:
            # Make interruptible wait instant in test
            with patch.object(worker.stop_event, "wait", return_value=False):
                ok = worker._ensure_signed_in_and_wait(wait_seconds=0.05)

        assert ok is True
        assert login_calls["count"] == 2
        mock_close.assert_called_with(driver1, log_queue=worker.log_queue)
        mock_setup.assert_called_once()
        assert worker.driver is driver2

    def test_reopens_browser_and_resumes_from_homepage_when_closed_mid_session(self, tmp_path):
        """If user accidentally closes Chrome during watching, app reopens Chrome, navigates to YouTube Home, and continues browsing."""
        worker = self._make_worker(tmp_path)
        driver1 = MagicMock(name="driver1")
        driver1.session_id = "s1"
        driver1.current_url = "https://www.youtube.com/"
        driver1.execute_script.return_value = 1

        driver2 = MagicMock(name="driver2")
        driver2.session_id = "s2"
        driver2.current_url = "https://www.youtube.com/"
        driver2.execute_script.return_value = 1

        drivers = [driver1, driver2]
        setup_calls = []

        def fake_setup(*args, **kwargs):
            d = drivers.pop(0)
            setup_calls.append(d)
            return d

        scan_calls = {"count": 0}

        def fake_scan_home(**kwargs):
            scan_calls["count"] += 1
            if scan_calls["count"] == 1:
                # Simulate user accidentally closing Chrome window mid-session
                raise InvalidSessionIdException("invalid session id: target window already closed")
            # On the reopened browser, return a matched video on Home feed!
            return (
                {"title": "Recovered Video", "href": "/watch?v=recovered123"},
                worker.channels[0],
                "https://www.youtube.com/watch?v=recovered123",
                "Recovered Video",
                "Channel A",
            )

        played_titles = []

        def fake_play_target(item, full_url, title, ch_display, session_watched_keys, is_home=True, source="home"):
            played_titles.append((title, source, worker.driver))
            worker.stop_event.set()
            return WatchResult(completed=True, watched_seconds=30.0, video_duration=60.0, playback_finished=True)

        safe_get_urls = []

        def fake_safe_get(drv, url, **kwargs):
            safe_get_urls.append((drv, url))
            drv.current_url = url
            return True

        with patch("src.automation.watching_worker.setup_driver", side_effect=fake_setup), \
             patch("src.automation.watching_worker.perform_google_login", return_value=True), \
             patch("src.automation.watching_worker.close_driver"), \
             patch("src.automation.watching_worker.safe_get", side_effect=fake_safe_get), \
             patch("src.automation.watching_worker.human_like_delay"), \
             patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
             patch.object(worker, "_scan_home_feed_for_target", side_effect=fake_scan_home), \
             patch.object(worker, "_play_target_video", side_effect=fake_play_target), \
             patch.object(worker, "_send_session_report"):
            with patch.object(worker.stop_event, "wait", return_value=False):
                worker.run()

        # Browser was opened initially (driver1) AND reopened (driver2) after close!
        assert len(setup_calls) == 2
        assert setup_calls[0] is driver1
        assert setup_calls[1] is driver2
        # Reopened driver was navigated to YouTube Home
        assert any(drv is driver2 and url == "https://www.youtube.com/" for drv, url in safe_get_urls)
        # Video browsing resumed from homepage on driver2
        assert len(played_titles) == 1
        assert played_titles[0] == ("Recovered Video", "home", driver2)

    def test_does_not_reopen_browser_when_stop_event_is_set(self, tmp_path):
        """When user clicks Stop in app UI, browser disconnect must exit cleanly without reopening."""
        worker = self._make_worker(tmp_path)
        driver1 = MagicMock(name="driver1")
        driver1.session_id = "s1"
        driver1.current_url = "https://www.youtube.com/"
        driver1.execute_script.return_value = 1

        def fake_scan_home(**kwargs):
            worker.stop_event.set()
            raise InvalidSessionIdException("invalid session id")

        with patch("src.automation.watching_worker.setup_driver", return_value=driver1) as mock_setup, \
             patch("src.automation.watching_worker.perform_google_login", return_value=True), \
             patch("src.automation.watching_worker.close_driver"), \
             patch("src.automation.watching_worker.human_like_delay"), \
             patch.object(worker, "_scan_home_feed_for_target", side_effect=fake_scan_home), \
             patch.object(worker, "_send_session_report"):
            worker.run()

        # Must only launch once, never reopen after Stop
        assert mock_setup.call_count == 1


# ---------------------------------------------------------------------------
# 4. SystemName on_save Callback Regression Check (NameError: logger)
# ---------------------------------------------------------------------------

def test_on_system_name_saved_does_not_raise_name_error(tmp_path):
    """Verify YTBotApp._on_system_name_saved saves SystemName without raising NameError."""
    from src.ui.app import YTBotApp

    cfg = ConfigManager(tmp_path / "settings.json")
    fake_app = MagicMock(spec=YTBotApp)
    fake_app.config_mgr = cfg
    fake_app.views = {}

    # Call the real method directly
    YTBotApp._on_system_name_saved(fake_app, "Worker-PC-99")

    assert cfg.get_setting("SystemName") == "Worker-PC-99"
