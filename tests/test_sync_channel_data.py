"""Unit tests for Sync Data button and 'New Videos Available' detection."""

from unittest.mock import MagicMock, patch
import customtkinter as ctk

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView
from src.utils.channel_fetcher import ChannelFetchResult


def test_channels_view_has_sync_data_button_and_no_sync_names(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)

    # Search buttons in view
    buttons = []
    for w in view.winfo_children():
        if isinstance(w, ctk.CTkFrame):
            for child in w.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    buttons.append(child.cget("text"))

    assert "🔍 Check New" in buttons
    assert "🔄 Sync Names" not in buttons
    assert "Sync Names" not in "".join(buttons)

    view.destroy()


def test_sync_channel_data_flags_new_videos_available(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    # Channel currently has 2 videos in stats
    fake_rec1 = MagicMock(video_key="v1", is_short=False)
    fake_rec2 = MagicMock(video_key="s1", is_short=True)
    app.stats_store.for_channel.return_value = [fake_rec1, fake_rec2]

    # And 2 videos in queue
    app.queued_video_tasks = [
        VideoTask(url="https://youtube.com/watch?v=v1", channel="Creator"),
        VideoTask(url="https://youtube.com/shorts/s1", channel="Creator"),
    ]

    view = ChannelsView(app, app)
    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=1,
        total_shorts=1,
        has_new_videos=False,
    )
    view.channels = [channel]
    view.refresh_list()

    # YouTube now reports 10 long-form videos and 5 shorts
    mock_res = ChannelFetchResult(
        channel_title="Creator",
        channel_url=channel.url,
        total_videos=10,
        total_shorts=5,
    )

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_all_channel_data()

        # Let the background jobs finish and drain
        import time
        time.sleep(0.1)
        app.background_jobs.drain()

    # Channel should now reflect 10 videos, 5 shorts, and has_new_videos=True
    assert channel.total_videos == 10
    assert channel.total_shorts == 5
    assert channel.has_new_videos is True

    # Check that 'New Videos Available' appears on the channel card
    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    labels = []
    def _collect_labels(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                labels.append(child.cget("text"))
            _collect_labels(child)

    for card in cards:
        _collect_labels(card)

    card_text = "\n".join(labels)
    assert "New Videos Available" in card_text
    assert "10 Videos" in card_text
    assert "5 Shorts" in card_text

    # Verify green color styling for New Videos Available
    new_lbls = []
    def _collect_new_lbls(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkLabel) and "New Videos Available" in child.cget("text"):
                new_lbls.append(child)
            _collect_new_lbls(child)

    for card in cards:
        _collect_new_lbls(card)

    assert len(new_lbls) == 1
    assert new_lbls[0].cget("text_color") == ("#16A34A", "#4ADE80")

    view.destroy()
