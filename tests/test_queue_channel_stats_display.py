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
    """Empty video queue displays '0' without text labels."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = []

    summary = view.get_queue_stats_summary()
    assert summary == "0"

    header = view.get_queue_header_title()
    assert header == "Queued Target Videos (0)"

    view.update_queue_header_stats()
    view.scroll_frame.configure.assert_called_with(label_text=header)


def test_empty_shorts_queue_stats():
    """Empty shorts queue displays '0' without text labels."""
    view = _make_dummy_queue_view(shorts_only=True)
    view.video_tasks = []

    summary = view.get_queue_stats_summary()
    assert summary == "0"

    header = view.get_queue_header_title()
    assert header == "Queued Target Shorts (0)"


def test_video_queue_counts_and_breakdown():
    """Video queue displays total number and channel counts formatted as X:Y without channel names."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel="Channel A"),
        VideoTask(title="V3", url="https://youtube.com/watch?v=3", channel="Channel B"),
        # A short task should be ignored in the regular video queue
        VideoTask(title="S1", url="https://youtube.com/shorts/s1", channel="Channel A"),
    ]

    summary = view.get_queue_stats_summary()
    assert summary == "3 | 2:1"

    header = view.get_queue_header_title()
    assert header == "Queued Target Videos (3 | 2:1)"


def test_shorts_queue_counts_and_breakdown():
    """Shorts queue displays total number and channel counts formatted as X:Y without channel names."""
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
    assert summary == "3 | 1:2"

    header = view.get_queue_header_title()
    assert header == "Queued Target Shorts (3 | 1:2)"


def test_uncategorized_tasks_breakdown():
    """Tasks without an assigned channel are categorized under Uncat: [number]."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel=""),
        VideoTask(title="V3", url="https://youtube.com/watch?v=3", channel=None),
    ]

    summary = view.get_queue_stats_summary()
    assert summary == "3 | 1 | Uncat: 2"
    assert view.get_queue_header_title() == "Queued Target Videos (3 | 1 | Uncat: 2)"


def test_user_scenario_matching_image():
    """Exact test replicating the user's screenshot: 36 on Channel 1, 32 on Channel 2, 2 Uncategorized -> 70 | 36:32 | Uncat: 2."""
    channels = [
        Channel(name="The Kidney Nutrition Institute", url="https://youtube.com/@kidney"),
        Channel(name="Lifestyle Mapping", url="https://youtube.com/@lifestyle"),
    ]
    view = _make_dummy_queue_view(shorts_only=False, channels=channels)

    tasks = []
    for i in range(36):
        tasks.append(VideoTask(title=f"K{i}", url=f"https://youtube.com/watch?v=k{i}", channel="The Kidney Nutrition Institute"))
    for i in range(32):
        tasks.append(VideoTask(title=f"L{i}", url=f"https://youtube.com/watch?v=l{i}", channel="Lifestyle Mapping"))
    for i in range(2):
        tasks.append(VideoTask(title=f"U{i}", url=f"https://youtube.com/watch?v=u{i}", channel=""))

    view.video_tasks = tasks

    summary = view.get_queue_stats_summary()
    assert summary == "70 | 36:32 | Uncat: 2"

    header = view.get_queue_header_title()
    assert header == "Queued Target Videos (70 | 36:32 | Uncat: 2)"


def test_no_uncategorized_omits_uncat():
    """When there are no uncategorized videos, 'Uncat' is completely omitted from the display."""
    channels = [
        Channel(name="The Kidney Nutrition Institute", url="https://youtube.com/@kidney"),
        Channel(name="Lifestyle Mapping", url="https://youtube.com/@lifestyle"),
    ]
    view = _make_dummy_queue_view(shorts_only=False, channels=channels)

    tasks = []
    for i in range(36):
        tasks.append(VideoTask(title=f"K{i}", url=f"https://youtube.com/watch?v=k{i}", channel="The Kidney Nutrition Institute"))
    for i in range(32):
        tasks.append(VideoTask(title=f"L{i}", url=f"https://youtube.com/watch?v=l{i}", channel="Lifestyle Mapping"))

    view.video_tasks = tasks

    summary = view.get_queue_stats_summary()
    assert summary == "68 | 36:32"
    assert "Uncat" not in summary

    header = view.get_queue_header_title()
    assert header == "Queued Target Videos (68 | 36:32)"


def test_channel_order_matches_queue_display_order():
    """The breakdown order strictly matches the active channel display order in the queue."""
    ch_a = Channel(name="Channel A", url="https://youtube.com/@A", watch_seconds=100.0)
    ch_b = Channel(name="Channel B", url="https://youtube.com/@B", watch_seconds=5000.0)
    view = _make_dummy_queue_view(shorts_only=False, channels=[ch_a, ch_b])

    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel="Channel B"),
        VideoTask(title="V3", url="https://youtube.com/watch?v=3", channel="Channel B"),
    ]

    # Default order: Channel A (1), Channel B (2) -> 3 | 1:2
    view.current_sort_key = None
    assert view.get_queue_stats_summary() == "3 | 1:2"

    # Sorted by watch time: Channel B has 5000s, Channel A has 100s -> Channel B (2), Channel A (1) -> 3 | 2:1
    view.current_sort_key = "watch_time"
    view._channel_watch_time = lambda name: 5000.0 if name == "Channel B" else 100.0

    summary_sorted = view.get_queue_stats_summary()
    assert summary_sorted == "3 | 2:1"
    assert view.get_queue_header_title() == "Queued Target Videos (3 | 2:1)"


def test_dynamic_header_update_on_task_changes():
    """When tasks are added or removed, update_queue_header_stats reflects new counts."""
    view = _make_dummy_queue_view(shorts_only=False)
    view.video_tasks = [
        VideoTask(title="V1", url="https://youtube.com/watch?v=1", channel="Channel A"),
    ]

    view.update_queue_header_stats()
    assert view.queue_stats_summary == "1 | 1"
    view.scroll_frame.configure.assert_called_with(
        label_text="Queued Target Videos (1 | 1)"
    )

    # Add tasks
    view.video_tasks.append(
        VideoTask(title="V2", url="https://youtube.com/watch?v=2", channel="Channel B")
    )
    view.update_queue_header_stats()
    assert view.queue_stats_summary == "2 | 1:1"
    view.scroll_frame.configure.assert_called_with(
        label_text="Queued Target Videos (2 | 1:1)"
    )
