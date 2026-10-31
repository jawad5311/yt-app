"""Unit tests for Check New button, New Videos/Shorts badge detection, and Sync Data logic."""

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


def test_sync_channel_data_flags_new_videos_and_shorts_available(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    # Channel currently has 1 video and 1 short in queue
    app.queued_video_tasks = [
        VideoTask(url="https://youtube.com/watch?v=v1", channel="Creator"),
        VideoTask(url="https://youtube.com/shorts/s1", channel="Creator"),
    ]
    app.stats_store.for_channel.return_value = []

    view = ChannelsView(app, app)
    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=1,
        total_shorts=1,
        has_new_videos=False,
        has_new_shorts=False,
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

        import time
        time.sleep(0.1)
        app.background_jobs.drain()

    assert channel.total_videos == 10
    assert channel.total_shorts == 5
    assert channel.has_new_videos is True
    assert channel.has_new_shorts is True
    assert channel.has_new_content is True
    assert channel.new_badge_text == "🔥 New Videos & Shorts Available"

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
    assert "New Videos & Shorts Available" in card_text
    assert "10 Videos" in card_text
    assert "5 Shorts" in card_text

    view.destroy()


def test_sync_channel_data_flags_only_new_videos(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    # Channel already has all shorts, but new long-form videos exist
    app.queued_video_tasks = [
        VideoTask(url="https://youtube.com/watch?v=v1", channel="Creator"),
        VideoTask(url="https://youtube.com/shorts/s1", channel="Creator"),
    ]
    app.stats_store.for_channel.return_value = []

    view = ChannelsView(app, app)
    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=1,
        total_shorts=1,
    )
    view.channels = [channel]
    view.refresh_list()

    mock_res = ChannelFetchResult(
        channel_title="Creator",
        channel_url=channel.url,
        total_videos=5,
        total_shorts=1,  # unchanged shorts count
    )

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_all_channel_data()
        import time
        time.sleep(0.1)
        app.background_jobs.drain()

    assert channel.has_new_videos is True
    assert channel.has_new_shorts is False
    assert channel.new_badge_text == "🔥 New Videos Available"

    view.destroy()


def test_sync_channel_data_flags_only_new_shorts(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    # Channel already has all long-form videos, but new shorts exist
    app.queued_video_tasks = [
        VideoTask(url="https://youtube.com/watch?v=v1", channel="Creator"),
        VideoTask(url="https://youtube.com/shorts/s1", channel="Creator"),
    ]
    app.stats_store.for_channel.return_value = []

    view = ChannelsView(app, app)
    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=1,
        total_shorts=1,
    )
    view.channels = [channel]
    view.refresh_list()

    mock_res = ChannelFetchResult(
        channel_title="Creator",
        channel_url=channel.url,
        total_videos=1,  # unchanged video count
        total_shorts=7,  # new shorts available
    )

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_all_channel_data()
        import time
        time.sleep(0.1)
        app.background_jobs.drain()

    assert channel.has_new_videos is False
    assert channel.has_new_shorts is True
    assert channel.new_badge_text == "🔥 New Shorts Available"

    view.destroy()


def test_sync_single_channel_data_fetches_and_adds_to_queue(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    existing_queue = [
        {"url": "https://youtube.com/watch?v=existing1", "title": "Old Video", "channel": "Creator"}
    ]
    app.config_mgr.get_video_queue.return_value = existing_queue
    app.queued_video_tasks = [VideoTask.from_dict(d) for d in existing_queue]
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.for_channel.return_value = []
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    channel = Channel(
        name="Creator",
        url="https://youtube.com/@creator",
        total_videos=2,
        total_shorts=1,
        has_new_videos=True,
        has_new_shorts=True,
    )
    view.channels = [channel]
    view.refresh_list()

    # YouTube fetch returns existing video (to update duration) and new video + new short
    fetched_vid1 = VideoTask(url="https://youtube.com/watch?v=existing1", title="Old Video", duration_seconds=120, duration_text="2:00")
    fetched_vid2 = VideoTask(url="https://youtube.com/watch?v=newvid2", title="Brand New Video", duration_seconds=300, duration_text="5:00")
    fetched_short = VideoTask(url="https://youtube.com/shorts/newshort1", title="Brand New Short", duration_seconds=45, duration_text="0:45")

    mock_res = ChannelFetchResult(
        channel_title="Creator",
        channel_url=channel.url,
        total_videos=2,
        total_shorts=1,
        videos=[fetched_vid1, fetched_vid2, fetched_short],
    )

    app.config_mgr.append_video_tasks.side_effect = lambda tasks, deduplicate=True: existing_queue + tasks

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch, \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_single_channel_data(channel)

        import time
        time.sleep(0.1)
        app.background_jobs.drain()

        # Both limits should be 50 when both videos and shorts are available
        mock_fetch.assert_called_once_with(
            channel.url,
            video_limit=50,
            shorts_limit=50,
            config_mgr=app.config_mgr,
        )

    # Added tasks should be saved with 3.0x multiplier and 5 max runs
    app.config_mgr.append_video_tasks.assert_called_once()
    appended = app.config_mgr.append_video_tasks.call_args[0][0]
    assert len(appended) == 2
    assert appended[0]["url"] == "https://youtube.com/watch?v=newvid2"
    assert appended[0]["target_multiplier"] == 3.0
    assert appended[0]["max_runs"] == 5
    assert appended[1]["url"] == "https://youtube.com/shorts/newshort1"
    assert appended[1]["target_multiplier"] == 3.0
    assert appended[1]["max_runs"] == 5

    view.destroy()


def test_channels_view_has_sync_data_all_button(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)

    buttons = []
    for w in view.winfo_children():
        if isinstance(w, ctk.CTkFrame):
            for child in w.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    buttons.append(child.cget("text"))

    assert "⚡ Sync Data All" in buttons
    assert "🔍 Check New" in buttons
    assert "+ Add Channel" in buttons

    view.destroy()


def test_sync_data_all_syncs_all_channels(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    existing_queue = []
    app.config_mgr.get_video_queue.return_value = existing_queue
    app.queued_video_tasks = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.for_channel.return_value = []
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    ch1 = Channel(name="ChanOne", url="https://youtube.com/@chan1", total_videos=1, has_new_videos=True, has_new_shorts=False)
    ch2 = Channel(name="ChanTwo", url="https://youtube.com/@chan2", total_shorts=1, has_new_videos=False, has_new_shorts=True)
    view.channels = [ch1, ch2]
    view.refresh_list()

    vid1 = VideoTask(url="https://youtube.com/watch?v=c1v1", title="Chan 1 Video", duration_seconds=100)
    short2 = VideoTask(url="https://youtube.com/shorts/c2s1", title="Chan 2 Short", duration_seconds=30)

    res1 = ChannelFetchResult(channel_title="ChanOne", channel_url=ch1.url, total_videos=1, total_shorts=0, videos=[vid1])
    res2 = ChannelFetchResult(channel_title="ChanTwo", channel_url=ch2.url, total_videos=0, total_shorts=1, videos=[short2])

    def _fetch_side_effect(url, **kwargs):
        if url == ch1.url:
            return res1
        return res2

    app.config_mgr.append_video_tasks.side_effect = lambda tasks, deduplicate=True: tasks

    with patch("src.ui.views.channels_view.fetch_channel_data", side_effect=_fetch_side_effect) as mock_fetch, \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_data_all()

        import time
        time.sleep(0.1)
        app.background_jobs.drain()

        assert mock_fetch.call_count == 2
        # ch1 only has new videos: video_limit=50, shorts_limit=None
        mock_fetch.assert_any_call(ch1.url, video_limit=50, shorts_limit=None, config_mgr=app.config_mgr)
        # ch2 only has new shorts: video_limit=None, shorts_limit=50
        mock_fetch.assert_any_call(ch2.url, video_limit=None, shorts_limit=50, config_mgr=app.config_mgr)

    app.config_mgr.append_video_tasks.assert_called_once()
    appended = app.config_mgr.append_video_tasks.call_args[0][0]
    assert len(appended) == 2
    assert appended[0]["url"] == "https://youtube.com/watch?v=c1v1"
    assert appended[0]["channel"] == "ChanOne"
    assert appended[0]["target_multiplier"] == 3.0
    assert appended[0]["max_runs"] == 5

    assert appended[1]["url"] == "https://youtube.com/shorts/c2s1"
    assert appended[1]["channel"] == "ChanTwo"
    assert appended[1]["target_multiplier"] == 3.0
    assert appended[1]["max_runs"] == 5

    view.destroy()
