from unittest.mock import MagicMock
import tkinter as tk
import pytest
from src.ui.theme import setup_ttk_styles
from src.ui.views.dashboard_view import DashboardView
from src.ui.app import YTBotApp
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

    assert hasattr(view, "card_slot2")
    assert view.card_slot2.cget("text") == "—"
    assert view.card_slot2.card_title == "Box 2"

    assert hasattr(view, "card_slot3")
    assert view.card_slot3.cget("text") == "—"
    assert view.card_slot3.card_title == "Box 3"

    assert hasattr(view, "card_slot4")
    assert view.card_slot4.cget("text") == "—"
    assert view.card_slot4.card_title == "Box 4"

    view.destroy()


def test_dashboard_update_session_watch_time(tk_root):
    """Verify updating session watch time reflects on the card."""
    controller = MagicMock()
    view = DashboardView(tk_root, controller)

    view.update_session_watch_time("1h 25m")
    assert view.card_session_watch_time.cget("text") == "1h 25m"

    view.destroy()


def test_dashboard_set_card_content(tk_root):
    """Verify set_card_content can update value, title, icon, and footer dynamically."""
    controller = MagicMock()
    view = DashboardView(tk_root, controller)

    view.set_card_content(
        view.card_slot2,
        value="14",
        title="Session Videos",
        footer="Videos watched",
        category_icon="🎬",
    )
    assert view.card_slot2.cget("text") == "14"
    assert "SESSION VIDEOS" in view.card_slot2.title_label.cget("text")
    assert view.card_slot2.footer_label.cget("text") == "Videos watched"

    view.destroy()


def test_app_get_current_session_watch_seconds():
    """Verify YTBotApp calculates cumulative watch seconds from active worker trackers."""
    app_mock = MagicMock(spec=YTBotApp)
    app_mock._current_session_watch_seconds = 0.0

    w1 = MagicMock()
    w1.channel_tracker.get_session_total_watch_seconds.return_value = 120.0

    w2 = MagicMock()
    w2.channel_tracker.get_session_total_watch_seconds.return_value = 35.0

    app_mock.active_workers = [w1, w2]

    sec = YTBotApp.get_current_session_watch_seconds(app_mock)
    assert sec == 155.0
