"""Unit tests verifying that the Unlimited Browsing switch is locked (interaction disabled)
during active browsing without being grayed out, and re-enabled once browsing stops.
"""
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


def test_unlimited_switch_not_grayed_out_and_locked_during_browsing(tk_root):
    """Verify that during active browsing, the unlimited browsing switch retains its vibrant
    visual appearance (not grayed out) but is locked from user interaction.
    """
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # Initially idle: unlimited browsing is checked (1) and interactive
    assert view.browsing_unlimited_cb.get() == 1
    assert view.browsing_unlimited_cb.cget("state") == "normal"
    assert view.browsing_unlimited_cb.cget("interactive") is True
    assert view.browsing_unlimited_cb._canvas.cget("cursor") == "hand2"

    # Start browsing session
    view.set_running_state(True)
    controller.is_running = True

    # 1. Verify it is NOT grayed out: state is "normal", label is "normal", not disabled style
    assert view.browsing_unlimited_cb.cget("state") == "normal"
    assert view.browsing_unlimited_cb._state != "disabled"
    if view.browsing_unlimited_cb._label:
        assert view.browsing_unlimited_cb._label.cget("state") != "disabled"

    # 2. Verify interactive flag is False and cursor is arrow
    assert view.browsing_unlimited_cb.cget("interactive") is False
    assert view.browsing_unlimited_cb._canvas.cget("cursor") == "arrow"

    # 3. Attempting to click or invoke the switch while browsing is in progress must NOT toggle it
    initial_val = view.browsing_unlimited_cb.get()
    view.browsing_unlimited_cb._on_click()
    assert view.browsing_unlimited_cb.get() == initial_val == 1  # Unchanged! Still Unlimited

    view.browsing_unlimited_cb.invoke()
    assert view.browsing_unlimited_cb.get() == initial_val == 1  # Still Unchanged!

    # 4. Stop browsing session
    view.set_running_state(False)
    controller.is_running = False

    # 5. After session is completed / stopped: interactive is re-enabled with normal cursor
    assert view.browsing_unlimited_cb.cget("interactive") is True
    assert view.browsing_unlimited_cb.cget("state") == "normal"
    assert view.browsing_unlimited_cb._canvas.cget("cursor") == "hand2"

    # 6. Now user can freely turn off and on the switch
    view.browsing_unlimited_cb._on_click()
    assert view.browsing_unlimited_cb.get() == 0  # Turned OFF successfully!
    assert str(view.browsing_limit_entry.cget("state")) == "normal"

    view.browsing_unlimited_cb._on_click()
    assert view.browsing_unlimited_cb.get() == 1  # Turned ON successfully!
    assert str(view.browsing_limit_entry.cget("state")) == "disabled"
