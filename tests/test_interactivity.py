import queue
import threading
from unittest.mock import MagicMock, patch

from src.automation.interactivity import (
    HumanInteractivitySimulator,
    browse_description_and_like_comment,
    click_and_verify_comment_like,
    ensure_normal_speed,
    expand_video_description,
    get_playback_speed,
    like_active_video,
    like_random_comments,
    set_playback_speed,
    set_video_quality,
    simulate_micro_pause,
    simulate_reading_comments_or_description,
    toggle_captions,
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
        pause_chance=1.0,
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
        pause_chance=1.0,
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
    # 95s has crossed the configured 90s cutoff for this 100s video.
    assert result.reason == "completion_cutoff"
    assert result.playback_finished is True


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


def test_simulator_plans_interactions_at_10_percent_and_70_to_80_percent():
    sim = HumanInteractivitySimulator(enabled=True, like_video=True, like_comments=True, expand_description=True)
    sim.plan_session(video_duration=200.0)

    # 1. Description expands after 10%
    assert sim.will_expand_description is True
    assert 19.0 <= sim.expand_description_target_time <= 25.0

    # 2. Like comments and video planned at 70-80% + random delay
    assert sim.will_like_video is True
    assert sim.will_like_comments is True
    # 70-80% of 200s is 140s - 160s, plus 2-6s delay -> 142s - 166s
    assert 140.0 <= sim.late_interactions_target_time <= 170.0
    assert sim.interaction_order in ("video_then_comments", "comments_then_video")


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

def test_like_random_comments_bounds():
    driver = MagicMock()
    driver.execute_script.return_value = {'found': True, 'author': '@user'}
    liked = like_random_comments(driver)
    assert liked in (1, 2)


def test_simulate_reading_comments_does_not_double_like_when_disabled():
    driver = MagicMock()
    driver.execute_script.return_value = {'found': True, 'author': '@user'}
    ok = simulate_reading_comments_or_description(
        driver,
        min_read_seconds=0.01,
        max_read_seconds=0.02,
        like_comments=False,
    )
    assert ok is True

def test_simulator_pauses_randomly():
    sim_no_pause = HumanInteractivitySimulator(enabled=True, micro_pauses=True, pause_chance=0.0)
    sim_no_pause.plan_session(video_duration=200.0)
    assert sim_no_pause.will_micro_pause is False
    assert sim_no_pause.micro_pause_target_time == 0.0

    sim_pause = HumanInteractivitySimulator(enabled=True, micro_pauses=True, pause_chance=1.0)
    sim_pause.plan_session(video_duration=200.0)
    assert sim_pause.will_micro_pause is True
    assert sim_pause.micro_pause_target_time > 0.0



def test_simulator_executes_10_percent_description_and_randomized_late_likes():
    driver = MagicMock()
    # Mock JS returns
    def exec_script(script, *args):
        if "tp-yt-paper-button#expand" in script or "description-inline-expander" in script:
            return {"expanded": True}
        if "ytd-toggle-button-renderer" in script or "like-button" in script or "LIKE_VIDEO_JS" in script:
            return {"success": True, "already_liked": False}
        if "ytd-comment-thread-renderer" in script or "LIKE_COMMENT_JS" in script:
            return {"found": True, "author": "@random_user"}
        return True

    driver.execute_script.side_effect = exec_script

    # Test 1: Order video_then_comments
    sim1 = HumanInteractivitySimulator(enabled=True, like_video=True, like_comments=True, expand_description=True)
    sim1.plan_session(video_duration=100.0)
    sim1.interaction_order = "video_then_comments"
    sim1.expand_description_target_time = 10.0
    sim1.late_interactions_target_time = 75.0

    call_order = []
    with patch("src.automation.interactivity.expand_video_description") as mock_exp, \
         patch("src.automation.interactivity.like_active_video") as mock_like_vid, \
         patch("src.automation.interactivity.like_random_comments") as mock_like_com, \
         patch("src.automation.interactivity._interruptible_sleep", return_value=False):

        mock_exp.side_effect = lambda *a, **k: call_order.append("expand_desc") or True
        mock_like_vid.side_effect = lambda *a, **k: call_order.append("like_video") or True
        mock_like_com.side_effect = lambda *a, **k: call_order.append("like_comment") or 1

        # At 5s: nothing due yet
        res = sim1.on_playback_tick(driver, curr_time=5.0, video_duration=100.0, is_ad=False, paused=False)
        assert len(call_order) == 0

        # At 10s: description expands!
        res = sim1.on_playback_tick(driver, curr_time=10.0, video_duration=100.0, is_ad=False, paused=False)
        assert call_order == ["expand_desc"]
        assert sim1.description_expanded is True

        # At 75s: late interactions fire in order: video then comments
        res = sim1.on_playback_tick(driver, curr_time=75.0, video_duration=100.0, is_ad=False, paused=False)
        assert call_order == ["expand_desc", "like_video", "like_comment"]
        assert sim1.video_liked is True
        assert sim1.comments_liked is True

    # Test 2: Order comments_then_video
    sim2 = HumanInteractivitySimulator(enabled=True, like_video=True, like_comments=True, expand_description=True)
    sim2.plan_session(video_duration=100.0)
    sim2.interaction_order = "comments_then_video"
    sim2.expand_description_target_time = 10.0
    sim2.late_interactions_target_time = 75.0

    call_order2 = []
    with patch("src.automation.interactivity.expand_video_description") as mock_exp, \
         patch("src.automation.interactivity.like_active_video") as mock_like_vid, \
         patch("src.automation.interactivity.like_random_comments") as mock_like_com, \
         patch("src.automation.interactivity._interruptible_sleep", return_value=False):

        mock_exp.side_effect = lambda *a, **k: call_order2.append("expand_desc") or True
        mock_like_vid.side_effect = lambda *a, **k: call_order2.append("like_video") or True
        mock_like_com.side_effect = lambda *a, **k: call_order2.append("like_comment") or 1

        # At 75s: late interactions fire in order: comments then video
        res = sim2.on_playback_tick(driver, curr_time=75.0, video_duration=100.0, is_ad=False, paused=False)
        assert call_order2 == ["expand_desc", "like_comment", "like_video"]
        assert sim2.video_liked is True
        assert sim2.comments_liked is True
