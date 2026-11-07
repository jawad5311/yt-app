"""Tests for in-app floating bubble notification in QueueView for target multiplier and max views updates."""
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from src.models.video_task import VideoTask
from src.ui.views.queue_view import QueueView


@pytest.fixture
def tk_root():
    """Create a hidden Tk root for widget testing."""
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        try:
            root.destroy()
        except Exception:
            pass
    except Exception:
        pytest.skip("Tkinter display not available in environment")


def test_queue_view_show_bubble_notification(tk_root):
    """Verify QueueView displays a floating bubble notification instead of a blocking messagebox."""
    app = MagicMock()
    app.current_view_name = "queue"
    app.appearance_mode = "Dark"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)
    app.stats_store.get.return_value = None

    view = QueueView(tk_root, app, shorts_only=False)

    try:
        # Trigger popup notification for max views update
        view.show_popup_notification(
            title="Max Views Updated",
            message="Max views value updated to 6 for:\n'Meta Ads MCP + Claude Just Chang...'",
        )

        assert view.last_bubble_title == "Max Views Updated"
        assert "Max views value updated to 6" in view.last_bubble_message
        assert getattr(view, "_bubble_widget", None) is not None

        # Verify bubble placement: centered at the top
        place_info = view._bubble_widget.place_info()
        assert float(place_info.get("relx", 0)) == 0.5
        assert int(place_info.get("y", 0)) == 20
        assert place_info.get("anchor", "") == "n"

        # Verify dismiss closes the bubble
        view.dismiss_bubble()
        assert getattr(view, "_bubble_widget", None) is None
        assert getattr(view, "_bubble_timer", None) is None
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_queue_view_show_bubble_notification_target_multiplier(tk_root):
    """Verify target multiplier update triggers bubble notification with proper formatting."""
    app = MagicMock()
    app.current_view_name = "queue"
    app.appearance_mode = "Light"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)
    app.stats_store.get.return_value = None

    view = QueueView(tk_root, app, shorts_only=False)

    try:
        view.show_popup_notification(
            title="Target Multiplier Updated",
            message="Target value updated to 2.5x for:\n'My Automated Video'",
        )

        assert view.last_bubble_title == "Target Multiplier Updated"
        assert "2.5x" in view.last_bubble_message
        bubble = getattr(view, "_bubble_widget", None)
        assert bubble is not None

        # Inspect children labels inside bubble
        labels = []
        for child in bubble.winfo_children():
            for sub in getattr(child, "winfo_children", lambda: [])():
                if isinstance(sub, tk.Label):
                    labels.append(sub.cget("text"))
                for leaf in getattr(sub, "winfo_children", lambda: [])():
                    if isinstance(leaf, tk.Label):
                        labels.append(leaf.cget("text"))

        assert "✓" in labels
        assert "Target Multiplier Updated" in labels
        assert any("2.5x" in t for t in labels)
        assert "✕" in labels
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_queue_view_bubble_replaces_existing(tk_root):
    """Verify a subsequent notification replaces any active bubble cleanly."""
    app = MagicMock()
    app.current_view_name = "queue"
    app.appearance_mode = "Dark"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)
    app.stats_store.get.return_value = None

    view = QueueView(tk_root, app, shorts_only=True)

    try:
        # First bubble
        view.show_popup_notification(
            title="Max Views Updated",
            message="Max views value updated to 3 for:\n'Short 1'",
        )
        first_bubble = view._bubble_widget
        assert first_bubble is not None
        assert view.last_bubble_title == "Max Views Updated"

        # Second bubble immediately follows
        view.show_popup_notification(
            title="Target Multiplier Updated",
            message="Target value updated to 1.5x for:\n'Short 2'",
        )
        second_bubble = view._bubble_widget
        assert second_bubble is not None
        assert second_bubble != first_bubble
        assert view.last_bubble_title == "Target Multiplier Updated"
        assert "1.5x" in view.last_bubble_message
    finally:
        try:
            view.destroy()
        except Exception:
            pass
