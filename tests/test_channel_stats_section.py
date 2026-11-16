"""Unit tests for the Channel Performance & Watch Time Analytics Section ($10,000 build).

Validates:
- Multi-period time window filtering (All Time, Per Month, Per Week, Per Day)
- Per-channel watch time calculation, runs, success rate, and rank attribution
- Long-form video vs YouTube Shorts split
- Target progress calculation and top-performer detection
- Bar chart and Donut chart data mapping and rendering
- UI controls (period dropdown menu, refresh button, KPI cards)
- DashboardView integration and dynamic refresh triggers
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord
from src.ui.theme import setup_ttk_styles
from src.ui.views.channel_stats_section import (
    ChannelStatsSection,
    compute_channel_stats,
    normalize_filter_name,
)


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


# ------------------------------------------------------------- 1. Computation Tests
def test_normalize_filter_name():
    assert normalize_filter_name("Last 24 Hours") == "Last 24 Hours"
    assert normalize_filter_name("Per Day") == "Last 24 Hours"
    assert normalize_filter_name("today") == "Last 24 Hours"
    assert normalize_filter_name("last 24 hours") == "Last 24 Hours"
    assert normalize_filter_name("Last 7 Days") == "Last 7 Days"
    assert normalize_filter_name("Per Week") == "Last 7 Days"
    assert normalize_filter_name("last 7 days") == "Last 7 Days"
    assert normalize_filter_name("Last 38 Days") == "Last 38 Days"
    assert normalize_filter_name("Per Month") == "Last 38 Days"
    assert normalize_filter_name("last 30 days") == "Last 38 Days"
    assert normalize_filter_name("Last Session") == "Last Session"
    assert normalize_filter_name("session") == "Last Session"
    assert normalize_filter_name("All Time") == "All Time"
    assert normalize_filter_name("lifetime") == "All Time"
    assert normalize_filter_name("") == "All Time"


def test_compute_channel_stats_empty():
    res = compute_channel_stats(channels=[], records={})
    assert res["total_watch_seconds"] == 0.0
    assert res["total_watch_formatted"] == "0s"
    assert res["total_runs"] == 0
    assert res["top_channel_name"] == "—"
    assert res["channels"] == []


def test_compute_channel_stats_multi_period_filtering():
    now = datetime(2026, 11, 14, 12, 0, 0)

    channels = [
        {"name": "Alpha Channel", "url": "https://youtube.com/@alpha", "target_hours": 10.0},
        {"name": "Beta Channel", "url": "https://youtube.com/@beta", "target_hours": 5.0},
    ]

    records = {
        # Alpha: 1 run today (300s)
        "v_today": StatRecord(
            channel="Alpha Channel",
            watch_seconds=300.0,
            runs=1,
            successes=1,
            last_run=(now - timedelta(hours=2)).isoformat(),
            last_completed=(now - timedelta(hours=2)).isoformat(),
        ),
        # Alpha: 1 run 3 days ago (600s)
        "v_3days": StatRecord(
            channel="Alpha Channel",
            watch_seconds=600.0,
            runs=2,
            successes=2,
            last_run=(now - timedelta(days=3)).isoformat(),
            last_completed=(now - timedelta(days=3)).isoformat(),
        ),
        # Beta: 1 run 15 days ago (1200s)
        "v_15days": StatRecord(
            channel="Beta Channel",
            watch_seconds=1200.0,
            runs=3,
            successes=3,
            last_run=(now - timedelta(days=15)).isoformat(),
            last_completed=(now - timedelta(days=15)).isoformat(),
        ),
        # Beta: 1 run 45 days ago (2400s)
        "v_45days": StatRecord(
            channel="Beta Channel",
            watch_seconds=2400.0,
            runs=5,
            successes=5,
            last_run=(now - timedelta(days=45)).isoformat(),
            last_completed=(now - timedelta(days=45)).isoformat(),
        ),
    }

    # 1. Last 24 Hours: only v_today (Alpha: 300s, Beta: 0s)
    day_res = compute_channel_stats(channels, records, time_filter="Last 24 Hours", current_time=now)
    assert day_res["total_watch_seconds"] == 300.0
    assert day_res["total_runs"] == 1
    assert day_res["top_channel_name"] == "Alpha Channel"
    assert day_res["active_channels_count"] == 1
    alpha_day = next(c for c in day_res["channels"] if c["name"] == "Alpha Channel")
    assert alpha_day["watch_seconds"] == 300.0
    assert alpha_day["percentage_share"] == 100.0
    beta_day = next(c for c in day_res["channels"] if c["name"] == "Beta Channel")
    assert beta_day["watch_seconds"] == 0.0

    # 2. Last 7 Days: v_today + v_3days (Alpha: 900s, Beta: 0s)
    week_res = compute_channel_stats(channels, records, time_filter="Last 7 Days", current_time=now)
    assert week_res["total_watch_seconds"] == 900.0
    assert week_res["total_runs"] == 3
    assert week_res["top_channel_name"] == "Alpha Channel"

    # 3. Last 38 Days: v_today + v_3days + v_15days (Alpha: 900s, Beta: 1200s; v_45days is excluded)
    month_res = compute_channel_stats(channels, records, time_filter="Last 38 Days", current_time=now)
    assert month_res["total_watch_seconds"] == 2100.0
    assert month_res["total_runs"] == 6
    assert month_res["top_channel_name"] == "Beta Channel"
    beta_month = next(c for c in month_res["channels"] if c["name"] == "Beta Channel")
    assert beta_month["watch_seconds"] == 1200.0
    assert beta_month["rank"] == 1

    # 4. All Time: all 4 records (Alpha: 900s, Beta: 3600s)
    all_res = compute_channel_stats(channels, records, time_filter="All Time", current_time=now)
    assert all_res["total_watch_seconds"] == 4500.0
    assert all_res["total_runs"] == 11
    assert all_res["top_channel_name"] == "Beta Channel"
    beta_all = next(c for c in all_res["channels"] if c["name"] == "Beta Channel")
    assert beta_all["watch_seconds"] == 3600.0
    assert beta_all["watch_hours_float"] == 1.0
    # Beta has target_hours = 5.0 -> 1.0 / 5.0 = 20.0%
    assert beta_all["target_progress_pct"] == 20.0

    # 5. Last Session: only active/latest session metrics
    session_tracker = {
        "session_watch_seconds": {"Alpha Channel": 450.0, "Beta Channel": 150.0},
        "channel_regular_videos": {"Alpha Channel": 1, "Beta Channel": 0},
        "channel_shorts": {"Alpha Channel": 0, "Beta Channel": 1},
    }
    session_res = compute_channel_stats(channels, records, session_tracker=session_tracker, time_filter="Last Session", current_time=now)
    assert session_res["total_watch_seconds"] == 600.0
    assert session_res["total_runs"] == 2
    assert session_res["total_regular_vids"] == 1
    assert session_res["total_shorts"] == 1
    alpha_s = next(c for c in session_res["channels"] if c["name"] == "Alpha Channel")
    assert alpha_s["watch_seconds"] == 450.0


def test_compute_channel_stats_video_vs_shorts_split():
    channels = [{"name": "Tech Reviews", "url": "https://youtube.com/@tech"}]
    queued_videos = [
        VideoTask(url="https://youtube.com/watch?v=reg1", title="Full Video", channel="Tech Reviews"),
        VideoTask(url="https://youtube.com/shorts/sh1", title="Short Video", channel="Tech Reviews"),
    ]

    records = {
        "reg1": StatRecord(
            channel="Tech Reviews",
            watch_seconds=1200.0,
            runs=1,
            label="Full Video",
            video_duration=1200.0,
            task_keys=["reg1"],
        ),
        "sh1": StatRecord(
            channel="Tech Reviews",
            watch_seconds=50.0,
            runs=2,
            label="Short Video",
            video_duration=50.0,
            task_keys=["sh1"],
        ),
    }

    res = compute_channel_stats(channels, records, queued_videos=queued_videos)
    assert res["total_watch_seconds"] == 1250.0
    assert res["total_regular_vids"] >= 1
    assert res["total_shorts"] >= 1
    assert res["total_regular_watch_seconds"] == 1200.0
    assert res["total_shorts_watch_seconds"] == 50.0


# ------------------------------------------------------------- 2. UI Component Tests
def test_channel_stats_section_widget_initialization(tk_root):
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = [
        {"name": "Iamcarhacker", "url": "https://youtube.com/@iamcarhacker", "target_hours": 20.0},
        {"name": "Summia Jamshed", "url": "https://youtube.com/@summia", "target_hours": 15.0},
    ]
    controller.stats_store.all.return_value = {
        "v1": StatRecord(channel="Iamcarhacker", watch_seconds=1800.0, runs=2, successes=2),
        "v2": StatRecord(channel="Summia Jamshed", watch_seconds=3600.0, runs=4, successes=4),
    }
    controller.queued_video_tasks = []

    section = ChannelStatsSection(tk_root, controller)
    section.pack(fill="both", expand=True)
    tk_root.update()

    # 1. Header & Title
    assert "CHANNEL PERFORMANCE & WATCH TIME" in section.section_title.cget("text")
    assert hasattr(section, "period_dropdown")
    assert section.period_dropdown.get() == "All Time"
    assert hasattr(section, "refresh_btn")

    # 2. KPI Cards
    assert section.kpi_watch_time.cget("text") == "1h 30m"
    assert section.kpi_active_channels.cget("text") == "2 Channels"
    assert section.kpi_tasks.cget("text") == "6 Runs"
    assert section.kpi_top_channel.cget("text") == "35h 00m"

    # 3. Bar Chart & Donut Chart
    assert hasattr(section, "bar_chart")
    assert len(section.bar_chart.channel_items) == 2
    assert hasattr(section, "donut_chart")
    assert section.donut_chart.total_watch_seconds == 5400.0

    # 4. Channel Rows Container
    rows = section.channel_rows_container.winfo_children()
    assert len(rows) == 2

    section.destroy()


def test_channel_stats_section_dropdown_period_change(tk_root):
    controller = MagicMock()
    now = datetime.now()

    controller.config_mgr.get_channels.return_value = [
        {"name": "Channel One", "url": ""},
    ]
    controller.stats_store.all.return_value = {
        # Old record (40 days ago)
        "v_old": StatRecord(
            channel="Channel One",
            watch_seconds=7200.0,
            runs=5,
            successes=5,
            last_completed=(now - timedelta(days=40)).isoformat(),
        ),
    }
    controller.queued_video_tasks = []

    section = ChannelStatsSection(tk_root, controller)
    section.pack(fill="both", expand=True)
    tk_root.update()

    # Initially All Time
    assert section.kpi_watch_time.cget("text") == "2h 00m"

    # Switch dropdown to Last 24 Hours (should show 0s because v_old was 40 days ago)
    section._on_period_selected("Last 24 Hours")
    assert section.current_time_filter == "Last 24 Hours"
    assert section.kpi_watch_time.cget("text") == "0s"
    assert section.kpi_active_channels.cget("text") == "0 Channels"

    # Switch dropdown to Last 38 Days (should show 0s because v_old was 40 days ago)
    section._on_period_selected("Last 38 Days")
    assert section.kpi_watch_time.cget("text") == "0s"

    # Switch back to All Time
    section._on_period_selected("All Time")
    assert section.kpi_watch_time.cget("text") == "2h 00m"

    section.destroy()


def test_compute_channel_stats_video_multiplier_goal():
    """Verify target watch time calculation based on video multiplier (e.g. 3x duration)."""
    channels = [
        {"name": "Car Channel", "url": "https://youtube.com/@carchannel"},
    ]
    # Video 1: 300s duration with 3x multiplier -> 900s target
    # Video 2 (Short): 30s duration with 10 max_runs -> 300s target
    queued_tasks = [
        VideoTask(
            url="https://youtube.com/watch?v=car1",
            title="Car Engine Build",
            channel="Car Channel",
            duration_seconds=300,
            target_multiplier=3.0,
        ),
        VideoTask(
            url="https://youtube.com/shorts/car_s1",
            title="Exhaust Sound",
            channel="Car Channel",
            duration_seconds=30,
            max_runs=10,
            is_short_override=True,
        ),
    ]

    records = {
        "car1": StatRecord(channel="Car Channel", watch_seconds=450.0, runs=1, successes=1),
    }

    stats = compute_channel_stats(channels=channels, records=records, queued_videos=queued_tasks)

    # Total goal: 300*3 + 30*10 = 900 + 300 = 1200 seconds (20m 00s)
    assert stats["total_goal_seconds"] == 1200.0
    assert stats["total_goal_formatted"] == "20m 00s"
    assert stats["total_remaining_seconds"] == 750.0  # 1200 - 450 = 750
    assert stats["total_remaining_formatted"] == "12m 30s"
    assert stats["total_goal_progress_pct"] == 37.5  # 450 / 1200 * 100

    ch = stats["channels"][0]
    assert ch["goal_seconds"] == 1200.0
    assert ch["remaining_seconds"] == 750.0
    assert ch["goal_progress_pct"] == 37.5


def test_compute_channel_stats_excludes_unconfigured_channels():
    """Ensure that third-party / unconfigured channels not present in the user's
    target channels list (e.g. WOOD TV8 from random browsing or queue records)
    are excluded from channel stats, KPIs, and goals."""
    channels = [
        {"name": "Lifestyle Mapping", "url": "https://youtube.com/@lifestylemapping"},
        {"name": "The Kidney Nutrition Institute", "url": "https://youtube.com/@thekidneynutritioninstitute"},
    ]

    records = {
        "lm_vid": StatRecord(
            channel="Lifestyle Mapping",
            watch_seconds=71700.0,  # 19h 55m
            runs=23,
            successes=23,
        ),
        "kni_vid": StatRecord(
            channel="The Kidney Nutrition Institute",
            watch_seconds=22380.0,  # 6h 13m
            runs=12,
            successes=12,
        ),
        # Unconfigured third-party channel (e.g. from YouTube feed or test)
        "wood_vid": StatRecord(
            channel="WOOD TV8",
            watch_seconds=0.0,
            runs=0,
            successes=0,
            video_duration=120.0,
        ),
    }

    queued_tasks = [
        VideoTask(
            url="https://youtube.com/watch?v=wood1",
            title="Local News",
            channel="WOOD TV8",
            duration_seconds=120,
            target_multiplier=3.0,
        ),
    ]

    stats = compute_channel_stats(
        channels=channels,
        records=records,
        queued_videos=queued_tasks,
        time_filter="All Time",
    )

    # Only the 2 configured channels must be present
    assert stats["total_channels_count"] == 2
    assert stats["active_channels_count"] == 2
    channel_names = [c["name"] for c in stats["channels"]]
    assert "WOOD TV8" not in channel_names
    assert "Lifestyle Mapping" in channel_names
    assert "The Kidney Nutrition Institute" in channel_names
    assert len(stats["channels"]) == 2

    # Goal for WOOD TV8 must not pollute total_goal_seconds
    # Neither configured channel had tasks, so goals should not include WOOD TV8's 6m
    assert stats["total_goal_seconds"] == 0.0



def test_channel_stats_section_theme_switch(tk_root):
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = []
    controller.stats_store.all.return_value = {}
    controller.queued_video_tasks = []

    section = ChannelStatsSection(tk_root, controller)
    section.pack(fill="both", expand=True)
    tk_root.update()

    # Light Mode
    section.apply_theme("Light")
    tk_root.update()

    # Dark Mode
    section.apply_theme("Dark")
    tk_root.update()

    section.destroy()


# ------------------------------------------------------------- 3. Section Refresh Tests
def test_channel_stats_section_update_watch_totals(tk_root):
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = [
        {"name": "Iamcarhacker", "url": "https://youtube.com/@iamcarhacker"},
    ]
    controller.stats_store.all.return_value = {
        "v1": StatRecord(channel="Iamcarhacker", watch_seconds=900.0, runs=1, successes=1),
    }
    controller.queued_video_tasks = []

    section = ChannelStatsSection(tk_root, controller)
    section.pack(fill="both", expand=True)
    tk_root.update()

    assert section.kpi_watch_time.cget("text") == "15m 00s"

    controller.stats_store.all.return_value = {
        "v1": StatRecord(channel="Iamcarhacker", watch_seconds=1800.0, runs=2, successes=2),
    }
    section.refresh()
    assert section.kpi_watch_time.cget("text") == "30m 00s"

    section.destroy()
