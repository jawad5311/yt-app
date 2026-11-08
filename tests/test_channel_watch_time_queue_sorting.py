from unittest.mock import MagicMock
import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord, Totals
from src.ui.views.queue_view import QueueView


class TestChannelWatchTimeQueueSorting:
    """Test channel-level and video-level watch time sorting in Video Queue & Shorts Queue."""

    def test_channels_sorted_by_watch_time_with_most_on_top_descending(self):
        """When sorting by watch time descending:
        - The channel with the most watch time is at the top.
        - Inside each channel, tasks are sorted highest watch time to lowest.
        """
        view = QueueView.__new__(QueueView)
        view.shorts_only = False
        view.app_controller = MagicMock()
        view.save_queue = MagicMock()
        view.refresh_list = MagicMock()
        view.current_sort_key = None
        view.current_sort_reverse = True
        view._original_task_order = []
        view.sort_btn = MagicMock()
        view.sort_dir_btn = MagicMock()
        view.sort_dir_tooltip = MagicMock()

        # Channel 1: "SmallChannel" - 100s watch time
        ch1 = Channel(name="SmallChannel")
        # Channel 2: "BigChannel" - 2000s watch time
        ch2 = Channel(name="BigChannel")
        # Channel 3: "MidChannel" - 500s watch time
        ch3 = Channel(name="MidChannel")
        view._channels = MagicMock(return_value=[ch1, ch2, ch3])

        t_small1 = VideoTask(title="Small V1", url="https://youtube.com/watch?v=s1", channel="SmallChannel")
        t_big1 = VideoTask(title="Big V1 (Low)", url="https://youtube.com/watch?v=b1", channel="BigChannel")
        t_big2 = VideoTask(title="Big V2 (High)", url="https://youtube.com/watch?v=b2", channel="BigChannel")
        t_mid1 = VideoTask(title="Mid V1", url="https://youtube.com/watch?v=m1", channel="MidChannel")
        view.video_tasks = [t_small1, t_big1, t_mid1, t_big2]

        store = MagicMock()
        store.find_for_task.side_effect = lambda t: {
            "https://youtube.com/watch?v=s1": StatRecord(watch_seconds=100.0),
            "https://youtube.com/watch?v=b1": StatRecord(watch_seconds=500.0),
            "https://youtube.com/watch?v=b2": StatRecord(watch_seconds=1500.0),
            "https://youtube.com/watch?v=m1": StatRecord(watch_seconds=500.0),
        }.get(t.url)
        store.channel_totals.side_effect = lambda name: {
            "SmallChannel": Totals(watch_seconds=100.0, runs=1),
            "BigChannel": Totals(watch_seconds=2000.0, runs=2),
            "MidChannel": Totals(watch_seconds=500.0, runs=1),
        }.get(name, Totals(0.0, 0))
        view._stats = MagicMock(return_value=store)

        # Apply sort by watch_time (first click -> descending)
        view.apply_sort("watch_time")

        assert view.current_sort_key == "watch_time"
        assert view.current_sort_reverse is True

        # Channel order check: BigChannel (2000s) on top, then MidChannel (500s), then SmallChannel (100s)
        display_channels = view._get_display_channels()
        assert [ch.name for ch in display_channels] == ["BigChannel", "MidChannel", "SmallChannel"]

        # Tasks order check:
        # BigChannel's tasks come first, sorted descending: Big V2 (1500s), Big V1 (500s)
        # MidChannel's tasks come next: Mid V1 (500s)
        # SmallChannel's tasks come last: Small V1 (100s)
        titles = [t.title for t in view.video_tasks]
        assert titles == ["Big V2 (High)", "Big V1 (Low)", "Mid V1", "Small V1"]

        # Sort arrow check: Descending -> arrow points DOWN (↓)
        view.sort_dir_btn.configure.assert_called_with(text="↓")
        assert "Descending" in view.sort_dir_tooltip.text

    def test_channels_with_most_watch_time_stays_on_top_when_ascending(self):
        """When sorting by watch time ascending:
        - The channel with the most watch time STILL stays at the top.
        - Inside each channel, tasks are sorted lowest watch time to highest.
        """
        view = QueueView.__new__(QueueView)
        view.shorts_only = False
        view.app_controller = MagicMock()
        view.save_queue = MagicMock()
        view.refresh_list = MagicMock()
        view.current_sort_key = "watch_time"
        view.current_sort_reverse = True
        view._original_task_order = []
        view.sort_btn = MagicMock()
        view.sort_dir_btn = MagicMock()
        view.sort_dir_tooltip = MagicMock()

        ch1 = Channel(name="SmallChannel")
        ch2 = Channel(name="BigChannel")
        view._channels = MagicMock(return_value=[ch1, ch2])

        t_small1 = VideoTask(title="Small V1", url="https://youtube.com/watch?v=s1", channel="SmallChannel")
        t_big1 = VideoTask(title="Big V1 (Low)", url="https://youtube.com/watch?v=b1", channel="BigChannel")
        t_big2 = VideoTask(title="Big V2 (High)", url="https://youtube.com/watch?v=b2", channel="BigChannel")
        view.video_tasks = [t_small1, t_big1, t_big2]

        store = MagicMock()
        store.find_for_task.side_effect = lambda t: {
            "https://youtube.com/watch?v=s1": StatRecord(watch_seconds=100.0),
            "https://youtube.com/watch?v=b1": StatRecord(watch_seconds=200.0),
            "https://youtube.com/watch?v=b2": StatRecord(watch_seconds=1800.0),
        }.get(t.url)
        store.channel_totals.side_effect = lambda name: {
            "SmallChannel": Totals(watch_seconds=100.0, runs=1),
            "BigChannel": Totals(watch_seconds=2000.0, runs=2),
        }.get(name, Totals(0.0, 0))
        view._stats = MagicMock(return_value=store)

        # Toggle to ascending
        view.toggle_sort_direction()
        assert view.current_sort_reverse is False

        # Channel order must STILL have BigChannel (most watch time) on top
        display_channels = view._get_display_channels()
        assert [ch.name for ch in display_channels] == ["BigChannel", "SmallChannel"]

        # Inside BigChannel, tasks are now sorted ascending: Big V1 (200s), Big V2 (1800s)
        titles = [t.title for t in view.video_tasks]
        assert titles == ["Big V1 (Low)", "Big V2 (High)", "Small V1"]

        # Sort arrow check: Ascending -> arrow points UP (↑)
        view.sort_dir_btn.configure.assert_called_with(text="↑")
        assert "Ascending" in view.sort_dir_tooltip.text

    def test_shorts_queue_sorting_by_watch_time(self):
        """Shorts queue correctly sorts shorts and channels by watch time."""
        view = QueueView.__new__(QueueView)
        view.shorts_only = True
        view.app_controller = MagicMock()
        view.save_queue = MagicMock()
        view.refresh_list = MagicMock()
        view.current_sort_key = None
        view.current_sort_reverse = True
        view._original_task_order = []
        view.sort_btn = MagicMock()
        view.sort_dir_btn = MagicMock()
        view.sort_dir_tooltip = MagicMock()

        ch_a = Channel(name="ChannelAlpha")
        ch_b = Channel(name="ChannelBeta")
        view._channels = MagicMock(return_value=[ch_a, ch_b])

        s_a1 = VideoTask(title="Alpha Short", url="https://youtube.com/shorts/a1", channel="ChannelAlpha", is_short_override=True)
        s_b1 = VideoTask(title="Beta Short 1", url="https://youtube.com/shorts/b1", channel="ChannelBeta", is_short_override=True)
        s_b2 = VideoTask(title="Beta Short 2", url="https://youtube.com/shorts/b2", channel="ChannelBeta", is_short_override=True)
        view.video_tasks = [s_a1, s_b1, s_b2]

        store = MagicMock()
        store.find_for_task.side_effect = lambda t: {
            "https://youtube.com/shorts/a1": StatRecord(watch_seconds=50.0),
            "https://youtube.com/shorts/b1": StatRecord(watch_seconds=300.0),
            "https://youtube.com/shorts/b2": StatRecord(watch_seconds=120.0),
        }.get(t.url)
        store.channel_totals.side_effect = lambda name: {
            "ChannelAlpha": Totals(watch_seconds=50.0, runs=1),
            "ChannelBeta": Totals(watch_seconds=420.0, runs=2),
        }.get(name, Totals(0.0, 0))
        view._stats = MagicMock(return_value=store)

        view.apply_sort("watch_time")

        # ChannelBeta has 420s -> top channel
        # ChannelBeta tasks sorted descending: Beta Short 1 (300s), Beta Short 2 (120s)
        # ChannelAlpha comes next: Alpha Short (50s)
        titles = [t.title for t in view.video_tasks]
        assert titles == ["Beta Short 1", "Beta Short 2", "Alpha Short"]

        # Arrow points DOWN (↓)
        view.sort_dir_btn.configure.assert_called_with(text="↓")
