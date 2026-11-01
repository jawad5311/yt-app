"""Tests for Issue 12: Keep video reservations until watch outcomes are recorded."""

import threading
import time

from src.automation import worker as worker_module
from src.automation.dispatcher import TaskDispatcher
from src.automation.worker import AutomationWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.video_task import VideoTask
from src.models.watch_stats import WatchResult

VID = "dQw4w9WgXcQ"


class FakeDriver:
    current_url = f"https://www.youtube.com/watch?v={VID}"

    def execute_script(self, script, *args):
        return 100.0


def make_worker(tmp_path, worker_id, dispatcher, store, config):
    worker = AutomationWorker(
        account=Account(email=f"tester{worker_id}@example.com", password="pw"),
        video_tasks=[],
        config_mgr=config,
        stats_store=store,
        dispatcher=dispatcher,
        worker_id=worker_id,
    )
    worker.driver = FakeDriver()
    return worker


def test_worker_b_cannot_acquire_while_a_records_and_skips_when_max_runs_reached(tmp_path, monkeypatch):
    """Configure a video with one remaining permitted full run.
    Worker A acquires it.
    Block A's statistics recording at a controlled point.
    Start worker B's acquisition attempt.
    Verify B cannot acquire the video while A's recording is pending.
    Allow recording to finish.
    Verify B then skips the video because its limit is reached.
    Verify attempts and recorded seconds are not duplicated.
    """
    config = ConfigManager(config_file=tmp_path / "settings.json")
    store = StatsStore(stats_file=tmp_path / "stats.json")
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={VID}",
        title="Limited Run Video",
        max_runs=1,
        duration_seconds=100,
    )
    config.set_video_queue([task.to_dict()])

    dispatcher = TaskDispatcher([task], randomize=False, stats_store=store)
    worker_a = make_worker(tmp_path, worker_id=1, dispatcher=dispatcher, store=store, config=config)
    worker_b = make_worker(tmp_path, worker_id=2, dispatcher=dispatcher, store=store, config=config)

    dispatcher.register_worker(1)
    dispatcher.register_worker(2)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=True, watched_seconds=100.0, playback_finished=True, reason="ended"),
    )

    a_recording_started = threading.Event()
    allow_a_to_finish = threading.Event()
    b_acquired = []
    b_finished = threading.Event()

    orig_record = store.record

    def blocked_record(outcome):
        a_recording_started.set()
        allow_a_to_finish.wait()
        return orig_record(outcome)

    monkeypatch.setattr(store, "record", blocked_record)

    def run_worker_a():
        t = dispatcher.acquire_next_task(worker_id=1)
        assert t is not None
        outcome = worker_a._run_task(t, direct_link_fallback=True)
        worker_a._record(outcome)
        dispatcher.release_task(t)

    def run_worker_b():
        t = dispatcher.acquire_next_task(worker_id=2, poll_interval=0.05)
        b_acquired.append(t)
        b_finished.set()

    thread_a = threading.Thread(target=run_worker_a)
    thread_b = threading.Thread(target=run_worker_b)

    thread_a.start()
    assert a_recording_started.wait(timeout=5.0)

    # A is currently recording (reservation still held). Start B's acquisition attempt.
    thread_b.start()

    # Verify B cannot acquire the video while A's recording is pending
    time.sleep(0.1)
    assert not b_finished.is_set()
    assert len(b_acquired) == 0

    # Allow A's recording to finish
    allow_a_to_finish.set()
    thread_a.join(timeout=5.0)

    # Now B wakes up, rechecks limits, and skips the video because max_runs=1 is reached
    assert b_finished.wait(timeout=5.0)
    thread_b.join(timeout=5.0)

    assert b_acquired == [None]

    # Verify attempts and recorded seconds are not duplicated
    rec = store.get(VID)
    assert rec is not None
    assert rec.runs == 1
    assert rec.attempts == 1
    assert rec.watch_seconds == 100.0


def test_parallel_workers_target_multiplier_limit_respected(tmp_path, monkeypatch):
    """Repeat verification for watch-time target multiplier."""
    config = ConfigManager(config_file=tmp_path / "settings.json")
    store = StatsStore(stats_file=tmp_path / "stats.json")
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={VID}",
        title="Multiplier Video",
        target_multiplier=1.0,
        duration_seconds=60,
    )
    config.set_video_queue([task.to_dict()])

    dispatcher = TaskDispatcher([task], randomize=False, stats_store=store)
    worker_a = make_worker(tmp_path, worker_id=1, dispatcher=dispatcher, store=store, config=config)

    dispatcher.register_worker(1)
    dispatcher.register_worker(2)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=True, watched_seconds=60.0, playback_finished=True, reason="ended"),
    )

    a_recording_started = threading.Event()
    allow_a_to_finish = threading.Event()
    b_acquired = []
    b_finished = threading.Event()

    orig_record = store.record

    def blocked_record(outcome):
        a_recording_started.set()
        allow_a_to_finish.wait()
        return orig_record(outcome)

    monkeypatch.setattr(store, "record", blocked_record)

    def run_worker_a():
        t = dispatcher.acquire_next_task(worker_id=1)
        assert t is not None
        outcome = worker_a._run_task(t, direct_link_fallback=True)
        worker_a._record(outcome)
        dispatcher.release_task(t)

    def run_worker_b():
        t = dispatcher.acquire_next_task(worker_id=2, poll_interval=0.05)
        b_acquired.append(t)
        b_finished.set()

    thread_a = threading.Thread(target=run_worker_a)
    thread_b = threading.Thread(target=run_worker_b)

    thread_a.start()
    assert a_recording_started.wait(timeout=5.0)

    thread_b.start()
    time.sleep(0.1)
    assert not b_finished.is_set()

    allow_a_to_finish.set()
    thread_a.join(timeout=5.0)
    assert b_finished.wait(timeout=5.0)
    thread_b.join(timeout=5.0)

    # B skips the video because 60s >= 1.0 * 60s
    assert b_acquired == [None]
    rec = store.get(VID)
    assert rec is not None
    assert rec.watch_seconds == 60.0
    assert rec.attempts == 1


def test_recording_exceptions_do_not_deadlock_and_exclude_video(tmp_path, monkeypatch):
    """Verify recording exceptions do not deadlock waiting workers, and the failed video is excluded."""
    config = ConfigManager(config_file=tmp_path / "settings.json")
    store = StatsStore(stats_file=tmp_path / "stats.json")
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={VID}",
        title="Failing Record Video",
        max_runs=2,
    )
    config.set_video_queue([task.to_dict()])

    dispatcher = TaskDispatcher([task], randomize=False, stats_store=store)
    worker_a = make_worker(tmp_path, worker_id=1, dispatcher=dispatcher, store=store, config=config)

    dispatcher.register_worker(1)
    dispatcher.register_worker(2)

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=True, watched_seconds=30.0, playback_finished=True, reason="ended"),
    )

    def broken_record(outcome):
        raise IOError("Disk full: cannot persist stats")

    monkeypatch.setattr(store, "record", broken_record)

    b_acquired = []
    b_finished = threading.Event()

    def run_worker_a():
        t = dispatcher.acquire_next_task(worker_id=1)
        assert t is not None
        try:
            outcome = worker_a._run_task(t, direct_link_fallback=True)
            worker_a._record(outcome)
        except Exception:
            pass
        finally:
            dispatcher.release_task(t)

    def run_worker_b():
        t = dispatcher.acquire_next_task(worker_id=2, poll_interval=0.05)
        b_acquired.append(t)
        b_finished.set()

    thread_b = threading.Thread(target=run_worker_b)
    # A acquires and fails record
    thread_a = threading.Thread(target=run_worker_a)
    thread_a.start()
    time.sleep(0.05)

    thread_b.start()

    thread_a.join(timeout=5.0)
    # B must not deadlock and must finish
    assert b_finished.wait(timeout=5.0)
    thread_b.join(timeout=5.0)

    # Video was excluded due to recording failure, so B gets None
    assert b_acquired == [None]
    assert dispatcher.is_video_excluded(VID) is True


def test_stop_interrupts_waiting_worker(tmp_path):
    """Verify Stop interrupts a waiting worker."""
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VID}", title="Video")
    dispatcher = TaskDispatcher([task], randomize=False)
    dispatcher.register_worker(1)
    dispatcher.register_worker(2)

    # Worker 1 acquires task
    t1 = dispatcher.acquire_next_task(worker_id=1)
    assert t1 is not None

    stop_event = threading.Event()
    b_result = []
    b_done = threading.Event()

    def worker_b_acquire():
        res = dispatcher.acquire_next_task(worker_id=2, stop_event=stop_event, poll_interval=0.5)
        b_result.append(res)
        b_done.set()

    t = threading.Thread(target=worker_b_acquire)
    t.start()

    # B is waiting on condition
    time.sleep(0.05)
    assert not b_done.is_set()

    # Interrupt with stop_event
    stop_event.set()
    dispatcher.wake_all()

    assert b_done.wait(timeout=2.0)
    t.join(timeout=2.0)
    assert b_result == [None]
