from unittest.mock import MagicMock, patch
import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.views.queue_view import QueueView


def _make_dummy_queue_view(shorts_only: bool = False, channels=None):
    """Create a QueueView instance with minimal mocking for unit testing."""
    view = QueueView.__new__(QueueView)
    view.shorts_only = shorts_only
    view.app_controller = MagicMock()
    view.video_tasks = []
    view.collapsed_groups = set()
    view.current_sort_key = None
    view.current_sort_reverse = True
    view.scroll_frame = MagicMock()
    view._label = MagicMock()
    view.scroll_frame._label = view._label

    chan_list = channels or [
        Channel(name="Channel A", url="https://youtube.com/@channelA"),
        Channel(name="Channel B", url="https://youtube.com/@channelB"),
    ]
    view._channels = MagicMock(return_value=chan_list)
    view._stats = MagicMock(return_value=None)
    view.save_queue = MagicMock()
    return view


def test_empty_video_queue_stats():
    """Empty video queue displays 'Total Videos: 0'."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = []

    summary = view.get_queue_stats_summary()
    assert summary == "Total Videos: 0"

    header = view.get_queue_header_title()
    assert header == "Queued Target Videos (Total Videos: 0)"

    view.update_queue_header_stats()
    view.scroll_frame.configure.assert_called_with(label_text=header)


def test_empty_shorts_queue_stats():
    """Empty shorts queue displays 'Total Shorts: 0'."""
    view = _make_dummy_queue_view(shorts_only=True)
    view.video_tasks = []

    summary = view.get_queue_stats_summary()
    assert summary == "Total Shorts: 0"

    header = view.get_queue_header_title()
    assert header == "Queued Target Shorts (Total Shorts: 0)"


def test_video_queue_counts_and_breakdown():
    """Video queue accurately counts regular videos and breaks down counts per channel."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel="Channel A"),
        VideoTask(title="V3", url="https://youtube.com/watch?v=3", channel="Channel B"),
        # A short task should be ignored in the regular video queue
        VideoTask(title="S1", url="https://youtube.com/shorts/s1", channel="Channel A"),
    ]

    summary = view.get_queue_stats_summary()
    assert summary == "Total Videos: 3 | Channel A: 2 | Channel B: 1"

    header = view.get_queue_header_title()
    assert header == "Queued Target Videos (Total Videos: 3 | Channel A: 2 | Channel B: 1)"


def test_shorts_queue_counts_and_breakdown():
    """Shorts queue accurately counts shorts and breaks down counts per channel."""
    channels = [
        Channel(name="Music", url="https://youtube.com/@music"),
        Channel(name="Gaming", url="https://youtube.com/@gaming"),
    ]
    view = _make_dummy_queue_view(shorts_only=True, channels=channels)
    view.video_tasks = [
        VideoTask(title="S1", url="https://youtube.com/shorts/s1", channel="Music"),
        VideoTask(title="S2", url="https://youtube.com/shorts/s2", channel="Gaming"),
        VideoTask(title="S3", url="https://youtube.com/shorts/s3", channel="Gaming"),
        # Regular video should be ignored in the shorts queue
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Music"),
    ]

    summary = view.get_queue_stats_summary()
    assert summary == "Total Shorts: 3 | Music: 1 | Gaming: 2"

    header = view.get_queue_header_title()
    assert header == "Queued Target Shorts (Total Shorts: 3 | Music: 1 | Gaming: 2)"


def test_uncategorized_tasks_breakdown():
    """Tasks without an assigned channel are categorized under Uncategorized."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel=""),
        VideoTask(title="V3", url="https://youtube.com/watch?v=3", channel=None),
    ]

    summary = view.get_queue_stats_summary()
    assert summary == "Total Videos: 3 | Channel A: 1 | Uncategorized: 2"
    assert view.get_queue_header_title() == "Queued Target Videos (Total Videos: 3 | Channel A: 1 | Uncategorized: 2)"


def test_channel_order_matches_queue_display_order():
    """The breakdown order strictly matches the active channel display order in the queue."""
    ch_a = Channel(name="Channel A", url="https://youtube.com/@A", watch_seconds=100.0)
    ch_b = Channel(name="Channel B", url="https://youtube.com/@B", watch_seconds=5000.0)
    view = _make_dummy_queue_view(shorts_only=False, channels=[ch_a, ch_b])

    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel="Channel B"),
    ]

    # Default order: Channel A, Channel B
    view.current_sort_key = None
    assert view.get_queue_stats_summary() == "Total Videos: 2 | Channel A: 1 | Channel B: 1"

    # Sorted by watch time: Channel B has 5000s, Channel A has 100s -> Channel B is first
    view.current_sort_key = "watch_time"
    view._channel_watch_time = lambda name: 5000.0 if name == "Channel B" else 100.0

    summary_sorted = view.get_queue_stats_summary()
    assert summary_sorted == "Total Videos: 2 | Channel B: 1 | Channel A: 1"
    assert view.get_queue_header_title() == "Queued Target Videos (Total Videos: 2 | Channel B: 1 | Channel A: 1)"


def test_dynamic_header_update_on_task_changes():
    """When tasks are added or removed, update_queue_header_stats reflects new counts."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
    ]

    view.update_queue_header_stats()
    assert view.queue_stats_summary == "Total Videos: 1 | Channel A: 1"
    view.scroll_frame.configure.assert_called_with(
        label_text="Queued Target Videos (Total Videos: 1 | Channel A: 1)"
    )

    # Add tasks
    view.video_tasks.append(
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel="Channel B")
    )
    view.update_queue_header_stats()
    assert view.queue_stats_summary == "Total Videos: 2 | Channel A: 1 | Channel B: 1"
    view.scroll_frame.configure.assert_called_with(
        label_text="Queued Target Videos (Total Videos: 2 | Channel A: 1 | Channel B: 1)"
    )
