import queue
import threading
from unittest.mock import MagicMock

from src.automation.interactivity import (
    browse_description_and_like_comment,
    click_and_verify_comment_like,
    HumanInteractivitySimulator,
    ensure_normal_speed,
    get_playback_speed,
    set_playback_speed,
    set_video_quality,
    simulate_micro_pause,
    simulate_reading_comments_or_description,
    toggle_captions,
    expand_video_description,
    like_random_comments,
    like_active_video,
)
from src.automation.youtube_actions import watch_video


def test_set_and_get_playback_speed():
    driver = MagicMock()
    driver.execute_script.return_value = True

    assert set_playback_speed(driver, 1.0) is True
    driver.execute_script.assert_called()

    driver.execute_script.return_value = 1.0
    assert get_playback_speed(driver) == 1.0

    # Ensure normal speed sets to 1.0 when speed is different
    driver.execute_script.return_value = 1.25
    assert ensure_normal_speed(driver) is True

    # Exception safety
    driver.execute_script.side_effect = Exception("JS error")
    assert set_playback_speed(driver, 1.0) is False
    assert get_playback_speed(driver) == 1.0


def test_set_video_quality_strictly_360_or_480():
    driver = MagicMock()
    driver.execute_script.return_value = True

    # Test 360p
    assert set_video_quality(driver, "360p") is True
    args = driver.execute_script.call_args[0]
    assert "360p" in args
    assert "medium" in args

    # Test 480p
    assert set_video_quality(driver, "480p") is True
    args = driver.execute_script.call_args[0]
    assert "480p" in args
    assert "large" in args

    # Non-standard or higher resolution defaults safely to 360p
    assert set_video_quality(driver, "720p") is True
    args = driver.execute_script.call_args[0]
    assert "360p" in args

    # Exception safety
    driver.execute_script.side_effect = Exception("failed to set quality")
    assert set_video_quality(driver, "360p") is False


def test_toggle_captions():
    driver = MagicMock()
    q: queue.Queue[str] = queue.Queue()

    # Success case: enabled
    driver.execute_script.return_value = {"success": True, "changed": True, "state": "enabled"}
    assert toggle_captions(driver, target_state="enable", log_queue=q) is True
    msg = q.get_nowait()
    assert "enabled closed captions" in msg

    # Success case: disabled
    driver.execute_script.return_value = {"success": True, "changed": True, "state": "disabled"}
    assert toggle_captions(driver, target_state="disable", log_queue=q) is True
    msg = q.get_nowait()
    assert "disabled closed captions" in msg

    # Already in desired state
    driver.execute_script.return_value = {"success": True, "changed": False, "state": "enabled"}
    assert toggle_captions(driver, target_state="enable", log_queue=q) is True

    # Exception safety
    driver.execute_script.side_effect = Exception("no captions available")
    assert toggle_captions(driver, target_state="enable") is False


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
    assert driver.execute_script.call_count >= 3
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
    calls = [call.args[0] for call in driver.execute_script.call_args_list]
    assert any("pause()" in str(c) for c in calls)
    assert any("play()" in str(c) for c in calls)

    log_pause = q.get_nowait()
    assert "paused video" in log_pause
    log_resume = q.get_nowait()
    assert "resumed video playback" in log_resume


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
        variable_speed=False,  # Strictly 1x
        adjust_quality=True,
        quality_level="480p",
        micro_pauses=True,
        toggle_captions=True,
        quality_chance=1.0,
        caption_chance=1.0,
    )

    sim.plan_session(video_duration=300.0)
    assert sim.planned is True
    assert sim.is_long_video is True
    assert sim.will_change_speed is False  # Speed remains 1x
    assert sim.will_adjust_quality is True
    assert sim.chosen_quality == "480p"
    assert sim.will_toggle_captions is True
    assert 15.0 <= sim.micro_pause_target_time <= 285.0
    assert 25.0 <= sim.scroll_target_time <= 240.0


def test_simulator_plan_session_short_video():
    sim = HumanInteractivitySimulator(
        enabled=True,
        micro_pauses=True,
        scroll_comments=True,
        toggle_captions=False,
    )

    sim.plan_session(video_duration=45.0, duration_budget=45)
    assert sim.planned is True
    assert sim.is_long_video is False
    assert sim.will_change_speed is False


def test_simulator_on_playback_tick_triggers_actions():
    driver = MagicMock()
    driver.execute_script.return_value = {"success": True, "changed": True, "state": "enabled"}
    q: queue.Queue[str] = queue.Queue()

    sim = HumanInteractivitySimulator(
        enabled=True,
        scroll_comments=True,
        variable_speed=False,  # strictly 1x
        adjust_quality=True,
        quality_level="360p",
        micro_pauses=True,
        toggle_captions=True,
        quality_chance=1.0,
        caption_chance=1.0,
    )
    sim.plan_session(video_duration=200.0)
    # Set targets to exact known times
    sim.caption_enable_time = 6.0
    sim.micro_pause_target_time = 15.0
    sim.scroll_target_time = 30.0

    # Early tick at 6s -> triggers quality and captions enable (no speed change)
    sim.on_playback_tick(
        driver,
        curr_time=6.0,
        video_duration=200.0,
        is_ad=False,
        paused=False,
        log_queue=q,
    )
    assert sim.quality_applied is True
    assert sim.caption_enable_done is True
    assert sim.will_change_speed is False

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


def test_expand_video_description():
    driver = MagicMock()
    q = queue.Queue()

    driver.execute_script.return_value = True
    assert expand_video_description(driver, log_queue=q) is True
    msg = q.get_nowait()
    assert "expand video description" in msg

    driver.execute_script.side_effect = Exception("not found")
    assert expand_video_description(driver) is False


def test_like_random_comments():
    driver = MagicMock()
    q = queue.Queue()

    driver.execute_script.return_value = {"found": True, "author": "@skywolf3r"}
    liked = like_random_comments(driver, min_likes=2, max_likes=2, log_queue=q)
    assert liked == 2
    msg = q.get_nowait()
    assert "liked comment by @skywolf3r" in msg

    # Interrupted or no comments case
    driver.execute_script.return_value = {"found": False}
    liked2 = like_random_comments(driver, min_likes=1, max_likes=2)
    assert liked2 == 0


def test_like_active_video():
    driver = MagicMock()
    q = queue.Queue()

    # Success: video was not liked before, now liked
    driver.execute_script.return_value = {"success": True, "already_liked": False}
    assert like_active_video(driver, log_queue=q) is True
    msg = q.get_nowait()
    assert "liked the video" in msg

    # Already liked: does not re-click or emit like log
    driver.execute_script.return_value = {"success": True, "already_liked": True}
    assert like_active_video(driver, log_queue=q) is True
    assert q.empty()

    # Failed / not found
    driver.execute_script.return_value = {"success": False}
    assert like_active_video(driver, log_queue=q) is False


def test_simulator_plans_like_video_at_50_to_60_percent():
    sim = HumanInteractivitySimulator(enabled=True, like_video=True)
    sim.plan_session(video_duration=200.0)
    assert sim.will_like_video is True
    # 50-60% of 200s is 100s - 120s, plus 2-6s delay -> between 102s and 126s
    assert 100.0 <= sim.like_video_target_time <= 130.0


def test_browse_description_and_like_comment_once():
    driver = MagicMock()
    q = queue.Queue()
    driver.execute_script.return_value = {"found": True, "author": "@tester"}

    desc_ok, comm_ok = browse_description_and_like_comment(
        driver,
        log_queue=q,
        expand_desc=True,
        like_comm=True,
    )
    assert desc_ok is True
    assert comm_ok is True


def test_click_and_verify_comment_like_rejects_already_liked():
    driver = MagicMock()
    mock_btn = MagicMock()
    # is_comment_liked returns True (already liked)
    driver.execute_script.return_value = True

    candidate = {"found": True, "button": mock_btn, "author": "@tester"}
    # Must reject liking an already-liked comment
    assert click_and_verify_comment_like(driver, candidate) is False
