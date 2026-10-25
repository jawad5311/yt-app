from unittest.mock import MagicMock
import pytest
from src.models.channel import Channel, is_video_task_runnable
from src.models.video_task import VideoTask
from src.automation.session_tracker import SessionChannelTracker
from src.ui.views.manage_channel_modal import ManageChannelModal


def test_channel_target_watch_time_properties():
    # Target not set
    ch = Channel(name="MyChannel", target_watch_seconds=0.0, watch_seconds=1800.0)
    assert ch.target_watch_hours == 0.0
    assert ch.target_achieved is False

    # Target set to 2.0 hours (7200 seconds), currently at 1800s
    ch.target_watch_seconds = 7200.0
    assert ch.target_watch_hours == 2.0
    assert ch.target_achieved is False

    # Progress reaches target
    ch.watch_seconds = 7200.0
    assert ch.target_achieved is True

    # Progress exceeds target
    ch.watch_seconds = 8000.0
    assert ch.target_achieved is True


def test_channel_serialization_with_target():
    ch = Channel(name="TestChan", target_watch_seconds=3600.0)
    data = ch.to_dict()
    assert data["target_watch_seconds"] == 3600.0

    restored = Channel.from_dict(data)
    assert restored.target_watch_seconds == 3600.0
    assert restored.target_watch_hours == 1.0


def test_is_video_task_runnable_respects_target_achieved():
    ch_active = Channel(name="ChanA", enabled=True, watch_seconds=100.0, target_watch_seconds=500.0)
    ch_done = Channel(name="ChanB", enabled=True, watch_seconds=500.0, target_watch_seconds=500.0)

    task_a = VideoTask(title="v1", channel="ChanA", enabled=True)
    task_b = VideoTask(title="v2", channel="ChanB", enabled=True)

    channels = [ch_active, ch_done]
    assert is_video_task_runnable(task_a, channels) is True
    assert is_video_task_runnable(task_b, channels) is False


def test_manage_channel_modal_apply():
    modal = ManageChannelModal.__new__(ManageChannelModal)
    channel = Channel(name="GamerChannel", target_watch_seconds=0.0)
    modal.channel = channel

    mock_app = MagicMock()
    mock_app.config_mgr.get_channels.return_value = [channel.to_dict()]
    modal.app_controller = mock_app

    t1 = VideoTask(title="v1", channel="GamerChannel", max_runs=1)
    t2 = VideoTask(title="v2", channel="GamerChannel", max_runs=2)
    modal.group_videos = [(0, t1), (1, t2)]

    modal.parent_view = MagicMock()
    modal.on_applied = MagicMock()
    modal.destroy = MagicMock()

    mock_run_entry = MagicMock()
    mock_run_entry.get.return_value = "5"
    modal.run_entry = mock_run_entry

    mock_target_entry = MagicMock()
    mock_target_entry.get.return_value = "2.5"
    modal.target_entry = mock_target_entry

    mock_err = MagicMock()
    modal.err_lbl = mock_err

    modal._apply()

    assert t1.max_runs == 5
    assert t2.max_runs == 5
    modal.parent_view.save_queue.assert_called_once()
    assert channel.target_watch_seconds == 2.5 * 3600.0
    mock_app.config_mgr.set_channels.assert_called_once()
    modal.on_applied.assert_called_once()
    modal.destroy.assert_called_once()


def test_manage_channel_modal_validation():
    modal = ManageChannelModal.__new__(ManageChannelModal)
    modal.channel = Channel(name="GamerChannel")
    modal.group_videos = []
    modal.err_lbl = MagicMock()

    # Invalid run limit
    modal.run_entry = MagicMock()
    modal.run_entry.get.return_value = "invalid"
    modal._apply()
    assert "valid whole number" in modal.err_lbl.configure.call_args[1]["text"]

    modal.run_entry.get.return_value = "0"
    modal._apply()
    assert "at least 1" in modal.err_lbl.configure.call_args[1]["text"]

    # Invalid target hours
    modal.run_entry.get.return_value = "3"
    modal.target_entry = MagicMock()
    modal.target_entry.get.return_value = "bad"
    modal._apply()
    assert "valid number" in modal.err_lbl.configure.call_args[1]["text"]

    modal.target_entry.get.return_value = "-1.5"
    modal._apply()
    assert "cannot be negative" in modal.err_lbl.configure.call_args[1]["text"]


def test_session_tracker_detects_target_achieved():
    mock_config = MagicMock()
    ch = Channel(name="TargetChan", watch_seconds=3500.0, target_watch_seconds=3600.0)
    mock_config.get_channels.return_value = [ch.to_dict()]

    events = []
    tracker = SessionChannelTracker(
        config_mgr=mock_config,
        channels=[ch],
        emit_event=lambda ev: events.append(ev),
    )
    tracker.set_active_channel("TargetChan")

    assert tracker.is_channel_target_achieved("TargetChan") is False

    # Add 100 seconds: total = 3500 + 100 = 3600 >= target (3600)
    tracker.add_watch_delta("TargetChan", 100.0)

    assert tracker.is_channel_target_achieved("TargetChan") is True


def test_manage_channel_modal_delete_all_videos():
    modal = ManageChannelModal.__new__(ManageChannelModal)
    modal.channel = Channel(name="MyChannel")
    modal.on_applied = MagicMock()
    modal.destroy = MagicMock()

    t1 = VideoTask(title="v1", channel="MyChannel")
    t2 = VideoTask(title="v2", channel="MyChannel")
    t3 = VideoTask(title="v3", channel="OtherChannel")

    mock_parent = MagicMock()
    mock_parent.video_tasks = [t1, t2, t3]
    modal.parent_view = mock_parent

    mock_app = MagicMock()
    mock_app.queued_video_tasks = [t1, t2, t3]
    modal.app_controller = mock_app

    # Execute deletion without confirmation prompt
    modal._delete_all_videos(confirm=False)

    # Only OtherChannel videos should remain
    assert modal.parent_view.video_tasks == [t3]
    assert mock_app.queued_video_tasks == [t3]
    mock_parent.save_queue.assert_called_once()
    mock_parent.refresh_list.assert_called_once()
    modal.on_applied.assert_called_once()
    modal.destroy.assert_called_once()
    # Stats store is untouched
    assert not hasattr(mock_app.stats_store, "remove_for_task") or not mock_app.stats_store.remove_for_task.called
