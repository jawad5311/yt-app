"""Tests for smooth scrolling and geometry bounds in ScrollableFrame and QueueView."""

from unittest.mock import MagicMock
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.components import ScrollableFrame
from src.ui.views.queue_view import QueueView


def test_scrollable_frame_expansion_and_scrolling(ctk_app):
    """Verify that ScrollableFrame allows content to expand naturally and scroll smoothly."""
    app = ctk_app
    app.geometry("800x500")
    app.update()

    sf = ScrollableFrame(app, width=800, height=500)
    sf.pack(fill="both", expand=True)
    app.update()

    # Populate with 30 items so content height exceeds canvas height
    for i in range(30):
        lbl = ttk.Label(sf, text=f"Scrollable item {i}", padding=15)
        lbl.pack(fill="x")

    sf.update_idletasks()
    app.update()

    canvas = sf._parent_canvas
    canvas_h = canvas.winfo_height()
    req_h = sf.winfo_reqheight()
    bbox = canvas.bbox("all")

    assert req_h > canvas_h, f"Expected req_h ({req_h}) > canvas_h ({canvas_h})"
    assert bbox is not None
    assert bbox[3] >= req_h, f"Canvas bbox height ({bbox[3]}) should encompass req_h ({req_h})"

    # Verify yview is not clamped to (0.0, 1.0)
    y_view = canvas.yview()
    assert y_view[0] <= 0.05
    assert y_view[1] < 1.0, f"Expected yview[1] < 1.0 when content overflows, got {y_view}"

    # Verify smooth mousewheel scrolling moves content down
    initial_top = canvas.yview()[0]
    dummy_event = MagicMock()
    dummy_event.widget = canvas
    dummy_event.delta = -120
    dummy_event.x_root = canvas.winfo_rootx() + 50
    dummy_event.y_root = canvas.winfo_rooty() + 50

    sf._smooth_mousewheel(dummy_event)
    for _ in range(15):
        app.update()

    new_top = canvas.yview()[0]
    assert new_top > initial_top, f"Expected canvas to scroll down (new_top={new_top} > initial_top={initial_top})"

    sf.destroy()


def test_queue_view_scrolling_videos_and_shorts(ctk_app):
    """Verify Video Queue and Shorts Queue allow mousewheel scrolling across queued cards."""
    app = ctk_app
    app.geometry("900x600")
    app.update()

    channel = Channel(name="ScrollTestChannel", url="https://youtube.com/@scrolltest", enabled=True)

    app.config_mgr = MagicMock()
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.find_for_task.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.on_queue_changed = MagicMock()
    app.current_state = "IDLE"
    app.queue_service = MagicMock()

    for is_shorts in (False, True):
        tasks = [
            VideoTask(
                url=f"https://www.youtube.com/watch?v=scrolldemo_{i}",
                title=f"Queue Item Title {i}",
                channel="ScrollTestChannel",
                duration_text="05:30",
                is_short_override=is_shorts,
            )
            for i in range(20)
        ]

        view = QueueView(app, app, shorts_only=is_shorts)
        view.video_tasks = tasks
        view._channels = MagicMock(return_value=[channel])
        view.pack(fill="both", expand=True)
        view.refresh_list()

        for _ in range(30):
            app.update()

        sf = view.scroll_frame
        canvas = sf._parent_canvas
        canvas_h = canvas.winfo_height()
        req_h = sf.winfo_reqheight()

        assert req_h > canvas_h, f"Expected {req_h} > {canvas_h} for {'shorts' if is_shorts else 'videos'}"
        y_view = canvas.yview()
        assert y_view[1] < 1.0, f"Expected yview[1] < 1.0, got {y_view}"

        # Test mousewheel scroll
        initial_y = canvas.yview()[0]
        ev = MagicMock()
        ev.widget = canvas
        ev.delta = -120
        ev.x_root = canvas.winfo_rootx() + 50
        ev.y_root = canvas.winfo_rooty() + 50

        sf._smooth_mousewheel(ev)
        for _ in range(15):
            app.update()

        scrolled_y = canvas.yview()[0]
        assert scrolled_y > initial_y, f"Expected scrolled_y ({scrolled_y}) > initial_y ({initial_y})"

        view.destroy()
        app.update()


def test_scrollable_frame_theme_canvas_background(ctk_app):
    """Verify ScrollableFrame updates its canvas background seamlessly between light and dark modes."""
    app = ctk_app
    sf = ScrollableFrame(app)
    sf.pack(fill="both", expand=True)

    sf.apply_theme("Light")
    assert sf._parent_canvas.cget("background") == "#f5f5f7"

    sf.apply_theme("Dark")
    assert sf._parent_canvas.cget("background") == "#161619"

    sf.destroy()
