from unittest.mock import MagicMock

import pytest

from src.automation.youtube_actions import (
    calculate_end_buffer,
    disable_player_autoplay,
    ensure_video_playing,
    find_skip_button,
    trigger_skip_ad,
    watch_video,
)


def test_calculate_end_buffer_handles_invalid_or_zero_duration():
    assert calculate_end_buffer(0) == 1.5
    assert calculate_end_buffer(-10) == 1.5


def test_calculate_end_buffer_scales_for_short_videos():
    # 10s video: buffer is capped to ~1.5s so 85% is watched
    buf_10 = calculate_end_buffer(10.0, target_buffer=10.0)
    assert 1.4 <= buf_10 <= 1.6

    # 50s video: 50 * 0.12 = 6.0s
    buf_50 = calculate_end_buffer(50.0, target_buffer=10.0)
    assert 5.9 <= buf_50 <= 6.1


def test_calculate_end_buffer_uses_full_target_for_long_videos():
    # 600s video (10m): 600 * 0.12 = 72s > 10.0s, so target 10.0s is used
    buf_600 = calculate_end_buffer(600.0, target_buffer=10.0)
    assert buf_600 == 10.0

    # Custom target buffer (e.g. 15s)
    buf_custom = calculate_end_buffer(600.0, target_buffer=15.0)
    assert buf_custom == 15.0


def test_disable_player_autoplay():
    driver = MagicMock()
    driver.execute_script.return_value = True
    assert disable_player_autoplay(driver) is True

    driver.execute_script.return_value = False
    assert disable_player_autoplay(driver) is False

    driver.execute_script.side_effect = Exception("boom")
    assert disable_player_autoplay(driver) is False


def test_watch_video_cutoff_is_not_a_full_run():
    """Verify that watch_video exits when reaching duration - buffer without waiting for 0s left."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/watch?v=testvid1234"

    states = [
        {"found": True, "paused": False, "ended": False, "currentTime": 85.0, "duration": 100.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 92.0, "duration": 100.0, "isAd": False},
    ]

    def mock_exec(script, *args):
        if "currentTime" in script:
            return states.pop(0) if states else {"found": True, "paused": False, "ended": True, "currentTime": 100.0, "duration": 100.0, "isAd": False}
        return None

    driver.execute_script.side_effect = mock_exec

    result = watch_video(
        driver,
        duration_seconds=None,
        end_buffer_seconds=10.0,
        completion_ratio=0.9,
        enable_interactivity=False,
    )

    assert result.completed is True
    assert result.playback_finished is False
    assert result.reason == "completion_cutoff"


def test_watch_video_detects_autoplay_transition():
    """Verify that if YouTube navigates away to an autoplayed video, watch_video halts cleanly."""
    driver = MagicMock()
    urls = [
        "https://www.youtube.com/watch?v=initial1234",
        "https://www.youtube.com/watch?v=initial1234",
        "https://www.youtube.com/watch?v=autoplayNext",
    ]

    def get_url():
        return urls.pop(0) if urls else "https://www.youtube.com/watch?v=autoplayNext"

    type(driver).current_url = property(lambda self: get_url())

    states = [
        {"found": True, "paused": False, "ended": False, "currentTime": 10.0, "duration": 100.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 2.0, "duration": 300.0, "isAd": False},
    ]

    def mock_exec(script, *args):
        if "currentTime" in script:
            return states.pop(0) if states else {"found": True, "paused": False, "ended": False, "currentTime": 5.0, "duration": 300.0, "isAd": False}
        return None

    driver.execute_script.side_effect = mock_exec

    result = watch_video(
        driver,
        duration_seconds=None,
        end_buffer_seconds=10.0,
    )

    assert result.completed is True
    assert result.reason == "autoplay_transition"


def test_ensure_video_playing_via_js():
    driver = MagicMock()
    driver.execute_script.return_value = {"found": True, "playing": True}
    assert ensure_video_playing(driver) is True


def test_ensure_video_playing_via_large_play_button():
    driver = MagicMock()
    driver.execute_script.return_value = {"found": True, "playing": False}

    large_btn = MagicMock()
    large_btn.is_displayed.return_value = True
    driver.find_elements.side_effect = lambda by, sel: [large_btn] if "ytp-large-play-button" in sel else []

    assert ensure_video_playing(driver) is True


def test_watch_video_pause_resume_does_not_expire_budget():
    """Verify pausing and resuming does not prematurely expire duration budget."""
    import threading
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/watch?v=pausevid123"

    pause_ev = threading.Event()
    stop_ev = threading.Event()

    ticks = 0
    def mock_exec(script, *args):
        nonlocal ticks
        if "currentTime" in script:
            ticks += 1
            if ticks == 1:
                # Pause on first tick
                pause_ev.set()
            elif ticks >= 3:
                stop_ev.set()
            return {"found": True, "paused": False, "ended": False, "currentTime": float(ticks * 5), "duration": 300.0, "isAd": False}
        return {"playing": True}

    driver.execute_script.side_effect = mock_exec

    # Unpause in another thread after short delay
    def unpause():
        import time
        time.sleep(0.1)
        pause_ev.clear()

    t = threading.Thread(target=unpause)
    t.start()

    from unittest.mock import patch
    with patch("time.sleep", return_value=None):
        # Stop after 3 ticks
        def stop_after_ticks(*args, **kwargs):
            if ticks >= 3:
                stop_ev.set()

        result = watch_video(
            driver,
            duration_seconds=60,
            pause_event=pause_ev,
            stop_event=stop_ev,
            enable_interactivity=False,
        )

    t.join()
    # It was stopped cleanly by user, not prematurely by duration_reached
    assert result.interrupted is True


def test_find_and_trigger_skip_ad_with_hover():
    driver = MagicMock()
    mock_btn = MagicMock()
    mock_btn.is_displayed.return_value = True
    driver.find_elements.return_value = [mock_btn]

    # find_skip_button finds the button
    btn = find_skip_button(driver)
    assert btn == mock_btn

    # trigger_skip_ad executes hover + click sequence
    res = trigger_skip_ad(driver)
    assert res is True


def test_skip_button_ignores_visit_site_cta():
    from src.automation.youtube_actions import is_genuine_skip_button

    # 1. Advertiser CTA button like "Visit site" should be rejected
    visit_btn = MagicMock()
    visit_btn.text = "Visit site"
    visit_btn.get_attribute.side_effect = lambda attr: {
        "class": "ytp-ad-button ytp-ad-button-link ytp-ad-component--clickable",
        "aria-label": "Visit site",
    }.get(attr, "")
    assert is_genuine_skip_button(visit_btn) is False

    # 2. Genuine skip button should be accepted
    skip_btn = MagicMock()
    skip_btn.text = "Skip"
    skip_btn.get_attribute.side_effect = lambda attr: {
        "class": "ytp-skip-ad-button",
        "id": "skip-button:g",
        "aria-label": "Skip ad",
    }.get(attr, "")
    assert is_genuine_skip_button(skip_btn) is True


def test_watch_video_logs_the_configured_cutoff():
    import queue
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/watch?v=test95percent"
    log_q = queue.Queue()

    # 1000s duration video (so 95% is 950s)
    states = [
        {"found": True, "paused": False, "ended": False, "currentTime": 500.0, "duration": 1000.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 951.0, "duration": 1000.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 996.0, "duration": 1000.0, "isAd": False},
    ]

    def mock_exec(script, *args):
        if "currentTime" in script:
            return states.pop(0) if states else {"found": True, "paused": False, "ended": True, "currentTime": 1000.0, "duration": 1000.0, "isAd": False}
        return None

    driver.execute_script.side_effect = mock_exec

    result = watch_video(
        driver,
        duration_seconds=None,
        log_queue=log_q,
        enable_interactivity=False,
        end_buffer_seconds=10,
    )

    assert result.completed is True
    assert result.playback_finished is False
    assert result.reason == "completion_cutoff"

    # The logs describe the configured cutoff rather than an unrelated percentage.
    logs = []
    while not log_q.empty():
        logs.append(log_q.get_nowait())
    log_text = " ".join(logs)

    assert "Full-video cutoff: 16:30 / 16:40" in log_text
    assert "stop buffer: 10s" in log_text
    assert "Closing randomly" not in log_text


def run_scripted_watch(monkeypatch, states, sleep_hook=None, wall_clock=None, **kwargs):
    """Exercise the real watch loop with a deterministic clock and player."""
    import threading
    from types import SimpleNamespace

    from src.automation import youtube_actions as module

    clock = [100.0]
    stop = threading.Event()
    samples = iter(states)
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def execute(script, *args):
        if script == module.PLAYER_STATE_JS:
            try:
                return next(samples)
            except StopIteration:
                stop.set()
                return {"found": False}
        return None

    driver.execute_script.side_effect = execute
    monkeypatch.setattr(module, "ensure_video_playing", lambda *a, **k: True)
    monkeypatch.setattr(module, "disable_player_autoplay", lambda *a, **k: True)
    monkeypatch.setattr(module, "find_skip_button", lambda *a, **k: None)
    def sleep(duration):
        clock[0] += duration
        if sleep_hook:
            sleep_hook(clock[0], duration)

    monkeypatch.setattr(module, "time", SimpleNamespace(
        time=wall_clock or (lambda: clock[0]), monotonic=lambda: clock[0], sleep=sleep,
    ))
    ticks = []
    result = module.watch_video(
        driver, stop_event=stop, enable_interactivity=False,
        on_watch_tick=ticks.append, **kwargs,
    )
    return result, ticks


def playback_state(position, **kwargs):
    values = dict(found=True, currentTime=position, duration=1000.0, paused=False, isAd=False)
    values.update(kwargs)
    return values


def test_watch_meter_excludes_resumed_position_seek_and_stall(monkeypatch):
    result, ticks = run_scripted_watch(monkeypatch, [
        playback_state(600), playback_state(600), playback_state(603),
        playback_state(900, seekCount=1), playback_state(903, seekCount=1),
    ], duration_seconds=6)
    assert result.watched_seconds == 6
    assert sum(ticks) == 6
    assert result.completed
    assert result.reason == "duration_reached"
    assert not result.playback_finished


def test_watch_meter_does_not_mix_ad_and_main_timelines(monkeypatch):
    result, ticks = run_scripted_watch(monkeypatch, [
        playback_state(0), playback_state(3),
        playback_state(100, isAd=True), playback_state(9), playback_state(12),
    ], duration_seconds=6, skip_ads=False, max_ad_seconds=0)
    assert result.watched_seconds == 6
    assert sum(ticks) == 6
    assert result.ad_seconds == 3


def test_actual_player_end_is_a_full_run(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(0, duration=6), playback_state(3, duration=6),
        playback_state(6, duration=6, ended=True, paused=True),
    ], duration_seconds=None)
    assert result.reason == "ended"
    assert result.playback_finished
    assert result.watched_seconds == 6


def test_timeout_is_failure_not_full_completion(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(0, duration=10, isAd=False) for _ in range(46)
    ], duration_seconds=None, full_timeout_seconds=1, skip_ads=False, max_ad_seconds=0)
    assert not result.completed
    assert not result.playback_finished
    assert result.reason == "full_timeout"


def test_cutoff_is_successful_partial_watch(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [playback_state(960)],
        duration_seconds=None, completion_ratio=0.95)
    assert result.completed
    assert result.reason == "completion_cutoff"
    assert not result.playback_finished
    assert result.watched_seconds == 0


@pytest.mark.parametrize("buffer,expected", [(10, 990), (30, 970)])
def test_configured_full_video_buffer_controls_cutoff(monkeypatch, buffer, expected):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(951), playback_state(expected - 0.1), playback_state(expected),
    ], duration_seconds=None, end_buffer_seconds=buffer)
    assert result.reason == "completion_cutoff"
    assert result.final_current_time == expected
    assert not result.playback_finished


def test_zero_buffer_selects_fallback_once(monkeypatch):
    from src.automation import youtube_actions as module

    choose = MagicMock(return_value=8)
    monkeypatch.setattr(module.random, "uniform", choose)
    result, _ = run_scripted_watch(monkeypatch, [playback_state(951), playback_state(992)],
        duration_seconds=None, end_buffer_seconds=0)
    assert result.final_current_time == 992
    choose.assert_called_once_with(7.0, 12.0)


@pytest.mark.parametrize("ratio,expected", [(0.9, 900), (1.0, 990)])
def test_explicit_ratio_can_only_advance_buffer_cutoff(monkeypatch, ratio, expected):
    result, _ = run_scripted_watch(monkeypatch, [playback_state(expected)],
        duration_seconds=None, end_buffer_seconds=10, completion_ratio=ratio)
    assert result.reason == "completion_cutoff"
    assert not result.playback_finished


def test_tiny_video_cannot_complete_at_zero(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(0, duration=1), playback_state(0.5, duration=1),
    ], duration_seconds=None, end_buffer_seconds=120)
    assert result.reason == "completion_cutoff"
    assert result.final_current_time > 0


def test_full_video_buffer_does_not_shorten_fixed_watch(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(p, duration=10) for p in (0, 3, 6, 9)
    ], duration_seconds=9, end_buffer_seconds=120)
    assert result.reason == "duration_reached"
    assert result.watched_seconds == 9


def test_ad_position_does_not_satisfy_full_video_cutoff(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(999, isAd=True), playback_state(990),
    ], duration_seconds=None, end_buffer_seconds=10, skip_ads=False, max_ad_seconds=0)
    assert result.reason == "completion_cutoff"
    assert result.ad_seconds == 0  # First probe establishes the ad clock.
    assert result.final_current_time == 990


@pytest.mark.parametrize("probe", [
    playback_state(0),
    playback_state(0, readyState=1),
])
def test_one_deadline_covers_every_player_state(monkeypatch, probe):
    result, _ = run_scripted_watch(monkeypatch, [probe] * 4,
        duration_seconds=None, full_timeout_seconds=6, skip_ads=False, max_ad_seconds=0)
    assert result.reason == "full_timeout"
    assert not result.completed and not result.playback_finished


def test_video_timer_starts_after_ad_is_skipped_and_video_playing(monkeypatch):
    """Verify video watch timer and safety timeout start only after ad completes and video plays."""
    samples = [
        # 5 ad samples (15s total of ad)
        playback_state(0, isAd=True),
        playback_state(0, isAd=True),
        playback_state(0, isAd=True),
        playback_state(0, isAd=True),
        playback_state(0, isAd=True),
        # Ad ends, video starts playing
        playback_state(1, isAd=False),
        playback_state(4, isAd=False),
        playback_state(7, isAd=False),
    ]
    # full_timeout_seconds=6: should NOT fire during the 15s of ad.
    # It should only fire after 6s of main video playback.
    result, _ = run_scripted_watch(
        monkeypatch,
        samples,
        duration_seconds=None,
        full_timeout_seconds=6,
        skip_ads=False,
        max_ad_seconds=0,
    )
    assert result.reason == "full_timeout"
    assert not result.completed
    assert result.ad_seconds == 12  # ticks tracked during ad
    assert result.watched_seconds == 6  # 6s watched after video started


def test_midroll_ad_freezes_video_watch_timer(monkeypatch):
    """Verify mid-roll ad freezes the video timer so ad time does not consume video timeout."""
    samples = [
        # 2 video samples (3s video playback)
        playback_state(1, isAd=False),
        playback_state(4, isAd=False),
        # 3 mid-roll ad samples (9s ad playback)
        playback_state(100, isAd=True),
        playback_state(100, isAd=True),
        playback_state(100, isAd=True),
        # Video resumes (3s video playback -> reaches 6s total video time)
        playback_state(7, isAd=False),
        playback_state(10, isAd=False),
    ]
    result, _ = run_scripted_watch(
        monkeypatch,
        samples,
        duration_seconds=None,
        full_timeout_seconds=6,
        skip_ads=False,
        max_ad_seconds=0,
    )
    assert result.reason == "full_timeout"
    assert result.watched_seconds == 6


def test_timeout_preserves_final_measured_interval(monkeypatch):
    result, ticks = run_scripted_watch(monkeypatch, [playback_state(p) for p in (0, 3, 6)],
        duration_seconds=None, full_timeout_seconds=6)
    assert result.reason == "full_timeout"
    assert result.watched_seconds == sum(ticks) == 6


def test_timeout_zero_and_wall_clock_changes(monkeypatch):
    def forbidden_wall_clock():
        raise AssertionError("Elapsed time must use monotonic clock")

    result, _ = run_scripted_watch(monkeypatch, [playback_state(0)] * 25,
        duration_seconds=None, full_timeout_seconds=0, wall_clock=forbidden_wall_clock)
    assert result.reason == "interrupted"
    assert result.watched_seconds == 0


def test_user_pause_does_not_consume_safety_timeout(monkeypatch):
    import threading

    pause = threading.Event()
    sleeps = []

    def pause_then_resume(now, duration):
        sleeps.append(duration)
        if len(sleeps) == 1:
            pause.set()
        if now >= 113:
            pause.clear()

    result, _ = run_scripted_watch(monkeypatch, [playback_state(p) for p in (0, 3, 6)],
        duration_seconds=None, full_timeout_seconds=6, pause_event=pause,
        sleep_hook=pause_then_resume)
    assert result.reason == "full_timeout"
    assert sum(sleeps) == 16  # 6 active seconds plus 10 explicitly paused.


def test_real_end_observed_at_deadline_wins(monkeypatch):
    result, _ = run_scripted_watch(monkeypatch, [
        playback_state(0, duration=6), playback_state(3, duration=6),
        playback_state(6, duration=6, ended=True),
    ], duration_seconds=None, full_timeout_seconds=6)
    assert result.reason == "ended"
    assert result.completed and result.playback_finished
