"""Tests for Issue 13: Apply cooldown consistently across execution paths."""

from datetime import datetime, timedelta
import queue
import threading
from unittest.mock import MagicMock

import pytest

from src.automation import worker as worker_module
from src.automation.dispatcher import TaskDispatcher
from src.automation.watching_worker import WatchingWorker
from src.automation.worker import AutomationWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel, check_video_task_eligibility, is_video_task_runnable
from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord, TaskOutcome, WatchResult, is_within_cooldown

VID_1 = "dQw4w9WgXcQ"
VID_2 = "abcdefghijk"


class FakeDriver:
    current_url = f"https://www.youtube.com/watch?v={VID_1}"

    def execute_script(self, script, *args):
        return 100.0


def make_store(tmp_path):
    return StatsStore(stats_file=tmp_path / "stats.json")


def make_config(tmp_path, cooldown_hours=12.0):
    config = ConfigManager(config_file=tmp_path / "settings.json")
    config.set_setting("VideoCooldownHours", str(cooldown_hours))
    return config


def test_categorized_and_uncategorized_identical_cooldown_rules(tmp_path):
    """Categorized and uncategorized videos follow identical cooldown rules."""
    store = make_store(tmp_path)
    config = make_config(tmp_path, cooldown_hours=12.0)
    ch = Channel(name="MyChannel", url="")
    config.set_channels([ch.to_dict()])

    now = datetime(2026, 10, 26, 12, 0, 0)
    two_hours_ago = (now - timedelta(hours=2)).isoformat()

    task_cat = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Categorized", channel="MyChannel")
    task_uncat = VideoTask(url=f"https://www.youtube.com/watch?v={VID_2}", title="Uncategorized", channel="")

    # Record 2 hours ago for both
    store.record(TaskOutcome(video_key=VID_1, task_key=VID_1, label="Categorized", watched_seconds=60, playback_finished=True, success=True))
    rec1 = store.get(VID_1)
    rec1.last_completed = two_hours_ago
    store.save_record = None  # direct memory update
    store.records[VID_1] = rec1

    store.record(TaskOutcome(video_key=VID_2, task_key=VID_2, label="Uncategorized", watched_seconds=60, playback_finished=True, success=True))
    rec2 = store.get(VID_2)
    rec2.last_completed = two_hours_ago
    store.records[VID_2] = rec2

    # Under 12h cooldown, both must be ineligible
    assert is_video_task_runnable(task_cat, [ch], store, cooldown_hours=12.0, current_time=now) is False
    assert is_video_task_runnable(task_uncat, [ch], store, cooldown_hours=12.0, current_time=now) is False

    # Under 1h cooldown (expired), both must be eligible
    assert is_video_task_runnable(task_cat, [ch], store, cooldown_hours=1.0, current_time=now) is True
    assert is_video_task_runnable(task_uncat, [ch], store, cooldown_hours=1.0, current_time=now) is True


def test_video_inside_window_rejected():
    """A video inside the cooldown window is rejected."""
    now = datetime(2026, 10, 26, 12, 0, 0)
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    is_cool, remaining = is_within_cooldown(one_hour_ago, 12.0, current_time=now)
    assert is_cool is True
    assert round(remaining, 1) == 11.0


def test_video_exactly_at_expiry_eligible():
    """A video exactly at expiry is eligible."""
    now = datetime(2026, 10, 26, 12, 0, 0)
    exact_expiry = (now - timedelta(hours=12.0)).isoformat()
    is_cool, remaining = is_within_cooldown(exact_expiry, 12.0, current_time=now)
    assert is_cool is False
    assert remaining == 0.0


def test_video_after_expiry_eligible():
    """A video after expiry is eligible."""
    now = datetime(2026, 10, 26, 12, 0, 0)
    after_expiry = (now - timedelta(hours=12.1)).isoformat()
    is_cool, remaining = is_within_cooldown(after_expiry, 12.0, current_time=now)
    assert is_cool is False
    assert remaining == 0.0


def test_zero_disables_cooldown():
    """Zero disables the cooldown restriction."""
    now = datetime(2026, 10, 26, 12, 0, 0)
    one_min_ago = (now - timedelta(minutes=1)).isoformat()
    is_cool, remaining = is_within_cooldown(one_min_ago, 0.0, current_time=now)
    assert is_cool is False
    assert remaining == 0.0


def test_fractional_hours_work():
    """Fractional hours work (e.g. 0.5 hours = 30 minutes)."""
    now = datetime(2026, 10, 26, 12, 0, 0)
    twenty_min_ago = (now - timedelta(minutes=20)).isoformat()
    forty_min_ago = (now - timedelta(minutes=40)).isoformat()

    # 20 min ago with 0.5h (30 min) cooldown -> inside cooldown window
    is_cool, remaining = is_within_cooldown(twenty_min_ago, 0.5, current_time=now)
    assert is_cool is True
    assert round(remaining, 2) == round(10 / 60, 2)

    # 40 min ago with 0.5h (30 min) cooldown -> expired
    is_cool, remaining = is_within_cooldown(forty_min_ago, 0.5, current_time=now)
    assert is_cool is False


def test_missing_timestamps_follow_documented_policy():
    """Missing timestamps are treated as not on cooldown."""
    assert is_within_cooldown("", 12.0)[0] is False
    assert is_within_cooldown(None, 12.0)[0] is False


def test_dashboard_counts_agree_with_worker_eligibility(tmp_path):
    """Dashboard counts agree with worker eligibility."""
    from src.ui.app import YTBotApp

    config = make_config(tmp_path, cooldown_hours=12.0)
    store = make_store(tmp_path)
    now = datetime.now()

    # Task 1: in cooldown (watched 1h ago)
    t1 = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Cooldown Video")
    store.record(TaskOutcome(video_key=VID_1, task_key=VID_1, watched_seconds=60, playback_finished=True, success=True))
    rec1 = store.get(VID_1)
    rec1.last_completed = (now - timedelta(hours=1)).isoformat()
    store.records[VID_1] = rec1

    # Task 2: expired cooldown (watched 15h ago)
    t2 = VideoTask(url=f"https://www.youtube.com/watch?v={VID_2}", title="Expired Video")
    store.record(TaskOutcome(video_key=VID_2, task_key=VID_2, watched_seconds=60, playback_finished=True, success=True))
    rec2 = store.get(VID_2)
    rec2.last_completed = (now - timedelta(hours=15)).isoformat()
    store.records[VID_2] = rec2

    # Task 3: never run
    t3 = VideoTask(url="https://www.youtube.com/watch?v=newvideo1234", title="Fresh Video")

    app = MagicMock(spec=YTBotApp)
    app.config_mgr = config
    app.stats_store = store
    app.queued_video_tasks = [t1, t2, t3]

    # Bound get_runnable_video_count method from class
    count = YTBotApp.get_runnable_video_count(app)
    # Only t2 and t3 are runnable (t1 is in cooldown)
    assert count == 2


def test_random_selection_returns_none_when_all_candidates_blocked(tmp_path):
    """If every random candidate is blocked, selection returns no candidate (no fallback)."""
    config = make_config(tmp_path, cooldown_hours=12.0)
    store = make_store(tmp_path)
    now = datetime.now()
    recent_ts = (now - timedelta(hours=2)).isoformat()

    # Pre-record both candidates on cooldown
    store.record(TaskOutcome(video_key=VID_1, task_key=VID_1, watched_seconds=50, playback_finished=True, success=True))
    r1 = store.get(VID_1)
    r1.last_completed = recent_ts
    store.records[VID_1] = r1

    store.record(TaskOutcome(video_key=VID_2, task_key=VID_2, watched_seconds=50, playback_finished=True, success=True))
    r2 = store.get(VID_2)
    r2.last_completed = recent_ts
    store.records[VID_2] = r2

    worker = WatchingWorker(
        account=Account(email="test@test.com", password="pw"),
        channels=[],
        config_mgr=config,
        stats_store=store,
    )

    candidates = [
        {"href": f"/watch?v={VID_1}", "title": "Card 1"},
        {"href": f"/watch?v={VID_2}", "title": "Card 2"},
    ]

    # Both candidates are in cooldown: must return None (no fallback)
    selected = worker._find_random_suggested_video(
        candidates=candidates,
        current_url="",
        session_watched_keys=set(),
        exclude_target_channels=False,
    )
    assert selected is None


def test_worker_rechecks_cooldown_after_another_worker_records(tmp_path):
    """A worker rechecks cooldown after another worker records the same video."""
    store = make_store(tmp_path)
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="Shared Video")

    dispatcher = TaskDispatcher([task], randomize=False, stats_store=store, cooldown_hours=12.0)
    dispatcher.register_worker(1)
    dispatcher.register_worker(2)

    # Worker 1 acquires task
    t1 = dispatcher.acquire_next_task(worker_id=1)
    assert t1 is not None

    # Worker 1 finishes and records outcome
    now_ts = datetime.now().isoformat()
    store.record(TaskOutcome(video_key=VID_1, task_key=VID_1, watched_seconds=60, playback_finished=True, success=True))
    dispatcher.release_task(t1)

    # Worker 2 now attempts to acquire -> should see task is on cooldown and return None
    t2 = dispatcher.acquire_next_task(worker_id=2, poll_interval=0.05)
    assert t2 is None


def test_worker_logs_actual_skip_reason_not_disabled_channel(tmp_path, monkeypatch):
    """Worker logs the actual reason for skipping a video (e.g. cooldown) instead of disabled channel."""
    config = make_config(tmp_path, cooldown_hours=12.0)
    store = make_store(tmp_path)
    now = datetime.now()

    # Video completed 1 hour ago
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VID_1}", title="My Video", channel="")
    config.set_video_queue([task.to_dict()])
    store.record(TaskOutcome(video_key=VID_1, task_key=VID_1, watched_seconds=60, playback_finished=True, success=True))
    rec = store.get(VID_1)
    rec.last_completed = (now - timedelta(hours=1)).isoformat()
    store.records[VID_1] = rec

    log_messages = []
    log_q = queue.Queue()

    worker = AutomationWorker(
        account=Account(email="test@example.com", password="pw"),
        video_tasks=[task],
        config_mgr=config,
        stats_store=store,
        log_queue=log_q,
    )

    monkeypatch.setattr(worker_module, "setup_driver", lambda *a, **k: FakeDriver())
    monkeypatch.setattr(worker_module, "perform_google_login", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "close_driver", lambda *a, **k: None)

    worker.run()

    while not log_q.empty():
        log_messages.append(log_q.get())

    # Verify log output mentioned cooldown, NOT "channel is unchecked/disabled"
    cooldown_logs = [m for m in log_messages if "cooldown" in str(m).lower()]
    assert len(cooldown_logs) > 0
    assert not any("channel '' is unchecked/disabled" in str(m) for m in log_messages)
