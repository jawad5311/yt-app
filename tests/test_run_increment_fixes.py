import pytest
from unittest.mock import MagicMock
from src.models.video_task import VideoTask
from src.models.watch_stats import (
    StatRecord,
    TaskOutcome,
    WatchResult,
    format_watch_line,
    resolve_video_key,
)
from src.config.stats_store import StatsStore
from src.automation.youtube_actions import watch_video


def test_legacy_zero_runs_healed_on_from_dict():
    """Verify that a legacy record loaded from dict with runs=0 but watch_seconds=2.3x duration heals to 2 runs."""
    data = {
        "label": "Test Video",
        "channel": "Test Channel",
        "runs": 0,
        "successes": 2,
        "failures": 0,
        "attempts": 2,
        "watch_seconds": 2300.0,
        "ad_seconds": 15.0,
        "last_run": "2026-11-10T08:00:00",
        "last_result": "video_target_achieved",
        "last_completed": "2026-11-10T08:00:00",
        "video_duration": 1000.0,
        "task_keys": ["test_video_1"],
    }
    rec = StatRecord.from_dict(data)
    assert rec.runs == 2
    assert rec.watch_seconds == 2300.0
    assert rec.video_duration == 1000.0


def test_format_watch_line_heals_zero_runs_when_2_3x_duration():
    """Verify format_watch_line displays '2/5 runs -- ... (2.3x)' when runs was 0 but watch time is 2.3x."""
    rec = StatRecord(
        runs=0,
        watch_seconds=2300.0,
        video_duration=1000.0,
        task_keys=["test_key"],
    )
    line = format_watch_line(
        record=rec,
        max_runs=5,
        task_duration=1000.0,
        show_multiplier=True,
    )
    assert "2/5 runs" in line
    assert "(2.3x)" in line


def test_stats_store_heals_zero_runs_for_task(tmp_path):
    """Verify StatsStore.find_for_task heals runs=0 when watch_seconds >= video_duration."""
    store = StatsStore(stats_file=tmp_path / "stats.json")
    task = VideoTask(
        url="https://www.youtube.com/watch?v=heal_test_1",
        duration_seconds=500.0,
        max_runs=5,
    )
    v_key = resolve_video_key(task)
    store.records[v_key] = StatRecord(
        runs=0,
        watch_seconds=1150.0,  # 2.3x of 500s
        video_duration=500.0,
        task_keys=[v_key],
    )

    found = store.find_for_task(task)
    assert found is not None
    assert found.runs == 2


def test_watch_video_completion_reasons_set_playback_finished():
    """Verify completion_cutoff and duration_reached set playback_finished=True."""
    res_cutoff = WatchResult(
        completed=True,
        reason="completion_cutoff",
        watched_seconds=100.0,
        video_duration=100.0,
    )
    assert res_cutoff.playback_finished is True
    assert res_cutoff.completed is True
    assert res_cutoff.interrupted is False

    res_dur = WatchResult(
        completed=True,
        reason="duration_reached",
        watched_seconds=50.0,
        video_duration=100.0,
    )
    assert res_dur.playback_finished is True


def test_watch_video_target_achieved_callback():
    """Verify when should_stop_callback returns video_target_achieved, it marks playback_finished=True and not interrupted."""
    driver = MagicMock()
    # Mock player state
    driver.execute_script.return_value = {
        "found": True,
        "currentTime": 50.0,
        "duration": 100.0,
        "ended": False,
        "paused": False,
    }

    # Callback triggers video_target_achieved immediately
    res = watch_video(
        driver,
        duration_seconds=0,
        should_stop_callback=lambda: "video_target_achieved",
    )
    assert res.completed is True
    assert res.playback_finished is True
    assert res.interrupted is False
    assert res.reason == "video_target_achieved"


def test_stat_record_increments_runs_on_target_achieved():
    """Verify StatRecord.merge increments runs when playback finishes with runs specified or playback_finished=True."""
    rec = StatRecord(runs=0, watch_seconds=0.0, video_duration=100.0)
    outcome = TaskOutcome(
        video_key="v1",
        task_key="v1",
        watched_seconds=100.0,
        video_duration=100.0,
        playback_finished=True,
        success=True,
        interrupted=False,
        runs=1,
    )
    rec.merge(outcome, "2026-11-10T08:00:00")
    assert rec.runs == 1

    # Second run without explicit runs argument (falls back to playback_finished)
    outcome2 = TaskOutcome(
        video_key="v1",
        task_key="v1",
        watched_seconds=100.0,
        video_duration=100.0,
        playback_finished=True,
        success=True,
        interrupted=False,
    )
    rec.merge(outcome2, "2026-11-10T08:10:00")
    assert rec.runs == 2
