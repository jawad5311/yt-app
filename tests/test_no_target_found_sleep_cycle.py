"""Unit tests for the autonomous sleep and repeat cycle when no targeted channel content is found."""

import queue
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import EmailReporter, SessionReport
from src.automation.session_tracker import SessionChannelTracker
from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager, safe_float
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.ui.views.settings_view import NO_TARGET_SLEEP_OPTIONS, SettingsView


@pytest.fixture
def test_worker(tmp_path):
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    stats_file = tmp_path / "stats.json"
    stats_store = StatsStore(stats_file=stats_file)

    account = Account(email="sleeptest@gmail.com", password="password123")
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


def test_config_manager_sleep_hours_parsing():
    """Verify safe_float and ConfigManager properly parse various sleep hour formats."""
    assert safe_float("2 Hours") == 2.0
    assert safe_float("1 Hour") == 1.0
    assert safe_float("12 Hours") == 12.0
    assert safe_float("Disabled") == 0.0
    assert safe_float("None") == 0.0
    assert safe_float("0") == 0.0
    assert safe_float(4) == 4.0

    cm = ConfigManager()
    cm.set_setting("NoTargetFoundSleepHours", "4 Hours")
    assert cm.get_no_target_sleep_hours() == 4.0

    cm.set_setting("NoTargetFoundSleepHours", "Disabled")
    assert cm.get_no_target_sleep_hours() == 0.0


def test_settings_view_sleep_duration_options(tmp_path):
    """Verify SettingsView properly exposes and saves NoTargetFoundSleepHours option."""
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    config_mgr.set_setting("NoTargetFoundSleepHours", "4 Hours")
    config_mgr.set_setting("SystemName", "TestPC")

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = config_mgr

    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tkinter display not available")

    try:
        view = SettingsView(root, app_ctrl)
        assert hasattr(view, "no_target_sleep_menu")
        assert view.no_target_sleep_menu.get() == "4 Hours"

        # Change selection and save
        view.no_target_sleep_menu.set("8 Hours")
        view.save_settings()

        saved_val = config_mgr.get_setting("NoTargetFoundSleepHours")
        assert saved_val == "8 Hours"
        assert config_mgr.get_no_target_sleep_hours() == 8.0
    finally:
        root.destroy()


def test_enter_no_target_sleep_cycle_disabled_stops_session(test_worker):
    """When NoTargetFoundSleepHours is Disabled, the worker stops immediately and sets stop_event."""
    test_worker.config_mgr.set_setting("NoTargetFoundSleepHours", "Disabled")

    session_keys = set()
    result = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)

    assert result is False
    assert test_worker.stop_event.is_set()

    logs = []
    while not test_worker.log_queue.empty():
        logs.append(test_worker.log_queue.get_nowait())
    log_text = "\n".join(logs)
    assert "Stopping watching session" in log_text


def test_enter_no_target_sleep_cycle_closes_driver_and_sends_report(test_worker):
    """Worker closes YouTube driver and dispatches sleep notification report under session thread before sleeping."""
    test_worker.config_mgr.set_setting("NoTargetFoundSleepHours", "2 Hours")
    mock_driver = MagicMock()
    test_worker.driver = mock_driver

    with patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.close_driver") as mock_close:

        test_worker.stop_event.set()

        session_keys = {"v1", "v2"}
        result = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)

        assert result is False
        mock_close.assert_called_with(mock_driver, log_queue=test_worker.log_queue)
        assert test_worker.driver is None
        assert test_worker.sleep_cycle_count == 1
        mock_report.assert_called_once()
        kwargs = mock_report.call_args[1]
        assert kwargs["status"] == "Going to Sleep"
        assert kwargs["is_sleep_notification"] is True
        assert kwargs["sleep_cycle_number"] == 1
        assert kwargs["sleep_duration_hours"] == 2.0
        assert test_worker.cycle_report_dispatched is True


def test_enter_no_target_sleep_cycle_increments_cycle_count(test_worker):
    """Every sleep notification increments the sleep cycle counter by one."""
    test_worker.config_mgr.set_setting("NoTargetFoundSleepHours", "0.00005 Hours")

    with patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True):

        # First sleep cycle
        res1 = test_worker._enter_no_target_sleep_cycle(session_watched_keys=set())
        assert res1 is True
        assert test_worker.sleep_cycle_count == 1
        assert mock_report.call_args[1]["sleep_cycle_number"] == 1

        # Second sleep cycle
        res2 = test_worker._enter_no_target_sleep_cycle(session_watched_keys=set())
        assert res2 is True
        assert test_worker.sleep_cycle_count == 2
        assert mock_report.call_args[1]["sleep_cycle_number"] == 2

        # Third sleep cycle
        res3 = test_worker._enter_no_target_sleep_cycle(session_watched_keys=set())
        assert res3 is True
        assert test_worker.sleep_cycle_count == 3
        assert mock_report.call_args[1]["sleep_cycle_number"] == 3


def test_sleep_notification_maintains_exact_same_subject_for_threading(test_worker):
    """Verify that every sleep notification email uses the exact same subject as the initial start email."""
    initial_subject = test_worker.session_thread_subject
    assert initial_subject is not None
    assert "Entertainment Hub Session" in initial_subject

    # Create sleep notification report
    report_data = test_worker.channel_tracker.get_session_report_data()
    wake_time = datetime.now() + timedelta(hours=2)
    report = SessionReport(
        account_email=test_worker.account.email,
        session_mode="Continuous Browsing",
        start_time=report_data["start_time"],
        end_time=report_data["end_time"],
        duration_seconds=report_data["duration_seconds"],
        total_session_watch_seconds=report_data["total_session_watch_seconds"],
        channel_reports=report_data["channels"],
        status="Going to Sleep",
        custom_subject=test_worker.session_thread_subject,
        sleep_cycle_number=1,
        sleep_duration_hours=2.0,
        wake_up_time=wake_time,
    )

    # Subject must match initial_subject exactly so Gmail threads all emails together
    assert report.generate_subject() == initial_subject


def test_sleep_notification_plain_text_and_html_contents():
    """Verify that the sleep notification email body presents sleep cycle stats, wake-up time, and activity summary."""
    wake_time = datetime(2026, 11, 8, 8, 30, 0)
    report = SessionReport(
        account_email="test@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 8, 6, 30, 0),
        end_time=datetime(2026, 11, 8, 7, 0, 0),
        duration_seconds=1800.0,
        total_session_watch_seconds=1500.0,
        channel_reports=[{
            "name": "My Channel",
            "session_watch_seconds": 1500.0,
            "final_watch_seconds": 5000.0,
            "target_goal_text": "Goal: 10 hrs",
            "total_videos_count": 2,
            "regular_videos_count": 1,
            "shorts_count": 1,
        }],
        status="Going to Sleep",
        total_videos_watched=2,
        regular_videos_count=1,
        shorts_count=1,
        sleep_cycle_number=2,
        sleep_duration_hours=2.0,
        wake_up_time=wake_time,
    )

    plain = report.generate_plain_text()
    assert ("SHORT SLEEP / NAP NOTIFICATION" in plain or "SLEEP NOTIFICATION" in plain) and ("#2" in plain)
    assert "Sleep Cycle:          #2" in plain
    assert "Sleep Duration:       2.0 hour(s)" in plain
    assert "2026-11-08 08:30:00" in plain
    assert "Videos Watched:       2 (1 regular, 1 Shorts)" in plain

    html_content = report.generate_html_body()
    assert "Going to Sleep" in html_content and "Cycle #2" in html_content
    assert "Sleep Cycle #2" in html_content
    assert "2.0 hour(s)" in html_content
    assert "My Channel" in html_content


def test_waking_up_resets_fresh_stats_and_clears_keys(test_worker):
    """Upon wake-up, the channel tracker and session metrics start from fresh stats while preserving thread subject."""
    test_worker.config_mgr.set_setting("NoTargetFoundSleepHours", "0.00005 Hours")
    initial_subject = test_worker.session_thread_subject

    # Simulate accumulated stats in the pass before sleeping
    test_worker.watched_count = 5
    test_worker.target_attempts = 5
    test_worker.channel_tracker.set_active_channel("Target Channel")
    test_worker.channel_tracker.add_watch_delta("Target Channel", 600.0)

    session_keys = {"video_key_1", "video_key_2"}

    with patch.object(test_worker, "_send_session_report"), \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True):

        res = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)

        assert res is True
        # Fresh stats verification
        assert test_worker.watched_count == 0
        assert test_worker.target_attempts == 0
        assert len(session_keys) == 0  # Cleared for fresh discovery
        assert test_worker.channel_tracker.session_watch_seconds["target channel"] == 0.0
        assert test_worker.channel_tracker.minute_counters.get("target channel", 0) == 0
        # Thread subject preserved for same thread
        assert test_worker.session_thread_subject == initial_subject


def test_enter_no_target_sleep_cycle_interruptible_by_stop_event(test_worker):
    """Clicking Stop while sleeping breaks the sleep cycle within seconds."""
    test_worker.config_mgr.set_setting("NoTargetFoundSleepHours", "6 Hours")
    test_worker.driver = None

    with patch.object(test_worker, "_send_session_report"):
        result_holder = {}

        def run_sleep():
            res = test_worker._enter_no_target_sleep_cycle(session_watched_keys=set())
            result_holder["result"] = res

        t = threading.Thread(target=run_sleep, daemon=True)
        t.start()

        # Wait until worker enters sleep mode
        start_wait = time.time()
        while not test_worker.is_sleeping and time.time() - start_wait < 3.0:
            time.sleep(0.05)

        assert test_worker.is_sleeping is True

        # Signal stop (equivalent to user clicking Stop button on dashboard)
        stop_start = time.time()
        test_worker.stop_event.set()
        t.join(timeout=3.0)

        assert not t.is_alive()
        assert time.time() - stop_start < 2.0  # Wakes up almost immediately
        assert result_holder.get("result") is False
        assert test_worker.is_sleeping is False


def test_full_watching_loop_triggers_sleep_cycle_when_no_targets(test_worker):
    """In WatchingWorker.run(), when Home, Search, and Shorts find no targets, enters sleep cycle."""
    test_worker.config_mgr.set_setting("NoTargetFoundSleepHours", "2 Hours")
    mock_driver = MagicMock()
    mock_driver.current_url = "https://www.youtube.com/"
    mock_driver.find_elements.return_value = [MagicMock()]
    mock_driver.execute_script.return_value = []

    with patch("src.automation.watching_worker.setup_driver", return_value=mock_driver), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch.object(test_worker, "_scan_home_feed_for_target", return_value=None), \
         patch.object(test_worker, "search_and_play_target_video", return_value=None), \
         patch.object(test_worker, "_check_and_play_target_shorts_before_sleep", return_value=0), \
         patch.object(test_worker, "_enter_no_target_sleep_cycle", return_value=False) as mock_sleep_cycle:

        test_worker.open_videos = True
        test_worker.run()

        mock_sleep_cycle.assert_called_once()

