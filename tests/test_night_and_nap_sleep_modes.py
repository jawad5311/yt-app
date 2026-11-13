"""Unit tests for Short Sleep (Nap) and Long Sleep (Night) modes."""

from datetime import datetime, timedelta
import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import EmailReporter, SessionReport
from src.automation.night_sleep import NightSleepScheduler
from src.automation.session_tracker import SessionChannelTracker
from src.automation.watching_worker import WatchingWorker
from src.config.manager import (
    ConfigManager,
    parse_duration_range,
    parse_sleep_hours,
    parse_time_of_day,
    safe_float,
)
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.ui.views.settings_view import (
    NIGHT_DURATION_OPTIONS,
    NIGHT_START_TIME_OPTIONS,
    NIGHT_WINDOW_OPTIONS,
    SHORT_SLEEP_OPTIONS,
    SettingsView,
)


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


def test_config_manager_short_and_night_sleep_defaults_and_parsers(tmp_path):
    """Verify safe parsing of minutes, hours, time of day, and duration ranges."""
    assert parse_sleep_hours("30 Minutes") == 0.5
    assert parse_sleep_hours("45 Minutes") == 0.75
    assert parse_sleep_hours("1 Hour") == 1.0
    assert parse_sleep_hours("2 Hours") == 2.0
    assert parse_sleep_hours("Disabled") == 0.0
    assert parse_sleep_hours("None") == 0.0

    assert parse_time_of_day("22:00 (10:00 PM)") == (22, 0)
    assert parse_time_of_day("23:15 (11:15 PM)") == (23, 15)
    assert parse_time_of_day("00:00 (12:00 AM)") == (0, 0)
    assert parse_time_of_day("08:30 (8:30 AM)") == (8, 30)
    assert parse_time_of_day("10:00 PM") == (22, 0)

    assert parse_duration_range("8 Hours") == (8.0, 8.0)
    assert parse_duration_range("7 - 8 Hours") == (7.0, 8.0)
    assert parse_duration_range("6 - 8 Hours (Random)") == (6.0, 8.0)

    cm = ConfigManager(config_file=tmp_path / "settings.json")
    cm.set_setting("ShortSleepHours", "30 Minutes")
    assert cm.get_short_sleep_hours() == 0.5

    cm.set_setting("NightSleepEnabled", True)
    cm.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    cm.set_setting("NightSleepWindowHours", "12 Hours")
    cm.set_setting("NightSleepDurationHours", "7 - 8 Hours")

    n_settings = cm.get_night_sleep_settings()
    assert n_settings["enabled"] is True
    assert n_settings["start_hour"] == 22
    assert n_settings["start_minute"] == 0
    assert n_settings["window_hours"] == 12.0
    assert n_settings["duration_min"] == 7.0
    assert n_settings["duration_max"] == 8.0


def test_settings_view_short_and_long_sleep_ui(tmp_path):
    """Verify SettingsView properly exposes and saves Short Sleep (Nap) and Long Sleep (Night) options."""
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    config_mgr.set_setting("ShortSleepHours", "2 Hours")
    config_mgr.set_setting("NightSleepEnabled", False)
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
        assert hasattr(view, "short_sleep_menu")
        assert hasattr(view, "no_target_sleep_menu")  # Backward-compat alias
        assert hasattr(view, "night_sleep_switch")
        assert hasattr(view, "night_start_menu")
        assert hasattr(view, "night_window_menu")
        assert hasattr(view, "night_duration_menu")

        assert view.short_sleep_menu.get() == "2 Hours"
        assert view.no_target_sleep_menu.get() == "2 Hours"

        # Update controls
        view.short_sleep_menu.set("30 Minutes")
        view.night_sleep_switch.select()
        view.night_start_menu.set("23:00 (11:00 PM)")
        view.night_window_menu.set("10 Hours")
        view.night_duration_menu.set("7 - 8 Hours (Random)")
        view.save_settings()

        assert config_mgr.get_setting("ShortSleepHours") == "30 Minutes"
        assert config_mgr.get_short_sleep_hours() == 0.5
        assert config_mgr.get_bool("NightSleepEnabled") is True
        assert config_mgr.get_setting("NightSleepStartTime") == "23:00 (11:00 PM)"
        assert config_mgr.get_setting("NightSleepWindowHours") == "10 Hours"
        assert config_mgr.get_setting("NightSleepDurationHours") == "7 - 8 Hours (Random)"
    finally:
        root.destroy()


def test_night_sleep_scheduler_evaluation_and_randomization(tmp_path):
    """Verify NightSleepScheduler accurately identifies night window and schedules randomized sleep."""
    config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
    config_mgr.set_setting("NightSleepEnabled", True)
    config_mgr.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    config_mgr.set_setting("NightSleepWindowHours", "12 Hours")  # 22:00 to 10:00
    config_mgr.set_setting("NightSleepDurationHours", "8 Hours")

    scheduler = NightSleepScheduler(config_mgr=config_mgr)

    # 1. Daytime: 14:00 (Outside night window)
    res_day = scheduler.evaluate(now=datetime(2026, 11, 8, 14, 0))
    assert res_day["enabled"] is True
    assert res_day["inside_night_window"] is False
    assert res_day["is_sleep_time"] is False

    # 2. Inside window: 22:00 to 10:00.
    # Force deterministic start offset = 1.5 hours (starts at 23:30, ends at 07:30)
    w_start, w_end, inside, win_id = scheduler.get_current_or_upcoming_window(now=datetime(2026, 11, 8, 22, 15))
    assert inside is True
    scheduler.schedule_for_window(w_start, w_end, win_id, now=datetime(2026, 11, 8, 22, 0), forced_duration=8.0, forced_start_offset=1.5)

    assert scheduler.scheduled_sleep_start == datetime(2026, 11, 8, 23, 30)
    assert scheduler.scheduled_sleep_end == datetime(2026, 11, 9, 7, 30)

    # At 22:45: inside window, but before scheduled sleep start (browsing should continue)
    res_wait = scheduler.evaluate(now=datetime(2026, 11, 8, 22, 45))
    assert res_wait["inside_night_window"] is True
    assert res_wait["is_sleep_time"] is False

    # At 23:45: scheduled sleep has arrived!
    res_sleep = scheduler.evaluate(now=datetime(2026, 11, 8, 23, 45))
    assert res_sleep["inside_night_window"] is True
    assert res_sleep["is_sleep_time"] is True
    assert res_sleep["sleep_duration_hours"] == 8.0
    assert res_sleep["remaining_sleep_seconds"] == (datetime(2026, 11, 9, 7, 30) - datetime(2026, 11, 8, 23, 45)).total_seconds()

    # 3. Mark completed: once completed, does not sleep again for this night window
    scheduler.mark_completed(win_id)
    res_done = scheduler.evaluate(now=datetime(2026, 11, 9, 8, 0))
    assert res_done["is_sleep_time"] is False
    assert res_done.get("completed_for_this_window") is True


def test_watching_worker_executes_night_sleep_cycle(test_worker):
    """When night sleep is triggered, worker closes YouTube driver, dispatches night notification, and resets stats."""
    test_worker.config_mgr.set_setting("NightSleepEnabled", True)
    test_worker.config_mgr.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    test_worker.config_mgr.set_setting("NightSleepWindowHours", "12 Hours")
    test_worker.config_mgr.set_setting("NightSleepDurationHours", "8 Hours")

    mock_driver = MagicMock()
    test_worker.driver = mock_driver

    # Simulate accumulated daytime activity before night sleep
    test_worker.target_attempts = 4
    test_worker.watched_count = 4
    test_worker.channel_tracker.set_active_channel("Target Channel")
    test_worker.channel_tracker.add_watch_delta("Target Channel", 1200.0)

    sched_info = {
        "enabled": True,
        "inside_night_window": True,
        "is_sleep_time": True,
        "window_id": "2026-11-08_2200",
        "scheduled_sleep_end": datetime.now() - timedelta(seconds=1),  # Already reached for instantaneous test
        "sleep_duration_hours": 8.0,
    }

    with patch.object(test_worker.night_scheduler, "evaluate", return_value=sched_info), \
         patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.watching_worker.close_driver") as mock_close:

        session_keys = {"v1", "v2"}
        result = test_worker._check_and_enter_night_sleep(session_watched_keys=session_keys)

        assert result is True
        # Closed driver before night sleep
        mock_close.assert_called_with(mock_driver, log_queue=test_worker.log_queue)
        assert test_worker.night_sleep_count == 1
        assert "2026-11-08_2200" in test_worker.night_scheduler.completed_window_ids

        # Dispatched night report & morning resumption report
        assert mock_report.call_count == 2
        kwargs1 = mock_report.call_args_list[0][1]
        assert kwargs1["status"] == "Long Sleep (Night Mode)"
        assert kwargs1["is_sleep_notification"] is True
        assert kwargs1["sleep_mode_type"] == "night"
        assert kwargs1["sleep_duration_hours"] == 8.0

        kwargs2 = mock_report.call_args_list[1][1]
        assert kwargs2["status"] == "Resuming Session"
        assert kwargs2["sleep_mode_type"] == "night_resume"
        assert kwargs2["sleep_cycle_number"] == 1

        # Reset to fresh stats upon morning wake-up
        assert test_worker.target_attempts == 0
        assert test_worker.watched_count == 0
        assert len(session_keys) == 0
        assert test_worker.channel_tracker.session_watch_seconds["target channel"] == 0.0


def test_short_sleep_nap_mode_messaging_and_fresh_stats(test_worker):
    """When no target videos are found during the day, enters Short Sleep / Nap mode with fresh stats."""
    test_worker.config_mgr.set_setting("ShortSleepHours", "1 Hour")
    test_worker.config_mgr.set_setting("NightSleepEnabled", False)
    test_worker.config_mgr.set_setting("SystemName", "TestPC")

    with patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True):

        test_worker.config_mgr.set_setting("ShortSleepHours", "0.00005 Hours")
        session_keys = {"v1"}
        result = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)

        assert result is True
        assert test_worker.sleep_cycle_count == 1
        assert mock_report.call_count == 2
        kwargs1 = mock_report.call_args_list[0][1]
        assert kwargs1["status"] == "Going to Sleep"
        assert kwargs1["sleep_mode_type"] == "nap"
        assert kwargs1["sleep_cycle_number"] == 1

        kwargs2 = mock_report.call_args_list[1][1]
        assert kwargs2["status"] == "Resuming Session"
        assert kwargs2["sleep_mode_type"] == "nap_resume"
        assert kwargs2["sleep_cycle_number"] == 1

        # Resets stats on wake-up
        assert test_worker.watched_count == 0
        assert len(session_keys) == 0


def test_email_report_formats_for_nap_and_night_modes():
    """Verify that email reports distinctly present Long Sleep / Night Mode vs Short Sleep / Nap Mode."""
    # 1. Night Mode Report
    wake_time_night = datetime(2026, 11, 9, 7, 30, 0)
    report_night = SessionReport(
        account_email="tester@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 8, 14, 0, 0),
        end_time=datetime(2026, 11, 8, 23, 0, 0),
        duration_seconds=32400.0,
        total_session_watch_seconds=28000.0,
        channel_reports=[{"name": "Ch 1", "session_watch_seconds": 28000.0}],
        status="Long Sleep (Night Mode)",
        sleep_mode_type="night",
        sleep_duration_hours=8.0,
        wake_up_time=wake_time_night,
    )
    plain_night = report_night.generate_plain_text()
    html_night = report_night.generate_html_body()

    assert "LONG SLEEP / NIGHT NOTIFICATION" in plain_night
    assert "Human System Time Schedule" in plain_night
    assert "Going to Sleep — Long Sleep / Night Mode" in html_night
    assert "Going to Long Sleep (Night Mode)" in html_night

    # 2. Nap Mode Report
    wake_time_nap = datetime(2026, 11, 8, 16, 0, 0)
    report_nap = SessionReport(
        account_email="tester@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 8, 14, 0, 0),
        end_time=datetime(2026, 11, 8, 15, 30, 0),
        duration_seconds=5400.0,
        total_session_watch_seconds=4000.0,
        channel_reports=[{"name": "Ch 1", "session_watch_seconds": 4000.0}],
        status="Going to Nap",
        sleep_mode_type="nap",
        sleep_cycle_number=3,
        sleep_duration_hours=0.5,
        wake_up_time=wake_time_nap,
    )
    plain_nap = report_nap.generate_plain_text()
    html_nap = report_nap.generate_html_body()

    assert "SHORT SLEEP / NAP NOTIFICATION (NAP CYCLE #3)" in plain_nap
    assert "Sleep Cycle:          #3" in plain_nap
    assert "Going to Sleep — Short Sleep / Nap Mode (Nap Cycle #3)" in html_nap
    assert "Going to Nap (Cycle #3)" in html_nap


def test_night_sleep_scheduler_bypasses_window_when_session_started_at_night(tmp_path):
    """If session is started during the nighttime window, bypass long sleep for that initial window."""
    config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
    config_mgr.set_setting("NightSleepEnabled", True)
    config_mgr.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    config_mgr.set_setting("NightSleepWindowHours", "12 Hours")  # 22:00 to 10:00 next day
    config_mgr.set_setting("NightSleepDurationHours", "8 Hours")

    # Session started at 22:30 (inside night window)
    session_start = datetime(2026, 11, 8, 22, 30)
    scheduler = NightSleepScheduler(config_mgr=config_mgr, session_start_time=session_start)

    # 1. Evaluate during the same night at 23:00
    res_same_night = scheduler.evaluate(now=datetime(2026, 11, 8, 23, 0))
    assert res_same_night["inside_night_window"] is True
    assert res_same_night["is_sleep_time"] is False
    assert res_same_night.get("bypassed_for_this_window") is True
    assert res_same_night["window_id"] == "2026-11-08_2200"

    # 2. Evaluate during the same night after midnight at 03:00
    res_after_midnight = scheduler.evaluate(now=datetime(2026, 11, 9, 3, 0))
    assert res_after_midnight["inside_night_window"] is True
    assert res_after_midnight["is_sleep_time"] is False
    assert res_after_midnight.get("bypassed_for_this_window") is True
    assert res_after_midnight["window_id"] == "2026-11-08_2200"

    # 3. Evaluate next day during daytime at 14:00
    res_daytime = scheduler.evaluate(now=datetime(2026, 11, 9, 14, 0))
    assert res_daytime["inside_night_window"] is False
    assert res_daytime["is_sleep_time"] is False

    # 4. Evaluate when next night window arrives at 22:00 (2026-11-09)
    # The next window must NOT be bypassed because session was started on the previous day!
    w_start, w_end, inside, win_id = scheduler.get_current_or_upcoming_window(now=datetime(2026, 11, 9, 22, 0))
    assert inside is True
    assert win_id == "2026-11-09_2200"
    assert scheduler.is_window_bypassed(win_id, w_start=w_start, w_end=w_end) is False

    # Scheduler schedules sleep for this new cycle
    scheduler.schedule_for_window(w_start, w_end, win_id, now=datetime(2026, 11, 9, 22, 0), forced_duration=8.0, forced_start_offset=0.0)
    res_next_night = scheduler.evaluate(now=datetime(2026, 11, 9, 22, 30))
    assert res_next_night["inside_night_window"] is True
    assert res_next_night["is_sleep_time"] is True
    assert res_next_night["window_id"] == "2026-11-09_2200"


def test_night_sleep_scheduler_does_not_bypass_when_started_in_daytime(tmp_path):
    """If session is started during daytime, night sleep is NOT bypassed when night arrives."""
    config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
    config_mgr.set_setting("NightSleepEnabled", True)
    config_mgr.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    config_mgr.set_setting("NightSleepWindowHours", "12 Hours")
    config_mgr.set_setting("NightSleepDurationHours", "8 Hours")

    # Started at 14:00 (daytime)
    session_start = datetime(2026, 11, 8, 14, 0)
    scheduler = NightSleepScheduler(config_mgr=config_mgr, session_start_time=session_start)

    w_start, w_end, inside, win_id = scheduler.get_current_or_upcoming_window(now=datetime(2026, 11, 8, 22, 0))
    assert inside is True
    assert scheduler.is_window_bypassed(win_id, w_start=w_start, w_end=w_end) is False

    scheduler.schedule_for_window(w_start, w_end, win_id, now=datetime(2026, 11, 8, 22, 0), forced_duration=8.0, forced_start_offset=0.0)
    res = scheduler.evaluate(now=datetime(2026, 11, 8, 22, 10))
    assert res["inside_night_window"] is True
    assert res["is_sleep_time"] is True
    assert res.get("bypassed_for_this_window") is not True


def test_watching_worker_nighttime_start_bypasses_long_sleep_and_allows_naps(test_worker):
    """Worker started during nighttime window does not enter long sleep, and takes normal naps instead."""
    test_worker.config_mgr.set_setting("NightSleepEnabled", True)
    test_worker.config_mgr.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    test_worker.config_mgr.set_setting("NightSleepWindowHours", "12 Hours")
    test_worker.config_mgr.set_setting("NightSleepDurationHours", "8 Hours")
    test_worker.config_mgr.set_setting("ShortSleepHours", "0.00005 Hours")

    # Session started at 22:30 (during night window)
    start_dt = datetime(2026, 11, 8, 22, 30)
    test_worker.session_start_time = start_dt
    test_worker.night_scheduler.set_session_start_time(start_dt)

    mock_driver = MagicMock()
    test_worker.driver = mock_driver

    # 1. Main loop check: _check_and_enter_night_sleep must return False and NOT enter long sleep
    with patch("src.automation.night_sleep.datetime") as mock_dt, \
         patch.object(test_worker, "_execute_night_sleep_cycle") as mock_night_exec:
        mock_dt.now.return_value = datetime(2026, 11, 8, 22, 35)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        res = test_worker._check_and_enter_night_sleep(session_watched_keys=set())
        assert res is False
        mock_night_exec.assert_not_called()
        assert test_worker.night_sleep_count == 0

    # Verify bypass notice was emitted to the log queue
    logged_messages = []
    while not test_worker.log_queue.empty():
        logged_messages.append(test_worker.log_queue.get_nowait())
    assert any("Session was started during nighttime" in msg for msg in logged_messages)
    assert any("Skipping long sleep for this initial cycle" in msg for msg in logged_messages)

    # 2. No target content found: _enter_no_target_sleep_cycle enters Short Sleep / Nap mode, NOT long sleep
    with patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.night_sleep.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 11, 8, 23, 0)
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        session_keys = {"v1"}
        result = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)
        assert result is True
        assert test_worker.sleep_cycle_count == 1
        assert test_worker.night_sleep_count == 0

        # Reports sent were nap reports, not night reports
        assert mock_report.call_count == 2
        kwargs_nap1 = mock_report.call_args_list[0][1]
        assert kwargs_nap1["status"] == "Going to Sleep"
        assert kwargs_nap1["sleep_mode_type"] == "nap"

        kwargs_nap2 = mock_report.call_args_list[1][1]
        assert kwargs_nap2["status"] == "Resuming Session"
        assert kwargs_nap2["sleep_mode_type"] == "nap_resume"


def test_watching_worker_enters_long_sleep_when_next_cycle_arrives(test_worker):
    """Worker that bypassed initial nighttime window enters long sleep when the next long sleep cycle arrives."""
    test_worker.config_mgr.set_setting("NightSleepEnabled", True)
    test_worker.config_mgr.set_setting("NightSleepStartTime", "22:00 (10:00 PM)")
    test_worker.config_mgr.set_setting("NightSleepWindowHours", "12 Hours")
    test_worker.config_mgr.set_setting("NightSleepDurationHours", "8 Hours")

    # Session started at 22:30 on Nov 8
    start_dt = datetime(2026, 11, 8, 22, 30)
    test_worker.session_start_time = start_dt
    test_worker.night_scheduler.set_session_start_time(start_dt)

    mock_driver = MagicMock()
    test_worker.driver = mock_driver

    # On Nov 9 at 22:00, next long sleep cycle window arrives
    next_night_sched = {
        "enabled": True,
        "inside_night_window": True,
        "is_sleep_time": True,
        "window_id": "2026-11-09_2200",
        "scheduled_sleep_end": datetime.now() - timedelta(seconds=1),
        "sleep_duration_hours": 8.0,
    }

    with patch.object(test_worker.night_scheduler, "evaluate", return_value=next_night_sched), \
         patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.watching_worker.close_driver") as mock_close:

        session_keys = {"v1", "v2"}
        result = test_worker._check_and_enter_night_sleep(session_watched_keys=session_keys)

        assert result is True
        assert test_worker.night_sleep_count == 1
        assert "2026-11-09_2200" in test_worker.night_scheduler.completed_window_ids
        mock_close.assert_called_with(mock_driver, log_queue=test_worker.log_queue)

        # Dispatched night report
        assert mock_report.call_count == 2
        kwargs_night = mock_report.call_args_list[0][1]
        assert kwargs_night["status"] == "Long Sleep (Night Mode)"
        assert kwargs_night["sleep_mode_type"] == "night"

