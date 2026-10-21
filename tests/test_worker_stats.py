"""Worker-side recording: one recordable TaskOutcome per queued task."""

from src.automation import worker as worker_module
from src.automation.worker import AutomationWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.video_task import VideoTask
from src.models.watch_stats import TaskOutcome, WatchResult


class FakeDriver:
    current_url = ""


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
    return worker, store


def test_successful_task_records_the_resolved_video(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path)
    worker.driver.current_url = "https://www.youtube.com/watch?v=abcdefghijk"
    task = VideoTask(keywords="timer", title="Timer", channel="Cooking")

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=True, watched_seconds=42.0, ad_seconds=3.0, reason="duration_reached"),
    )

    outcome = worker._run_task(task, direct_link_fallback=True)
    worker._record(outcome)

    assert outcome.success is True
    assert outcome.attempts == 1
    assert outcome.watched_seconds == 42.0
    assert outcome.ad_seconds == 3.0
    assert outcome.channel == "Cooking"
    # Identity comes from the resolved page URL, the task key stays as alias.
    assert outcome.video_key == "abcdefghijk"
    assert outcome.task_key == "task:Timer"

    record = store.find_for_task(task)
    assert record is not None
    assert record.runs == 1
    assert record.successes == 1
    assert store.get("task:timer") is None


def test_retry_folds_into_a_single_record(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path)
    task = VideoTask(url="https://youtu.be/dQw4w9WgXcQ")
    nav_calls = {"count": 0}

    def flaky_nav(*args, **kwargs):
        nav_calls["count"] += 1
        return nav_calls["count"] > 1  # first attempt fails, retry succeeds

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", flaky_nav)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=True, watched_seconds=15.0, reason="ended"),
    )

    outcome = worker._run_task(task, direct_link_fallback=True)
    worker._record(outcome)

    assert outcome.attempts == 2
    assert outcome.success is True
    assert outcome.watched_seconds == 15.0

    record = store.get("dQw4w9WgXcQ")
    assert record.runs == 1  # one task processing, not one per attempt
    assert record.attempts == 2


def test_failed_task_records_a_single_failure(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path)
    task = VideoTask(keywords="timer")

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=False, watched_seconds=8.0, reason="no_video"),
    )

    outcome = worker._run_task(task, direct_link_fallback=True)
    worker._record(outcome)

    assert outcome.success is False
    assert outcome.interrupted is False
    assert outcome.attempts == 3
    assert outcome.watched_seconds == 24.0  # all 3 attempts contributed

    record = store.find_for_task(task)
    assert record.runs == 1
    assert record.failures == 1
    assert record.last_result == "failed"


def test_stop_during_watch_records_an_interrupted_run(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path)
    task = VideoTask(keywords="timer")

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", lambda *a, **k: True)

    def stopping_watch(*args, **kwargs):
        worker.stop_event.set()
        return WatchResult(completed=False, interrupted=True, watched_seconds=9.0, reason="interrupted")

    monkeypatch.setattr(worker_module, "watch_video", stopping_watch)

    outcome = worker._run_task(task, direct_link_fallback=True)
    worker._record(outcome)

    assert outcome.interrupted is True
    assert outcome.watched_seconds == 9.0

    record = store.find_for_task(task)
    assert record.runs == 1
    assert record.failures == 0  # pressing Stop is not a failure
    assert record.last_result == "interrupted"


def test_stopped_before_the_first_attempt_is_not_recorded(tmp_path):
    worker, store = make_worker(tmp_path)
    worker.stop_event.set()

    outcome = worker._run_task(VideoTask(keywords="timer"), direct_link_fallback=True)
    worker._record(outcome)

    assert outcome.interrupted is True
    assert outcome.attempts == 0
    assert store.all() == {}


def test_recording_is_optional(tmp_path):
    config = ConfigManager(config_file=tmp_path / "settings.json")
    worker = AutomationWorker(
        account=Account(email="tester@example.com", password="pw"),
        video_tasks=[],
        config_mgr=config,
    )
    worker.driver = FakeDriver()

    worker._record(TaskOutcome(video_key="dQw4w9WgXcQ", attempts=1, success=True))

    assert not (tmp_path / "stats.json").exists()


def test_a_stats_error_never_breaks_the_session(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path)

    def broken_record(outcome):
        raise OSError("disk full")

    monkeypatch.setattr(store, "record", broken_record)

    worker._record(TaskOutcome(video_key="dQw4w9WgXcQ", attempts=1, success=True))  # must not raise


def test_organic_retry_appends_channel_name_on_third_attempt(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path)
    task = VideoTask(title="My Video", channel="Cooking Channel")

    captured_overrides = []

    def mock_nav(*args, **kwargs):
        captured_overrides.append(kwargs.get("search_query_override"))
        if len(captured_overrides) >= 3:
            return True
        return False

    monkeypatch.setattr(worker_module, "navigate_to_video_organically", mock_nav)
    monkeypatch.setattr(
        worker_module,
        "watch_video",
        lambda *a, **k: WatchResult(completed=True, watched_seconds=10.0, ad_seconds=0.0, reason="duration_reached"),
    )

    outcome = worker._run_task(task, direct_link_fallback=False)
    assert outcome.success is True
    assert outcome.attempts == 3
    # First 2 attempts used the plain title
    assert captured_overrides[0] == "My Video"
    assert captured_overrides[1] == "My Video"
    # Third attempt appended the channel name
    assert captured_overrides[2] == "My Video Cooking Channel"

