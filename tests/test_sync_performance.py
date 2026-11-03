import threading
from unittest.mock import MagicMock, patch

import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView
from src.ui.views.queue_view import QueueView
from src.utils.channel_fetcher import ChannelFetchResult, FetchedVideo, enrich_video_durations


def test_enrich_video_durations_concurrent_and_progress():
    """Verify that enrich_video_durations processes chunks concurrently and reports progress."""
    videos = [
        FetchedVideo(video_id=f"vid_{i:03d}", url=f"https://www.youtube.com/watch?v=vid_{i:03d}", title=f"Video {i}")
        for i in range(120)  # 3 chunks of 50, 50, 20
    ]

    mock_responses = {}
    for i in range(120):
        mock_responses[f"vid_{i:03d}"] = "PT1M30S"

    progress_reports = []

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        # extract ids
        query = url.split("?", 1)[-1]
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        ids = params.get("id", "").split(",")
        items = [{"id": vid, "contentDetails": {"duration": "PT1M30S"}} for vid in ids]
        import io
        import json
        payload = json.dumps({"items": items}).encode("utf-8")
        return io.BytesIO(payload)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        enrich_video_durations(
            videos,
            api_key="TEST_API_KEY",
            timeout=5,
            progress_callback=lambda count, kind: progress_reports.append((count, kind)),
        )

    assert len(videos) == 120
    for v in videos:
        assert v.duration_seconds == 90
        assert v.duration_text == "1:30"

    assert len(progress_reports) == 3
    for count, kind in progress_reports:
        assert kind == "durations"
    assert progress_reports[-1][0] == 120


def test_enrich_video_durations_cancel_event():
    """Verify enrich_video_durations stops immediately if cancel_event is set."""
    videos = [
        FetchedVideo(video_id=f"vid_{i:03d}", url=f"https://www.youtube.com/watch?v=vid_{i:03d}", title=f"Video {i}")
        for i in range(100)
    ]

    cancel_ev = threading.Event()
    cancel_ev.set()  # cancelled from the start

    with patch("urllib.request.urlopen") as mock_urlopen:
        enrich_video_durations(
            videos,
            api_key="TEST_API_KEY",
            cancel_event=cancel_ev,
        )
        mock_urlopen.assert_not_called()


def test_sync_channel_all_lazy_queue_refresh(ctk_app):
    """Verify sync_channel_all defers queue view refresh when user is on Channels view."""
    app = ctk_app
    app.current_view_name = "channels"
    app.config_mgr = MagicMock()
    app.config_mgr.get_video_queue.return_value = []
    app.queued_video_tasks = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.for_channel.return_value = []
    app.background_jobs = BackgroundJobs()

    mock_queue = MagicMock()
    mock_queue.video_tasks = []
    mock_shorts_queue = MagicMock()
    mock_shorts_queue.video_tasks = []

    app.views = {
        "queue": mock_queue,
        "shorts_queue": mock_shorts_queue,
    }

    view = ChannelsView(app, app)
    channel = Channel(
        name="TechHub",
        url="https://youtube.com/@techhub",
    )
    view.channels = [channel]

    fetched_vids = [
        VideoTask(url=f"https://youtube.com/watch?v=v{i}", title=f"Video {i}", duration_seconds=120)
        for i in range(10)
    ]
    mock_res = ChannelFetchResult(
        channel_title="TechHub",
        channel_url=channel.url,
        total_videos=10,
        total_shorts=0,
        videos=fetched_vids,
    )

    app.config_mgr.append_video_tasks.return_value = [t.to_dict() for t in fetched_vids]

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_channel_all(channel)

        import time
        time.sleep(0.1)
        app.background_jobs.drain()

    # Since app.current_view_name is "channels", refresh_list should NOT be called on queue or shorts_queue
    mock_queue.refresh_list.assert_not_called()
    mock_shorts_queue.refresh_list.assert_not_called()
    # But flags indicating deferred refresh should be set
    assert app._queue_needs_refresh is True
    assert app._shorts_queue_needs_refresh is True


def test_queue_view_progressive_render_no_inner_idletasks(ctk_app):
    """Verify _process_card_render_batch does not call update_idletasks in loop."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_video_queue.return_value = []
    app.queued_video_tasks = []
    app.stats_store = None
    app.on_queue_changed = MagicMock()
    app.get_channels = MagicMock(return_value=[])

    qv = QueueView(app, app, shorts_only=False)
    qv.scroll_frame.update_idletasks = MagicMock()

    # Populate 30 pending card items
    container = MagicMock()
    task = VideoTask(url="https://youtube.com/watch?v=test", title="Test")
    for i in range(30):
        qv._pending_card_queue.append((
            container, i, task, i, None, False, False, None, None
        ))

    with patch.object(qv, "_build_task_card", return_value=MagicMock()):
        # Process first batch of 25
        qv._process_card_render_batch(batch_size=25)

    # During processing when queue is not empty, update_idletasks must NOT be called
    assert qv.scroll_frame.update_idletasks.call_count == 0
    assert len(qv._pending_card_queue) == 5

    # Now finish the remaining 5 items
    with patch.object(qv, "_build_task_card", return_value=MagicMock()):
        qv._process_card_render_batch(batch_size=25)

    assert len(qv._pending_card_queue) == 0
    # Now that the queue is fully drained, update_idletasks is called once at the end
    assert qv.scroll_frame.update_idletasks.call_count == 1
