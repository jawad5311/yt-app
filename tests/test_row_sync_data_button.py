"""Unit tests for the per-channel Sync Recent and Sync All buttons."""

from unittest.mock import MagicMock, patch
import customtkinter as ctk

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView
from src.utils.channel_fetcher import ChannelFetchResult, FetchedVideo


def test_row_sync_recent_button_initially_disabled_and_sync_all_present(ctk_app):
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

    # Locate action buttons in row action frame
    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 1
    action_frame = cards[0].grid_slaves(row=0, column=2)[0]
    
    # 🎬 Videos button must be removed
    videos_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Videos" in b.cget("text")), None)
    assert videos_btn is None

    # Sync All button must be present and enabled
    sync_all_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync All" in b.cget("text")), None)
    assert sync_all_btn is not None
    assert sync_all_btn.cget("state") == "normal"

    # Sync Recent button must be present and initially disabled because has_new is False
    sync_recent_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync Recent" in b.cget("text")), None)
    assert sync_recent_btn is not None
    assert sync_recent_btn.cget("state") == "disabled"

    view.destroy()


def test_row_sync_recent_and_sync_all_button_commands(ctk_app):
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
    sync_recent_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync Recent" in b.cget("text")), None)
    sync_all_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync All" in b.cget("text")), None)

    assert sync_recent_btn is not None
    # Since has_new_videos is True, button is enabled!
    assert sync_recent_btn.cget("state") == "normal"

    # Clicking Sync Recent calls sync_single_channel_data for this channel
    with patch.object(view, "sync_single_channel_data") as mock_sync:
        sync_recent_btn._command()
        mock_sync.assert_called_once_with(channel)

    # Clicking Sync All calls sync_channel_all for this channel
    with patch.object(view, "sync_channel_all") as mock_sync_all:
        sync_all_btn._command()
        mock_sync_all.assert_called_once_with(channel)

    view.destroy()


def test_sync_channel_all_executes_unlimited_fetch_and_imports(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()
    app.queued_video_tasks = []
    app.on_queue_changed = MagicMock()

    mock_queue = MagicMock()
    mock_queue.video_tasks = []
    mock_shorts_queue = MagicMock()
    mock_shorts_queue.video_tasks = []

    view = ChannelsView(app, app)
    view._queue_view = MagicMock(return_value=mock_queue)
    view._shorts_queue_view = MagicMock(return_value=mock_shorts_queue)

    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=0,
        total_shorts=0,
        has_new_videos=True,
        has_new_shorts=True,
    )
    view.channels = [channel]
    view.refresh_list()

    mock_res = ChannelFetchResult(
        channel_title="Creator",
        channel_url=channel.url,
        videos=[
            FetchedVideo(video_id="v1", url="https://youtube.com/watch?v=v1", title="Video 1", is_short=False, duration_seconds=120.0),
            FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", is_short=True, duration_seconds=30.0),
        ],
        total_videos=1,
        total_shorts=1,
        status="complete",
    )

    app.config_mgr.append_video_tasks.return_value = [
        {"url": "https://youtube.com/watch?v=v1", "title": "Video 1", "channel": "Creator", "target_multiplier": 3.0, "max_runs": 5, "duration_seconds": 120.0},
        {"url": "https://youtube.com/shorts/s1", "title": "Short 1", "channel": "Creator", "target_multiplier": 3.0, "max_runs": 5, "duration_seconds": 30.0},
    ]

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch, \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_channel_all(channel)
        app.background_jobs.drain()

    # Verify fetch_channel_data was called with unlimited limits (video_limit=0, shorts_limit=0)
    mock_fetch.assert_called_once()
    _, kwargs = mock_fetch.call_args
    assert kwargs.get("video_limit") == 0
    assert kwargs.get("shorts_limit") == 0

    # Verify append_video_tasks was called with new tasks having default target multiplier 3.0 and max_runs 5
    app.config_mgr.append_video_tasks.assert_called_once()
    appended = app.config_mgr.append_video_tasks.call_args[0][0]
    assert len(appended) == 2
    assert appended[0]["target_multiplier"] == 3.0
    assert appended[0]["max_runs"] == 5
    assert appended[0]["channel"] == "Creator"

    # Verify on_queue_changed was notified
    app.on_queue_changed.assert_called_once()

    # Verify queue views refreshed
    assert mock_queue.refresh_list.call_count >= 1
    assert mock_shorts_queue.refresh_list.call_count >= 1

    # Badges cleared
    assert channel.has_new_videos is False
    assert channel.has_new_shorts is False

    view.destroy()
