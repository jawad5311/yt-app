from unittest.mock import MagicMock
import tkinter as tk
import pytest

from src.ui.theme import setup_ttk_styles
from src.ui.views.dashboard_view import DashboardView


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


def test_apple_ui_styles_registered(tk_root):
    """Verify that all Apple macOS HIG styles are registered on TTK Style."""
    style = setup_ttk_styles(tk_root, mode="Dark")

    # Frames
    assert style.lookup("AppleCard.TFrame", "background") != ""
    assert style.lookup("AppleInset.TFrame", "background") != ""
    assert style.lookup("ApplePill.TFrame", "background") != ""

    # Buttons
    assert style.lookup("ApplePrimary.TButton", "background") != ""
    assert style.lookup("AppleDanger.TButton", "background") != ""
    assert style.lookup("AppleWarning.TButton", "background") != ""

    # Progressbar
    assert style.lookup("Apple.Horizontal.TProgressbar", "background") != ""


def test_dashboard_view_apple_layout_and_widget_structure(tk_root):
    """Verify DashboardView initializes with Apple header, widgets, control center, and status pill."""
    controller = MagicMock()
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # 1. Header & Subtitle
    assert view.title_label.cget("text") == "Dashboard Overview"
    assert hasattr(view, "subtitle_label")
    assert "Control Center" in view.subtitle_label.cget("text")

    # 2. System Status Capsule / Pill
    assert hasattr(view, "system_pill_frame")
    assert hasattr(view, "system_pill_dot")
    assert hasattr(view, "system_pill_text")
    assert view.system_pill_text.cget("text") == "System Ready"

    # 3. Four Apple Widget Cards
    assert view.card_accounts.cget("text") == "0"
    assert view.card_videos.cget("text") == "0"
    assert view.card_status.cget("text") == "Idle"
    assert view.card_watch_time.cget("text") == "0s"

    # 4. Action Frame & Section Titles
    assert hasattr(view, "control_title")
    assert view.control_title.cget("text") == "CONTROL CENTER"
    assert hasattr(view, "activity_title")
    assert view.activity_title.cget("text") == "ACTIVITY & PROGRESS"

    # 5. Buttons with Apple styles
    assert "ApplePrimary" in view.start_watching_btn.cget("style")
    assert "AppleDanger" in view.stop_btn.cget("style")

    # 6. Progress bar
    assert "Apple" in view.progress_bar.cget("style")


def test_dashboard_apple_view_state_transitions(tk_root):
    """Verify dynamic Apple status pill and button transitions across Idle, Running, and Paused states."""
    controller = MagicMock()
    controller.config_mgr.get_int.return_value = 1

    view = DashboardView(tk_root, controller)

    # Initially Idle
    assert view.system_pill_text.cget("text") == "System Ready"
    assert view.card_status.cget("text") == "Idle"
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("text") == "▶ Start Browsing"
    assert str(view.stop_btn.cget("state")) == "disabled"

    # Transition to Running
    view.set_running_state(True)
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("text") == "⏸ Pause"
    assert str(view.stop_btn.cget("state")) == "normal"
    assert view.card_status.cget("text") == "Browsing"
    assert view.system_pill_text.cget("text") == "Browsing Active"

    # Transition to Paused
    view.set_paused_state(True)
    assert view.start_watching_btn.cget("text") == "▶ Resume"
    assert view.card_status.cget("text") == "Paused"
    assert view.system_pill_text.cget("text") == "Session Paused"

    # Resume from Paused
    view.set_paused_state(False)
    assert view.start_watching_btn.cget("text") == "⏸ Pause"
    assert view.card_status.cget("text") == "Browsing"
    assert view.system_pill_text.cget("text") == "Browsing Active"

    # Stop back to Idle
    view.set_running_state(False)
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("text") == "▶ Start Browsing"
    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.card_status.cget("text") == "Idle"
    assert view.system_pill_text.cget("text") == "System Ready"


def test_dashboard_apple_view_stats_update(tk_root):
    """Verify metric values and watch totals update cleanly."""
    controller = MagicMock()
    view = DashboardView(tk_root, controller)

    view.update_stats(accounts_count=5, videos_count=12, status="Idle")
    assert view.card_accounts.cget("text") == "5"
    assert view.card_videos.cget("text") == "12"
    assert view.card_status.cget("text") == "Idle"

    view.update_watch_totals("2h 15m")
    assert view.card_watch_time.cget("text") == "2h 15m"
