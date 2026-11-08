"""Unit tests for Dashboard control buttons clickable/unclickable states and Apple HIG styling."""
import tkinter as tk
from unittest.mock import MagicMock

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


def test_dashboard_buttons_idle_state_clickability(tk_root):
    """When browsing has not started, unified Start Browsing is clickable, and Stop is unclickable."""
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # 1. Verify unified Start Browsing is enabled and clickable
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert "Start Browsing" in view.start_watching_btn.cget("text")

    # 2. Verify Stop is disabled and unclickable
    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.stop_btn.cget("cursor") == "arrow"

    # 3. Triggering Stop while idle must do nothing
    view.stop_btn.invoke()
    controller.stop_automation.assert_not_called()
    assert getattr(view.stop_btn, "_feedback_timer_id", None) is None


def test_dashboard_buttons_running_state_transitions(tk_root):
    """When browsing starts, unified button switches to Pause, and Stop becomes clickable."""
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # Simulate start watching callback execution
    def fake_start():
        controller.is_running = True
        view.set_running_state(True)

    controller.start_watching.side_effect = fake_start
    view.start_watching_btn.invoke()
    controller.start_watching.assert_called_once()

    # Now in running state: Unified button becomes Pause
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert "Pause" in view.start_watching_btn.cget("text")

    assert str(view.stop_btn.cget("state")) == "normal"
    assert view.stop_btn.cget("cursor") == "hand2"
    assert "Stop" in view.stop_btn.cget("text")

    # Clicking unified button while running invokes toggle_pause_watching
    view.start_watching_btn.invoke()
    controller.toggle_pause_watching.assert_called_once()

    # When paused, updates to Resume
    view.set_paused_state(True)
    assert view.start_watching_btn.cget("text") == "▶ Resume"

    # When resumed, updates to Pause
    view.set_paused_state(False)
    assert view.start_watching_btn.cget("text") == "⏸ Pause"

    # Stop browsing returns to idle state
    view.set_running_state(False)
    controller.is_running = False

    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert "Start Browsing" in view.start_watching_btn.cget("text")

    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.stop_btn.cget("cursor") == "arrow"


def test_apple_button_disabled_styles_lookup(tk_root):
    """Verify Apple button styles map disabled states to muted backgrounds and foregrounds in Dark and Light modes."""
    # Dark Mode
    style_dark = setup_ttk_styles(tk_root, mode="Dark")
    for btn_style in ("ApplePrimary.TButton", "AppleDanger.TButton", "AppleWarning.TButton"):
        normal_bg = style_dark.lookup(btn_style, "background", ())
        disabled_bg = style_dark.lookup(btn_style, "background", ("disabled",))
        disabled_fg = style_dark.lookup(btn_style, "foreground", ("disabled",))

        # Disabled background and foreground must be distinct and muted
        assert disabled_bg != normal_bg
        assert disabled_bg == "#222226"
        assert disabled_fg == "#55555c"

    # Light Mode
    style_light = setup_ttk_styles(tk_root, mode="Light")
    for btn_style in ("ApplePrimary.TButton", "AppleDanger.TButton", "AppleWarning.TButton"):
        normal_bg = style_light.lookup(btn_style, "background", ())
        disabled_bg = style_light.lookup(btn_style, "background", ("disabled",))
        disabled_fg = style_light.lookup(btn_style, "foreground", ("disabled",))

        assert disabled_bg != normal_bg
        assert disabled_bg == "#e5e5ea"
        assert disabled_fg == "#aeaeb2"


def test_dashboard_buttons_normal_size_not_stretched(tk_root):
    """Verify that Start Browsing and Stop buttons have normal dimensions and are not stretched out."""
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr = MagicMock()
    controller.config_mgr.get_int.return_value = 1
    controller.stats_store = MagicMock()
    controller.stats_store.lifetime_totals.return_value = MagicMock(watch_seconds=0)

    tk_root.geometry("1180x740")
    view = DashboardView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    start_w = view.start_watching_btn.winfo_width()
    stop_w = view.stop_btn.winfo_width()

    # Normal buttons are around 150-200px, not 400px-600px stretched across the card
    assert 100 <= start_w <= 220, f"Start button width {start_w} is not a normal button size"
    assert 100 <= stop_w <= 220, f"Stop button width {stop_w} is not a normal button size"

    view.destroy()
