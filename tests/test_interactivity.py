import queue
import threading
from unittest.mock import MagicMock

from src.automation.interactivity import (
    HumanInteractivitySimulator,
    get_playback_speed,
    set_playback_speed,
    set_video_quality,
    simulate_micro_pause,
    simulate_reading_comments_or_description,
)
from src.automation.youtube_actions import watch_video


def test_set_and_get_playback_speed():
    driver = MagicMock()
    driver.execute_script.return_value = True

    assert set_playback_speed(driver, 1.25) is True
    driver.execute_script.assert_called()

    driver.execute_script.return_value = 1.25
    assert get_playback_speed(driver) == 1.25

    # Exception safety
    driver.execute_script.side_effect = Exception("JS error")
    assert set_playback_speed(driver, 1.25) is False
    assert get_playback_speed(driver) == 1.0


def test_set_video_quality():
    driver = MagicMock()
    driver.execute_script.return_value = True

    assert set_video_quality(driver, "360p") is True
    driver.execute_script.assert_called()

    assert set_video_quality(driver, "720p") is True
    assert set_video_quality(driver, "480p") is True
    assert set_video_quality(driver, "auto") is True

    # Exception safety
    driver.execute_script.side_effect = Exception("failed to set quality")
    assert set_video_quality(driver, "360p") is False


def test_simulate_reading_comments_or_description_normal():
    driver = MagicMock()
    q: queue.Queue[str] = queue.Queue()

    ok = simulate_reading_comments_or_description(
        driver,
        stop_event=None,
        log_queue=q,
        min_read_seconds=0.01,
        max_read_seconds=0.02,
    )
    assert ok is True
    # At least two scrollBys and one scrollTo
    assert driver.execute_script.call_count >= 3
    # Check that log message was emitted
    log_msg = q.get_nowait()
    assert "browsing" in log_msg.lower() or "browse" in log_msg.lower() or "scrolling" in log_msg.lower()


def test_simulate_reading_comments_or_description_interrupted():
    driver = MagicMock()
    stop_event = threading.Event()
    stop_event.set()

    ok = simulate_reading_comments_or_description(
        driver,
        stop_event=stop_event,
        min_read_seconds=0.1,
        max_read_seconds=0.2,
    )
    assert ok is False


def test_simulate_micro_pause_normal():
    driver = MagicMock()
    q: queue.Queue[str] = queue.Queue()

    paused_time = simulate_micro_pause(
        driver,
        pause_duration=0.05,
        stop_event=None,
        log_queue=q,
    )
    assert paused_time == 0.05
    # Video pause and video play were both called
    calls = [call.args[0] for call in driver.execute_script.call_args_list]
    assert any("pause()" in str(c) for c in calls)
    assert any("play()" in str(c) for c in calls)

    log_msg = q.get_nowait()
    assert "micro-pause" in log_msg


def test_simulate_micro_pause_interrupted():
    driver = MagicMock()
    stop_event = threading.Event()
    stop_event.set()

    paused_time = simulate_micro_pause(
        driver,
        pause_duration=1.0,
        stop_event=stop_event,
    )
    assert paused_time == 0.0


def test_simulator_plan_session_long_video():
    sim = HumanInteractivitySimulator(
        enabled=True,
        scroll_comments=True,
        variable_speed=True,
        adjust_quality=True,
        micro_pauses=True,
        speed_chance=1.0,  # force speed change
        quality_chance=1.0,  # force quality change
    )

    sim.plan_session(video_duration=300.0)
    assert sim.planned is True
    assert sim.is_long_video is True
    assert sim.will_change_speed is True
    assert sim.will_adjust_quality is True
    assert 25.0 <= sim.micro_pause_target_time <= 285.0
    assert 25.0 <= sim.scroll_target_time <= 240.0


def test_simulator_plan_session_short_video():
    sim = HumanInteractivitySimulator(
        enabled=True,
        micro_pauses=True,
        scroll_comments=True,
    )

    sim.plan_session(video_duration=45.0, duration_budget=45)
    assert sim.planned is True
    assert sim.is_long_video is False


def test_simulator_on_playback_tick_triggers_actions():
    driver = MagicMock()
    driver.execute_script.return_value = True
    q: queue.Queue[str] = queue.Queue()

    sim = HumanInteractivitySimulator(
        enabled=True,
        scroll_comments=True,
        variable_speed=True,
        adjust_quality=True,
        micro_pauses=True,
        speed_chance=1.0,
        quality_chance=1.0,
    )
    sim.plan_session(video_duration=200.0)
    # Set targets to exact known times
    sim.micro_pause_target_time = 15.0
    sim.scroll_target_time = 30.0

    # Early tick at 7s -> triggers quality and speed
    sim.on_playback_tick(
        driver,
        curr_time=7.0,
        video_duration=200.0,
        is_ad=False,
        paused=False,
        log_queue=q,
    )
    assert sim.speed_applied is True
    assert sim.quality_applied is True

    # Midway tick at 15s -> triggers micro-pause
    res2 = sim.on_playback_tick(
        driver,
        curr_time=15.0,
        video_duration=200.0,
        is_ad=False,
        paused=False,
        log_queue=q,
    )
    assert "micro_paused" in res2
    assert sim.micro_pause_done is True

    # Later tick at 30s -> triggers scrolling comments
    res3 = sim.on_playback_tick(
        driver,
        curr_time=30.0,
        video_duration=200.0,
        is_ad=False,
        paused=False,
        log_queue=q,
    )
    assert res3.get("scrolled_comments") is True
    assert sim.scroll_comments_done is True


def test_watch_video_with_interactivity_disabled():
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/watch?v=mockvideo1"

    states = [
        {"found": True, "paused": False, "ended": False, "currentTime": 10.0, "duration": 100.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 95.0, "duration": 100.0, "isAd": False},
    ]

    def mock_exec(script, *args):
        if "currentTime" in script:
            return states.pop(0) if states else {"found": True, "paused": False, "ended": True, "currentTime": 100.0, "duration": 100.0, "isAd": False}
        return True

    driver.execute_script.side_effect = mock_exec

    result = watch_video(
        driver,
        duration_seconds=None,
        end_buffer_seconds=10.0,
        enable_interactivity=False,
    )
    assert result.completed is True
    assert result.reason == "ended"
