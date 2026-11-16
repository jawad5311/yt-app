"""Unit tests for the dedicated StatsView (tab between Dashboard and Channels).

Validates:
- Sidebar navigation tab order: dashboard -> stats -> channels
- Video-level record filtering by channel and timeframe
- Top Channel selector dropdown (All Channels vs Single Channel)
- Top Timeframe dropdown (All Time, Per Month, Per Week, Per Day)
- Interactive drill-down into a single channel from the channel list
- Single-channel detailed view (target progress, format split, video table)
- Theme switching (Dark and Light)
- Integration with YTBotApp
"""
import inspect
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.models.watch_stats import StatRecord
from src.ui.app import YTBotApp
from src.ui.theme import setup_ttk_styles
from src.ui.views.stats_view import StatsView, get_channel_video_records


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


def test_sidebar_nav_order_stats_between_dashboard_and_channels():
    """Verify Stats tab is positioned below Dashboard and above Channels in sidebar nav_items."""
    src = inspect.getsource(YTBotApp._build_ui)
    idx_dash = src.find('"dashboard"')
    idx_stats = src.find('"stats"')
    idx_channels = src.find('"channels"')

    assert idx_dash != -1
    assert idx_stats != -1
    assert idx_channels != -1
    assert idx_dash < idx_stats < idx_channels, "Order must be dashboard -> stats -> channels"


def test_get_channel_video_records_filtering():
    now = datetime(2026, 11, 14, 12, 0, 0)
    records = {
        "v1": StatRecord(
            channel="Iamcarhacker",
            label="OBD2 Scanner Review",
            watch_seconds=1200.0,
            runs=2,
            successes=2,
            video_duration=600.0,
            last_completed=(now - timedelta(hours=3)).isoformat(),
        ),
        "v2_old": StatRecord(
            channel="Iamcarhacker",
            label="Key Fob Programming",
            watch_seconds=300.0,
            runs=1,
            successes=1,
            video_duration=300.0,
            last_completed=(now - timedelta(days=20)).isoformat(),
        ),
        "v3_other": StatRecord(
            channel="Other Channel",
            label="Cooking Tutorial",
            watch_seconds=900.0,
            runs=1,
            successes=1,
        ),
    }

    # 1. All Time for Iamcarhacker
    all_time = get_channel_video_records("Iamcarhacker", records, time_filter="All Time", current_time=now)
    assert len(all_time) == 2
    assert all_time[0]["video_key"] == "v1"
    assert all_time[0]["watch_seconds"] == 1200.0
    assert all_time[0]["is_short"] is False
    assert all_time[1]["video_key"] == "v2_old"

    # 2. Last 24 Hours for Iamcarhacker (only v1 within 24h)
    per_day = get_channel_video_records("Iamcarhacker", records, time_filter="Last 24 Hours", current_time=now)
    assert len(per_day) == 1
    assert per_day[0]["video_key"] == "v1"

    # 3. Last 7 Days for Iamcarhacker (only v1 within 7 days)
    per_week = get_channel_video_records("Iamcarhacker", records, time_filter="Last 7 Days", current_time=now)
    assert len(per_week) == 1
    assert per_week[0]["video_key"] == "v1"

    # 4. Last 38 Days for Iamcarhacker (both v1 and v2_old which is 20 days old)
    per_38 = get_channel_video_records("Iamcarhacker", records, time_filter="Last 38 Days", current_time=now)
    assert len(per_38) == 2

    # 5. Non-existent channel
    none_res = get_channel_video_records("Unknown Channel", records, current_time=now)
    assert len(none_res) == 0


def test_stats_view_all_channels_initialization(tk_root):
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

    view = StatsView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    # Title & Controls
    assert view.title_label.cget("text") == "Channel Stats & Analytics"
    assert view.channel_btn.cget("text") == "📺  All Channels ▾"
    assert view.period_btn.cget("text") == "⏱  All Time ▾"

    # KPI Cards
    assert view.card_slot1.cget("text") == "1h 30m"
    assert view.card_slot2.cget("text") == "2 Channels"
    assert view.card_slot3.cget("text") == "6 Runs"
    # Card 4 is TOTAL WATCH TIME GOAL (20.0h + 15.0h = 35h 00m)
    assert view.card_slot4.cget("text") == "35h 00m"
    assert "Remaining: 33h 30m" in view.card_slot4.footer_lbl.cget("text")

    view.destroy()


def test_stats_view_video_multiplier_goal_display(tk_root):
    """Verify that Card 4 displays the combined watch time goal based on video duration and multipliers."""
    from src.models.video_task import VideoTask

    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = [
        {"name": "Performance Garage", "url": "https://youtube.com/@garage"},
        {"name": "Track Day Racing", "url": "https://youtube.com/@trackday"},
    ]
    # Channel 1: Video with 300s duration and 3.0x multiplier -> 900s goal
    # Channel 2: Video with 600s duration and 2.0x multiplier -> 1200s goal
    # Combined Goal: 2100s -> 35m 00s
    controller.queued_video_tasks = [
        VideoTask(
            url="https://youtube.com/watch?v=g1",
            title="Dyno Tune Test",
            channel="Performance Garage",
            duration_seconds=300,
            target_multiplier=3.0,
        ),
        VideoTask(
            url="https://youtube.com/watch?v=t1",
            title="Hot Lap Nurburgring",
            channel="Track Day Racing",
            duration_seconds=600,
            target_multiplier=2.0,
        ),
    ]
    controller.stats_store.all.return_value = {
        "g1": StatRecord(channel="Performance Garage", watch_seconds=300.0, runs=1, successes=1),
        "t1": StatRecord(channel="Track Day Racing", watch_seconds=400.0, runs=1, successes=1),
    }

    view = StatsView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    # 1. Total Watch Time: 300s + 400s = 700s -> 11m 40s
    assert view.card_slot1.cget("text") == "11m 40s"

    # 2. Combined Goal: 900s + 1200s = 2100s -> 35m 00s
    assert view.card_slot4.cget("text") == "35m 00s"
    # Remaining: 2100 - 700 = 1400s -> 23m 20s (33.3% reached)
    assert "Remaining: 23m 20s" in view.card_slot4.footer_lbl.cget("text")
    assert "33.3% reached" in view.card_slot4.footer_lbl.cget("text")

    # 3. Drill down into Performance Garage
    view.select_channel("Performance Garage")
    tk_root.update()
    # Performance Garage goal: 900s -> 15m 00s, watched 300s -> 5m 00s, remaining 600s -> 10m 00s (33.3% reached)
    assert view.card_slot1.cget("text") == "5m 00s"
    assert view.card_slot4.cget("text") == "15m 00s"
    assert "Remaining: 10m 00s" in view.card_slot4.footer_lbl.cget("text")
    assert "33.3% reached" in view.card_slot4.footer_lbl.cget("text")

    view.destroy()


def test_stats_view_last_session_filtering(tk_root):
    """Verify that selecting Last Session filters watch metrics exclusively to the latest session."""
    controller = MagicMock()
    now = datetime.now()

    controller.config_mgr.get_channels.return_value = [
        {"name": "Channel Alpha", "url": ""},
        {"name": "Channel Beta", "url": ""},
    ]
    # Lifetime stats have 10 hours on Beta from days ago
    controller.stats_store.all.return_value = {
        "old_beta": StatRecord(
            channel="Channel Beta",
            watch_seconds=36000.0,
            runs=10,
            successes=10,
            last_completed=(now - timedelta(days=5)).isoformat(),
        ),
    }
    controller.queued_video_tasks = []

    # Current/last session produced 600s on Alpha and 0 on Beta
    controller.session_tracker = {
        "session_watch_seconds": {"channel alpha": 600.0},
        "channel_regular_videos": {"channel alpha": 2},
        "channel_shorts": {"channel alpha": 0},
        "total_session_watch_seconds": 600.0,
        "session_start_time": now - timedelta(minutes=30),
    }

    view = StatsView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    # All Time shows lifetime total (36000s + 600s = 36600s -> 10h 10m)
    assert view.card_slot1.cget("text") == "10h 10m"

    # Select Last Session
    view.select_period("Last Session")
    tk_root.update()

    # In Last Session, only 600s from Channel Alpha is counted!
    assert view.card_slot1.cget("text") == "10m 00s"
    assert view.card_slot2.cget("text") == "1 Channels"
    assert view.card_slot3.cget("text") == "2 Runs"

    view.destroy()


def test_stats_view_period_dropdown_menu_items(tk_root):
    """Verify the timeframe dropdown menu exposes all 5 required timeframe options."""
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = []
    controller.stats_store.all.return_value = {}
    controller.queued_video_tasks = []

    view = StatsView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    view._open_period_dropdown()
    menu = view._active_dropdown_menu
    assert menu is not None

    labels = [item.get("label", "") for item in menu._items if item.get("type") == "command"]
    badges = [item.get("badge", "") for item in menu._items if item.get("type") == "command"]

    assert any("All Time" in lbl for lbl in labels)
    assert any("Last 38 Days" in lbl for lbl in labels)
    assert any("Last 7 Days" in lbl for lbl in labels)
    assert any("Last 24 Hours" in lbl for lbl in labels)
    assert any("Last Session" in lbl for lbl in labels)

    assert "Lifetime" in badges
    assert "Past 38 Days" in badges
    assert "Past Week" in badges
    assert "Today" in badges
    assert "Latest Run" in badges

    menu.close()
    view.destroy()


def test_stats_view_filters_out_unconfigured_channels(tk_root):
    """Verify that unconfigured channels (e.g. WOOD TV8) present in stats_store records
    are not displayed in StatsView KPI, channel list, or dropdown when user has configured channels."""
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = [
        {"name": "Lifestyle Mapping", "url": "https://youtube.com/@lifestylemapping"},
        {"name": "The Kidney Nutrition Institute", "url": "https://youtube.com/@thekidneynutritioninstitute"},
    ]
    controller.stats_store.all.return_value = {
        "lm1": StatRecord(channel="Lifestyle Mapping", watch_seconds=3600.0, runs=1, successes=1),
        "kni1": StatRecord(channel="The Kidney Nutrition Institute", watch_seconds=1800.0, runs=1, successes=1),
        # Unconfigured third-party record
        "wood1": StatRecord(channel="WOOD TV8", watch_seconds=0.0, runs=0, successes=0, video_duration=120.0),
    }
    controller.queued_video_tasks = []
    controller.session_tracker = None

    view = StatsView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    # Active channels KPI must show 2 channels (2 of 2 channels generating time)
    assert view.card_slot2.cget("text") == "2 Channels"
    assert "2 of 2 channels generating time" in view.card_slot2.footer_lbl.cget("text")

    # Dropdown menu must only include the 2 configured channels
    view._open_channel_dropdown()
    menu = view._active_dropdown_menu
    assert menu is not None
    labels = [item.get("label", "") for item in menu._items if item.get("type") == "command"]
    assert any("Lifestyle Mapping" in lbl for lbl in labels)
    assert any("The Kidney Nutrition Institute" in lbl for lbl in labels)
    assert not any("WOOD TV8" in lbl for lbl in labels)
    menu.close()

    view.destroy()


