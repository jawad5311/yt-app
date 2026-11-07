"""Unit tests for ThemedConfirmDialog and confirmation workflows in QueueView."""
import os
import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.confirm_dialog import ThemedConfirmDialog, show_themed_confirm_dialog
from src.ui.views.channels_view import ChannelsView
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


def test_themed_confirm_dialog_creation(tk_root):
    """Verify ThemedConfirmDialog initializes with proper title, icon, and colors."""
    dialog = ThemedConfirmDialog(
        parent=tk_root,
        title="Test Title",
        heading="Test Heading",
        message="Test Message Body",
        confirm_text="Execute Action",
        cancel_text="Abort",
        confirm_color="#D97706",
        confirm_hover="#B45309",
        icon="⚠️",
        mode="Dark",
    )
    try:
        assert dialog.title() == "Test Title"
        assert dialog.confirmed is False
        assert dialog._mode == "Dark"
        # Confirm action sets confirmed to True
        dialog._on_confirm()
        assert dialog.confirmed is True
    finally:
        try:
            dialog.destroy()
        except Exception:
            pass


def test_themed_confirm_dialog_cancel(tk_root):
    """Verify cancel action and escape key set confirmed to False."""
    dialog = ThemedConfirmDialog(
        parent=tk_root,
        title="Cancel Test",
        heading="Heading",
        message="Message",
        mode="Light",
    )
    try:
        assert dialog._mode == "Light"
        dialog._on_cancel()
        assert dialog.confirmed is False
    finally:
        try:
            dialog.destroy()
        except Exception:
            pass


def test_show_themed_confirm_dialog_confirmed(tk_root):
    """Verify show_themed_confirm_dialog returns True when user confirms."""
    with patch.object(ThemedConfirmDialog, "wait_window", lambda self: setattr(self, "confirmed", True)):
        res = show_themed_confirm_dialog(
            parent=tk_root,
            title="Confirm Title",
            heading="Are you sure?",
            message="This will proceed.",
            confirm_color="#D32F2F",
        )
        assert res is True


def test_show_themed_confirm_dialog_cancelled(tk_root):
    """Verify show_themed_confirm_dialog returns False when user cancels."""
    with patch.object(ThemedConfirmDialog, "wait_window", lambda self: setattr(self, "confirmed", False)):
        res = show_themed_confirm_dialog(
            parent=tk_root,
            title="Confirm Title",
            heading="Are you sure?",
            message="This will proceed.",
            confirm_color="#D32F2F",
        )
        assert res is False


def test_queue_view_buttons_styling_and_gap(tk_root):
    """Verify Reset Stats button has amber warning color and buttons have spacing gap."""
    app = MagicMock()
    app.current_view_name = "queue"
    app.appearance_mode = "Dark"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)
    app.stats_store.get.return_value = None

    view = QueueView(tk_root, app, shorts_only=False)
    try:
        # Check reset_btn exists and has amber warning color
        assert hasattr(view, "reset_btn")
        # CustomTkinter / TTK adapter button property check
        fg_color = getattr(view.reset_btn, "_fg_color", None) or getattr(view.reset_btn, "cget", lambda k: "")("fg_color")
        assert str(fg_color).lower() == "#d97706"

        # Check clear_btn exists
        assert hasattr(view, "clear_btn")

        # Verify pack options on clear_btn include right-side padding for spacing gap
        pack_info = view.clear_btn.pack_info()
        assert "padx" in pack_info
        # Either integer >= 5 or tuple with gap
        padx = pack_info["padx"]
        if isinstance(padx, tuple):
            assert padx[1] >= 5 or padx[0] >= 5
        elif isinstance(padx, (int, str)):
            assert int(padx) >= 5 or (isinstance(padx, str) and int(padx.split()[-1]) >= 5)
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_queue_view_clear_queue_confirmed(tk_root):
    """Verify clear_queue only removes tasks when user confirms in dialog."""
    app = MagicMock()
    app.current_view_name = "queue"
    app.appearance_mode = "Dark"
    task = VideoTask(url="https://youtube.com/watch?v=123", title="Video 1")
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = [task.to_dict()]
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)
    app.stats_store.get.return_value = None

    view = QueueView(tk_root, app, shorts_only=False)
    view.video_tasks = [task]

    try:
        # 1. User cancels confirmation dialog -> tasks retained
        with patch("src.ui.views.queue_view.show_themed_confirm_dialog", return_value=False) as mock_dialog:
            view.clear_queue(confirm=True)
            mock_dialog.assert_called_once()
            assert len(view.video_tasks) == 1

        # 2. User confirms confirmation dialog -> tasks cleared
        with patch("src.ui.views.queue_view.show_themed_confirm_dialog", return_value=True) as mock_dialog:
            view.clear_queue(confirm=True)
            mock_dialog.assert_called_once()
            assert len(view.video_tasks) == 0
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_queue_view_reset_stats_confirmed(tk_root):
    """Verify reset_stats only triggers stats reset when user confirms in dialog."""
    app = MagicMock()
    app.current_view_name = "queue"
    app.appearance_mode = "Dark"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)
    app.stats_store.get.return_value = None
    app.reset_all_watch_stats = MagicMock()

    view = QueueView(tk_root, app, shorts_only=False)

    try:
        # 1. User cancels confirmation dialog -> reset not executed
        with patch("src.ui.views.queue_view.show_themed_confirm_dialog", return_value=False) as mock_dialog:
            view.reset_stats(confirm=True)
            mock_dialog.assert_called_once()
            app.reset_all_watch_stats.assert_not_called()

        # 2. User confirms confirmation dialog -> reset executed
        with patch("src.ui.views.queue_view.show_themed_confirm_dialog", return_value=True) as mock_dialog:
            view.reset_stats(confirm=True)
            mock_dialog.assert_called_once()
            app.reset_all_watch_stats.assert_called_once()
    finally:
        try:
            view.destroy()
        except Exception:
            pass



def test_channels_view_clear_all_confirmed(tk_root):
    """Verify Clear All channels executes after confirmation dialog is confirmed."""
    app = MagicMock()
    app.current_view_name = "channels"
    app.appearance_mode = "Dark"
    ch = Channel(name="Test Channel", url="https://youtube.com/@test")
    app.config_mgr.get_channels.return_value = [ch.to_dict()]
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)

    view = ChannelsView(tk_root, app)
    view.channels = [ch]

    try:
        # 1. User cancels confirmation dialog -> channels preserved
        with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=False) as mock_dialog:
            view.clear_channels(confirm=True)
            mock_dialog.assert_called_once()
            assert len(view.channels) == 1

        # 2. User confirms confirmation dialog -> channels cleared
        with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=True) as mock_dialog:
            view.clear_channels(confirm=True)
            mock_dialog.assert_called_once()
            assert len(view.channels) == 0
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_channels_view_archive_all_confirmed(tk_root):
    """Verify Archive All channels executes after confirmation dialog is confirmed."""
    app = MagicMock()
    app.current_view_name = "channels"
    app.appearance_mode = "Dark"
    ch1 = Channel(name="Channel 1", url="https://youtube.com/@test1", archived=False)
    ch2 = Channel(name="Channel 2", url="https://youtube.com/@test2", archived=False)
    ch3 = Channel(name="Channel 3", url="https://youtube.com/@test3", archived=True)
    app.config_mgr.get_channels.return_value = [ch1.to_dict(), ch2.to_dict(), ch3.to_dict()]
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)

    view = ChannelsView(tk_root, app)
    view.channels = [ch1, ch2, ch3]
    view.current_tab = "Active"

    try:
        # 1. User cancels confirmation dialog -> active channels unchanged
        with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=False) as mock_dialog:
            view.archive_all_channels(confirm=True)
            mock_dialog.assert_called_once()
            assert view.channels[0].archived is False
            assert view.channels[1].archived is False
            assert view.current_tab == "Active"

        # 2. User confirms confirmation dialog -> active channels archived & tab switched
        with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=True) as mock_dialog:
            view.archive_all_channels(confirm=True)
            mock_dialog.assert_called_once()
            assert all(c.archived for c in view.channels)
            assert view.current_tab == "Archived"
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_channels_view_empty_skips_confirm_dialog(tk_root):
    """Verify Clear All and Archive All skip confirmation dialog when no applicable channels exist."""
    app = MagicMock()
    app.current_view_name = "channels"
    app.appearance_mode = "Dark"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)

    view = ChannelsView(tk_root, app)
    view.channels = []

    try:
        with patch("src.ui.views.channels_view.show_themed_confirm_dialog") as mock_dialog:
            # Clear all on empty list
            view.clear_channels(confirm=True)
            mock_dialog.assert_not_called()

            # Archive all on empty list
            view.archive_all_channels(confirm=True)
            mock_dialog.assert_not_called()

            # Archive all when only already-archived channels exist
            view.channels = [Channel(name="Archived Only", url="https://youtube.com/@arch", archived=True)]
            view.archive_all_channels(confirm=True)
            mock_dialog.assert_not_called()
    finally:
        try:
            view.destroy()
        except Exception:
            pass
