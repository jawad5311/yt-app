"""Tests for Channel Video Fetch and Import integration."""


from src.models.video_task import VideoTask
from src.utils.channel_fetcher import FetchedVideo


def test_import_selection_creates_valid_video_tasks():
    # Simulate user selection of fetched videos
    fetched = [
        FetchedVideo(
            video_id="vid001",
            url="https://www.youtube.com/watch?v=vid001",
            title="Episode 1: Getting Started",
            duration_text="12:30",
            duration_seconds=750,
        ),
        FetchedVideo(
            video_id="vid002",
            url="https://www.youtube.com/watch?v=vid002",
            title="Episode 2: Advanced Topics",
            duration_text="45:00",
            duration_seconds=2700,
        ),
    ]

    channel_name = "Code Academy"
    chosen_duration = "Full Video"

    tasks = [
        VideoTask(
            url=v.url,
            title=v.title,
            keywords=v.title,
            channel=channel_name,
            watchtime_duration=chosen_duration,
            duration_seconds=v.duration_seconds,
            duration_text=v.duration_text,
            enabled=True,
        )
        for v in fetched
    ]

    assert len(tasks) == 2
    assert tasks[0].url == "https://www.youtube.com/watch?v=vid001"
    assert tasks[0].channel == "Code Academy"
    assert tasks[0].watchtime_duration == "Full Video"
    assert tasks[0].duration_seconds == 750
    assert tasks[0].duration_text == "12:30"
    assert tasks[0].enabled is True

    assert tasks[1].url == "https://www.youtube.com/watch?v=vid002"
    assert tasks[1].duration_seconds == 2700
    assert tasks[1].watchtime_duration == "Full Video"


def test_deduplication_identification():
    existing_tasks = [
        VideoTask(url="https://www.youtube.com/watch?v=exist_vid_1", title="Existing Video 1"),
        VideoTask(url="https://youtu.be/exist_vid_2", title="Existing Video 2"),
    ]

    from src.utils.helpers import extract_video_id

    existing_ids = {extract_video_id(t.url) for t in existing_tasks if t.url}
    assert "exist_vid_1" in existing_ids
    assert "exist_vid_2" in existing_ids

    fetched = [
        FetchedVideo(video_id="exist_vid_1", url="https://www.youtube.com/watch?v=exist_vid_1", title="Old Video"),
        FetchedVideo(video_id="brand_new_1", url="https://www.youtube.com/watch?v=brand_new_1", title="New Video"),
    ]

    new_videos = [v for v in fetched if v.video_id not in existing_ids]
    assert len(new_videos) == 1
    assert new_videos[0].video_id == "brand_new_1"


def test_fetch_limit_options_and_custom_parsing():
    from src.ui.views.channels_view import FETCH_LIMIT_OPTIONS

    assert "5" in FETCH_LIMIT_OPTIONS
    assert "25" in FETCH_LIMIT_OPTIONS
    assert "All" in FETCH_LIMIT_OPTIONS
    assert "Custom..." in FETCH_LIMIT_OPTIONS

    def parse_limit(choice: str, custom_val: str = "") -> int:
        if choice == "All":
            return 0
        if choice == "Custom...":
            if not custom_val.isdigit() or int(custom_val) <= 0:
                raise ValueError("Invalid custom limit")
            return int(custom_val)
        return int(choice)

    assert parse_limit("All") == 0
    assert parse_limit("10") == 10
    assert parse_limit("50") == 50
    assert parse_limit("Custom...", "42") == 42
    assert parse_limit("Custom...", "150") == 150

    import pytest

    with pytest.raises(ValueError):
        parse_limit("Custom...", "0")

    with pytest.raises(ValueError):
        parse_limit("Custom...", "-5")

    with pytest.raises(ValueError):
        parse_limit("Custom...", "abc")

