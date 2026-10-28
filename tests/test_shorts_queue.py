import pytest
from unittest.mock import MagicMock

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
