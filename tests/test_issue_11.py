"""Tests for Issue 11: Live task controls and stale channel fallback."""

import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.automation import worker as worker_module
from src.automation import watching_worker as watching_module
from src.automation.worker import AutomationWorker
from src.automation.watching_worker import WatchingWorker
from src.automation.youtube_actions import watch_video
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord, TaskOutcome, WatchResult

VID_1 = "dQw4w9WgXcQ"
VID_2 = "abcdefghijk"


class FakeDriver:
    def __init__(self, current_url: str = f"https://www.youtube.com/watch?v={VID_1}"):
        self.current_url = current_url

    def execute_script(self, script, *args):
        if "pause" in script:
            return None
        return 100.0


def make_worker(tmp_path):
    config = ConfigManager(config_file=tmp_path / "settings.json")
    store = StatsStore(stats_file=tmp_path / "stats.json")
    worker = AutomationWorker(
        account=Account(email="tester@example.com", password="pw"),
        video_tasks=[],
        config_mgr=config,
        stats_store=store,
    )
    worker.driver = FakeDriver()
    return worker, store, config


def test_uncheck_active_video_stops_playback(tmp_path, monkeypatch):
    """Uncheck an active video: playback stops by the next control check."""
    worker, store, config = make_worker(tmp_path)
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Video 1", enabled=True)
    config.set_video_queue([task.to_dict()])
    worker.video_tasks = [task]

    def mock_watch_video(*args, **kwargs):
        cb = kwargs.get("should_stop_callback")
        assert cb() is None
        # Now uncheck task in config
        task_disabled = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Video 1", enabled=False)
        config.set_video_queue([task_disabled.to_dict()])
        # Next control check returns stop reason
        reason = cb()
        assert reason == "task_disabled"
        return WatchResult(completed=False, watched_seconds=15.0, reason=reason, interrupted=True)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "watch_video", mock_watch_video)

    outcome = worker._run_task(task, direct_link_fallback=True)
    assert outcome.interrupted is True
    assert outcome.watched_seconds == 15.0
    assert outcome.success is False
    assert worker.stop_event.is_set() is False


def test_remove_active_video_stops_playback_and_not_recreated(tmp_path, monkeypatch):
    """Remove an active video: playback stops and the video is not recreated."""
    worker, store, config = make_worker(tmp_path)
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Video 1", enabled=True)
    config.set_video_queue([task.to_dict()])
    worker.video_tasks = [task]

    def mock_watch_video(*args, **kwargs):
        cb = kwargs.get("should_stop_callback")
        assert cb() is None
        # Remove task from queue
        config.set_video_queue([])
        reason = cb()
        assert reason == "task_removed"
        return WatchResult(completed=False, watched_seconds=10.0, reason=reason, interrupted=True)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "watch_video", mock_watch_video)

    outcome = worker._run_task(task, direct_link_fallback=True)
    assert outcome.interrupted is True
    # Verify video was not recreated in config
    assert config.get_video_queue() == []


def test_lower_multiplier_below_accumulated_time_stops_playback(tmp_path, monkeypatch):
    """Lower an active multiplier below already accumulated watch time: playback stops."""
    worker, store, config = make_worker(tmp_path)
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={VID_1}",
        title="Video 1",
        enabled=True,
        target_multiplier=2.0,
        duration_seconds=100,
    )
    config.set_video_queue([task.to_dict()])
    worker.video_tasks = [task]

    # Pre-record 120s of watch time (1.2x)
    outcome_init = TaskOutcome(
        video_key=VID_1,
        task_key=VID_1,
        label="Video 1",
        watched_seconds=120.0,
        video_duration=100.0,
        success=True,
    )
    store.record(outcome_init)

    def mock_watch_video(*args, **kwargs):
        cb = kwargs.get("should_stop_callback")
        # Currently 120s < 2.0 * 100s -> not stopped
        assert cb() is None
        # Lower multiplier to 1.0x in config
        task_lowered = VideoTask(
            url=f"https://www.youtube.com/watch?v={VID_1}",
            title="Video 1",
            enabled=True,
            target_multiplier=1.0,
            duration_seconds=100,
        )
        config.set_video_queue([task_lowered.to_dict()])
        # Now 120s >= 1.0 * 100s -> stops!
        reason = cb()
        assert reason == "task_target_reached"
        return WatchResult(completed=False, watched_seconds=5.0, reason=reason, interrupted=True)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "watch_video", mock_watch_video)

    outcome = worker._run_task(task, direct_link_fallback=True)
    assert outcome.interrupted is True


def test_lower_max_runs_to_completed_run_count_stops_playback(tmp_path, monkeypatch):
    """Lower the maximum-run limit to the existing completed-run count: playback stops."""
    worker, store, config = make_worker(tmp_path)
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={VID_1}",
        title="Video 1",
        enabled=True,
        max_runs=5,
    )
    config.set_video_queue([task.to_dict()])
    worker.video_tasks = [task]

    # Pre-record 3 runs
    for _ in range(3):
        store.record(TaskOutcome(
            video_key=VID_1,
            task_key=VID_1,
            label="Video 1",
            watched_seconds=100.0,
            video_duration=100.0,
            playback_finished=True,
            success=True,
        ))

    def mock_watch_video(*args, **kwargs):
        cb = kwargs.get("should_stop_callback")
        assert cb() is None
        # Lower max_runs to 3 in config
        task_lowered = VideoTask(
            url=f"https://www.youtube.com/watch?v={VID_1}",
            title="Video 1",
            enabled=True,
            max_runs=3,
        )
        config.set_video_queue([task_lowered.to_dict()])
        reason = cb()
        assert reason == "task_limit_reached"
        return WatchResult(completed=False, watched_seconds=5.0, reason=reason, interrupted=True)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "watch_video", mock_watch_video)

    outcome = worker._run_task(task, direct_link_fallback=True)
    assert outcome.interrupted is True


def test_change_limit_to_zero_remains_unlimited(tmp_path, monkeypatch):
    """Change a limit to zero: it remains unlimited."""
    worker, store, config = make_worker(tmp_path)
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={VID_1}",
        title="Video 1",
        enabled=True,
        max_runs=3,
    )
    config.set_video_queue([task.to_dict()])
    worker.video_tasks = [task]

    # Pre-record 3 runs
    for _ in range(3):
        store.record(TaskOutcome(
            video_key=VID_1,
            task_key=VID_1,
            label="Video 1",
            watched_seconds=100.0,
            video_duration=100.0,
            playback_finished=True,
            success=True,
        ))

    def mock_watch_video(*args, **kwargs):
        cb = kwargs.get("should_stop_callback")
        # Lowering to 0 makes it unlimited
        task_unlimited = VideoTask(
            url=f"https://www.youtube.com/watch?v={VID_1}",
            title="Video 1",
            enabled=True,
            max_runs=0,
            target_multiplier=0.0,
        )
        config.set_video_queue([task_unlimited.to_dict()])
        assert cb() is None  # Does not stop!
        return WatchResult(completed=True, watched_seconds=30.0, reason="ended", playback_finished=True)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "watch_video", mock_watch_video)

    outcome = worker._run_task(task, direct_link_fallback=True)
    assert outcome.success is True
    assert outcome.interrupted is False


def test_clear_every_channel_original_list_does_not_return(tmp_path):
    """Clear every channel: the original channel list does not return."""
    config_file = tmp_path / "settings.json"
    config = ConfigManager(config_file=config_file)
    initial_ch = [Channel(name="Orig Channel", url="https://youtube.com/@orig")]
    config.set_channels([c.to_dict() for c in initial_ch])

    watcher = WatchingWorker(
        account=Account(email="test@test.com", password="pw"),
        channels=initial_ch,
        config_mgr=config,
    )

    # Initially has 1 channel
    assert len(watcher.get_target_channels()) == 1

    # Clear all channels in config
    config.set_channels([])

    # Must return empty list, NOT fallback to initial_ch
    target_channels = watcher.get_target_channels()
    assert target_channels == []


def test_disable_or_archive_channel_while_paused_worker_notices(tmp_path):
    """Disable or archive the active channel while paused: the worker notices."""
    config_file = tmp_path / "settings.json"
    config = ConfigManager(config_file=config_file)
    channel = Channel(name="MyChannel", url="https://youtube.com/@mych", enabled=True, archived=False)
    config.set_channels([channel.to_dict()])

    task = VideoTask(url=f"https://youtube.com/watch?v={VID_1}", channel="MyChannel")
    config.set_video_queue([task.to_dict()])

    pause_event = threading.Event()
    stop_event = threading.Event()

    driver = MagicMock()
    driver.execute_script = MagicMock(return_value=1)

    # Start paused
    pause_event.set()

    def disable_channel():
        time.sleep(0.1)
        channel.enabled = False
        config.set_channels([channel.to_dict()])

    t = threading.Thread(target=disable_channel)
    t.start()

    def check_cb():
        raw_channels = config.get_channels()
        for c in raw_channels:
            if c.get("name") == "MyChannel":
                ch_obj = Channel.from_dict(c)
                if not ch_obj.enabled or ch_obj.archived:
                    return "channel_disabled"
        return None

    res = watch_video(
        driver,
        duration_seconds=30,
        stop_event=stop_event,
        pause_event=pause_event,
        should_stop_callback=check_cb,
    )
    t.join()

    assert res.interrupted is True
    assert res.reason == "channel_disabled"


def test_measured_time_preserved_without_full_run_on_cancellation(tmp_path, monkeypatch):
    """Previously measured time remains recorded without adding a full run."""
    worker, store, config = make_worker(tmp_path)
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Video 1")
    config.set_video_queue([task.to_dict()])
    worker.video_tasks = [task]

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=False, watched_seconds=45.0, ad_seconds=5.0, reason="task_disabled", interrupted=True),
    )

    outcome = worker._run_task(task, direct_link_fallback=True)
    worker._record(outcome)

    rec = store.get(VID_1)
    assert rec is not None
    assert rec.watch_seconds == 45.0
    assert rec.ad_seconds == 5.0
    assert rec.runs == 0  # Not a full run
    assert rec.failures == 0  # Not an ordinary failure
    assert rec.last_result == "interrupted"


def test_cancelling_one_task_does_not_set_global_stop_event(tmp_path, monkeypatch):
    """Cancelling one task does not incorrectly set the global Stop event."""
    worker, store, config = make_worker(tmp_path)
    task1 = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Task 1", enabled=True)
    task2 = VideoTask(url=f"https://www.youtube.com/watch?v={VID_2}", title="Task 2", enabled=True)
    config.set_video_queue([task1.to_dict(), task2.to_dict()])
    worker.video_tasks = [task1, task2]

    # Task 1 gets cancelled via should_stop_callback, Task 2 succeeds
    current_task = {"task": None}

    def mock_watch_video(*args, **kwargs):
        if current_task["task"] == task1:
            return WatchResult(completed=False, watched_seconds=20.0, reason="task_disabled", interrupted=True)
        return WatchResult(completed=True, watched_seconds=50.0, reason="ended", playback_finished=True)

    def mock_nav(driver, t, *args, **kwargs):
        current_task["task"] = t
        driver.current_url = t.url
        return True

    fake_driver = FakeDriver()
    monkeypatch.setattr(worker_module, "setup_driver", lambda *a, **k: fake_driver)
    monkeypatch.setattr(worker_module, "perform_google_login", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "close_driver", lambda *a, **k: None)
    monkeypatch.setattr(worker_module, "navigate_to_video_organically", mock_nav)
    monkeypatch.setattr(worker_module, "watch_video", mock_watch_video)

    worker.run()

    assert worker.stop_event.is_set() is False
    rec1 = store.get(VID_1)
    rec2 = store.get(VID_2)
    assert rec1 is not None and rec1.watch_seconds == 20.0 and rec1.runs == 0
    assert rec2 is not None and rec2.runs == 1
