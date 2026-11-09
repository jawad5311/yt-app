"""Tests for Fullscreen & Theater Mode Human Interactivity Simulation."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.interactivity import (
    HumanInteractivitySimulator,
    enter_fullscreen_mode,
    enter_theater_mode,
    exit_fullscreen_mode,
    exit_theater_mode,
    is_in_fullscreen,
    is_in_theater,
)
from src.automation.youtube_actions import watch_video
from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


def test_fullscreen_helpers():
    driver = MagicMock()
    log_q = queue.Queue()

    # is_in_fullscreen True
    driver.execute_script.return_value = True
    assert is_in_fullscreen(driver) is True

    # is_in_fullscreen False
    driver.execute_script.return_value = False
    assert is_in_fullscreen(driver) is False

    # enter_fullscreen_mode executes JS and logs
    driver.execute_script.return_value = {"success": True, "mode": "fullscreen"}
    assert enter_fullscreen_mode(driver, log_queue=log_q) is True
    driver.execute_script.assert_called()
    assert not log_q.empty()
    msg = log_q.get()
    assert "switched video to full screen mode" in msg

    # exit_fullscreen_mode executes JS and logs
    driver.execute_script.return_value = {"success": True, "mode": "standard"}
    assert exit_fullscreen_mode(driver, log_queue=log_q) is True
    assert not log_q.empty()
    msg2 = log_q.get()
    assert "exited full screen mode" in msg2


def test_theater_helpers():
    driver = MagicMock()
    log_q = queue.Queue()

    # is_in_theater
    driver.execute_script.return_value = True
    assert is_in_theater(driver) is True

    driver.execute_script.return_value = False
    assert is_in_theater(driver) is False

    # enter_theater_mode
    driver.execute_script.return_value = {"success": True, "mode": "theater"}
    assert enter_theater_mode(driver, log_queue=log_q) is True
    assert not log_q.empty()
    assert "switched video to theater mode" in log_q.get()

    # exit_theater_mode
    driver.execute_script.return_value = {"success": True, "mode": "standard"}
    assert exit_theater_mode(driver, log_queue=log_q) is True
    assert not log_q.empty()
    assert "exited theater mode" in log_q.get()


def test_simulator_plans_fullscreen_and_theater_distribution():
    # When fullscreen_chance is 1.0 -> always chooses fullscreen
    sim_fs = HumanInteractivitySimulator(
        enabled=True,
        fullscreen_theater_mode=True,
        fullscreen_chance=1.0,
    )
    sim_fs.plan_session(video_duration=300.0)
    assert sim_fs.screen_mode_chosen == "fullscreen"
    assert sim_fs.screen_mode_enter_time > 0.0

    # When fullscreen_chance is 0.0 -> always chooses theater
    sim_th = HumanInteractivitySimulator(
        enabled=True,
        fullscreen_theater_mode=True,
        fullscreen_chance=0.0,
    )
    sim_th.plan_session(video_duration=300.0)
    assert sim_th.screen_mode_chosen == "theater"
    assert sim_th.screen_mode_enter_time > 0.0

    # When fullscreen_theater_mode is False -> neither is planned
    sim_disabled = HumanInteractivitySimulator(
        enabled=True,
        fullscreen_theater_mode=False,
    )
    sim_disabled.plan_session(video_duration=300.0)
    assert sim_disabled.screen_mode_chosen is None
    assert sim_disabled.screen_mode_enter_time == 0.0


def test_simulator_enters_fullscreen_after_few_seconds_and_exits_at_94_percent():
    driver = MagicMock()
    log_q = queue.Queue()

    sim = HumanInteractivitySimulator(
        enabled=True,
        fullscreen_theater_mode=True,
        fullscreen_chance=1.0,  # Force fullscreen
        screen_mode_enter_delay=5.0,
        cutoff_progress=0.94,
    )
    sim.plan_session(video_duration=100.0)
    assert sim.screen_mode_chosen == "fullscreen"

    with patch("src.automation.interactivity.enter_fullscreen_mode") as mock_enter, \
         patch("src.automation.interactivity.exit_fullscreen_mode") as mock_exit:

        mock_enter.return_value = True
        mock_exit.return_value = True

        # At 2s: Before delay -> screen mode not entered yet
        res1 = sim.on_playback_tick(driver, curr_time=2.0, video_duration=100.0, is_ad=False, paused=False, log_queue=log_q)
        assert mock_enter.call_count == 0
        assert sim.screen_mode_entered is False
        assert "screen_mode" not in res1

        # While ad is displaying at 5s -> should NOT enter mode
        res_ad = sim.on_playback_tick(driver, curr_time=5.0, video_duration=100.0, is_ad=True, paused=False, log_queue=log_q)
        assert mock_enter.call_count == 0

        # At 5s after ad skipped and video is playing -> enters fullscreen!
        res2 = sim.on_playback_tick(driver, curr_time=5.0, video_duration=100.0, is_ad=False, paused=False, log_queue=log_q)
        assert mock_enter.call_count == 1
        assert sim.screen_mode_entered is True
        assert res2.get("screen_mode") == "fullscreen"

        # At 50s (50% progress) -> still watching in fullscreen, not exited
        res3 = sim.on_playback_tick(driver, curr_time=50.0, video_duration=100.0, is_ad=False, paused=False, log_queue=log_q)
        assert mock_exit.call_count == 0
        assert sim.screen_mode_exited is False

        # At 94s (94% progress) -> reaches 94%, exits fullscreen to browse suggested section!
        res4 = sim.on_playback_tick(driver, curr_time=94.0, video_duration=100.0, is_ad=False, paused=False, log_queue=log_q)
        assert mock_exit.call_count == 1
        assert sim.screen_mode_exited is True
        assert res4.get("screen_mode_exited") is True
        assert res4.get("target_cutoff_reached") is True

        # Verify log message
        logs = []
        while not log_q.empty():
            logs.append(log_q.get())
        combined = " ".join(logs)
        assert "94%" in combined
        assert "Exited full screen to standard mode" in combined


def test_simulator_enters_theater_after_few_seconds_and_keeps_theater_at_94_percent():
    driver = MagicMock()
    log_q = queue.Queue()

    sim = HumanInteractivitySimulator(
        enabled=True,
        fullscreen_theater_mode=True,
        fullscreen_chance=0.0,  # Force theater mode
        screen_mode_enter_delay=4.0,
        cutoff_progress=0.94,
    )
    sim.plan_session(video_duration=100.0)
    assert sim.screen_mode_chosen == "theater"

    with patch("src.automation.interactivity.enter_theater_mode") as mock_enter, \
         patch("src.automation.interactivity.exit_theater_mode") as mock_exit:

        mock_enter.return_value = True
        mock_exit.return_value = True

        # At 4s: enters theater mode
        res1 = sim.on_playback_tick(driver, curr_time=4.0, video_duration=100.0, is_ad=False, paused=False, log_queue=log_q)
        assert mock_enter.call_count == 1
        assert sim.screen_mode_entered is True
        assert res1.get("screen_mode") == "theater"

        # At 94s: reaches 94% progress -> NO NEED to exit theater mode! Keeps theater mode.
        res2 = sim.on_playback_tick(driver, curr_time=94.0, video_duration=100.0, is_ad=False, paused=False, log_queue=log_q)
        assert mock_exit.call_count == 0  # Did not exit theater mode
        assert sim.screen_mode_exited is True
        assert res2.get("screen_mode_exited") is False
        assert res2.get("target_cutoff_reached") is True

        logs = []
        while not log_q.empty():
            logs.append(log_q.get())
        combined = " ".join(logs)
        assert "94%" in combined
        assert "Remaining in theater mode" in combined


def test_watch_video_with_fullscreen_simulation_cutoff_at_94_percent():
    driver = MagicMock()
    log_q = queue.Queue()

    # 1000s duration video -> 94% is 940s
    states = [
        {"found": True, "paused": False, "ended": False, "currentTime": 10.0, "duration": 1000.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 500.0, "duration": 1000.0, "isAd": False},
        {"found": True, "paused": False, "ended": False, "currentTime": 941.0, "duration": 1000.0, "isAd": False},
    ]

    def mock_exec(script, *args):
        if "currentTime" in script:
            return states.pop(0) if states else {"found": True, "paused": False, "ended": True, "currentTime": 1000.0, "duration": 1000.0, "isAd": False}
        return {"success": True}

    driver.execute_script.side_effect = mock_exec

    with patch("src.automation.youtube_actions.ensure_video_playing", return_value=True), \
         patch("src.automation.youtube_actions.disable_player_autoplay", return_value=True), \
         patch("src.automation.youtube_actions.find_skip_button", return_value=None), \
         patch("src.automation.youtube_actions.time.sleep", return_value=None), \
         patch("src.automation.youtube_actions.exit_fullscreen_mode") as mock_exit_fs:

        res = watch_video(
            driver,
            duration_seconds=None,  # Full Video mode
            log_queue=log_q,
            enable_interactivity=True,
            interactivity_config={"fullscreen_theater_mode": True},
        )

        assert res.completed is True
        assert res.reason == "completion_cutoff"

        logs = []
        while not log_q.empty():
            logs.append(log_q.get())
        combined = " ".join(logs)
        assert "target progress: 94%" in combined


def test_settings_view_fullscreen_theater_toggle(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)

    mgr.set_setting("SystemName", "TestPC")
    # Initial default is True
    assert mgr.get_bool("FullscreenTheaterMode", True) is True

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr
    view = SettingsView(ctk_app, app_ctrl)

    # Verify toggle switch exists in UI and is selected
    assert hasattr(view, "screen_mode_switch")
    assert view.screen_mode_switch.get() == 1

    # Deselect switch and save
    view.screen_mode_switch.deselect()
    assert view.screen_mode_switch.get() == 0

    view.save_settings()

    # Verify persisted in ConfigManager
    assert mgr.get_bool("FullscreenTheaterMode") is False

    # Re-select and save
    view.screen_mode_switch.select()
    assert view.screen_mode_switch.get() == 1
    view.save_settings()
    assert mgr.get_bool("FullscreenTheaterMode") is True
