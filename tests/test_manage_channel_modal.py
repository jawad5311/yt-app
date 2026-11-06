from unittest.mock import MagicMock

from src.automation.session_tracker import SessionChannelTracker
from src.config.manager import ConfigManager
from src.models.channel import Channel, is_video_task_runnable
from src.models.video_task import VideoTask
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
    modal.max_views_entry = mock_run_entry

    mock_mult_entry = MagicMock()
    mock_mult_entry.get.return_value = "2.0"
    modal.multiplier_entry = mock_mult_entry

    mock_target_entry = MagicMock()
    mock_target_entry.get.return_value = "2.5"
    modal.target_entry = mock_target_entry

    mock_err = MagicMock()
    modal.err_lbl = mock_err

    modal._apply()

    assert t1.max_runs == 5
    assert t2.max_runs == 5
    assert t1.target_multiplier == 2.0
    assert t2.target_multiplier == 2.0
    modal.parent_view.save_queue.assert_called_once()
    assert channel.target_watch_seconds == 2.5 * 3600.0
    mock_app.config_mgr.set_channels.assert_called_once()
    modal.on_applied.assert_called_once()
    modal.destroy.assert_called_once()


def test_manage_channel_modal_validation():
    modal = ManageChannelModal.__new__(ManageChannelModal)
    modal.channel = Channel(name="GamerChannel")
    modal.group_videos = []
    modal.parent_view = MagicMock()
    modal.err_lbl = MagicMock()

    # Invalid multiplier
    modal.multiplier_entry = MagicMock()
    modal.multiplier_entry.get.return_value = "-1.0"
    modal._apply()
    assert "cannot be negative" in modal.err_lbl.configure.call_args[1]["text"]

    modal.multiplier_entry.get.return_value = "invalid"
    modal._apply()
    assert "valid number" in modal.err_lbl.configure.call_args[1]["text"]

    # Valid multiplier, invalid max views
    modal.multiplier_entry.get.return_value = "2.0"
    modal.max_views_entry = MagicMock()
    modal.max_views_entry.get.return_value = "invalid"
    modal._apply()
    assert "valid whole number" in modal.err_lbl.configure.call_args[1]["text"]

    modal.max_views_entry.get.return_value = "-5"
    modal._apply()
    assert "cannot be negative" in modal.err_lbl.configure.call_args[1]["text"]

    # Valid views, invalid target hours
    modal.max_views_entry.get.return_value = "5"
    modal.target_entry = MagicMock()
    modal.target_entry.get.return_value = "bad"
    modal._apply()
    assert "valid number" in modal.err_lbl.configure.call_args[1]["text"]

    modal.target_entry.get.return_value = "-1.5"
    modal._apply()
    assert "cannot be negative" in modal.err_lbl.configure.call_args[1]["text"]


def test_session_tracker_detects_target_achieved(tmp_path):
    mock_config = ConfigManager(tmp_path / "settings.json")
    ch = Channel(name="TargetChan", watch_seconds=3500.0, target_watch_seconds=3600.0)
    mock_config.set_channels([ch.to_dict()])

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


def test_manage_channel_modal_shorts_only_separation():
    # 1. Test shorts_only apply logic: only max_runs is updated, target_multiplier and channel target hours untouched
    modal = ManageChannelModal.__new__(ManageChannelModal)
    channel = Channel(name="ShortsCreator", target_watch_seconds=1200.0)
    modal.channel = channel
    modal.shorts_only = True

    mock_app = MagicMock()
    mock_app.config_mgr.get_channels.return_value = [channel.to_dict()]
    modal.app_controller = mock_app

    short_task1 = VideoTask(title="s1", channel="ShortsCreator", max_runs=1, target_multiplier=3.5, is_short_override=True)
    short_task2 = VideoTask(title="s2", channel="ShortsCreator", max_runs=2, target_multiplier=3.5, is_short_override=True)
    modal.group_videos = [(0, short_task1), (1, short_task2)]

    modal.parent_view = MagicMock()
    modal.on_applied = MagicMock()
    modal.destroy = MagicMock()

    mock_views_entry = MagicMock()
    mock_views_entry.get.return_value = "8"
    modal.max_views_entry = mock_views_entry
    modal.err_lbl = MagicMock()

    modal._apply()

    assert short_task1.max_runs == 8
    assert short_task2.max_runs == 8
    # Multiplier and channel target watch seconds must remain completely unchanged
    assert short_task1.target_multiplier == 3.5
    assert short_task2.target_multiplier == 3.5
    assert channel.target_watch_seconds == 1200.0
    mock_app.config_mgr.set_channels.assert_not_called()
    modal.parent_view.save_queue.assert_called_once()
    modal.on_applied.assert_called_once()
    modal.destroy.assert_called_once()


def test_manage_channel_modal_shorts_only_delete_all():
    modal = ManageChannelModal.__new__(ManageChannelModal)
    modal.channel = Channel(name="MultiFormatChannel")
    modal.shorts_only = True
    modal.on_applied = MagicMock()
    modal.destroy = MagicMock()

    regular_video = VideoTask(title="Long Video", channel="MultiFormatChannel", is_short_override=False)
    short_video = VideoTask(title="Short 1", channel="MultiFormatChannel", is_short_override=True)
    other_short = VideoTask(title="Short 2", channel="OtherChannel", is_short_override=True)

    mock_parent = MagicMock()
    mock_parent.video_tasks = [regular_video, short_video, other_short]
    modal.parent_view = mock_parent

    mock_app = MagicMock()
    mock_app.queued_video_tasks = [regular_video, short_video, other_short]
    modal.app_controller = mock_app

    # Execute deletion in shorts_only mode
    modal._delete_all_videos(confirm=False)

    # Only short_video of MultiFormatChannel should be deleted; regular_video and other_short must remain!
    assert modal.parent_view.video_tasks == [regular_video, other_short]
    assert mock_app.queued_video_tasks == [regular_video, other_short]
    mock_parent.save_queue.assert_called_once()
    mock_parent.refresh_list.assert_called_once()
    modal.on_applied.assert_called_once()
    modal.destroy.assert_called_once()


def test_manage_channel_modal_switch_defaults_and_toggles(ctk_app):
    """Test that switches default to multiplier=ON, views=ON, target=OFF, and toggle entry states."""
    channel = Channel(name="SwitchChannel", target_watch_seconds=7200.0, watch_seconds=1800.0)
    t1 = VideoTask(title="v1", channel="SwitchChannel", max_runs=3, target_multiplier=2.5)
    t2 = VideoTask(title="v2", channel="SwitchChannel", max_runs=3, target_multiplier=2.5)

    mock_app = MagicMock()
    mock_app.config_mgr.get_channels.return_value = [channel.to_dict()]

    modal = ManageChannelModal(
        parent=ctk_app,
        channel=channel,
        group_videos=[(0, t1), (1, t2)],
        app_controller=mock_app,
        shorts_only=False,
    )

    # 1. Verify default switch states
    assert modal.multiplier_switch.get() == 1
    assert modal.views_switch.get() == 1
    assert modal.target_switch.get() == 0

    # 2. Verify initial entry editable states
    assert str(modal.multiplier_entry.cget("state")) == "normal"
    assert str(modal.max_views_entry.cget("state")) == "normal"
    assert str(modal.target_entry.cget("state")) == "disabled"
    assert modal.target_entry.get() == "2" or modal.target_entry.get() == "2.0"

    # 3. Test toggling target switch ON -> becomes editable
    modal.target_switch.select()
    modal._on_target_toggled()
    assert str(modal.target_entry.cget("state")) == "normal"

    # Edit target hours, then toggle switch OFF -> resets to current channel value and becomes disabled
    modal.target_entry.delete(0, "end")
    modal.target_entry.insert(0, "99.0")
    modal.target_switch.deselect()
    modal._on_target_toggled()
    assert str(modal.target_entry.cget("state")) == "disabled"
    assert modal.target_entry.get() == modal.init_target_str

    # 4. Test toggling multiplier switch OFF -> resets to current value and becomes disabled
    modal.multiplier_switch.deselect()
    modal._on_multiplier_toggled()
    assert str(modal.multiplier_entry.cget("state")) == "disabled"
    assert modal.multiplier_entry.get() == "2.5"

    modal.multiplier_switch.select()
    modal._on_multiplier_toggled()
    assert str(modal.multiplier_entry.cget("state")) == "normal"

    # 5. Test toggling views switch OFF -> resets to current value and becomes disabled
    modal.views_switch.deselect()
    modal._on_views_toggled()
    assert str(modal.max_views_entry.cget("state")) == "disabled"
    assert modal.max_views_entry.get() == "3"

    modal.views_switch.select()
    modal._on_views_toggled()
    assert str(modal.max_views_entry.cget("state")) == "normal"

    modal.destroy()


def test_manage_channel_modal_selective_updates_via_switches(ctk_app):
    """Test that only checked/ON switches update values, while unchecked/OFF remain untouched."""
    channel = Channel(name="SelectChannel", target_watch_seconds=7200.0)
    t1 = VideoTask(title="v1", channel="SelectChannel", max_runs=5, target_multiplier=2.0)
    t2 = VideoTask(title="v2", channel="SelectChannel", max_runs=5, target_multiplier=2.0)

    mock_app = MagicMock()
    mock_app.config_mgr.get_channels.return_value = [channel.to_dict()]

    parent = MagicMock()
    parent.shorts_only = False

    # Scenario A: ONLY max views cap switch is ON, multiplier and target watch hours are OFF
    modal = ManageChannelModal(
        parent=ctk_app,
        channel=channel,
        group_videos=[(0, t1), (1, t2)],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal.parent_view = parent

    # Turn multiplier switch OFF
    modal.multiplier_switch.deselect()
    modal._on_multiplier_toggled()

    # Target switch is OFF by default

    # Update max views cap to 12
    modal.max_views_entry.delete(0, "end")
    modal.max_views_entry.insert(0, "12")

    modal._apply()

    # Only max_runs is updated; multiplier and channel target hours are untouched!
    assert t1.max_runs == 12
    assert t2.max_runs == 12
    assert t1.target_multiplier == 2.0
    assert t2.target_multiplier == 2.0
    assert channel.target_watch_seconds == 7200.0
    mock_app.config_mgr.set_channels.assert_not_called()

    # Scenario B: All three switches are ON
    modal2 = ManageChannelModal(
        parent=ctk_app,
        channel=channel,
        group_videos=[(0, t1), (1, t2)],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal2.parent_view = parent

    modal2.multiplier_switch.select()
    modal2.views_switch.select()
    modal2.target_switch.select()
    modal2._on_multiplier_toggled()
    modal2._on_views_toggled()
    modal2._on_target_toggled()

    modal2.multiplier_entry.delete(0, "end")
    modal2.multiplier_entry.insert(0, "4.5")
    modal2.max_views_entry.delete(0, "end")
    modal2.max_views_entry.insert(0, "20")
    modal2.target_entry.delete(0, "end")
    modal2.target_entry.insert(0, "5.0")

    modal2._apply()

    # All three values are updated!
    assert t1.target_multiplier == 4.5
    assert t2.target_multiplier == 4.5
    assert t1.max_runs == 20
    assert t2.max_runs == 20
    assert channel.target_watch_seconds == 5.0 * 3600.0
    mock_app.config_mgr.set_channels.assert_called_once()

    # Scenario C: Target watch time switch is ON, others are OFF
    mock_app.config_mgr.set_channels.reset_mock()
    modal3 = ManageChannelModal(
        parent=ctk_app,
        channel=channel,
        group_videos=[(0, t1), (1, t2)],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal3.parent_view = parent

    modal3.multiplier_switch.deselect()
    modal3.views_switch.deselect()
    modal3.target_switch.select()
    modal3._on_multiplier_toggled()
    modal3._on_views_toggled()
    modal3._on_target_toggled()

    modal3.target_entry.delete(0, "end")
    modal3.target_entry.insert(0, "10.0")

    modal3._apply()

    # Multiplier and max_runs untouched, target watch hours updated!
    assert t1.target_multiplier == 4.5
    assert t2.target_multiplier == 4.5
    assert t1.max_runs == 20
    assert t2.max_runs == 20
    assert channel.target_watch_seconds == 10.0 * 3600.0
    mock_app.config_mgr.set_channels.assert_called_once()


def test_manage_channel_modal_shorts_only_switch_behavior(ctk_app):
    """Test that Shorts Queue mode presents only views_switch and respects toggle."""
    channel = Channel(name="ShortsChan")
    s1 = VideoTask(title="s1", channel="ShortsChan", max_runs=5, is_short_override=True)
    s2 = VideoTask(title="s2", channel="ShortsChan", max_runs=5, is_short_override=True)

    mock_app = MagicMock()
    parent = MagicMock()
    parent.shorts_only = True

    modal = ManageChannelModal(
        parent=ctk_app,
        channel=channel,
        group_videos=[(0, s1), (1, s2)],
        app_controller=mock_app,
        shorts_only=True,
    )
    modal.parent_view = parent

    # Verify multiplier and target switches are NOT present in shorts_only mode
    assert not hasattr(modal, "multiplier_switch")
    assert not hasattr(modal, "target_switch")

    # views_switch is present and ON by default
    assert hasattr(modal, "views_switch")
    assert modal.views_switch.get() == 1
    assert str(modal.max_views_entry.cget("state")) == "normal"

    # Turn views switch OFF -> entry disabled
    modal.views_switch.deselect()
    modal._on_views_toggled()
    assert str(modal.max_views_entry.cget("state")) == "disabled"

    modal._apply()
    # Not updated since switch was OFF
    assert s1.max_runs == 5
    assert s2.max_runs == 5
