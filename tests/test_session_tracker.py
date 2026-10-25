from unittest.mock import MagicMock
from src.models.channel import Channel
from src.automation.session_tracker import SessionChannelTracker


def test_session_tracker_increments_by_minute_only_for_active_channel():
    mock_config = MagicMock()
    mock_config.get_channels.return_value = [
        {"name": "@ChannelA", "enabled": True, "watch_seconds": 0.0},
        {"name": "@ChannelB", "enabled": True, "watch_seconds": 0.0},
    ]

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


def test_session_tracker_converts_to_hours_at_60_minutes():
    mock_config = MagicMock()
    mock_config.get_channels.return_value = [
        {"name": "@ChannelA", "enabled": True, "watch_seconds": 0.0},
    ]
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
    assert ch_a.watch_seconds == 0.0  # Not converted yet

    # Add 1 more minute (60 seconds) -> reaches 60 minutes!
    tracker.add_watch_delta("@ChannelA", 60.0)
    # Immediately converted into 1.0 hour (3600 seconds)
    assert ch_a.watch_seconds == 3600.0
    # Minute counter rolled over to 0
    assert tracker.get_counter_minutes("@ChannelA") == 0
    # Event emitted to update UI in app
    assert any(ev[0] == "channel_watch_time_updated" for ev in events)


def test_session_tracker_converts_remaining_minutes_on_stop():
    mock_config = MagicMock()
    mock_config.get_channels.return_value = [
        {"name": "@ChannelA", "enabled": True, "watch_seconds": 3600.0},
    ]
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
    assert ch_a.watch_seconds == 3600.0  # Still at 1 hour

    # Script stopped!
    tracker.on_stop()

    # Counter reset
    assert tracker.get_counter_minutes("@ChannelA") == 0
    # 25 minutes converted to 25 * 60 = 1500 seconds -> total 5100 seconds (1.4 hours)
    assert ch_a.watch_seconds == 3600.0 + 1500.0
    assert any(ev[0] == "channel_watch_time_updated" for ev in events)
