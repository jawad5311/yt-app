from unittest.mock import MagicMock
import pytest

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.views.queue_view import QueueView, compute_task_search_rank


def _make_dummy_queue_view(shorts_only: bool = False, channels=None):
    """Helper to instantiate QueueView without launching full application window."""
    app = MagicMock()
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_channels.return_value = [c.to_dict() for c in (channels or [])]
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = None
    app.queued_video_tasks = []
    app.on_queue_changed = MagicMock()

    view = QueueView.__new__(QueueView)
    view.app_controller = app
    view.shorts_only = shorts_only
    view.video_tasks = []
    view.collapsed_groups = set()
    view._pending_card_queue = []
    view.current_sort_key = None
    view.current_sort_reverse = False
    view._channels = lambda: list(channels or [])
    view._get_display_channels = lambda: list(channels or [])
    view.scroll_frame = MagicMock()
    view.scroll_frame.winfo_children.return_value = []
    return view


# ---------------------------------------------------------------------------
# 1. Matching & Ranking Algorithm Tests
# ---------------------------------------------------------------------------

def test_compute_task_search_rank_exact_title_match():
    """Exact substring matches in title or keywords get top priority (Rank 0)."""
    task = VideoTask(
        title="How to find any radio code",
        keywords="car radio unlock",
        channel="Iamcarhacker",
        url="https://youtube.com/watch?v=radio123",
    )
    # Exact phrase in title
    assert compute_task_search_rank(task, "radio code") == 0
    # Case-insensitive
    assert compute_task_search_rank(task, "RADIO CODE") == 0
    # Exact phrase in keywords
    assert compute_task_search_rank(task, "car radio") == 0
    # Single word in title
    assert compute_task_search_rank(task, "radio") == 0


def test_compute_task_search_rank_exact_channel_and_url_match():
    """Exact substring matches in channel name or URL get high priority (Rank 1)."""
    task = VideoTask(
        title="Oil change tutorial",
        channel="CarRepairGuru",
        url="https://youtube.com/watch?v=oil12345",
    )
    # Substring in channel name
    assert compute_task_search_rank(task, "repair") == 1
    assert compute_task_search_rank(task, "carrepairguru") == 1
    # Substring in URL
    assert compute_task_search_rank(task, "oil12345") == 1


def test_compute_task_search_rank_word_matching_any_order():
    """Words in any order match if all query words are present (Rank 2 or 3)."""
    task = VideoTask(
        title="Renault mileage fraud detected and resolved",
        keywords="odometer tampering fix",
        channel="AutoInspectors",
        url="https://youtube.com/watch?v=fraud99",
    )
    # Words in reverse order in title (Rank 2)
    assert compute_task_search_rank(task, "fraud renault") == 2
    assert compute_task_search_rank(task, "resolved detected renault") == 2

    # Words spread across title and channel (Rank 3)
    assert compute_task_search_rank(task, "renault autoinspectors") == 3
    assert compute_task_search_rank(task, "fraud inspectors") == 3


def test_compute_task_search_rank_no_match():
    """Returns None when any word in the query is missing."""
    task = VideoTask(
        title="How to find any radio code",
        channel="Iamcarhacker",
    )
    # Completely unrelated query
    assert compute_task_search_rank(task, "cooking recipe") is None
    # Partial word missing
    assert compute_task_search_rank(task, "radio bluetooth") is None


def test_compute_task_search_rank_empty_query():
    """Empty or whitespace queries return Rank 0 (matches all tasks)."""
    task = VideoTask(title="Any Video", channel="Any Channel")
    assert compute_task_search_rank(task, "") == 0
    assert compute_task_search_rank(task, "   ") == 0
    assert compute_task_search_rank(task, None) == 0


# ---------------------------------------------------------------------------
# 2. UI Components & Placement Tests
# ---------------------------------------------------------------------------

def test_search_bar_instantiated_in_both_queues(ctk_app):
    """Verify search bar, entry, icon, and clear button exist in Video Queue and Shorts Queue."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.on_queue_changed = MagicMock()
    app.stats_store = None
    app.queued_video_tasks = []

    # Video Queue
    video_view = QueueView(app, app, shorts_only=False)
    assert hasattr(video_view, "search_entry")
    assert hasattr(video_view, "search_clear_btn")
    assert "Search videos" in video_view.search_entry.placeholder_text

    # Shorts Queue
    shorts_view = QueueView(app, app, shorts_only=True)
    assert hasattr(shorts_view, "search_entry")
    assert hasattr(shorts_view, "search_clear_btn")
    assert "Search shorts" in shorts_view.search_entry.placeholder_text

    video_view.destroy()
    shorts_view.destroy()


# ---------------------------------------------------------------------------
# 3. Real-time Filtering & Priority Ordering Tests
# ---------------------------------------------------------------------------

def test_queue_filters_tasks_in_real_time(ctk_app):
    """QueueView displays only matching tasks and sorts exact matches ahead of word matches."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.on_queue_changed = MagicMock()
    app.stats_store = None
    app.queued_video_tasks = []

    view = QueueView(app, app, shorts_only=False)
    t1 = VideoTask(title="Radio security unlock guide", channel="AutoChannel")
    t2 = VideoTask(title="How to find car radio code", channel="AutoChannel")
    t3 = VideoTask(title="Brake pad replacement", channel="AutoChannel")

    view.video_tasks = [t1, t2, t3]
    view.refresh_list()

    # Search for "car radio"
    # t2 contains "car radio" exact (Rank 0)
    # t1 contains "radio" but not "car" (no match)
    # t3 contains neither
    view.search_entry.delete(0, "end")
    view.search_entry.insert(0, "car radio")
    assert view.get_search_query() == "car radio"

    view.refresh_list()
    # Check rendered task cards
    rendered_tasks = view.displayed_tasks
    assert len(rendered_tasks) == 1
    assert rendered_tasks[0].title == "How to find car radio code"

    # Search for "unlock radio" (words in any order)
    # t1 has "Radio security unlock guide" (contains both "unlock" and "radio", Rank 2)
    view.search_entry.delete(0, "end")
    view.search_entry.insert(0, "unlock radio")
    view.refresh_list()
    rendered_tasks = view.displayed_tasks
    assert len(rendered_tasks) == 1
    assert rendered_tasks[0].title == "Radio security unlock guide"

    # Search for "radio" -> matches both t1 and t2
    view.search_entry.delete(0, "end")
    view.search_entry.insert(0, "radio")
    view.refresh_list()
    rendered_tasks = view.displayed_tasks
    assert len(rendered_tasks) == 2
    assert {t.title for t in rendered_tasks} == {
        "Radio security unlock guide",
        "How to find car radio code",
    }

    view.destroy()


def test_exact_matches_prioritized_over_word_matches(ctk_app):
    """When both exact matches and word matches exist, exact matches appear first."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.on_queue_changed = MagicMock()
    app.stats_store = None
    app.queued_video_tasks = []

    view = QueueView(app, app, shorts_only=False)
    # t_word matches words "code" and "radio" in reverse order (Rank 2)
    t_word = VideoTask(title="Decode any car radio easily", channel="ChannelA")
    # t_exact matches "radio code" exact substring (Rank 0)
    t_exact = VideoTask(title="How to get radio code instantly", channel="ChannelA")

    # In underlying queue, t_word is first
    view.video_tasks = [t_word, t_exact]

    # Search for "radio code"
    view.search_entry.delete(0, "end")
    view.search_entry.insert(0, "radio code")
    view.refresh_list()

    rendered_tasks = view.displayed_tasks
    assert len(rendered_tasks) == 2
    # Exact match must be prioritized first!
    assert rendered_tasks[0].title == "How to get radio code instantly"
    assert rendered_tasks[1].title == "Decode any car radio easily"

    view.destroy()


def test_clear_search_restores_all_tasks(ctk_app):
    """Calling clear_search() or clicking clear button restores the full queue."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.on_queue_changed = MagicMock()
    app.stats_store = None
    app.queued_video_tasks = []

    view = QueueView(app, app, shorts_only=False)
    t1 = VideoTask(title="Alpha video", channel="Ch1")
    t2 = VideoTask(title="Beta video", channel="Ch1")
    view.video_tasks = [t1, t2]

    # Filter by "Alpha"
    view.search_entry.insert(0, "Alpha")
    view.refresh_list()
    assert len(view.displayed_tasks) == 1

    # Clear search
    view.clear_search()
    assert view.get_search_query() == ""
    assert len(view.displayed_tasks) == 2

    view.destroy()


def test_empty_search_results_message(ctk_app):
    """When no tasks match search query, a friendly message is shown."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.on_queue_changed = MagicMock()
    app.stats_store = None
    app.queued_video_tasks = []

    view = QueueView(app, app, shorts_only=False)
    view.video_tasks = [VideoTask(title="Cat video", channel="Ch1")]

    view.search_entry.insert(0, "nonexistent keyword 12345")
    view.refresh_list()

    # No pending cards queued
    assert len(view.displayed_tasks) == 0

    # Summary reflects 0 matches
    assert "Matching: 0 of 1" in view.get_queue_stats_summary()

    view.destroy()
