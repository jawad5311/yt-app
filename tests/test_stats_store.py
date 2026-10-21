"""Tests for StatsStore: persistence, identity aliasing, channel cascades, concurrency."""

import json
import threading
from typing import Any, Dict

from src.config.stats_store import StatsStore
from src.models.video_task import VideoTask
from src.models.watch_stats import TaskOutcome, resolve_video_key

VIDEO_ID = "dQw4w9WgXcQ"
KEYWORD_TASK = VideoTask(keywords="timer", title="Timer", channel="Cooking")
URL_TASK = VideoTask(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", channel="Cooking")


def make_store(tmp_path, name="stats.json"):
    return StatsStore(stats_file=tmp_path / name)


def outcome(**kwargs: Any) -> TaskOutcome:
    base: Dict[str, Any] = dict(
        video_key=VIDEO_ID,
        task_key=VIDEO_ID,
        label="Timer",
        channel="Cooking",
        attempts=1,
        success=True,
        watched_seconds=60.0,
    )
    base.update(kwargs)
    return TaskOutcome(**base)


# ------------------------------------------------------------------ lifecycle


def test_missing_file_starts_empty_and_stays_untouched(tmp_path):
    store = make_store(tmp_path)
    assert store.all() == {}
    assert not store.stats_file.exists()


def test_record_persists_a_versioned_schema(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome())

    payload = json.loads(store.stats_file.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["videos"][VIDEO_ID]["runs"] == 1
    assert payload["videos"][VIDEO_ID]["channel"] == "Cooking"

    # A fresh instance reads the same counters back.
    assert make_store(tmp_path).get(VIDEO_ID).runs == 1


def test_repeated_records_accumulate(tmp_path):
    store = make_store(tmp_path)
    for _ in range(3):
        store.record(outcome(watched_seconds=30.0, ad_seconds=5.0))

    record = store.get(VIDEO_ID)
    assert record.runs == 3
    assert record.successes == 3
    assert record.watch_seconds == 90.0
    assert record.ad_seconds == 15.0


def test_interrupted_run_counts_as_a_run_but_not_a_failure(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(success=False, interrupted=True))

    record = store.get(VIDEO_ID)
    assert record.runs == 1
    assert record.failures == 0
    assert record.last_result == "interrupted"


def test_outcome_without_any_key_is_ignored(tmp_path):
    store = make_store(tmp_path)
    assert store.record(TaskOutcome(success=True, attempts=1)) is None
    assert store.all() == {}
    assert not store.stats_file.exists()


def test_clear_all_empties_the_store_and_the_file(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome())
    store.clear_all()

    assert store.all() == {}
    assert make_store(tmp_path).all() == {}


def test_clear_video_removes_one_record(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome())
    store.record(outcome(video_key="abcdefghijk", task_key="abcdefghijk"))

    assert store.clear_video(VIDEO_ID) is True
    assert store.clear_video(VIDEO_ID) is False
    assert list(store.all()) == ["abcdefghijk"]


def test_corrupt_file_is_quarantined_and_tracking_starts_fresh(tmp_path):
    stats_file = tmp_path / "stats.json"
    stats_file.write_text("{ this is not json", encoding="utf-8")

    store = StatsStore(stats_file=stats_file)

    assert store.all() == {}
    assert not stats_file.exists()
    assert (tmp_path / "stats.json.bak").exists()


# ------------------------------------------------------------------ identity


def test_resolved_id_folds_into_the_task_key_record(tmp_path):
    store = make_store(tmp_path)
    # Run 1 never resolved a real video ID (navigation failed) -> task key.
    store.record(outcome(video_key="task:timer", task_key="task:timer", watched_seconds=10.0))
    # Run 2 navigated successfully -> recorded under the resolved video ID.
    store.record(outcome(video_key="abcdefghijk", task_key="task:timer", watched_seconds=20.0))

    assert store.get("task:timer") is None
    record = store.get("abcdefghijk")
    assert record is not None
    assert record.runs == 2
    assert record.watch_seconds == 30.0
    assert "task:timer" in record.task_keys


def test_find_for_task_matches_by_key_then_by_alias(tmp_path):
    store = make_store(tmp_path)
    assert store.find_for_task(URL_TASK) is None

    # Keyword task recorded under a resolved video ID.
    store.record(outcome(video_key="abcdefghijk", task_key=resolve_video_key(KEYWORD_TASK)))
    found = store.find_for_task(KEYWORD_TASK)
    assert found is not None
    assert found.runs == 1
    assert store.find_for_task(VideoTask(keywords="something else")) is None

    # URL task recorded under its own (identical) key.
    store.record(outcome())
    assert store.find_for_task(URL_TASK) is store.get(VIDEO_ID)


def test_two_task_entries_sharing_one_video_share_one_record(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(video_key=VIDEO_ID, task_key=VIDEO_ID, watched_seconds=60.0))
    store.record(outcome(video_key=VIDEO_ID, task_key=resolve_video_key(KEYWORD_TASK), watched_seconds=45.0))

    assert len(store.all()) == 1
    record = store.find_for_task(KEYWORD_TASK)
    assert record.runs == 2
    assert record.watch_seconds == 105.0


# ------------------------------------------------------------------- channel


def test_channel_totals_sum_the_snapshotted_records(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(channel="A", watched_seconds=60.0))
    store.record(outcome(video_key="k2", task_key="k2", channel="A", watched_seconds=30.0))
    store.record(outcome(video_key="k3", task_key="k3", channel="B", watched_seconds=5.0))

    channel_a = store.channel_totals("A")
    assert channel_a.videos == 2
    assert channel_a.runs == 2
    assert channel_a.watch_seconds == 90.0

    lifetime = store.lifetime_totals()
    assert lifetime.videos == 3
    assert lifetime.runs == 3
    assert lifetime.watch_seconds == 95.0


def test_rename_channel_cascades_into_stats(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(channel="Cooking"))
    store.record(outcome(video_key="abcdefghijk", task_key="abcdefghijk", channel="Cooking"))

    assert store.rename_channel("Cooking", "Food") == 2
    assert store.channel_totals("Cooking").runs == 0
    assert store.channel_totals("Food").runs == 2
    assert store.rename_channel("Does Not Exist", "Nope") == 0


def test_unassign_channel_moves_stats_to_uncategorized(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(channel="Cooking"))

    assert store.unassign_channel("Cooking") == 1
    assert store.channel_totals("Cooking").runs == 0
    uncategorized = store.for_channel("")
    assert len(uncategorized) == 1
    assert uncategorized[0].runs == 1


def test_clear_channel_assignments_detaches_every_record(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(channel="A"))
    store.record(outcome(video_key="k2", task_key="k2", channel="B"))

    assert store.clear_channel_assignments() == 2
    assert store.for_channel("A") == []
    assert len(store.for_channel("")) == 2


# --------------------------------------------------------------- concurrency


def test_parallel_recordings_sum_exactly(tmp_path):
    store = make_store(tmp_path)
    errors = []

    def record_batch():
        try:
            for _ in range(25):
                store.record(outcome(watched_seconds=1.0))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=record_batch) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    record = store.get(VIDEO_ID)
    assert record.runs == 100
    assert record.watch_seconds == 100.0
    payload = json.loads(store.stats_file.read_text(encoding="utf-8"))
    assert payload["videos"][VIDEO_ID]["runs"] == 100


def test_remove_for_task_clears_record(tmp_path):
    store = make_store(tmp_path)
    task = VideoTask(url=f"https://www.youtube.com/watch?v={VIDEO_ID}", title="Timer")
    store.record(outcome())
    assert store.get(VIDEO_ID) is not None

    assert store.remove_for_task(task) is True
    assert store.get(VIDEO_ID) is None
    assert store.remove_for_task(task) is False


def test_update_task_metadata_updates_label_and_channel(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(label="Old Title", channel="Cooking"))

    task = VideoTask(url=f"https://www.youtube.com/watch?v={VIDEO_ID}", title="New Title", channel="Gaming")
    assert store.update_task_metadata(task) is True

    rec = store.get(VIDEO_ID)
    assert rec.label == "New Title"
    assert rec.channel == "Gaming"


def test_handle_task_edited_url_change_removes_old_record(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(video_key=VIDEO_ID))

    old_task = VideoTask(url=f"https://www.youtube.com/watch?v={VIDEO_ID}", title="Old Video")
    new_task = VideoTask(url="https://www.youtube.com/watch?v=newVideo1234", title="New Video")

    store.handle_task_edited(old_task, new_task)
    assert store.get(VIDEO_ID) is None


def test_handle_task_edited_metadata_change_updates_existing_record(tmp_path):
    store = make_store(tmp_path)
    store.record(outcome(video_key=VIDEO_ID, label="Old Title", channel="Cooking"))

    old_task = VideoTask(url=f"https://www.youtube.com/watch?v={VIDEO_ID}", title="Old Title", channel="Cooking")
    new_task = VideoTask(url=f"https://www.youtube.com/watch?v={VIDEO_ID}", title="Updated Title", channel="Travel")

    store.handle_task_edited(old_task, new_task)
    rec = store.get(VIDEO_ID)
    assert rec is not None
    assert rec.label == "Updated Title"
    assert rec.channel == "Travel"
