"""Unit tests for the unified Start / Pause / Resume button on DashboardView.

Validates the complete state lifecycle:
- Idle: "▶ Start Browsing" (normal, hand2 cursor), Stop button disabled.
- Starting Browsing: Button text updates to "⏸ Pause" (normal, hand2 cursor), Stop button enabled.
- Pausing: Clicking "⏸ Pause" transitions to "▶ Resume" (normal, hand2 cursor, Primary style).
- Resuming: Clicking "▶ Resume" transitions to "⏸ Pause" (normal, hand2 cursor, Warning style).
- Stopping: Clicking "⏹ Stop" resets primary button to "▶ Start Browsing" and disables Stop.
"""
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


def test_unified_button_initial_idle_state(tk_root):
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # 2-button layout in btn_grid: column 0 and 1
    start_info = view.start_watching_btn.grid_info()
    stop_info = view.stop_btn.grid_info()
    assert start_info["column"] == 0
    assert stop_info["column"] == 1

    # In idle state:
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert view.start_watching_btn.cget("text") == "▶ Start Browsing"
    assert "ApplePrimary" in view.start_watching_btn.cget("style")

    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.stop_btn.cget("cursor") == "arrow"
    assert view.card_status.cget("text") == "Idle"


def test_unified_button_running_pause_resume_cycle(tk_root):
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # 1. Start browsing -> Button becomes "⏸ Pause"
    view.set_running_state(True)
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert view.start_watching_btn.cget("text") == "⏸ Pause"
    assert "AppleWarning" in view.start_watching_btn.cget("style")
    assert str(view.stop_btn.cget("state")) == "normal"
    assert view.card_status.cget("text") == "Browsing"

    # 2. Pause browsing -> Button becomes "▶ Resume"
    view.set_paused_state(True)
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert view.start_watching_btn.cget("text") == "▶ Resume"
    assert "ApplePrimary" in view.start_watching_btn.cget("style")
    assert str(view.stop_btn.cget("state")) == "normal"
    assert view.card_status.cget("text") == "Paused"

    # 3. Resume browsing -> Button becomes "⏸ Pause"
    view.set_paused_state(False)
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert view.start_watching_btn.cget("text") == "⏸ Pause"
    assert "AppleWarning" in view.start_watching_btn.cget("style")
    assert view.card_status.cget("text") == "Browsing"

    # 4. Stop browsing -> Resets to "▶ Start Browsing"
    view.set_running_state(False)
    assert str(view.start_watching_btn.cget("state")) == "normal"
    assert view.start_watching_btn.cget("cursor") == "hand2"
    assert view.start_watching_btn.cget("text") == "▶ Start Browsing"
    assert "ApplePrimary" in view.start_watching_btn.cget("style")
    assert str(view.stop_btn.cget("state")) == "disabled"
    assert view.card_status.cget("text") == "Idle"


def test_unified_button_click_dispatches_correct_controller_actions(tk_root):
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # Click when idle -> starts watching
    view.start_watching_btn.invoke()
    controller.start_watching.assert_called_once()
    controller.toggle_pause_watching.assert_not_called()

    # When running -> click toggles pause
    controller.is_running = True
    view.set_running_state(True)
    view.start_watching_btn.invoke()
    controller.toggle_pause_watching.assert_called_once()

    # When paused -> click resumes (toggles pause)
    view.set_paused_state(True)
    view.start_watching_btn.invoke()
    assert controller.toggle_pause_watching.call_count == 2
