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

        # Dispatched night report
        mock_report.assert_called_once()
        kwargs = mock_report.call_args[1]
        assert kwargs["status"] == "Long Sleep (Night Mode)"
        assert kwargs["is_sleep_notification"] is True
        assert kwargs["sleep_mode_type"] == "night"
        assert kwargs["sleep_duration_hours"] == 8.0

        # Reset to fresh stats upon morning wake-up
        assert test_worker.target_attempts == 0
        assert test_worker.watched_count == 0
        assert len(session_keys) == 0
        assert test_worker.channel_tracker.session_watch_seconds["target channel"] == 0.0


def test_short_sleep_nap_mode_messaging_and_fresh_stats(test_worker):
    """When no target videos are found during the day, enters Short Sleep / Nap mode with fresh stats."""
    test_worker.config_mgr.set_setting("ShortSleepHours", "1 Hour")
    test_worker.config_mgr.set_setting("NightSleepEnabled", False)

    with patch.object(test_worker, "_send_session_report") as mock_report, \
         patch("src.automation.watching_worker.setup_driver", return_value=MagicMock()), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True):

        test_worker.config_mgr.set_setting("ShortSleepHours", "0.00005 Hours")
        session_keys = {"v1"}
        result = test_worker._enter_no_target_sleep_cycle(session_watched_keys=session_keys)

        assert result is True
        assert test_worker.sleep_cycle_count == 1
        mock_report.assert_called_once()
        kwargs = mock_report.call_args[1]
        assert kwargs["status"] == "Going to Sleep"
        assert kwargs["sleep_mode_type"] == "nap"
        assert kwargs["sleep_cycle_number"] == 1

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
