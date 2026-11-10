"""Unit tests for sign-in wait before start notification and sleep/nap resumption notifications."""

from datetime import datetime, timedelta
import queue
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.automation.email_reporter import EmailReporter, SessionReport
from src.automation.session_tracker import SessionChannelTracker
from src.automation.watching_worker import WatchingWorker
from src.automation.worker import AutomationWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel


@pytest.fixture
def test_worker(tmp_path):
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    stats_file = tmp_path / "stats.json"
    stats_store = StatsStore(stats_file=stats_file)

    account = Account(email="waituser@gmail.com", password="password123")
    channels = [Channel(name="Target Channel", url="https://youtube.com/@targetchannel")]
    config_mgr.set_channels([c.to_dict() for c in channels])
    config_mgr.set_setting("EmailReportingEnabled", False)
    config_mgr.set_setting("SessionStartNotification", False)
    config_mgr.set_setting("ReportEmailMethod", "Disabled")

    log_q = queue.Queue()
    stop_ev = threading.Event()
    event_q = queue.Queue()

    worker = WatchingWorker(
        account=account,
        channels=channels,
        config_mgr=config_mgr,
        log_queue=log_q,
        stop_event=stop_ev,
        event_queue=event_q,
        stats_store=stats_store,
    )
    return worker


# ---------------------------------------------------------------------------
# 1. Sign-in Wait & Session Start Email Notification Tests
# ---------------------------------------------------------------------------

def test_signin_wait_delays_start_notification_until_login_successful(test_worker):
    """When an account is initially signed out, worker waits and does not send start notification until signed in."""
    test_worker.config_mgr.set_setting("SignInWaitSeconds", 0.05)

    # First attempt fails, second attempt succeeds
    login_attempts = [False, True]
    def fake_login(*args, **kwargs):
        return login_attempts.pop(0) if login_attempts else True

    mock_driver = MagicMock()
    test_worker.driver = mock_driver

    with patch("src.automation.watching_worker.perform_google_login", side_effect=fake_login) as mock_login, \
         patch("src.automation.watching_worker.is_logged_in", return_value=False):

        result = test_worker._ensure_signed_in_and_wait(wait_seconds=0.05)
        assert result is True
        assert mock_login.call_count == 2


def test_signin_wait_detects_login_during_countdown(test_worker):
    """If user manually signs in or login is detected during the wait window, wait ends immediately."""
    test_worker.config_mgr.set_setting("SignInWaitSeconds", 10.0)

    # perform_google_login initially fails
    with patch("src.automation.watching_worker.perform_google_login", return_value=False) as mock_login, \
         patch("src.automation.watching_worker.is_logged_in", return_value=True) as mock_is_logged:

        t0 = time.time()
        result = test_worker._ensure_signed_in_and_wait(wait_seconds=10.0)
        t1 = time.time()

        assert result is True
        # Returned quickly without waiting the full 10 seconds because is_logged_in returned True!
        assert (t1 - t0) < 3.0
        mock_login.assert_called_once()
        mock_is_logged.assert_called()


def test_signin_wait_aborts_cleanly_on_user_stop(test_worker):
    """If user cancels/stops during the sign-in wait window, worker exits cleanly."""
    def trigger_stop():
        time.sleep(0.05)
        test_worker.stop_event.set()

    stopper = threading.Thread(target=trigger_stop)
    stopper.start()

    with patch("src.automation.watching_worker.perform_google_login", return_value=False), \
         patch("src.automation.watching_worker.is_logged_in", return_value=False):

        result = test_worker._ensure_signed_in_and_wait(wait_seconds=5.0)
        assert result is False
    stopper.join()


def test_worker_run_waits_for_signin_before_spawning_start_notification(test_worker):
    """WatchingWorker.run() does not dispatch start notification thread if sign-in fails."""
    mock_driver = MagicMock()
    test_worker.config_mgr.set_setting("SignInWaitSeconds", 0.001)
    test_worker.config_mgr.set_setting("SignInMaxRetries", 1)

    with patch("src.automation.watching_worker.setup_driver", return_value=mock_driver), \
         patch("src.automation.watching_worker.perform_google_login", return_value=False), \
         patch("src.automation.watching_worker.close_driver"):

        test_worker._async_init_and_send_start_notification = MagicMock()
        test_worker.run()

        # Start notification must NOT have been called because sign-in never succeeded!
        assert test_worker._async_init_and_send_start_notification.called is False


def test_automation_worker_waits_for_signin_before_start_notification(tmp_path):
    """AutomationWorker (task queue) also waits for sign-in before dispatching start notification."""
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    config_mgr.set_setting("SignInWaitSeconds", 0.001)
    config_mgr.set_setting("SignInMaxRetries", 1)

    account = Account(email="q_user@gmail.com", password="password")
    worker = AutomationWorker(
        account=account,
        video_tasks=[],
        config_mgr=config_mgr,
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        worker_id=0,
    )

    mock_driver = MagicMock()
    with patch("src.automation.worker.setup_driver", return_value=mock_driver), \
         patch("src.automation.worker.perform_google_login", return_value=False), \
         patch("src.automation.worker.close_driver"):

        worker._async_init_and_send_start_notification = MagicMock()
        worker.run()

        assert worker._async_init_and_send_start_notification.called is False


# ---------------------------------------------------------------------------
# 2. Nap and Night Sleep Resumption Notification Tests
# ---------------------------------------------------------------------------

def test_nap_sleep_wake_up_sends_resumption_notification(test_worker):
    """When short sleep / nap mode completes, worker sends a resumption notification under the same thread."""
    test_worker.config_mgr.set_setting("ShortSleepHours", "0.00005 Hours")
    test_worker.config_mgr.set_setting("NightSleepEnabled", False)

    with patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True):

        session_keys = {"item1"}
        result = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)

        assert result is True
        assert test_worker.sleep_cycle_count == 1

        # Two reports sent:
        # 1. Entering sleep
        # 2. Waking up and resuming session
        assert mock_report.call_count == 2

        call_sleep = mock_report.call_args_list[0][1]
        assert call_sleep["status"] == "Going to Sleep"
        assert call_sleep["sleep_mode_type"] == "nap"
        assert call_sleep["sleep_cycle_number"] == 1

        call_resume = mock_report.call_args_list[1][1]
        assert call_resume["status"] == "Resuming Session"
        assert call_resume["sleep_mode_type"] == "nap_resume"
        assert call_resume["sleep_cycle_number"] == 1


def test_night_sleep_wake_up_sends_resumption_notification(test_worker):
    """When long sleep / night mode completes, worker sends a morning resumption notification."""
    test_worker.config_mgr.set_setting("NightSleepEnabled", True)

    sched_info = {
        "enabled": True,
        "inside_night_window": True,
        "is_sleep_time": True,
        "window_id": "2026-11-10_2200",
        "scheduled_sleep_end": datetime.now() - timedelta(seconds=1),
        "sleep_duration_hours": 8.0,
    }

    mock_driver = MagicMock()
    test_worker.driver = mock_driver

    with patch.object(test_worker.night_scheduler, "evaluate", return_value=sched_info), \
         patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.watching_worker.close_driver"):

        session_keys = {"v1", "v2"}
        result = test_worker._check_and_enter_night_sleep(session_watched_keys=session_keys)

        assert result is True
        assert test_worker.night_sleep_count == 1

        # Two reports sent:
        # 1. Long Sleep notification
        # 2. Morning wake-up resumption notification
        assert mock_report.call_count == 2

        call_sleep = mock_report.call_args_list[0][1]
        assert call_sleep["status"] == "Long Sleep (Night Mode)"
        assert call_sleep["sleep_mode_type"] == "night"

        call_resume = mock_report.call_args_list[1][1]
        assert call_resume["status"] == "Resuming Session"
        assert call_resume["sleep_mode_type"] == "night_resume"
        assert call_resume["sleep_cycle_number"] == 1


def test_resumption_email_report_formats():
    """Verify plain text and HTML rendering for nap and night mode resumption reports."""
    # 1. Nap Resume Report
    report_nap = SessionReport(
        account_email="resumetester@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 10, 8, 0, 0),
        end_time=datetime(2026, 11, 10, 10, 0, 0),
        duration_seconds=7200.0,
        total_session_watch_seconds=0.0,
        channel_reports=[],
        status="Resuming Session",
        sleep_mode_type="nap_resume",
        sleep_cycle_number=2,
    )

    plain_nap = report_nap.generate_plain_text()
    assert "NAP FINISHED • RESUMING SESSION (NAP CYCLE #2)" in plain_nap
    assert "Nap cycle has concluded" in plain_nap

    html_nap = report_nap.generate_html_body()
    assert "Nap Mode Finished • Resuming Session (Nap Cycle #2)" in html_nap
    assert "Nap Finished: Resuming Session (Cycle #2)" in html_nap

    # 2. Night Resume Report
    report_night = SessionReport(
        account_email="resumetester@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 10, 8, 0, 0),
        end_time=datetime(2026, 11, 10, 8, 5, 0),
        duration_seconds=300.0,
        total_session_watch_seconds=0.0,
        channel_reports=[],
        status="Resuming Session",
        sleep_mode_type="night_resume",
        sleep_cycle_number=1,
    )

    plain_night = report_night.generate_plain_text()
    assert "NIGHT MODE COMPLETED • MORNING RESUMPTION" in plain_night
    assert "Nighttime sleep window has concluded" in plain_night

    html_night = report_night.generate_html_body()
    assert "Morning Wake-Up — Long Sleep / Night Mode Completed" in html_night
    assert "Morning Wake-up: Daytime Browsing Resumed" in html_night
