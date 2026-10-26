from unittest.mock import MagicMock

import pytest

from src.automation.session_tracker import SessionChannelTracker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.channel import Channel
from src.models.watch_stats import TaskOutcome


def test_session_tracker_increments_by_minute_only_for_active_channel(tmp_path):
    mock_config = ConfigManager(tmp_path / "settings.json")
    mock_config.set_channels([
        {"name": "@ChannelA", "enabled": True, "watch_seconds": 0.0},
        {"name": "@ChannelB", "enabled": True, "watch_seconds": 0.0},
    ])

    ch_a = Channel(name="@ChannelA", watch_seconds=0.0)
    ch_b = Channel(name="@ChannelB", watch_seconds=0.0)

    events = []
    tracker = SessionChannelTracker(
        config_mgr=mock_config,
        channels=[ch_a, ch_b],
        emit_event=lambda ev: events.append(ev),
    )

    # Initially counters are empty
    assert tracker.get_counter_minutes("@ChannelA") == 0
    assert tracker.get_counter_minutes("@ChannelB") == 0

    # Without active channel, deltas are ignored
    tracker.add_watch_delta("@ChannelA", 120.0)
    assert tracker.get_counter_minutes("@ChannelA") == 0

    # Set ChannelA active
    tracker.set_active_channel("@ChannelA")

    # Adding 50 seconds: less than 1 minute -> counter is still 0
    tracker.add_watch_delta("@ChannelA", 50.0)
    assert tracker.get_counter_minutes("@ChannelA") == 0

    # Adding 20 seconds: total 70s -> 1 minute accumulated, 10s remainder
    tracker.add_watch_delta("@ChannelA", 20.0)
    assert tracker.get_counter_minutes("@ChannelA") == 1

    # Attempting to add delta to ChannelB while ChannelA is active -> must be ignored!
    tracker.add_watch_delta("@ChannelB", 180.0)
    assert tracker.get_counter_minutes("@ChannelB") == 0

    # Switch active channel to ChannelB
    tracker.set_active_channel("@ChannelB")
    tracker.add_watch_delta("@ChannelB", 125.0)  # 2 minutes, 5s remainder
    assert tracker.get_counter_minutes("@ChannelB") == 2
    # ChannelA remained at 1 minute
    assert tracker.get_counter_minutes("@ChannelA") == 1


def test_session_tracker_checkpoints_before_and_after_60_minutes(tmp_path):
    mock_config = ConfigManager(tmp_path / "settings.json")
    mock_config.set_channels([
        {"name": "@ChannelA", "enabled": True, "watch_seconds": 0.0},
    ])
    ch_a = Channel(name="@ChannelA", watch_seconds=0.0)

    events = []
    tracker = SessionChannelTracker(
        config_mgr=mock_config,
        channels=[ch_a],
        emit_event=lambda ev: events.append(ev),
    )
    tracker.set_active_channel("@ChannelA")

    # Add 59 minutes (3540 seconds)
    tracker.add_watch_delta("@ChannelA", 59 * 60.0)
    assert tracker.get_counter_minutes("@ChannelA") == 59
    assert ch_a.watch_seconds == 3540.0  # Already checkpointed.

    # Add 1 more minute (60 seconds) -> reaches 60 minutes!
    tracker.add_watch_delta("@ChannelA", 60.0)
    # All 3600 seconds have been checkpointed exactly once.
    assert ch_a.watch_seconds == 3600.0
    # Minute counter rolled over to 0
    assert tracker.get_counter_minutes("@ChannelA") == 0
    # Event emitted to update UI in app
    assert any(ev[0] == "channel_watch_time_updated" for ev in events)


def test_session_tracker_stop_does_not_recount_checkpointed_minutes(tmp_path):
    mock_config = ConfigManager(tmp_path / "settings.json")
    mock_config.set_channels([
        {"name": "@ChannelA", "enabled": True, "watch_seconds": 3600.0},
    ])
    ch_a = Channel(name="@ChannelA", watch_seconds=3600.0)  # already has 1.0 hour

    events = []
    tracker = SessionChannelTracker(
        config_mgr=mock_config,
        channels=[ch_a],
        emit_event=lambda ev: events.append(ev),
    )
    tracker.set_active_channel("@ChannelA")

    # Watch 25 minutes
    tracker.add_watch_delta("@ChannelA", 25 * 60.0)
    assert tracker.get_counter_minutes("@ChannelA") == 25
    assert ch_a.watch_seconds == 5100.0  # Already persisted before Stop.

    # Script stopped!
    tracker.on_stop()

    # Counter reset
    assert tracker.get_counter_minutes("@ChannelA") == 0
    # Stop keeps the already checkpointed total at 5100 seconds (1.4 hours).
    assert ch_a.watch_seconds == 3600.0 + 1500.0
    assert any(ev[0] == "channel_watch_time_updated" for ev in events)


@pytest.mark.parametrize("watch_seconds", [0.125, 59.5, 119.75, 120.0, 3600.0, 3720.125])
def test_session_and_video_record_count_watch_time_once(tmp_path, watch_seconds):
    config = ConfigManager(tmp_path / "settings.json")
    channel = Channel(name="Cooking")
    config.set_channels([channel.to_dict()])
    store = StatsStore(tmp_path / "stats.json")
    tracker = SessionChannelTracker(config_mgr=config, stats_store=store, channels=[channel])
    tracker.set_active_channel(channel.name)
    tracker.add_watch_delta(channel.name, watch_seconds)

    # Session checkpoints must not create a second StatsStore entry.
    assert store.all() == {}
    store.record(TaskOutcome(
        video_key="dQw4w9WgXcQ", channel=channel.name, attempts=1,
        success=True, playback_finished=True, video_duration=watch_seconds,
        watched_seconds=watch_seconds,
    ))
    tracker.on_stop()
    tracker.on_stop()

    assert config.get_channels()[0]["watch_seconds"] == watch_seconds
    assert store.channel_totals(channel.name).watch_seconds == watch_seconds
    assert store.lifetime_totals().watch_seconds == watch_seconds
    assert store.lifetime_totals().runs == 1
    assert list(store.all()) == ["dQw4w9WgXcQ"]
    reloaded = StatsStore(tmp_path / "stats.json")
    assert reloaded.channel_totals(channel.name).watch_seconds == watch_seconds
    assert reloaded.lifetime_totals().watch_seconds == watch_seconds


def test_channel_checkpoint_survives_restart_without_stop(tmp_path):
    path = tmp_path / "settings.json"
    config = ConfigManager(path)
    config.set_channels([Channel(name="Cooking").to_dict()])
    tracker = SessionChannelTracker(config_mgr=config, channels=[Channel(name="Cooking")])
    tracker.set_active_channel("Cooking")
    tracker.add_watch_delta("Cooking", 119.75)

    # Simulate an abrupt process exit: no on_stop call on the old tracker.
    reloaded = ConfigManager(path)
    channel = Channel.from_dict(reloaded.get_channels()[0])
    assert channel.watch_seconds == 119.75
    restarted = SessionChannelTracker(config_mgr=reloaded, channels=[channel])
    restarted.set_active_channel("Cooking")
    restarted.add_watch_delta("Cooking", 0.25)
    restarted.on_stop()
    restarted.on_stop()
    assert ConfigManager(path).get_channels()[0]["watch_seconds"] == 120.0


def test_checkpointed_time_is_not_added_to_session_time_again(tmp_path):
    config = ConfigManager(tmp_path / "settings.json")
    channel = Channel(name="Cooking", target_watch_seconds=120)
    config.set_channels([channel.to_dict()])
    tracker = SessionChannelTracker(config_mgr=config, channels=[channel])
    tracker.set_active_channel(channel.name)
    tracker.add_watch_delta(channel.name, 119.75)
    assert tracker.get_current_total_watch_seconds(channel.name) == 119.75
    assert not tracker.is_channel_target_achieved(channel.name)
    tracker.add_watch_delta(channel.name, 0.25)
    assert tracker.get_current_total_watch_seconds(channel.name) == 120
    assert tracker.is_channel_target_achieved(channel.name)


def test_failed_checkpoint_is_retried_without_recounting(tmp_path, monkeypatch):
    config = ConfigManager(tmp_path / "settings.json")
    channel = Channel(name="Cooking")
    config.set_channels([channel.to_dict()])
    tracker = SessionChannelTracker(config_mgr=config, channels=[channel])
    tracker.set_active_channel(channel.name)
    save = config._save_unlocked
    monkeypatch.setattr(config, "_save_unlocked", MagicMock(side_effect=OSError("disk unavailable")))
    tracker.add_watch_delta(channel.name, 1.25)
    assert channel.watch_seconds == 1.25
    assert config.get_channels()[0].get("watch_seconds", 0) == 0
    assert tracker.get_current_total_watch_seconds(channel.name) == 1.25

    monkeypatch.setattr(config, "_save_unlocked", save)
    tracker.on_stop()
    tracker.on_stop()
    assert config.get_channels()[0]["watch_seconds"] == 1.25
    assert channel.watch_seconds == 1.25


def test_parallel_channel_checkpoints_accumulate_atomically(tmp_path):
    import threading

    config = ConfigManager(tmp_path / "settings.json")
    config.set_channels([Channel(name="Cooking").to_dict()])
    trackers = [SessionChannelTracker(config_mgr=config, channels=[Channel(name="Cooking")]) for _ in range(2)]
    errors = []

    def record(tracker):
        try:
            tracker.set_active_channel("Cooking")
            for _ in range(10):
                tracker.add_watch_delta("Cooking", 0.125)
            tracker.on_stop()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=record, args=(tracker,)) for tracker in trackers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert config.get_channels()[0]["watch_seconds"] == 2.5
