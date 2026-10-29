"""Unit tests for the per-channel Sync Data button and conditional enablement."""

from unittest.mock import MagicMock, patch
import customtkinter as ctk

from src.models.channel import Channel
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView


def test_row_sync_data_button_initially_disabled(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=10,
        total_shorts=5,
        has_new_videos=False,
    )
    view.channels = [channel]
    view.refresh_list()

    # Locate per-channel Sync Data button in row action frame
    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 1
    action_frame = cards[0].grid_slaves(row=0, column=2)[0]
    sync_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync Data" in b.cget("text")), None)

    assert sync_btn is not None
    assert sync_btn.cget("state") == "disabled"

    view.destroy()


def test_row_sync_data_button_enabled_when_new_videos_found_and_clicks_to_sync(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    channel = Channel(
        name="Active Creator",
        url="https://youtube.com/@activecreator",
        total_videos=50,
        total_shorts=20,
        has_new_videos=True,
    )
    view.channels = [channel]
    view.refresh_list()

    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    action_frame = cards[0].grid_slaves(row=0, column=2)[0]
    sync_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync Data" in b.cget("text")), None)

    assert sync_btn is not None
    # Since has_new_videos is True, button is enabled!
    assert sync_btn.cget("state") == "normal"

    # Clicking it calls sync_single_channel_data for this channel
    with patch.object(view, "sync_single_channel_data") as mock_sync:
        sync_btn._command()
        mock_sync.assert_called_once_with(channel)

    view.destroy()
