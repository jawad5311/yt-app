"""
Unit tests for Shorts view counting and max_views configuration:
- Target multiplier omitted for shorts in shorts_queue
- Default max_views = 10 for shorts
- 80% duration threshold for view counting
- Continuous 5-second check
- Real-time view crediting via on_view_counted callback
"""
import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from src.automation.youtube_actions import watch_short
from src.config.stats_store import StatsStore
from src.models.video_task import DEFAULT_MAX_RUNS, DEFAULT_SHORTS_MAX_RUNS, VideoTask
from src.models.watch_stats import StatRecord, TaskOutcome, WatchResult, format_watch_line


def test_default_shorts_max_runs():
    assert DEFAULT_SHORTS_MAX_RUNS == 5
    assert DEFAULT_MAX_RUNS == 5


def test_format_watch_line_shorts_omits_multiplier():
    record = StatRecord(runs=3, watch_seconds=90.0, video_duration=30.0)
    line = format_watch_line(
        record,
        max_runs=10,
        task_duration=30.0,
        show_multiplier=False,
        unit="views",
    )
    # Should say '3/10 views -- 1m 30s watched' without '(3.0x)' multiplier
    assert "3/10 views" in line
    assert "1m 30s watched" in line
    assert "x)" not in line


def test_format_watch_line_regular_video_keeps_multiplier():
    record = StatRecord(runs=2, watch_seconds=120.0, video_duration=60.0)
    line = format_watch_line(
        record,
        max_runs=5,
        task_duration=60.0,
        show_multiplier=True,
        unit="runs",
    )
    assert "2/5 runs" in line
    assert "(2x)" in line


def test_stats_store_record_view():
    store = StatsStore.__new__(StatsStore)
    store._lock = MagicMock()
    store.records = {}
    store._save_unlocked = MagicMock()

    task = VideoTask(
        url="https://www.youtube.com/shorts/testshort123",
        title="Test Short",
        channel="MyChannel",
        max_runs=10,
    )

    rec = store.record_view(task, views=1)
    assert rec is not None
    assert rec.runs == 1
    assert rec.successes == 1
    assert rec.label == "Test Short"
    assert rec.channel == "MyChannel"
    assert store._save_unlocked.called

    # Record another view
    rec2 = store.record_view(task, views=1)
    assert rec2.runs == 2


def test_watch_short_80_percent_view_counting():
    """Verify that as soon as the short reaches 80% duration, 1 view is credited."""
    mock_driver = MagicMock()

    # Duration = 20.0s. 80% threshold = 16.0s
    # Sequence of player states:
    # 1. 0s, 2. 5s, 3. 10s, 4. 16.5s (reaches 80%), 5. 19.5s (ended)
    states = [
        {"found": True, "currentTime": 0.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 5.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 10.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 16.5, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 19.5, "duration": 20.0, "paused": False, "ended": True},
    ]

    mock_driver.execute_script.side_effect = states
    log_q = queue.Queue()
    credited_views = []

    def on_view(loop_num, cur_t, dur):
        credited_views.append((loop_num, cur_t, dur))

    with patch("src.automation.youtube_actions.ensure_video_playing"), patch("time.sleep"):
        res = watch_short(
            mock_driver,
            target_loops=1,
            log_queue=log_q,
            on_view_counted=on_view,
        )

    assert res.completed is True
    assert res.views_counted == 1
    assert len(credited_views) == 1
    assert credited_views[0][0] == 1
    assert credited_views[0][1] >= 16.0
    assert credited_views[0][2] == 20.0


def test_watch_short_under_80_percent_does_not_count_view():
    """If playback is stopped before 80% duration, view is not counted."""
    mock_driver = MagicMock()

    # Duration = 20.0s. 80% threshold = 16.0s.
    # Player stops at 12s (< 80%).
    states = [
        {"found": True, "currentTime": 2.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 8.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 12.0, "duration": 20.0, "paused": False, "ended": False},
    ]

    mock_driver.execute_script.side_effect = states
    log_q = queue.Queue()
    credited_views = []

    def on_view(loop_num, cur_t, dur):
        credited_views.append((loop_num, cur_t, dur))

    stop_ev = MagicMock()
    # Let it run 3 samples then signal stop
    call_count = 0
    def is_stopped():
        nonlocal call_count
        call_count += 1
        return call_count > 3
    stop_ev.is_set.side_effect = is_stopped

    with patch("src.automation.youtube_actions.ensure_video_playing"), patch("time.sleep"):
        res = watch_short(
            mock_driver,
            target_loops=1,
            stop_event=stop_ev,
            log_queue=log_q,
            on_view_counted=on_view,
        )

    assert res.views_counted == 0
    assert len(credited_views) == 0


def test_watch_short_multiple_loops_credits_each_80_percent():
    """If watching for 2 loops and both reach 80%, each loop credits 1 view."""
    mock_driver = MagicMock()

    # Duration = 10.0s. 80% = 8.0s.
    # Loop 1: 0s -> 5s -> 8.5s (credited) -> 9.5s -> wrap around (currentTime=1.0s)
    # Loop 2: 1s -> 8.2s (credited) -> 9.8s (ended)
    states = [
        {"found": True, "currentTime": 0.0, "duration": 10.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 5.0, "duration": 10.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 8.5, "duration": 10.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 9.5, "duration": 10.0, "paused": False, "ended": False},
        # Wrap around: currentTime drops back to 1.0s
        {"found": True, "currentTime": 1.0, "duration": 10.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 8.2, "duration": 10.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 9.8, "duration": 10.0, "paused": False, "ended": True},
    ]

    mock_driver.execute_script.side_effect = states
    credited_views = []

    def on_view(loop_num, cur_t, dur):
        credited_views.append((loop_num, cur_t, dur))

    with patch("src.automation.youtube_actions.ensure_video_playing"), patch("time.sleep"):
        res = watch_short(
            mock_driver,
            target_loops=2,
            on_view_counted=on_view,
        )

    assert res.views_counted == 2
    assert len(credited_views) == 2
    assert credited_views[0][0] == 1  # Loop 1
    assert credited_views[1][0] == 2  # Loop 2
