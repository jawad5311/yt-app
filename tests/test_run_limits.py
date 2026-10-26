"""Tests for video task run limit (max_runs), stats checking, and GUI progress bar logic."""


from src.automation.dispatcher import TaskDispatcher
from src.config.stats_store import StatsStore
from src.models.channel import Channel, is_video_task_runnable
from src.models.video_task import VideoTask
from src.models.watch_stats import (
    StatRecord,
    TaskOutcome,
    format_watch_line,
    resolve_video_key,
)


def test_video_task_max_runs_defaults_and_roundtrip():
    # Default is 0 (unlimited)
    t = VideoTask(title="Test Video")
    assert t.max_runs == 0
    assert t.to_dict()["max_runs"] == 0

    # Serialization and deserialization
    t_limit = VideoTask(title="Limit Video", max_runs=5)
    data = t_limit.to_dict()
    assert data["max_runs"] == 5
    restored = VideoTask.from_dict(data)
    assert restored.max_runs == 5

    # from_dict handles strings and invalid inputs gracefully
    assert VideoTask.from_dict({"max_runs": "10"}).max_runs == 10
    assert VideoTask.from_dict({"max_runs": "-3"}).max_runs == 0
    assert VideoTask.from_dict({"max_runs": "invalid"}).max_runs == 0
    assert VideoTask.from_dict({}).max_runs == 0


def test_video_task_run_limit_helpers():
    t = VideoTask(title="Test", max_runs=5)
    assert t.is_run_limit_reached(0) is False
    assert t.is_run_limit_reached(4) is False
    assert t.is_run_limit_reached(5) is True
    assert t.is_run_limit_reached(6) is True

    assert t.remaining_runs(0) == 5
    assert t.remaining_runs(3) == 2
    assert t.remaining_runs(5) == 0
    assert t.remaining_runs(10) == 0

    # Unlimited (max_runs == 0)
    t_unlimited = VideoTask(title="Unlimited", max_runs=0)
    assert t_unlimited.is_run_limit_reached(0) is False
    assert t_unlimited.is_run_limit_reached(100) is False
    assert t_unlimited.remaining_runs(50) is None


def test_format_watch_line_with_run_limits():
    # Not run yet without limit
    assert format_watch_line(None, max_runs=0) == "0 runs -- 0s watched"

    # Not run yet with limit
    assert format_watch_line(None, max_runs=5) == "0/5 runs -- 0s watched"

    # Partial runs remaining
    rec = StatRecord(runs=2, watch_seconds=125.0, last_result="success")
    line = format_watch_line(rec, max_runs=5)
    assert "2/5 runs" in line
    assert "2m 05s watched" in line

    # 1 run remaining singular formatting
    rec_one_left = StatRecord(runs=4, watch_seconds=200.0)
    assert "4/5 runs" in format_watch_line(rec_one_left, max_runs=5)

    # Limit reached exactly
    rec_full = StatRecord(runs=5, watch_seconds=300.0, last_result="success")
    assert "5/5 runs" in format_watch_line(rec_full, max_runs=5)

    # Exceeded limit
    rec_over = StatRecord(runs=7, watch_seconds=420.0)
    assert "7/5 runs" in format_watch_line(rec_over, max_runs=5)


def test_is_video_task_runnable_respects_run_limit(tmp_path):
    store = StatsStore(stats_file=tmp_path / "stats.json")
    task = VideoTask(url="https://www.youtube.com/watch?v=12345678901", max_runs=5)
    v_key = resolve_video_key(task)
    channels = [Channel(name="Default")]

    # No runs recorded yet: runnable
    assert is_video_task_runnable(task, channels, stats_store=store) is True

    # Record 4 runs: still runnable (1 remaining)
    for _ in range(4):
        store.record(
            TaskOutcome(
                video_key=v_key,
                task_key=v_key,
                watched_seconds=30,
                success=True,
                attempts=1,
            )
        )
    assert is_video_task_runnable(task, channels, stats_store=store) is True

    # Record 5th run: limit of 5 is now reached in stats.json -> NOT runnable!
    store.record(
        TaskOutcome(
            video_key=v_key,
            task_key=v_key,
            watched_seconds=30,
            success=True,
            attempts=1,
        )
    )
    rec = store.find_for_task(task)
    assert rec is not None and rec.runs == 5
    assert is_video_task_runnable(task, channels, stats_store=store) is False

    # Increasing max_runs to 6 makes it runnable again
    task.max_runs = 6
    assert is_video_task_runnable(task, channels, stats_store=store) is True

    # Unlimited (max_runs == 0) makes it runnable
    task.max_runs = 0
    assert is_video_task_runnable(task, channels, stats_store=store) is True


def test_task_dispatcher_filters_out_tasks_at_run_limit(tmp_path):
    store = StatsStore(stats_file=tmp_path / "stats.json")
    t1 = VideoTask(url="https://www.youtube.com/watch?v=11111111111", max_runs=2)
    t2 = VideoTask(url="https://www.youtube.com/watch?v=22222222222", max_runs=5)
    k1 = resolve_video_key(t1)

    # Pre-record 2 runs for task1 (limit reached)
    for _ in range(2):
        store.record(TaskOutcome(video_key=k1, task_key=k1, attempts=1, success=True))

    dispatcher = TaskDispatcher(tasks=[t1, t2], randomize=False, stats_store=store)
    dispatcher.register_worker(0)

    # Worker acquires task: task1 has reached limit, so dispatcher gives task2!
    acquired = dispatcher.acquire_next_task(0)
    assert acquired is not None
    assert acquired.url == t2.url

    # No more available tasks
    assert dispatcher.acquire_next_task(0) is None


def test_length_equivalent_runs_calculation():
    from src.models.watch_stats import calculate_length_equivalent_runs

    # 100-minute video (6000 seconds)
    dur = 100 * 60.0

    # 0 watch time -> 0 runs
    assert calculate_length_equivalent_runs(0, dur) == 0

    # 90 minutes -> 1 run (started and watched, but less than 2x length)
    assert calculate_length_equivalent_runs(90 * 60, dur) == 1

    # 100 minutes -> 1 run
    assert calculate_length_equivalent_runs(100 * 60, dur) == 1

    # 190 minutes -> 1 run (less than 200m)
    assert calculate_length_equivalent_runs(190 * 60, dur) == 1

    # 200 minutes -> 2 runs (doubled length reached!)
    assert calculate_length_equivalent_runs(200 * 60, dur) == 2

    # 310 minutes -> 3 runs (tripled length reached)
    assert calculate_length_equivalent_runs(310 * 60, dur) == 3

    # 490 minutes -> 4 runs
    assert calculate_length_equivalent_runs(490 * 60, dur) == 4

    # 500 minutes -> 5 runs
    assert calculate_length_equivalent_runs(500 * 60, dur) == 5


def test_stat_record_updates_runs_when_playback_finishes(tmp_path):
    store = StatsStore(stats_file=tmp_path / "stats.json")
    task = VideoTask(url="https://www.youtube.com/watch?v=12345678901", max_runs=5)
    v_key = resolve_video_key(task)

    # Video is 100 minutes (6000s)
    dur = 6000.0

    # First session: watched 90 minutes but did NOT finish (playback_finished=False) -> 0 runs
    store.record(TaskOutcome(
        video_key=v_key,
        task_key=v_key,
        watched_seconds=5400.0,
        video_duration=dur,
        playback_finished=False,
        success=True,
        attempts=1,
    ))
    rec = store.find_for_task(task)
    assert rec is not None
    assert rec.runs == 0
    assert rec.watch_seconds == 5400.0
    assert rec.video_duration == dur

    # Second session: video playback finished! -> increments to 1 run
    store.record(TaskOutcome(
        video_key=v_key,
        task_key=v_key,
        watched_seconds=5400.0,
        video_duration=dur,
        playback_finished=True,
        success=True,
        attempts=1,
    ))
    rec = store.find_for_task(task)
    assert rec is not None
    assert rec.runs == 1

    # Third session: another partial session without finishing -> still 1 run
    store.record(TaskOutcome(
        video_key=v_key,
        task_key=v_key,
        watched_seconds=1800.0,
        video_duration=dur,
        playback_finished=False,
        success=True,
        attempts=1,
    ))
    rec = store.find_for_task(task)
    assert rec is not None
    assert rec.runs == 1

    # Fourth session: playback finishes again! -> reaches 2 runs
    store.record(TaskOutcome(
        video_key=v_key,
        task_key=v_key,
        watched_seconds=6000.0,
        video_duration=dur,
        playback_finished=True,
        success=True,
        attempts=1,
    ))
    rec = store.find_for_task(task)
    assert rec is not None
    assert rec.runs == 2


def test_app_runnable_video_count_excludes_unchecked_and_archived(tmp_path):
    from unittest.mock import MagicMock

    from src.config.manager import ConfigManager
    from src.ui.app import YTBotApp

    config = ConfigManager(config_file=tmp_path / "settings.json")
    store = StatsStore(stats_file=tmp_path / "stats.json")

    config.set_channels([
        {"name": "ActiveChannel", "enabled": True},
        {"name": "DisabledChannel", "enabled": False},
        {"name": "ArchivedChannel", "enabled": True, "archived": True},
    ])

    app = MagicMock(spec=YTBotApp)
    app.config_mgr = config
    app.stats_store = store
    app.queued_video_tasks = [
        VideoTask(title="Checked Active", channel="ActiveChannel", enabled=True),
        VideoTask(title="Unchecked Active", channel="ActiveChannel", enabled=False),
        VideoTask(title="Checked in Disabled Channel", channel="DisabledChannel", enabled=True),
        VideoTask(title="Checked in Archived Channel", channel="ArchivedChannel", enabled=True),
        VideoTask(title="Checked Uncategorized", channel="", enabled=True),
        VideoTask(title="Unchecked Uncategorized", channel="", enabled=False),
    ]

    count = YTBotApp.get_runnable_video_count(app)
    # Only "Checked Active" and "Checked Uncategorized" are runnable (2 out of 6)
    assert count == 2



def test_video_task_target_multiplier_and_max_views():
    # Video duration = 600s (10 min)
    task = VideoTask(
        title="Multiplier Video",
        duration_seconds=600.0,
        target_multiplier=2.5,
        max_runs=5,
    )

    # 1. Neither reached: 1 run (10 min = 600s watched, mult = 1.0x < 2.5x, runs = 1 < 5)
    assert task.is_target_reached(current_runs=1, watch_seconds=600.0, video_duration=600.0) is False

    # 2. Target multiplier reached: 2 runs but 1500s watched (mult = 2.5x >= 2.5x, runs = 2 < 5)
    assert task.is_target_reached(current_runs=2, watch_seconds=1500.0, video_duration=600.0) is True

    # 3. Max views cap reached: 5 runs but only 900s watched (mult = 1.5x < 2.5x, runs = 5 >= 5)
    assert task.is_target_reached(current_runs=5, watch_seconds=900.0, video_duration=600.0) is True

    # 4. Unlimited multiplier (0.0): only max_runs is checked
    task_no_mult = VideoTask(title="Views Only", max_runs=3, target_multiplier=0.0)
    assert task_no_mult.is_target_reached(current_runs=2, watch_seconds=10000.0, video_duration=600.0) is False
    assert task_no_mult.is_target_reached(current_runs=3, watch_seconds=10000.0, video_duration=600.0) is True

    # 5. Unlimited views cap (0): only target_multiplier is checked
    task_no_cap = VideoTask(title="Mult Only", max_runs=0, target_multiplier=2.0)
    assert task_no_cap.is_target_reached(current_runs=10, watch_seconds=1100.0, video_duration=600.0) is False
    assert task_no_cap.is_target_reached(current_runs=10, watch_seconds=1200.0, video_duration=600.0) is True


def test_is_video_task_runnable_respects_target_multiplier(tmp_path):
    store = StatsStore(stats_file=tmp_path / "stats.json")
    # 10 minute video (600s), target multiplier 2.0x, max_runs=10
    task = VideoTask(
        url="https://www.youtube.com/watch?v=targetmulttest",
        duration_seconds=600.0,
        target_multiplier=2.0,
        max_runs=10,
    )
    v_key = resolve_video_key(task)
    channels = [Channel(name="Default")]

    assert is_video_task_runnable(task, channels, stats_store=store) is True

    # Watch 1 run of 600s -> multiplier 1.0x < 2.0x -> still runnable
    store.record(TaskOutcome(
        video_key=v_key,
        task_key=v_key,
        watched_seconds=600.0,
        video_duration=600.0,
        playback_finished=True,
        success=True,
    ))
    assert is_video_task_runnable(task, channels, stats_store=store) is True

    # Watch another 600s -> total 1200s, multiplier 2.0x >= 2.0x target reached -> NOT runnable!
    store.record(TaskOutcome(
        video_key=v_key,
        task_key=v_key,
        watched_seconds=600.0,
        video_duration=600.0,
        playback_finished=True,
        success=True,
    ))
    assert is_video_task_runnable(task, channels, stats_store=store) is False


def test_queue_view_inline_entry_blank_and_zero_behavior():
    def parse_target(val_str, current_val):
        s = val_str.strip()
        if not s or s in ("0", "0.0", "∞"):
            return 0.0
        try:
            return max(0.0, float(s))
        except ValueError:
            return current_val

    def parse_views(val_str, current_val):
        s = val_str.strip()
        if not s or s in ("0", "∞"):
            return 0
        try:
            return max(0, int(s))
        except ValueError:
            return current_val

    assert parse_target("", 2.0) == 0.0
    assert parse_target("   ", 3.0) == 0.0
    assert parse_views("", 5) == 0
    assert parse_views("   ", 3) == 0

    assert parse_target("3", 0.0) == 3.0
    assert parse_target("2.5", 0.0) == 2.5
    assert parse_views("5", 0) == 5
    assert parse_views("10", 2) == 10

    assert parse_target("0", 3.0) == 0.0
    assert parse_views("0", 5) == 0
