import pytest
from unittest.mock import MagicMock, patch

from src.models.channel import Channel, VideoGroup, group_videos_by_channel
from src.models.video_task import VideoTask
from src.utils.helpers import clean_youtube_url


def test_video_task_is_short_property():
    long_task = VideoTask(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
    )
    assert long_task.is_short is False

    short_task = VideoTask(
        url="https://www.youtube.com/shorts/3jz_b1YvQwk",
        title="Quick Cat Video",
    )
    assert short_task.is_short is True

    empty_url_task = VideoTask(title="Query Only Video")
    assert empty_url_task.is_short is False


def test_clean_youtube_url_as_short():
    # Long-form video URL with extra parameters cleaned to watch
    raw_watch = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s&feature=share"
    assert clean_youtube_url(raw_watch, as_short=False) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Converting watch URL to canonical shorts URL
    assert clean_youtube_url(raw_watch, as_short=True) == "https://www.youtube.com/shorts/dQw4w9WgXcQ"

    # Cleaning a shorts URL with query parameters
    raw_short = "https://www.youtube.com/shorts/3jz_b1YvQwk?feature=share"
    assert clean_youtube_url(raw_short, as_short=True) == "https://www.youtube.com/shorts/3jz_b1YvQwk"
    # When as_short=False, standard cleaner converts to watch?v= format
    assert clean_youtube_url(raw_short, as_short=False) == "https://www.youtube.com/watch?v=3jz_b1YvQwk"


def test_channel_grouping_shorts_and_videos():
    channel1 = Channel(name="Tech Channel", url="https://www.youtube.com/@tech")
    channel2 = Channel(name="Gaming Channel", url="https://www.youtube.com/@gaming")

    tasks = [
        VideoTask(url="https://www.youtube.com/watch?v=vid1", title="Tech Video 1", channel="Tech Channel"),
        VideoTask(url="https://www.youtube.com/shorts/short1", title="Tech Short 1", channel="Tech Channel"),
        VideoTask(url="https://www.youtube.com/shorts/short2", title="Gaming Short 1", channel="Gaming Channel"),
        VideoTask(url="https://www.youtube.com/watch?v=vid2", title="Gaming Video 1", channel="Gaming Channel"),
    ]

    groups = group_videos_by_channel(tasks, [channel1, channel2])

    # Filter for Videos Queue
    videos_by_group = {
        g.name: [(idx, t) for (idx, t) in g.videos if not t.is_short]
        for g in groups
    }
    assert len(videos_by_group["Tech Channel"]) == 1
    assert videos_by_group["Tech Channel"][0][1].title == "Tech Video 1"
    assert len(videos_by_group["Gaming Channel"]) == 1
    assert videos_by_group["Gaming Channel"][0][1].title == "Gaming Video 1"

    # Filter for Shorts Queue
    shorts_by_group = {
        g.name: [(idx, t) for (idx, t) in g.videos if t.is_short]
        for g in groups
    }
    assert len(shorts_by_group["Tech Channel"]) == 1
    assert shorts_by_group["Tech Channel"][0][1].title == "Tech Short 1"
    assert len(shorts_by_group["Gaming Channel"]) == 1
    assert shorts_by_group["Gaming Channel"][0][1].title == "Gaming Short 1"


def test_importing_shorts_updates_shorts_queue_view():
    from src.ui.views.queue_view import QueueView

    mock_app = MagicMock()
    mock_app.config_mgr = MagicMock()
    mock_app.config_mgr.get_video_queue.return_value = []
    mock_app.config_mgr.get_channels.return_value = []
    mock_app.stats_store = MagicMock()
    mock_app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    # Instantiate shorts_queue view
    view = QueueView.__new__(QueueView)
    view.shorts_only = True
    view.video_tasks = []
    view.collapsed_groups = set()
    view.app_controller = mock_app
    view.scroll_frame = MagicMock()
    view.scroll_frame.winfo_children.return_value = []
    view._channels = MagicMock(return_value=[])
    view._render_group = MagicMock()

    # Initially empty
    assert len([t for t in view.video_tasks if t.is_short]) == 0

    # Imported shorts are added to app_controller.queued_video_tasks
    imported_short = VideoTask(
        url="https://www.youtube.com/shorts/imported_short1",
        title="Imported Short 1",
        channel="MyChannel",
    )
    mock_app.queued_video_tasks = [imported_short]

    # Calling refresh_list should sync from app_controller.queued_video_tasks
    view.refresh_list()

    matching = [t for t in view.video_tasks if t.is_short]
    assert len(matching) == 1
    assert matching[0].title == "Imported Short 1"
    assert matching[0].url == "https://www.youtube.com/shorts/imported_short1"
    view._render_group.assert_called_once()
