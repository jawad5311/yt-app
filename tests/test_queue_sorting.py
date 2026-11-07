from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord
from src.ui.views.queue_view import QueueView, format_last_played, _get_task_sort_val


def test_format_last_played_intervals():
    now = datetime(2026, 11, 6, 12, 0, 0)
    
    # None or empty
    assert format_last_played(None, now) == "Never"
    assert format_last_played("", now) == "Never"
    assert format_last_played("   ", now) == "Never"
    
    # Just now (<60s)
    t_30s = (now - timedelta(seconds=30)).isoformat()
    assert format_last_played(t_30s, now) == "Just now"

    # Minutes (<60m)
    t_15m = (now - timedelta(minutes=15)).isoformat()
    assert format_last_played(t_15m, now) == "15m ago"

    # Hours (<24h)
    t_4h = (now - timedelta(hours=4)).isoformat()
    assert format_last_played(t_4h, now) == "4h ago"

    # Days (<7d)
    t_3d = (now - timedelta(days=3)).isoformat()
    assert format_last_played(t_3d, now) == "3d ago"

    # Beyond 7 days -> Calendar format
    t_20d = (now - timedelta(days=20)).isoformat()
    assert format_last_played(t_20d, now) == "Oct 17, 2026"


def test_queue_sorting_by_watch_time():
    view = QueueView.__new__(QueueView)
    view.shorts_only = False
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.current_sort_key = None
    view.current_sort_reverse = True
    view._original_task_order = []
    view._channels = MagicMock(return_value=[])

    # Three tasks with different watch times in stats
    t1 = VideoTask(title="Video Low", url="https://youtube.com/watch?v=low")
    t2 = VideoTask(title="Video High", url="https://youtube.com/watch?v=high")
    t3 = VideoTask(title="Video Mid", url="https://youtube.com/watch?v=mid")
    view.video_tasks = [t1, t2, t3]

    store = MagicMock()
    store.find_for_task.side_effect = lambda t: {
        "https://youtube.com/watch?v=low": StatRecord(watch_seconds=50.0),
        "https://youtube.com/watch?v=high": StatRecord(watch_seconds=1200.0),
        "https://youtube.com/watch?v=mid": StatRecord(watch_seconds=300.0),
    }.get(t.url)
    view._stats = MagicMock(return_value=store)

    view.sort_btn = MagicMock()
    view.sort_dir_btn = MagicMock()

    # 1. Apply sort: watch_time (first click -> descending)
    view.apply_sort("watch_time")
    assert view.current_sort_key == "watch_time"
    assert view.current_sort_reverse is True
    # In descending: High (1200), Mid (300), Low (50)
    assert [t.title for t in view.video_tasks] == ["Video High", "Video Mid", "Video Low"]

    # 2. Click sort watch_time again -> reverses to ascending
    view.apply_sort("watch_time")
    assert view.current_sort_key == "watch_time"
    assert view.current_sort_reverse is False
    # In ascending: Low (50), Mid (300), High (1200)
    assert [t.title for t in view.video_tasks] == ["Video Low", "Video Mid", "Video High"]

    # 3. Click again -> reverses back to descending
    view.apply_sort("watch_time")
    assert view.current_sort_reverse is True
    assert [t.title for t in view.video_tasks] == ["Video High", "Video Mid", "Video Low"]


def test_queue_sorting_by_published_date():
    view = QueueView.__new__(QueueView)
    view.shorts_only = False
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.current_sort_key = None
    view.current_sort_reverse = True
    view._original_task_order = []
    view._channels = MagicMock(return_value=[])
    view._stats = MagicMock(return_value=None)
    view.sort_btn = MagicMock()
    view.sort_dir_btn = MagicMock()

    t_old = VideoTask(title="Video 2022", url="https://youtube.com/watch?v=2022", published_at="2022-01-10T12:00:00Z")
    t_new = VideoTask(title="Video 2026", url="https://youtube.com/watch?v=2026", published_at="2026-05-20T12:00:00Z")
    t_mid = VideoTask(title="Video 2024", url="https://youtube.com/watch?v=2024", published_at="2024-03-15T12:00:00Z")
    t_unknown = VideoTask(title="Video Unknown", url="https://youtube.com/watch?v=unknown", published_at="")

    view.video_tasks = [t_old, t_unknown, t_new, t_mid]

    # 1. First click: published_date -> descending (newest first, unknown at bottom)
    view.apply_sort("published_date")
    assert view.current_sort_key == "published_date"
    assert view.current_sort_reverse is True
    assert [t.title for t in view.video_tasks] == ["Video 2026", "Video 2024", "Video 2022", "Video Unknown"]

    # 2. Click again: reverse to ascending (oldest first, unknown still at bottom)
    view.apply_sort("published_date")
    assert view.current_sort_reverse is False
    assert [t.title for t in view.video_tasks] == ["Video 2022", "Video 2024", "Video 2026", "Video Unknown"]


def test_queue_sorting_by_last_played():
    view = QueueView.__new__(QueueView)
    view.shorts_only = False
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.current_sort_key = None
    view.current_sort_reverse = True
    view._original_task_order = []
    view._channels = MagicMock(return_value=[])
    view.sort_btn = MagicMock()
    view.sort_dir_btn = MagicMock()

    t_recent = VideoTask(title="Recently Watched", url="https://youtube.com/watch?v=recent")
    t_older = VideoTask(title="Older Watched", url="https://youtube.com/watch?v=older")
    t_never = VideoTask(title="Never Watched", url="https://youtube.com/watch?v=never")

    view.video_tasks = [t_older, t_never, t_recent]

    store = MagicMock()
    store.find_for_task.side_effect = lambda t: {
        "https://youtube.com/watch?v=recent": StatRecord(last_run="2026-11-06T10:00:00"),
        "https://youtube.com/watch?v=older": StatRecord(last_run="2026-11-01T10:00:00"),
        "https://youtube.com/watch?v=never": StatRecord(last_run=""),
    }.get(t.url)
    view._stats = MagicMock(return_value=store)

    # 1. Sort by last_played -> Descending (most recently played first, never played at bottom)
    view.apply_sort("last_played")
    assert view.current_sort_key == "last_played"
    assert view.current_sort_reverse is True
    assert [t.title for t in view.video_tasks] == ["Recently Watched", "Older Watched", "Never Watched"]

    # 2. Click again -> Ascending (never played / oldest played first, most recent last)
    view.apply_sort("last_played")
    assert view.current_sort_reverse is False
    assert [t.title for t in view.video_tasks] == ["Never Watched", "Older Watched", "Recently Watched"]


def test_toggle_direction_button():
    view = QueueView.__new__(QueueView)
    view.shorts_only = False
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.current_sort_key = "watch_time"
    view.current_sort_reverse = True
    view._channels = MagicMock(return_value=[])
    view._stats = MagicMock(return_value=None)
    view.sort_btn = MagicMock()
    view.sort_dir_btn = MagicMock()
    view.video_tasks = [
        VideoTask(title="A", url="https://youtube.com/watch?v=a"),
        VideoTask(title="B", url="https://youtube.com/watch?v=b"),
    ]

    # Calling toggle_sort_direction flips reverse
    view.toggle_sort_direction()
    assert view.current_sort_reverse is False
    view.sort_dir_btn.configure.assert_called_with(text="↑")

    view.toggle_sort_direction()
    assert view.current_sort_reverse is True
    view.sort_dir_btn.configure.assert_called_with(text="↓")


def test_shorts_queue_sorting_and_card_display():
    view = QueueView.__new__(QueueView)
    view.shorts_only = True
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.show_popup_notification = MagicMock()
    view.current_sort_key = None
    view.current_sort_reverse = True
    view._channels = MagicMock(return_value=[])
    view.sort_btn = MagicMock()
    view.sort_dir_btn = MagicMock()

    s1 = VideoTask(
        title="Short Alpha",
        url="https://youtube.com/shorts/alpha",
        is_short_override=True,
    )
    s2 = VideoTask(
        title="Short Beta",
        url="https://youtube.com/shorts/beta",
        is_short_override=True,
    )
    view.video_tasks = [s1, s2]

    # Stats with last_run timestamp
    now_iso = datetime.now().isoformat(timespec="seconds")
    rec_alpha = StatRecord(watch_seconds=60.0, runs=2, last_run=now_iso)
    store = MagicMock()
    store.find_for_task.side_effect = lambda t: rec_alpha if t.url == "https://youtube.com/shorts/alpha" else None
    view._stats = MagicMock(return_value=store)

    # Verify sort by watch time works for shorts
    view.apply_sort("watch_time")
    assert [t.title for t in view.video_tasks] == ["Short Alpha", "Short Beta"]

    # Verify card renders "Last Played" next to watch time in second line
    with patch("customtkinter.CTkCheckBox"), \
         patch("customtkinter.CTkLabel") as mock_label_cls, \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkProgressBar"), \
         patch("customtkinter.CTkFont"), \
         patch("customtkinter.CTkEntry"):

        mock_parent = MagicMock()
        view._build_task_card(mock_parent, index=0, position=1, task=s1, channel_enabled=True)

        lbl_call = mock_label_cls.call_args_list[0]
        text_arg = lbl_call[1].get("text")
        lines = text_arg.split("\n")
        assert len(lines) == 2
        assert "Watch Time:" not in lines[1]
        assert "Last Played:" not in lines[1]
        assert "1 min" in lines[1]

        # Verify Last Played is rendered in the third line next to watched
        stats_lbl_call = mock_label_cls.call_args_list[1]
        line3_text = stats_lbl_call[1].get("text")
        assert "watched · Last Played: Just now" in line3_text
