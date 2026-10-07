"""Tests for the ad watch policy (skip timing, caps, detection signals)."""

from src.automation import ads
from src.automation.ads import PLAYER_STATE_JS, SKIP_AFTER_MAX, SKIP_AFTER_MIN, AdWatchState


def feed(state, seconds, dt=1.0, is_ad=True):
    steps = int(seconds / dt)
    for _ in range(steps):
        state.tick(is_ad, dt)


def test_pick_skip_delay_stays_in_monetisation_window():
    for _ in range(50):
        delay = ads.pick_skip_delay()
        assert SKIP_AFTER_MIN <= delay <= SKIP_AFTER_MAX


def test_skip_ads_waits_for_monetisation_window(monkeypatch):
    monkeypatch.setattr(ads, "pick_skip_delay", lambda: 32.0)
    state = AdWatchState()

    feed(state, 29)
    assert state.skip_reason(skip_ads=True, max_ad_seconds=32) is None

    state.tick(True, 3.0)  # 32s into the break
    reason = state.skip_reason(skip_ads=True, max_ad_seconds=32)
    assert reason is not None
    assert "monetisation" in reason


def test_skip_ads_mode_is_not_governed_by_the_cap(monkeypatch):
    monkeypatch.setattr(ads, "pick_skip_delay", lambda: 32.0)
    state = AdWatchState()
    feed(state, 31)  # under the 32s window even though cap=5
    assert state.skip_reason(skip_ads=True, max_ad_seconds=5) is None


def test_watch_all_mode_skips_only_at_the_cumulative_cap():
    state = AdWatchState()
    feed(state, 20)
    state.tick(False, 5)  # ad ends, break resets, cumulative time kept
    feed(state, 10)

    assert state.total_ad_seconds == 30
    assert state.skip_reason(skip_ads=False, max_ad_seconds=32) is None

    feed(state, 3)
    reason = state.skip_reason(skip_ads=False, max_ad_seconds=32)
    assert reason is not None
    assert "cap" in reason


def test_watch_all_mode_never_escapes_when_cap_is_zero():
    state = AdWatchState()
    feed(state, 600)
    assert state.skip_reason(skip_ads=False, max_ad_seconds=0) is None


def test_break_reset_rearms_the_skip_target(monkeypatch):
    targets = iter([30.0, 35.0])
    monkeypatch.setattr(ads, "pick_skip_delay", lambda: next(targets))
    state = AdWatchState()

    feed(state, 30)
    assert state.skip_reason(skip_ads=True, max_ad_seconds=0) is not None

    state.tick(False, 1.0)  # first ad break ends
    assert state.break_ad_seconds == 0.0

    feed(state, 30)  # second break, new (later) target
    assert state.skip_reason(skip_ads=True, max_ad_seconds=0) is None
    feed(state, 5)
    assert state.skip_reason(skip_ads=True, max_ad_seconds=0) is not None
    assert state.ad_breaks == 2


def test_tick_ignores_non_positive_delta():
    state = AdWatchState()
    state.tick(True, 0)
    state.tick(True, -5)
    assert state.total_ad_seconds == 0.0


def test_player_state_probe_uses_strict_ad_signals():
    # Strict player-state checks (the old probe matched `.video-ads`, which is
    # always in the DOM and made the up-next countdown look like an ad).
    assert "ytp-showing-ad" in PLAYER_STATE_JS
    assert "childElementCount" in PLAYER_STATE_JS
    assert "adformat=" in PLAYER_STATE_JS
    # Prefers the *playing* video element when several exist (main + ad).
    assert "querySelectorAll('video')" in PLAYER_STATE_JS
