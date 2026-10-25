"""Tests for the pure watch-statistics models (identity, counters, formatting)."""

from typing import Any, Dict

import pytest

from src.models.video_task import VideoTask
from src.models.watch_stats import (
    RESULT_SUCCESS,
    StatRecord,
    TaskOutcome,
    Totals,
    WatchResult,
    fold_records,
    format_duration,
    format_totals_line,
    format_watch_line,
    group_records_by_channel,
    resolve_video_id,
    resolve_video_key,
    totals_for,
)

WATCH_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
RESOLVED_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def outcome(**kwargs: Any) -> TaskOutcome:
    base: Dict[str, Any] = dict(
        video_key="dQw4w9WgXcQ",
        task_key="dQw4w9WgXcQ",
        label="Timer",
        channel="Cooking",
        attempts=1,
        success=True,
        watched_seconds=60.0,
    )
    base.update(kwargs)
    return TaskOutcome(**base)


# ------------------------------------------------------------------ identity


def test_key_from_task_url_uses_video_id():
    task = VideoTask(url=WATCH_URL)
    assert resolve_video_id(task) == "dQw4w9WgXcQ"
    assert resolve_video_key(task) == "dQw4w9WgXcQ"


def test_task_url_takes_precedence_over_resolved_url():
    task = VideoTask(url=WATCH_URL)
    stale_url = "https://www.youtube.com/watch?v=stale_video_123"
    assert resolve_video_id(task, stale_url) == "dQw4w9WgXcQ"
    assert resolve_video_key(task, stale_url) == "dQw4w9WgXcQ"


def test_resolved_page_url_gives_keyword_tasks_a_real_id():
    task = VideoTask(keywords="python tutorial")
    assert resolve_video_key(task) == "task:python tutorial"
    assert resolve_video_key(task, RESOLVED_URL) == "abcdefghijk"


def test_key_falls_back_to_raw_url_then_task_label():
    assert resolve_video_key(VideoTask(url="https://example.com/watch")) == "url:https://example.com/watch"
    assert resolve_video_key(VideoTask(title="Timer")) == "task:Timer"
    assert resolve_video_key(VideoTask(keywords="timer")) == "task:timer"
    assert resolve_video_key(VideoTask()) == "task:unnamed"


def test_watch_result_bool_follows_completed():
    assert bool(WatchResult(completed=True)) is True
    assert bool(WatchResult(completed=False)) is False
    # An interrupted watch is falsy but distinguishable from a failure.
    result = WatchResult(completed=False, interrupted=True, reason="interrupted")
    assert bool(result) is False and result.interrupted is True


# ----------------------------------------------------------------- counters


def test_merge_counts_a_successful_run():
    record = StatRecord()
    record.merge(outcome(watched_seconds=120.5, ad_seconds=12.0), "2026-10-08T10:00:00")

    assert record.runs == 1
    assert record.successes == 1
    assert record.failures == 0
    assert record.attempts == 1
    assert record.watch_seconds == 120.5
    assert record.ad_seconds == 12.0
    assert record.label == "Timer"
    assert record.channel == "Cooking"
    assert record.last_result == RESULT_SUCCESS
    assert record.last_run == "2026-10-08T10:00:00"
    assert "dQw4w9WgXcQ" in record.task_keys


def test_merge_counts_failures_but_not_interruptions():
    record = StatRecord()
    record.merge(outcome(success=False), "t1")
    assert record.runs == 1 and record.failures == 1 and record.last_result == "failed"

    record.merge(outcome(success=False, interrupted=True), "t2")
    assert record.runs == 2
    assert record.failures == 1  # pressing Stop is not a failure
    assert record.last_result == "interrupted"


def test_merge_snapshots_the_channel_of_the_latest_run():
    record = StatRecord()
    record.merge(outcome(channel="Cooking"), "t1")
    record.merge(outcome(channel="Travel"), "t2")
    assert record.channel == "Travel"
    record.merge(outcome(channel=""), "t3")
    assert record.channel == ""


def test_merge_records_task_key_alias_once():
    record = StatRecord()
    record.merge(outcome(task_key="task:timer"), "t1")
    record.merge(outcome(task_key="task:timer"), "t2")
    assert record.task_keys == ["task:timer"]


def test_fold_records_sums_counters_and_aliases():
    primary = StatRecord(runs=2, successes=1, failures=1, watch_seconds=60.0, task_keys=["abc"])
    secondary = StatRecord(
        runs=1,
        successes=1,
        watch_seconds=30.0,
        last_run="2026-01-01T00:00:00",
        last_result="success",
        task_keys=["task:timer"],
    )

    fold_records(primary, secondary)

    assert primary.runs == 3
    assert primary.successes == 2
    assert primary.failures == 1
    assert primary.watch_seconds == 90.0
    assert primary.task_keys == ["abc", "task:timer"]
    assert primary.last_run == "2026-01-01T00:00:00"
    assert primary.last_result == "success"
    assert fold_records(primary, None) is primary
    assert fold_records(primary, primary) is primary


def test_from_dict_tolerates_junk_values():
    record = StatRecord.from_dict(
        {
            "runs": "7",
            "successes": None,
            "watch_seconds": "bad",
            "ad_seconds": -5,
            "channel": 5,
            "label": "ok",
            "task_keys": ["x", 3, ""],
        }
    )
    assert record.runs == 7
    assert record.successes == 0
    assert record.watch_seconds == 0.0
    assert record.ad_seconds == 0.0
    assert record.channel == ""
    assert record.label == "ok"
    assert record.task_keys == ["x"]
    assert StatRecord.from_dict(["not a record"]).runs == 0


def test_record_roundtrips_through_dict():
    record = StatRecord(runs=2, successes=1, watch_seconds=12.5, channel="A", task_keys=["abc"])
    assert StatRecord.from_dict(record.to_dict()) == record


# -------------------------------------------------------------- aggregation


def test_totals_for_sums_a_set_of_records():
    a = StatRecord(runs=3, successes=2, failures=1, watch_seconds=100.0, channel="A")
    b = StatRecord(runs=1, successes=1, watch_seconds=50.0, channel="A")

    totals = totals_for([a, b])

    assert (totals.videos, totals.runs, totals.successes, totals.failures) == (2, 4, 3, 1)
    assert totals.watch_seconds == 150.0
    assert totals_for([]) == Totals()


def test_group_records_by_channel_buckets_by_snapshot():
    records = {
        "a": StatRecord(channel="A"),
        "b": StatRecord(channel="A"),
        "c": StatRecord(channel=""),
        "d": StatRecord(channel="B"),
    }

    groups = group_records_by_channel(records)

    assert sorted(groups) == ["", "A", "B"]
    assert len(groups["A"]) == 2
    assert len(groups[""]) == 1


# ---------------------------------------------------------------- formatting


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (45, "45s"),
        (59, "59s"),
        (60, "1m 00s"),
        (65, "1m 05s"),
        (3599, "59m 59s"),
        (3600, "1h 00m"),
        (15120, "4h 12m"),
        (43440, "12h 04m"),
        (-10, "0s"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_format_watch_line_for_a_video_that_never_ran():
    assert format_watch_line(None) == "▶ Not run yet"
    assert format_watch_line(StatRecord()) == "▶ Not run yet"


def test_format_watch_line_shows_runs_time_and_last_result():
    success = StatRecord(runs=12, watch_seconds=15840, last_result="success")
    assert format_watch_line(success) == "▶ 12 full runs · 4h 24m watched · last ✅"

    single = StatRecord(runs=1, watch_seconds=30, last_result="failed")
    assert format_watch_line(single) == "▶ 1 full run · 30s watched · last ❌"

    stopped = StatRecord(runs=3, watch_seconds=61, last_result="interrupted")
    assert format_watch_line(stopped) == "▶ 3 full runs · 1m 01s watched · last ⏸"

    unknown = StatRecord(runs=2, watch_seconds=10)
    assert format_watch_line(unknown) == "▶ 2 full runs · 10s watched"


def test_format_totals_line():
    assert format_totals_line(Totals(runs=1, watch_seconds=58)) == "1 full run · 58s watched"
    assert format_totals_line(Totals(runs=27, watch_seconds=43440)) == "27 full runs · 12h 04m watched"
