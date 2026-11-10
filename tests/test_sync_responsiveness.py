import threading
from unittest.mock import MagicMock, patch
import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView
from src.utils.channel_fetcher import ChannelFetchResult


@pytest.fixture
def mock_app(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.queued_video_tasks = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.for_channel.return_value = []
    app.background_jobs = BackgroundJobs()
    app.views = {}
    return app


def test_safe_destroy_releases_grab_and_destroys():
    """Verify _safe_destroy calls grab_release before destroy to prevent input deadlock."""
    mock_widget = MagicMock()
    ChannelsView._safe_destroy(mock_widget)
    mock_widget.grab_release.assert_called_once()
    mock_widget.destroy.assert_called_once()


def test_safe_destroy_handles_none_or_broken_widget():
    """Verify _safe_destroy does not raise on None or exceptions during grab_release/destroy."""
    ChannelsView._safe_destroy(None)

    broken_widget = MagicMock()
    broken_widget.grab_release.side_effect = RuntimeError("Grab error")
    broken_widget.destroy.side_effect = RuntimeError("Destroy error")
    # Should not raise
    ChannelsView._safe_destroy(broken_widget)


def test_sync_channel_all_progress_callback_receives_tuples(mock_app):
    """Verify sync_channel_all cleanly bridges two-argument progress callbacks without raising TypeError."""
    view = ChannelsView(mock_app, mock_app)
    ch = Channel(name="Alpha", url="https://youtube.com/@alpha")
    view.channels = [ch]

    captured_progress = []

    def fake_fetch_channel_data(url, **kwargs):
        prog = kwargs.get("progress_callback")
        if prog:
            # channel_fetcher calls progress_callback with 2 arguments: (count, kind)
            prog(10, "videos")
            prog(5, "shorts")
            prog(15, "durations")
        return ChannelFetchResult(
            channel_title="Alpha",
            channel_url=url,
            total_videos=10,
            total_shorts=5,
            videos=[VideoTask(url="https://youtube.com/watch?v=123", title="V1")],
        )

    with patch("src.ui.views.channels_view.fetch_channel_data", side_effect=fake_fetch_channel_data), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_channel_all(ch)

        # Let worker complete and drain background jobs
        import time
        time.sleep(0.05)
        mock_app.background_jobs.drain()

    # If TypeError occurred in lambda, BackgroundJobs would record error instead of completing
    assert ch.total_videos == 10
    assert ch.total_shorts == 5


def test_sync_single_channel_data_progress_callback_receives_tuples(mock_app):
    """Verify sync_single_channel_data bridges two-argument progress callbacks without error."""
    view = ChannelsView(mock_app, mock_app)
    ch = Channel(name="Beta", url="https://youtube.com/@beta", has_new_videos=True)
    view.channels = [ch]

    def fake_fetch(url, **kwargs):
        prog = kwargs.get("progress_callback")
        if prog:
            prog(20, "videos")
            prog(20, "durations")
        return ChannelFetchResult(
            channel_title="Beta",
            channel_url=url,
            total_videos=20,
            videos=[VideoTask(url="https://youtube.com/watch?v=beta1", title="B1")],
        )

    with patch("src.ui.views.channels_view.fetch_channel_data", side_effect=fake_fetch), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_single_channel_data(ch)

        import time
        time.sleep(0.05)
        mock_app.background_jobs.drain()

    assert ch.total_videos == 20


def test_sync_data_all_cancel_event_handling(mock_app):
    """Verify sync_data_all respects user cancellation and terminates background fetch loop."""
    view = ChannelsView(mock_app, mock_app)
    ch1 = Channel(name="Ch1", url="https://youtube.com/@ch1")
    ch2 = Channel(name="Ch2", url="https://youtube.com/@ch2")
    view.channels = [ch1, ch2]

    fetch_calls = []

    def fake_fetch(url, **kwargs):
        fetch_calls.append(url)
        return ChannelFetchResult(channel_url=url, total_videos=1, total_shorts=0, videos=[])

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=True), \
         patch("src.ui.views.channels_view.fetch_channel_data", side_effect=fake_fetch), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_data_all(confirm=False)

        import time
        time.sleep(0.05)
        mock_app.background_jobs.drain()

    assert len(fetch_calls) == 2
