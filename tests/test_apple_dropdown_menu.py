import os
import sys
import tkinter as tk
from unittest.mock import MagicMock

from src.models.video_task import VideoTask
from src.ui.dropdown_menu import AppleDropdownMenu
from src.ui.theme import get_theme_colors
from src.ui.views.queue_view import QueueView


def test_apple_dropdown_menu_structure(ctk_app):
    """Test AppleDropdownMenu creation, item registration, and theme tokens."""
    menu = AppleDropdownMenu(master=ctk_app, mode="Dark")
    assert not menu.is_open()

    cb_mock = MagicMock()
    menu.add_command(label="Published Date", command=cb_mock, active=True, badge="↓ Desc")
    menu.add_command(label="Watch Time", command=None, active=False)
    menu.add_separator()
    menu.add_command(label="Reset Sort Order", command=None)

    assert len(menu._items) == 4
    assert menu._items[0]["label"] == "Published Date"
    assert menu._items[0]["active"] is True
    assert menu._items[0]["badge"] == "↓ Desc"
    assert menu._items[2]["type"] == "separator"


def test_apple_dropdown_menu_popup_and_styling(ctk_app):
    """Verify AppleDropdownMenu popup window adheres to Apple card HIG styling."""
    menu = AppleDropdownMenu(master=ctk_app, mode="Dark")
    invoked = []

    def on_click():
        invoked.append(True)

    menu.add_command(label="✓ Published Date (↓ Desc)", command=on_click)
    menu.add_separator()
    menu.add_command(label="Reset Sort Order", command=None)

    menu.tk_popup(100, 150)
    ctk_app.update_idletasks()

    assert menu.is_open()
    top = menu._top
    assert top is not None

    # Verify borderless popup window (overrideredirect True)
    assert top.wm_overrideredirect() is True

    # Outer border frame: #323238
    outer = top.winfo_children()[0]
    assert outer.cget("bg") == "#323238"

    # Inner container card: #202024
    container = outer.winfo_children()[0]
    assert container.cget("bg") == "#202024"

    # Find rows
    rows = [w for w in container.winfo_children() if isinstance(w, tk.Frame)]
    cmd_row = rows[0]
    labels = [w for w in cmd_row.winfo_children() if isinstance(w, tk.Label)]
    chk_lbl = labels[0]
    title_lbl = labels[1]
    badge_lbl = labels[2]

    assert chk_lbl.cget("text") == "✓"
    assert title_lbl.cget("text") == "Published Date"
    assert badge_lbl.cget("text") == "↓ Desc"

    # Trigger item click via attached handler
    assert hasattr(cmd_row, "_click")
    cmd_row._click()
    ctk_app.update_idletasks()

    assert len(invoked) == 1
    assert not menu.is_open()


def test_queue_view_open_sort_menu_with_apple_dropdown(ctk_app):
    """Verify QueueView.open_sort_menu utilizes AppleDropdownMenu and toggles properly."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.find_for_task.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.on_queue_changed = MagicMock()
    app.current_state = "IDLE"
    app.queue_service = MagicMock()

    view = QueueView(app, app, shorts_only=False)
    view.video_tasks = [
        VideoTask(title="Task A", url="https://youtube.com/watch?v=A"),
        VideoTask(title="Task B", url="https://youtube.com/watch?v=B"),
    ]

    assert hasattr(view, "sort_btn")

    # 1. Open sort menu
    view.open_sort_menu()
    app.update_idletasks()

    assert hasattr(view, "_active_sort_menu")
    assert isinstance(view._active_sort_menu, AppleDropdownMenu)
    assert view._active_sort_menu.is_open()

    # 2. Toggle sort menu again -> closes
    view.open_sort_menu()
    app.update_idletasks()
    assert not view._active_sort_menu.is_open() if view._active_sort_menu else True

    # 3. Open and select criteria
    view.open_sort_menu()
    app.update_idletasks()
    assert view._active_sort_menu.is_open()

    view.apply_sort("watch_time")
    assert view.current_sort_key == "watch_time"

    view.destroy()
