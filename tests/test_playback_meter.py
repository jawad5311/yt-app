import pytest

from src.automation.playback_meter import PlaybackMeter


def state(position, **kwargs):
    return dict(found=True, currentTime=position, **kwargs)


def test_resumed_position_is_only_a_baseline():
    meter = PlaybackMeter()
    assert meter.sample(state(600), 0) == 0
    assert meter.sample(state(603), 3) == 3


def test_stalls_and_paused_gaps_do_not_accumulate_time():
    meter = PlaybackMeter()
    meter.sample(state(10), 0)
    assert meter.sample(state(10, paused=False), 30) == 0
    assert meter.sample(state(10, paused=True), 60) == 0
    assert meter.sample(state(12, paused=False), 90) == 2


@pytest.mark.parametrize("jump", [600, 1])
def test_seeking_events_discard_large_and_small_jumps(jump):
    meter = PlaybackMeter()
    meter.sample(state(10, seekCount=0), 0)
    assert meter.sample(state(10 + jump, seekCount=1), 3) == 0
    assert meter.sample(state(13 + jump, seekCount=1), 6) == 3


def test_unreported_forward_and_backward_seeks_reset_baseline():
    meter = PlaybackMeter()
    meter.sample(state(10), 0)
    assert meter.sample(state(500), 3) == 0
    assert meter.sample(state(503), 6) == 3
    assert meter.sample(state(1), 9) == 0
    assert meter.sample(state(4), 12) == 3


@pytest.mark.parametrize("broken", [
    {"found": False}, state(5, isAd=True),
    state(5, readyState=1), state(5, seeking=True),
])
def test_ads_missing_or_unready_players_break_continuity(broken):
    meter = PlaybackMeter()
    meter.sample(state(10), 0)
    assert meter.sample(broken, 3) == 0
    assert meter.sample(state(20), 6) == 0
    assert meter.sample(state(23), 9) == 3


def test_playback_speed_is_converted_to_real_seconds():
    meter = PlaybackMeter()
    meter.sample(state(10, playbackRate=2), 0)
    assert meter.sample(state(16, playbackRate=2), 3) == 3
    assert meter.sample(state(19, playbackRate=1), 6) == 0
    assert meter.sample(state(22, playbackRate=1), 9) == 3


def test_final_ended_sample_counts_its_progress():
    meter = PlaybackMeter()
    meter.sample(state(97), 0)
    assert meter.sample(state(100, ended=True, paused=True), 3) == 3
