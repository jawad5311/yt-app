"""Tests verifying that Sync All Channels Shorts first fetches the channel's shorts count before fetching shorts."""

import pytest
from unittest.mock import MagicMock, patch

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.views.channels_view import ChannelsView
from src.ui.background_jobs import BackgroundJobs
from src.utils.channel_fetcher import ChannelFetchResult, FetchedVideo, fetch_channel_data


def test_fetch_channel_data_checks_shorts_count_first():
    """Verify that fetch_channel_data checks total shorts count before downloading playlist items."""
    events = []

    def mock_prog(count, kind):
        events.append((count, kind))

    with patch("src.utils.channel_fetcher.get_configured_youtube_api_key", return_value="fake_api_key"), \
         patch("src.utils.channel_fetcher.urllib.request.urlopen") as mock_urlopen, \
         patch("src.utils.channel_fetcher.get_playlist_total_results", return_value=5) as mock_count, \
         patch("src.utils.channel_fetcher.fetch_playlist_items_api") as mock_items:

        # Mock channel lookup
        import io
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "items": [{
                "id": "UC12345678901234567890",
                "snippet": {"title": "Test Channel"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU12345678901234567890"}},
            }]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        mock_items.return_value = (
            [FetchedVideo(video_id="v1", url="https://youtube.com/shorts/v1", title="Short 1", is_short=True)],
            None, None, False, False, 5
        )

        res = fetch_channel_data(
            "https://youtube.com/@test",
            video_limit=None,
            shorts_limit=0,
            progress_callback=mock_prog,
        )

        # Total shorts count was checked first
        from unittest.mock import call
        assert mock_count.call_args_list[0] == call("UUSH12345678901234567890", api_key="fake_api_key", timeout=15)
        # Progress callback received shorts_count first
        assert (5, "shorts_count") in events
        # And then playlist items were fetched
        mock_items.assert_called_once()
        assert res.total_shorts == 5


def test_sync_all_channels_shorts_progress_shows_count_then_fetch(ctk_app):
    """Verify ChannelsView.sync_all_channels_shorts reflects count checking then fetching in progress label."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.config_mgr.append_video_tasks.side_effect = lambda tasks, deduplicate=True: tasks
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()
    app.queued_video_tasks = []

    view = ChannelsView(app, app)
    ch = Channel(name="CountChannel", url="https://youtube.com/@count")
    view.channels = [ch]
    view.refresh_list()

    res = ChannelFetchResult(
        channel_title="CountChannel",
        channel_url=ch.url,
        videos=[
            FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", is_short=True, duration_seconds=30.0),
        ],
        total_shorts=1,
        status="complete",
    )

    def mock_fetch(url, **kwargs):
        prog = kwargs.get("progress_callback")
        if prog:
            prog(1, "shorts_count")
            prog(1, "shorts")
        return res

    with patch("src.ui.views.channels_view.fetch_channel_data", side_effect=mock_fetch), \
         patch("tkinter.messagebox.showinfo"):
        view.sync_all_channels_shorts()
        app.background_jobs.drain()

        assert ch.total_shorts == 1
        assert app.config_mgr.append_video_tasks.called
