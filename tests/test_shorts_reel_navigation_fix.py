"""Tests for YouTube Shorts reel navigation and stuck resolution."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest
from selenium.webdriver.common.keys import Keys

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel


@pytest.fixture
def test_worker(tmp_path):
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    account = Account(email="shortstest@gmail.com", password="password123")
    channel = Channel(name="TestChannel", url="https://youtube.com/@testchannel", enabled=True)
    config_mgr.set_channels([channel.to_dict()])

    log_q = queue.Queue()
    stop_ev = threading.Event()
    event_q = queue.Queue()

    worker = WatchingWorker(
        account=account,
        channels=[channel],
        config_mgr=config_mgr,
        log_queue=log_q,
        stop_event=stop_ev,
        event_queue=event_q,
    )
    worker.driver = MagicMock()
    return worker


def test_scroll_to_next_short_sends_arrow_down_and_detects_url_change(test_worker):
    """Verify scroll_to_next_short uses ArrowDown and detects url progression."""
    url_states = [
        "https://www.youtube.com/shorts/11111111111",
        "https://www.youtube.com/shorts/11111111111",
        "https://www.youtube.com/shorts/22222222222",
    ]
    def _get_url():
        return url_states.pop(0) if url_states else "https://www.youtube.com/shorts/22222222222"

    type(test_worker.driver).current_url = property(lambda self: _get_url())
    test_worker.driver.execute_script = MagicMock(return_value=False)
    test_worker.driver.find_elements = MagicMock(return_value=[])

    with patch("src.automation.watching_worker.ActionChains") as mock_ac_cls, \
         patch("time.sleep"):
        mock_ac = MagicMock()
        mock_ac.send_keys.return_value = mock_ac
        mock_ac_cls.return_value = mock_ac
        res = test_worker.scroll_to_next_short(prev_url="https://www.youtube.com/shorts/11111111111")

    assert res is True
    # Verify send_keys was called with ARROW_DOWN
    assert mock_ac.send_keys.called
    calls_args = [call[0] for call in mock_ac.send_keys.call_args_list]
    assert any(Keys.ARROW_DOWN in arg for arg in calls_args)


def test_scroll_to_next_short_clicks_navigation_down_button(test_worker):
    """Verify that when down navigation button is present and displayed, ActionChains clicks it."""
    mock_btn = MagicMock()
    mock_btn.is_displayed.return_value = True
    test_worker.driver.find_elements = MagicMock(return_value=[mock_btn])
    test_worker.driver.execute_script = MagicMock(return_value=False)
    test_worker.driver.current_url = "https://www.youtube.com/shorts/22222222222"

    with patch("src.automation.watching_worker.ActionChains") as mock_ac_cls, \
         patch("time.sleep"):
        mock_ac = MagicMock()
        mock_ac_cls.return_value = mock_ac
        mock_ac.move_to_element.return_value = mock_ac
        mock_ac.pause.return_value = mock_ac
        mock_ac.click.return_value = mock_ac
        mock_ac.send_keys.return_value = mock_ac
        res = test_worker.scroll_to_next_short(prev_url="https://www.youtube.com/shorts/11111111111")

    assert res is True
    mock_ac.move_to_element.assert_called_with(mock_btn)
    mock_ac.click.assert_called()


def test_reel_loop_consecutive_same_video_advances_and_recovers(test_worker):
    """When same video is observed, the loop attempts advancement up to 4 times and continues without premature abort."""
    short_infos = [
        {"found": True, "video_id": "11111111111", "title": "Stuck Short", "channel_name": "@Untargeted", "url": "https://www.youtube.com/shorts/11111111111"},
        {"found": True, "video_id": "11111111111", "title": "Stuck Short", "channel_name": "@Untargeted", "url": "https://www.youtube.com/shorts/11111111111"},
        {"found": True, "video_id": "22222222222", "title": "New Short", "channel_name": "@Untargeted", "url": "https://www.youtube.com/shorts/22222222222"},
    ]

    test_worker.get_active_short_info = MagicMock(side_effect=short_infos)
    test_worker.scroll_to_next_short = MagicMock(return_value=True)
    test_worker._is_unknown_channel_short = MagicMock(return_value=True)

    # Allow 1 non-target short to exit loop gracefully
    test_worker.config_mgr.set_setting("NonTargetReelsLimit", 1)

    with patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.time.sleep"):
        res = test_worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
            current_total_non_target=0,
            cycle_max_total_non_target=10,
            initial_prev_url="https://www.youtube.com/shorts/11111111111",
        )

    # Should have attempted scroll on same video and then progressed to 22222222222 and exited with reel_limit_reached (1 non-target)
    assert test_worker.scroll_to_next_short.call_count >= 2
    assert res.exit_reason == "reel_limit_reached"


def test_run_shorts_cycle_aborts_on_consecutive_reel_stuck(test_worker):
    """Verify that _run_shorts_cycle terminates instead of thrashing indefinitely when reel is repeatedly stuck."""
    from src.automation.watching_worker import ReelLoopResult

    test_worker._has_suggested_shorts_shelf = MagicMock(return_value=False)
    test_worker.find_target_short_on_suggested_shelf = MagicMock(return_value=None)
    test_worker.find_target_short_on_home = MagicMock(return_value=None)
    test_worker.fetch_home_shorts = MagicMock(return_value=[])
    test_worker.click_shorts_tab = MagicMock(return_value=True)
    test_worker.get_active_short_info = MagicMock(return_value={"found": True, "title": "Untargeted", "channel_name": "Other"})
    test_worker.scroll_to_next_short = MagicMock(return_value=True)

    # Reel loop returns reel_stuck every time
    test_worker._run_shorts_reel_loop = MagicMock(return_value=ReelLoopResult(0, 1, "reel_stuck"))

    with patch("src.automation.watching_worker.safe_get"), \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("time.sleep"):
        watched = test_worker._run_shorts_cycle(session_watched_keys=set())

    # Must abort after 2 consecutive stuck exits instead of looping indefinitely
    assert test_worker._run_shorts_reel_loop.call_count == 2
    assert watched == 0


def test_run_shorts_cycle_aborts_on_login_verification_failed(test_worker):
    """Verify that _run_shorts_cycle immediately halts on login_verification_failed."""
    from src.automation.watching_worker import ReelLoopResult

    test_worker._has_suggested_shorts_shelf = MagicMock(return_value=False)
    test_worker.find_target_short_on_suggested_shelf = MagicMock(return_value=None)
    test_worker.find_target_short_on_home = MagicMock(return_value=None)
    test_worker.fetch_home_shorts = MagicMock(return_value=[])
    test_worker.click_shorts_tab = MagicMock(return_value=True)
    test_worker.get_active_short_info = MagicMock(return_value={"found": True, "title": "Untargeted", "channel_name": "Other"})
    test_worker.scroll_to_next_short = MagicMock(return_value=True)

    # Reel loop returns login_verification_failed
    test_worker._run_shorts_reel_loop = MagicMock(return_value=ReelLoopResult(0, 0, "login_verification_failed"))

    with patch("src.automation.watching_worker.safe_get"), \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("time.sleep"):
        watched = test_worker._run_shorts_cycle(session_watched_keys=set())

    # Must halt immediately after 1 attempt and set flag
    assert test_worker._run_shorts_reel_loop.call_count == 1
    assert watched == 0
    assert test_worker._last_login_verification_failed is True
