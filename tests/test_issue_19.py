"""Lifetime history survives ordinary queue maintenance; explicit resets erase it."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.channel import Channel, VideoGroup
from src.models.video_task import VideoTask
from src.models.watch_stats import TaskOutcome, resolve_video_key
from src.ui.views.manage_channel_modal import ManageChannelModal
from src.ui.views.queue_view import QueueView


def make_view(tmp_path, tasks):
    config = ConfigManager(tmp_path / "settings.json")
    store = StatsStore(tmp_path / "stats.json")
    app = SimpleNamespace(config_mgr=config, stats_store=store, queued_video_tasks=tasks)
    app.on_queue_changed = lambda updated: setattr(app, "queued_video_tasks", updated)
    view = QueueView.__new__(QueueView)
    view.app_controller = app
    view.video_tasks = tasks
    view.refresh_list = MagicMock()
    view.save_queue()
    return view, store


def record_history(store, task, seconds=60, *, key=None, finished=True):
    base = resolve_video_key(task)
    store.record(TaskOutcome(
        task_key=base, video_key=key or base, label=task.title,
        channel=task.channel, attempts=1, success=True,
        playback_finished=finished, watched_seconds=seconds,
    ))


@pytest.mark.parametrize("deletion", ["single", "clear", "channel"])
def test_delete_paths_preserve_history_on_disk_and_in_totals(tmp_path, deletion):
    task = VideoTask(url="https://youtu.be/dQw4w9WgXcQ", channel="Saved", title="Old")
    view, store = make_view(tmp_path, [task])
    record_history(store, task)
    before = store.stats_file.read_bytes()

    if deletion == "single":
        view.remove_task(0)
    elif deletion == "clear":
        view.clear_queue()
    else:
        modal = ManageChannelModal.__new__(ManageChannelModal)
        modal.channel = Channel(name="Saved")
        modal.parent_view = view
        modal.app_controller = view.app_controller
        modal.on_applied = MagicMock()
        modal.destroy = MagicMock()
        modal._delete_all_videos(confirm=False)

    assert view.video_tasks == view.app_controller.queued_video_tasks == []
    assert view.app_controller.config_mgr.get_video_queue() == []
    assert store.stats_file.read_bytes() == before
    reloaded = StatsStore(store.stats_file)
    assert reloaded.lifetime_totals().watch_seconds == 60
    assert reloaded.channel_totals("Saved").runs == 1
    assert view._group_totals_line(VideoGroup(Channel(name="Saved"), []))

    # Re-adding the same video (or a keyword alias) recovers the same history.
    assert reloaded.find_for_task(task).runs == 1


def test_deleting_keyword_alias_does_not_erase_resolved_record(tmp_path):
    task = VideoTask(title="Resolved title", keywords="search term", channel="Saved")
    view, store = make_view(tmp_path, [task])
    record_history(store, task, key="dQw4w9WgXcQ")
    view.remove_task(0)
    reloaded = StatsStore(store.stats_file)
    assert reloaded.find_for_task(task).watch_seconds == 60
    assert resolve_video_key(task) in reloaded.get("dQw4w9WgXcQ").task_keys


@pytest.mark.parametrize("new_has_history", [False, True])
def test_edit_identity_retains_both_histories_and_clears_old_duration(tmp_path, new_has_history):
    old = VideoTask(url="https://youtu.be/dQw4w9WgXcQ", title="Old", channel="Saved",
                    duration_seconds=600, duration_text="10:00")
    new = VideoTask(url="https://youtu.be/abcdefghijk", title="New", channel="Other",
                    duration_seconds=600, duration_text="10:00")
    view, store = make_view(tmp_path, [old])
    record_history(store, old, 60)
    if new_has_history:
        record_history(store, new, 120)
    before = store.stats_file.read_bytes()
    view._apply_task_edit(0, new)
    view.save_queue()
    assert store.stats_file.read_bytes() == before
    assert new.duration_seconds == 0 and new.duration_text == ""
    assert store.find_for_task(old).watch_seconds == 60
    if new_has_history:
        assert store.find_for_task(new).watch_seconds == 120
    else:
        assert store.find_for_task(new) is None


def test_same_identity_edit_updates_metadata_without_erasing_counters(tmp_path):
    old = VideoTask(url="https://youtu.be/dQw4w9WgXcQ", title="Old", channel="Saved")
    new = VideoTask(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", title="New",
                    channel="Renamed", duration_seconds=600, duration_text="10:00")
    view, store = make_view(tmp_path, [old])
    record_history(store, old)
    original = store.find_for_task(old).to_dict()
    view._apply_task_edit(0, new)
    updated = store.find_for_task(new).to_dict()
    assert updated == {**original, "label": "New", "channel": "Renamed"}
    assert new.duration_seconds == 600


def test_channel_totals_do_not_depend_on_queue_membership_or_duplicates(tmp_path):
    task = VideoTask(url="https://youtu.be/dQw4w9WgXcQ", channel="Saved")
    view, store = make_view(tmp_path, [task, task])
    record_history(store, task)
    empty = VideoGroup(Channel(name="Saved"), [])
    duplicate = VideoGroup(Channel(name="Saved"), list(enumerate(view.video_tasks)))
    assert view._group_totals_line(empty) == view._group_totals_line(duplicate)
    assert "1 full run" in view._group_totals_line(empty)


def test_partial_uncategorized_history_and_explicit_erasure(tmp_path):
    task = VideoTask(url="https://youtu.be/dQw4w9WgXcQ")
    view, store = make_view(tmp_path, [task])
    record_history(store, task, 30, finished=False)
    view.clear_queue()
    assert view._group_totals_line(VideoGroup(Channel(), []))
    assert store.lifetime_totals().watch_seconds == 30
    assert store.lifetime_totals().runs == 0
    assert store.remove_for_task(task)
    assert StatsStore(store.stats_file).lifetime_totals().watch_seconds == 0
