from unittest.mock import MagicMock

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


def test_watch_video_stops_early_before_autoplay():
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
    )

    assert result.completed is True
    assert result.playback_finished is True
    assert result.reason == "ended"


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
