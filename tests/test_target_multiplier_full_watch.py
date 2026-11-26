"""Tests for Target Multiplier Full Watch & Post-Watch Update.

Verifies:
1. An actively playing targeted video is NEVER closed or stopped mid-playback
   when reaching its target multiplier or channel target.
2. The target multiplier is only calculated at the end of the video when successfully watched.
3. If the achieved multiplier is greater than the set multiplier (e.g. 3.5x > 3.0x),
   the task's target multiplier is updated atomically in settings and emitted to the UI.
4. VideoTask.is_target_reached correctly recognizes the achieved multiplier as completed.
"""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord, TaskOutcome, WatchResult


def test_active_video_not_stopped_mid_playback_when_target_multiplier_reached(tmp_path):
    """Actively playing targeted video is NOT stopped mid-playback when watch time reaches target multiplier."""
    config_file = tmp_path / "settings.json"
    stats_file = tmp_path / "stats.json"

    cfg = ConfigManager(config_file=config_file)
    stats = StatsStore(stats_file=stats_file)

    task = VideoTask(
        url="https://www.youtube.com/watch?v=anemia123",
        title="Anemia in CKD, Part 2",
        channel="The Kidney Nutrition Institute",
        duration_seconds=2112,  # 35:12
        target_multiplier=3.0,
        enabled=True,
    )
    cfg.set_video_queue([task.to_dict()])

    # Record 2 runs previously (4224s = 2.0x)
    stats.record(TaskOutcome(
        video_key="anemia123",
        task_key="anemia123",
        label="Anemia in CKD, Part 2",
        channel="The Kidney Nutrition Institute",
        watched_seconds=4224.0,
        video_duration=2112.0,
        playback_finished=True,
        success=True,
        runs=2,
    ))

    account = MagicMock()
    account.email = "test@example.com"

    worker = WatchingWorker(
        account=account,
        channels=[Channel(name="The Kidney Nutrition Institute", url="")],
        config_mgr=cfg,
        stats_store=stats,
        stop_event=threading.Event(),
        pause_event=threading.Event(),
        log_queue=queue.Queue(),
    )
    worker.driver = MagicMock()

    # Capture the should_stop_callback passed to watch_video
    captured_cb = None

    def mock_watch_video(*args, **kwargs):
        nonlocal captured_cb
        captured_cb = kwargs.get("should_stop_callback")
        # Simulate active watch delta advancing past 3.0x during playback
        # 4224 + 2112 = 6336s (3.0x reached mid-playback)
        worker.channel_tracker.add_watch_delta("The Kidney Nutrition Institute", 2112.0)

        # Call should_stop_callback: it must NOT return "video_target_achieved"!
        res = captured_cb() if captured_cb else None
        assert res is False or res is None, f"Expected playback to continue, but should_stop returned {res}"

        return WatchResult(
            completed=True,
            watched_seconds=2112.0,
            video_duration=2112.0,
            reason="completion_cutoff",
            playback_finished=True,
        )

    with patch("src.automation.watching_worker.watch_video", side_effect=mock_watch_video), \
         patch.object(worker, "_check_account_login_before_play", return_value=True), \
         patch.object(worker, "click_home_video", return_value=True), \
         patch("src.automation.watching_worker.ensure_video_playing", return_value=True), \
         patch("src.automation.watching_worker.ensure_video_at_start", return_value=True), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs", return_value=None), \
         patch("src.automation.watching_worker.human_like_delay", return_value=None):

        worker.driver.current_url = "https://www.youtube.com/watch?v=anemia123"
        card_info = {"url": "https://www.youtube.com/watch?v=anemia123", "title": "Anemia in CKD, Part 2"}

        watch_res = worker._play_target_video(
            card_info=card_info,
            full_url="https://www.youtube.com/watch?v=anemia123",
            title="Anemia in CKD, Part 2",
            channel_display="The Kidney Nutrition Institute",
            session_watched_keys=set(),
            is_home=True,
        )

        assert watch_res.completed is True
        assert watch_res.reason == "completion_cutoff"


def test_target_multiplier_updated_at_end_of_video_when_exceeded(tmp_path):
    """When a video finishes and achieved multiplier > set multiplier, target_multiplier updates."""
    config_file = tmp_path / "settings.json"
    stats_file = tmp_path / "stats.json"

    cfg = ConfigManager(config_file=config_file)
    stats = StatsStore(stats_file=stats_file)

    # Video duration: 2000s, set target multiplier: 3.0x (target watch time = 6000s)
    task = VideoTask(
        url="https://www.youtube.com/watch?v=vid35x12345",
        title="Sample Health Video",
        channel="The Kidney Nutrition Institute",
        duration_seconds=2000,
        target_multiplier=3.0,
        enabled=True,
    )
    cfg.set_video_queue([task.to_dict()])

    # Previous watch: 5000s (2.5x)
    stats.record(TaskOutcome(
        video_key="vid35x12345",
        task_key="vid35x12345",
        label="Sample Health Video",
        channel="The Kidney Nutrition Institute",
        watched_seconds=5000.0,
        video_duration=2000.0,
        playback_finished=True,
        success=True,
        runs=2,
    ))

    account = MagicMock()
    account.email = "test@example.com"
    event_q = queue.Queue()

    worker = WatchingWorker(
        account=account,
        channels=[Channel(name="The Kidney Nutrition Institute", url="")],
        config_mgr=cfg,
        stats_store=stats,
        stop_event=threading.Event(),
        pause_event=threading.Event(),
        log_queue=queue.Queue(),
        event_queue=event_q,
    )
    worker.driver = MagicMock()

    # Now watching 2000s in this session -> total watch = 5000 + 2000 = 7000s
    # 7000 / 2000 = 3.5x achieved multiplier!
    def mock_watch(*args, **kwargs):
        return WatchResult(
            completed=True,
            watched_seconds=2000.0,
            video_duration=2000.0,
            reason="ended",
            playback_finished=True,
        )

    with patch("src.automation.watching_worker.watch_video", side_effect=mock_watch), \
         patch.object(worker, "_check_account_login_before_play", return_value=True), \
         patch.object(worker, "click_home_video", return_value=True), \
         patch("src.automation.watching_worker.ensure_video_playing", return_value=True), \
         patch("src.automation.watching_worker.ensure_video_at_start", return_value=True), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs", return_value=None), \
         patch("src.automation.watching_worker.human_like_delay", return_value=None):

        worker.driver.current_url = "https://www.youtube.com/watch?v=vid35x12345"
        card_info = {"url": "https://www.youtube.com/watch?v=vid35x12345", "title": "Sample Health Video"}

        watch_res = worker._play_target_video(
            card_info=card_info,
            full_url="https://www.youtube.com/watch?v=vid35x12345",
            title="Sample Health Video",
            channel_display="The Kidney Nutrition Institute",
            session_watched_keys=set(),
            is_home=True,
        )

        assert watch_res.completed is True

        # Check that config_mgr queue has the updated target_multiplier of 3.5
        updated_queue = cfg.get_video_queue()
        assert len(updated_queue) == 1
        updated_task = VideoTask.from_dict(updated_queue[0])
        assert updated_task.target_multiplier == 3.5

        # Check event emitted to update UI
        events = []
        while not event_q.empty():
            events.append(event_q.get_nowait())
        mult_events = [e for e in events if e[0] == "update_task_target_multiplier"]
        assert len(mult_events) == 1
        assert mult_events[0][2] == 3.5


def test_target_multiplier_not_updated_if_below_set_multiplier(tmp_path):
    """When a video finishes and achieved multiplier <= set multiplier, target_multiplier remains unchanged."""
    config_file = tmp_path / "settings.json"
    stats_file = tmp_path / "stats.json"

    cfg = ConfigManager(config_file=config_file)
    stats = StatsStore(stats_file=stats_file)

    task = VideoTask(
        url="https://www.youtube.com/watch?v=vid1x",
        title="Sample Video 1x",
        channel="The Kidney Nutrition Institute",
        duration_seconds=2000,
        target_multiplier=3.0,
        enabled=True,
    )
    cfg.set_video_queue([task.to_dict()])

    account = MagicMock()
    account.email = "test@example.com"
    event_q = queue.Queue()

    worker = WatchingWorker(
        account=account,
        channels=[Channel(name="The Kidney Nutrition Institute", url="")],
        config_mgr=cfg,
        stats_store=stats,
        stop_event=threading.Event(),
        pause_event=threading.Event(),
        log_queue=queue.Queue(),
        event_queue=event_q,
    )
    worker.driver = MagicMock()

    # First watch of 2000s -> 2000 / 2000 = 1.0x (<= 3.0x set target)
    def mock_watch(*args, **kwargs):
        return WatchResult(
            completed=True,
            watched_seconds=2000.0,
            video_duration=2000.0,
            reason="ended",
            playback_finished=True,
        )

    with patch("src.automation.watching_worker.watch_video", side_effect=mock_watch), \
         patch.object(worker, "_check_account_login_before_play", return_value=True), \
         patch.object(worker, "click_home_video", return_value=True), \
         patch("src.automation.watching_worker.ensure_video_playing", return_value=True), \
         patch("src.automation.watching_worker.ensure_video_at_start", return_value=True), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs", return_value=None), \
         patch("src.automation.watching_worker.human_like_delay", return_value=None):

        worker.driver.current_url = "https://www.youtube.com/watch?v=vid1x"
        card_info = {"url": "https://www.youtube.com/watch?v=vid1x", "title": "Sample Video 1x"}

        worker._play_target_video(
            card_info=card_info,
            full_url="https://www.youtube.com/watch?v=vid1x",
            title="Sample Video 1x",
            channel_display="The Kidney Nutrition Institute",
            session_watched_keys=set(),
            is_home=True,
        )

        # Target multiplier should remain at set value 3.0
        updated_queue = cfg.get_video_queue()
        updated_task = VideoTask.from_dict(updated_queue[0])
        assert updated_task.target_multiplier == 3.0

        # No update_task_target_multiplier event emitted
        events = []
        while not event_q.empty():
            events.append(event_q.get_nowait())
        mult_events = [e for e in events if e[0] == "update_task_target_multiplier"]
        assert len(mult_events) == 0


def test_video_task_is_target_reached_with_updated_multiplier():
    """VideoTask.is_target_reached correctly recognizes when watch time achieves or equals updated multiplier."""
    task = VideoTask(
        url="https://www.youtube.com/watch?v=testvid",
        title="Test Vid",
        duration_seconds=2000,
        target_multiplier=3.5,
    )

    # At 7000s / 2000s = 3.5x -> target reached!
    assert task.is_target_reached(current_runs=3, watch_seconds=7000.0, video_duration=2000.0) is True

    # A target is reached only after the exact watch-time threshold is met.
    assert task.is_target_reached(current_runs=3, watch_seconds=6950.0, video_duration=2000.0) is False

    # At 4000s / 2000s = 2.0x -> not reached
    assert task.is_target_reached(current_runs=2, watch_seconds=4000.0, video_duration=2000.0) is False


def test_worker_updates_task_target_multiplier(tmp_path):
    """AutomationWorker._update_task_target_multiplier updates settings.json and emits event."""
    from src.automation.worker import AutomationWorker

    config_file = tmp_path / "settings.json"
    cfg = ConfigManager(config_file=config_file)

    task = VideoTask(
        url="https://www.youtube.com/watch?v=worker12345",
        title="Worker Test Video",
        channel="Test Channel",
        duration_seconds=1000,
        target_multiplier=2.0,
        enabled=True,
    )
    cfg.set_video_queue([task.to_dict()])

    account = MagicMock()
    account.email = "worker@example.com"
    event_q = queue.Queue()

    worker = AutomationWorker(
        account=account,
        video_tasks=[task],
        config_mgr=cfg,
        worker_id=0,
        event_queue=event_q,
    )

    worker._update_task_target_multiplier(task, 3.5)

    assert task.target_multiplier == 3.5
    updated_queue = cfg.get_video_queue()
    assert updated_queue[0]["target_multiplier"] == 3.5

    events = []
    while not event_q.empty():
        events.append(event_q.get_nowait())
    mult_events = [e for e in events if e[0] == "update_task_target_multiplier"]
    assert len(mult_events) == 1
    assert mult_events[0][2] == 3.5
