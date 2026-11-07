"""Tests for arrow up/down button highlighting, vertical stacking, and boundary disablement.

Verifies:
1. Channels tab displays top arrow on top and bottom arrow below it (column 0, row 0 and row 1).
2. Boundary channels disable non-clickable arrows (first channel cannot move up; last cannot move down).
3. Video Queue tab and Shorts Queue tab highlight only clickable arrows, disabling the top arrow for the first item and bottom arrow for the last item.
"""

from unittest.mock import MagicMock
import customtkinter as ctk
import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView
from src.ui.views.queue_view import QueueView


def test_channels_tab_arrows_stacked_and_boundary_highlighting(ctk_app):
    """Verify on Channels tab: arrows are vertically stacked, and boundary items disable non-clickable arrows."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    channels = [
        Channel(name=f"Channel {i}", url=f"https://youtube.com/@channel_{i}", enabled=True)
        for i in range(1, 4)
    ]
    view.channels = channels
    view.refresh_list()
    app.update()

    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 3

    # Inspect each card
    for idx, card in enumerate(cards):
        action_frame = card.grid_slaves(row=0, column=2)[0]
        btn_up = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "▲"), None)
        btn_down = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "▼"), None)

        assert btn_up is not None, f"Card {idx} missing btn_up"
        assert btn_down is not None, f"Card {idx} missing btn_down"

        # Check vertical stacking in action_frame: top arrow at row 0, bottom arrow at row 1 of column 0
        up_info = btn_up.grid_info()
        dn_info = btn_down.grid_info()

        assert up_info["column"] == 0, f"Card {idx} btn_up should be in column 0, got {up_info['column']}"
        assert up_info["row"] == 0, f"Card {idx} btn_up should be at top (row 0), got {up_info['row']}"
        assert dn_info["column"] == 0, f"Card {idx} btn_down should be in column 0, got {dn_info['column']}"
        assert dn_info["row"] == 1, f"Card {idx} btn_down should be below btn_up (row 1), got {dn_info['row']}"

        # Boundary checks
        if idx == 0:
            # First channel (top of list): up arrow must NOT be clickable, down arrow must be clickable
            assert btn_up.cget("state") == "disabled"
            assert btn_up.cget("cursor") == "arrow"
            assert btn_up.cget("style") == "ArrowDisabled.TButton"

            assert btn_down.cget("state") == "normal"
            assert btn_down.cget("cursor") == "hand2"
            assert btn_down.cget("style") == "ArrowActive.TButton"
        elif idx == 1:
            # Middle channel: both arrows must be highlighted and clickable
            assert btn_up.cget("state") == "normal"
            assert btn_up.cget("cursor") == "hand2"
            assert btn_up.cget("style") == "ArrowActive.TButton"

            assert btn_down.cget("state") == "normal"
            assert btn_down.cget("cursor") == "hand2"
            assert btn_down.cget("style") == "ArrowActive.TButton"
        elif idx == 2:
            # Last channel (bottom of list): up arrow is clickable, down arrow must NOT be clickable
            assert btn_up.cget("state") == "normal"
            assert btn_up.cget("cursor") == "hand2"
            assert btn_up.cget("style") == "ArrowActive.TButton"

            assert btn_down.cget("state") == "disabled"
            assert btn_down.cget("cursor") == "arrow"
            assert btn_down.cget("style") == "ArrowDisabled.TButton"

    view.destroy()


def test_queue_views_arrows_boundary_highlighting(ctk_app):
    """Verify Video Queue and Shorts Queue highlight only clickable arrows and disable boundary arrows."""
    app = ctk_app
    app.queued_video_tasks = None
    app.config_mgr = MagicMock()
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.find_for_task.return_value = MagicMock(runs=0, watch_seconds=0.0, video_duration=0.0)
    app.on_queue_changed = MagicMock()
    app.current_state = "IDLE"
    app.queue_service = MagicMock()

    channel = Channel(name="TestArrowChan", url="https://youtube.com/@arrowchan", enabled=True)

    for is_shorts in (False, True):
        tasks = [
            VideoTask(
                url=f"https://www.youtube.com/watch?v=task_{i}",
                title=f"Task Title {i}",
                channel="TestArrowChan",
                duration_text="04:20",
                is_short_override=is_shorts,
            )
            for i in range(1, 4)
        ]

        view = QueueView(app, app, shorts_only=is_shorts)
        view.video_tasks = tasks
        view._channels = MagicMock(return_value=[channel])
        view.pack(fill="both", expand=True)
        view.refresh_list()
        app.update()

        # Find the container
        containers = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
        assert len(containers) >= 1
        container = containers[0]

        # In container, task cards are at rows 1, 2, 3
        row_frames = [w for w in container.grid_slaves() if isinstance(w, ctk.CTkFrame) and w.grid_info()["row"] >= 1]
        # Sort row_frames by row index ascending
        row_frames.sort(key=lambda w: w.grid_info()["row"])
        assert len(row_frames) == 3

        for idx, row in enumerate(row_frames):
            reorder_frame = row.grid_slaves(row=0, column=0)[0]
            btn_up = next((b for b in reorder_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "▲"), None)
            btn_down = next((b for b in reorder_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "▼"), None)

            assert btn_up is not None
            assert btn_down is not None

            if idx == 0:
                # Top item: up arrow not clickable / not highlighted
                assert btn_up.cget("state") == "disabled"
                assert btn_up.cget("cursor") == "arrow"
                assert btn_up.cget("style") == "ArrowDisabled.TButton"

                # Down arrow highlighted and clickable
                assert btn_down.cget("state") == "normal"
                assert btn_down.cget("cursor") == "hand2"
                assert btn_down.cget("style") == "ArrowActive.TButton"
            elif idx == 1:
                # Middle item: both arrows highlighted and clickable
                assert btn_up.cget("state") == "normal"
                assert btn_up.cget("cursor") == "hand2"
                assert btn_up.cget("style") == "ArrowActive.TButton"

                assert btn_down.cget("state") == "normal"
                assert btn_down.cget("cursor") == "hand2"
                assert btn_down.cget("style") == "ArrowActive.TButton"
            elif idx == 2:
                # Bottom item: up arrow highlighted and clickable
                assert btn_up.cget("state") == "normal"
                assert btn_up.cget("cursor") == "hand2"
                assert btn_up.cget("style") == "ArrowActive.TButton"

                # Down arrow not clickable / not highlighted
                assert btn_down.cget("state") == "disabled"
                assert btn_down.cget("cursor") == "arrow"
                assert btn_down.cget("style") == "ArrowDisabled.TButton"

        view.destroy()
        app.update()
