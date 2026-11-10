from unittest.mock import MagicMock
from datetime import datetime, timedelta
import tkinter as tk
import pytest
from src.ui.theme import setup_ttk_styles
from src.ui.views.dashboard_view import DashboardView
from src.ui.app import YTBotApp
from src.automation.session_tracker import SessionChannelTracker
from src.models.watch_stats import format_duration


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


def test_dashboard_row2_cards_initialization(tk_root):
    """Verify that the second row of four boxes is initialized on the dashboard."""
    controller = MagicMock()
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # Row 1 cards
    assert view.card_accounts.cget("text") == "0"
    assert view.card_videos.cget("text") == "0"
    assert view.card_status.cget("text") == "Idle"
    assert view.card_watch_time.cget("text") == "0s"

    # Row 2 cards
    assert hasattr(view, "card_session_watch_time")
    assert view.card_session_watch_time.cget("text") == "0s"
    assert view.card_session_watch_time.card_title == "Session Watch Time"

    assert hasattr(view, "card_session_duration")
    assert view.card_session_duration.cget("text") == "0s"
    assert view.card_session_duration.card_title == "Session Duration"

    assert hasattr(view, "card_target_videos")
    assert view.card_target_videos.cget("text") == "0 Vids · 0 Shorts"
    assert view.card_target_videos.card_title == "Targeted Watched"

    assert hasattr(view, "card_slot4")
    assert view.card_slot4.cget("text") == "—"
    assert view.card_slot4.card_title == "Box 4"

    view.destroy()


def test_dashboard_update_session_watch_time_and_duration(tk_root):
    """Verify updating session watch time and duration reflects on the cards."""
    controller = MagicMock()
    view = DashboardView(tk_root, controller)

    view.update_session_watch_time("1h 25m")
    assert view.card_session_watch_time.cget("text") == "1h 25m"

    view.update_session_duration("45m 12s")
    assert view.card_session_duration.cget("text") == "45m 12s"

    view.update_target_videos_watched(5, 3)
    assert view.card_target_videos.cget("text") == "5 Vids · 3 Shorts"

    view.destroy()


def test_dashboard_set_card_content(tk_root):
    """Verify set_card_content can update value, title, icon, and footer dynamically."""
    controller = MagicMock()
    view = DashboardView(tk_root, controller)

    view.set_card_content(
        view.card_slot4,
        value="Active",
        title="Custom Slot",
        footer="Custom footer",
        category_icon="✨",
    )
    assert view.card_slot4.cget("text") == "Active"
    assert "CUSTOM SLOT" in view.card_slot4.title_label.cget("text")
    assert view.card_slot4.footer_label.cget("text") == "Custom footer"

    view.destroy()


def test_session_channel_tracker_target_counts():
    """Verify SessionChannelTracker counts target regular videos and shorts separately."""
    tracker = SessionChannelTracker()
    assert tracker.get_target_counts() == (0, 0)

    # Record target regular video (is_random=False)
    tracker.record_video_watched(is_short=False, channel_name="TargetChan", is_random=False)
    assert tracker.get_target_counts() == (1, 0)

    # Record target short (is_random=False)
    tracker.record_video_watched(is_short=True, channel_name="TargetChan", is_random=False)
    assert tracker.get_target_counts() == (1, 1)

    # Record non-target / random video (is_random=True) - should NOT increment target counts
    tracker.record_video_watched(is_short=False, channel_name="RandomChan", is_random=True)
    tracker.record_video_watched(is_short=True, channel_name="RandomChan", is_random=True)
    assert tracker.get_target_counts() == (1, 1)


def test_app_get_current_session_metrics():
    """Verify YTBotApp calculates cumulative watch seconds, duration, and target counts from workers."""
    app_mock = MagicMock(spec=YTBotApp)
    app_mock._current_session_watch_seconds = 0.0
    app_mock._current_session_duration_seconds = 0.0
    app_mock._current_session_target_vids = 0
    app_mock._current_session_target_shorts = 0
    app_mock.is_running = True
    app_mock._session_start_time = datetime.now() - timedelta(seconds=150)

    w1 = MagicMock()
    w1.channel_tracker.get_session_total_watch_seconds.return_value = 120.0
    w1.channel_tracker.get_target_counts.return_value = (3, 2)

    w2 = MagicMock()
    w2.channel_tracker.get_session_total_watch_seconds.return_value = 35.0
    w2.channel_tracker.get_target_counts.return_value = (1, 4)

    app_mock.active_workers = [w1, w2]

    sec = YTBotApp.get_current_session_watch_seconds(app_mock)
    assert sec == 155.0

    el = YTBotApp.get_current_session_duration_seconds(app_mock)
    assert el >= 150.0

    tv, ts = YTBotApp.get_current_session_target_counts(app_mock)
    assert tv == 4
    assert ts == 6
