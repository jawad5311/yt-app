"""Unit tests for Dashboard control buttons clickable/unclickable states and Apple HIG styling."""
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from src.ui.theme import setup_ttk_styles
from src.ui.views.dashboard_view import DashboardView


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.withdraw()
    setup_ttk_styles(root, mode="Dark")
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def test_dashboard_buttons_idle_state_clickability(tk_root):
    """When browsing has not started, only Start Browsing is clickable, and Stop/Pause are unclickable."""
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # 1. Verify Start Browsing is enabled and clickable
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert "Start Browsing" in view.start_watching_btn.cget("text")

    # 2. Verify Stop is disabled and unclickable
    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.stop_btn.cget("cursor") == "arrow"

    # 3. Verify Pause is disabled and unclickable
    assert str(view.pause_btn.cget("state")) == "disabled"
    assert view.pause_btn.cget("cursor") == "arrow"

    # 4. Triggering Stop or Pause while idle must do nothing
    view.stop_btn.invoke()
    controller.stop_automation.assert_not_called()
    assert getattr(view.stop_btn, "_feedback_timer_id", None) is None

    view.pause_btn.invoke()
    controller.toggle_pause_watching.assert_not_called()


def test_dashboard_buttons_running_state_transitions(tk_root):
    """When browsing starts, Start Browsing becomes unclickable, while Stop and Pause become clickable."""
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

    # Now in running state
    assert str(view.start_watching_btn.cget("state")) == "disabled"
    assert view.start_watching_btn.cget("cursor") == "arrow"
    assert "Browsing started" in view.start_watching_btn.cget("text")

    assert str(view.stop_btn.cget("state")) == "normal"
    assert view.stop_btn.cget("cursor") == "hand2"
    assert "Stop" in view.stop_btn.cget("text")

    assert str(view.pause_btn.cget("state")) == "normal"
    assert view.pause_btn.cget("cursor") == "hand2"
    assert "Pause" in view.pause_btn.cget("text")

    # Clicking Start Browsing while running must not re-trigger
    controller.start_watching.reset_mock()
    view.start_watching_btn.invoke()
    controller.start_watching.assert_not_called()

    # Stop browsing returns to idle state
    view.set_running_state(False)
    controller.is_running = False

    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert "Start Browsing" in view.start_watching_btn.cget("text")

    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.stop_btn.cget("cursor") == "arrow"

    assert str(view.pause_btn.cget("state")) == "disabled"
    assert view.pause_btn.cget("cursor") == "arrow"


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
