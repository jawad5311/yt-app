import threading
from unittest.mock import MagicMock

from src.automation.youtube_actions import (
    calculate_end_buffer,
    disable_player_autoplay,
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

    # Sequence of states: video duration 100s, starts at 85s, then 92s (which is >= 100 - 10s buffer)
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
        duration_seconds=None,  # Full Video
        end_buffer_seconds=10.0,
    )

    assert result.completed is True
    assert result.reason == "ended"


def test_watch_video_detects_autoplay_transition_to_new_url():
    """Verify that if YouTube navigates away to a new video, watch_video stops immediately."""
    driver = MagicMock()
    urls = [
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
