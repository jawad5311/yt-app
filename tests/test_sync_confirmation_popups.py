"""Tests verifying confirmation pop-up messages for Sync All Content and Sync All Channels Shorts buttons."""

import pytest
from unittest.mock import MagicMock, patch

from src.models.channel import Channel
from src.ui.views.channels_view import ChannelsView
from src.ui.background_jobs import BackgroundJobs


@pytest.fixture
def mock_app(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.for_channel.return_value = []
    app.background_jobs = BackgroundJobs()
    app.queued_video_tasks = []
    return app


def test_sync_all_content_confirmation_dialog_cancel(mock_app):
    """Verify that clicking Cancel in Sync All Content confirmation aborts without making download requests."""
    view = ChannelsView(mock_app, mock_app)
    ch1 = Channel(name="Alpha", url="https://youtube.com/@alpha", total_videos=10, total_shorts=20)
    ch2 = Channel(name="Beta", url="https://youtube.com/@beta", total_videos=5, total_shorts=15)
    view.channels = [ch1, ch2]
    view.refresh_list()

    mock_res = MagicMock(error=None, videos=[], total_videos=10, total_shorts=20)

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=False) as mock_dialog, \
         patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch:

        view.sync_data_all(confirm=True)

        # Dialog was displayed with estimated content and API credits
        mock_dialog.assert_called_once()
        kwargs = mock_dialog.call_args[1]
        assert "Confirm Sync All Content" in kwargs["title"]
        assert "2 channels" in kwargs["heading"].lower()
        msg = kwargs["message"]
        assert "items found across" in msg
        assert "Approximately" in msg
        assert "YouTube API quota units will be consumed" in msg
        assert "10,000 units/day" not in msg

        # Pre-check was performed, but because user cancelled, download fetch was never called
        assert not any(call.kwargs.get("video_limit") is not None or call.kwargs.get("shorts_limit") is not None for call in mock_fetch.call_args_list)


def test_sync_all_content_confirmation_dialog_confirm(mock_app):
    """Verify that confirming Sync All Content proceeds with background sync."""
    view = ChannelsView(mock_app, mock_app)
    ch = Channel(name="Alpha", url="https://youtube.com/@alpha", total_videos=10, total_shorts=5)
    view.channels = [ch]
    view.refresh_list()

    mock_res = MagicMock(error=None, videos=[], total_videos=10, total_shorts=5)

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=True) as mock_dialog, \
         patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch, \
         patch("tkinter.messagebox.showinfo"):

        view.sync_data_all(confirm=True)
        mock_app.background_jobs.drain()

        mock_dialog.assert_called_once()
        # Called once for pre-check, and once for actual sync
        assert mock_fetch.call_count == 2


def test_sync_all_channels_shorts_confirmation_dialog_cancel(mock_app):
    """Verify that clicking Cancel in Sync All Channels Shorts aborts without downloading."""
    view = ChannelsView(mock_app, mock_app)
    ch = Channel(name="ShortsChan", url="https://youtube.com/@shortschan", total_shorts=45)
    view.channels = [ch]
    view.refresh_list()

    mock_res = MagicMock(error=None, videos=[], total_shorts=45)

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=False) as mock_dialog, \
         patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch:

        view.sync_all_channels_shorts(confirm=True)

        mock_dialog.assert_called_once()
        kwargs = mock_dialog.call_args[1]
        assert "Confirm Sync All Channels Shorts" in kwargs["title"]
        msg = kwargs["message"]
        assert "Shorts found across" in msg
        assert "45 Shorts found across 1 Channel." in msg
        assert "Approximately" in msg
        assert "YouTube API quota units will be consumed" in msg
        assert "10,000 units/day" not in msg

        # Pre-check was called, but download sync (shorts_limit=0) was never called
        assert not any(call.kwargs.get("shorts_limit") == 0 for call in mock_fetch.call_args_list)


def test_sync_all_channels_shorts_confirmation_dialog_confirm(mock_app):
    """Verify that confirming Sync All Channels Shorts proceeds with background sync."""
    view = ChannelsView(mock_app, mock_app)
    ch = Channel(name="ShortsChan", url="https://youtube.com/@shortschan", total_shorts=25)
    view.channels = [ch]
    view.refresh_list()

    mock_res = MagicMock(error=None, videos=[], total_shorts=25)

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=True) as mock_dialog, \
         patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch, \
         patch("tkinter.messagebox.showinfo"):

        view.sync_all_channels_shorts(confirm=True)
        mock_app.background_jobs.drain()

        mock_dialog.assert_called_once()
        # Called once for pre-check, and once for actual sync
        assert mock_fetch.call_count == 2


def test_sync_bypasses_dialog_when_confirm_false(mock_app):
    """Verify that passing confirm=False skips the confirmation dialog entirely."""
    view = ChannelsView(mock_app, mock_app)
    ch = Channel(name="Direct", url="https://youtube.com/@direct")
    view.channels = [ch]
    view.refresh_list()

    mock_res = MagicMock(error=None, videos=[], total_videos=0, total_shorts=0)

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog") as mock_dialog, \
         patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch, \
         patch("tkinter.messagebox.showinfo"):

        view.sync_data_all(confirm=False)
        mock_app.background_jobs.drain()

        mock_dialog.assert_not_called()
        mock_fetch.assert_called_once()


def test_sync_all_content_uncapped_count_and_unlimited_fetch(mock_app):
    """Verify that Sync All Content removes the 50-cap, reflects all shorts/videos in popup, and fetches uncapped."""
    view = ChannelsView(mock_app, mock_app)
    ch1 = Channel(name="Alpha", url="https://youtube.com/@alpha", total_videos=80, total_shorts=70)
    ch2 = Channel(name="Beta", url="https://youtube.com/@beta", total_videos=20, total_shorts=40)
    view.channels = [ch1, ch2]
    view.refresh_list()

    def _fetch_side_effect(url, **kwargs):
        if url == ch1.url:
            return MagicMock(error=None, videos=[], total_videos=80, total_shorts=70)
        return MagicMock(error=None, videos=[], total_videos=20, total_shorts=40)

    with patch("src.ui.views.channels_view.show_themed_confirm_dialog", return_value=True) as mock_dialog,          patch("src.ui.views.channels_view.fetch_channel_data", side_effect=_fetch_side_effect) as mock_fetch,          patch("tkinter.messagebox.showinfo"):

        view.sync_data_all(confirm=True)
        import time
        for _ in range(50):
            mock_app.background_jobs.drain()
            download_calls = [c for c in mock_fetch.call_args_list if c.kwargs.get("video_limit") == 0 and c.kwargs.get("shorts_limit") == 0]
            if len(download_calls) == 2:
                break
            time.sleep(0.02)

        mock_dialog.assert_called_once()
        msg = mock_dialog.call_args[1]["message"]
        # Total items: 80+20 videos (100) + 70+40 shorts (110) = 210 items
        assert "210 items found across 2 Channels (100 regular videos, 110 shorts)" in msg

        # Ensure download calls used video_limit=0 and shorts_limit=0 (unlimited)
        assert len(download_calls) == 2
