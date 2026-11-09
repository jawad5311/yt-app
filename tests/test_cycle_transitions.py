"""Tests for cycle transitions between Home, Suggested, Search, and Shorts."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.models.watch_stats import WatchResult


@pytest.fixture
def test_worker(tmp_path):
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file=config_file)
    stats_file = tmp_path / "stats.json"
    stats_store = StatsStore(stats_file=stats_file)

    account = Account(email="cycletest@gmail.com", password="password123")
    channel = Channel(name="TestChannel", url="https://youtube.com/@testchannel", enabled=True)
    config_mgr.set_channels([channel.to_dict()])
    config_mgr.set_setting("EmailReportingEnabled", False)

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
        stats_store=stats_store,
        is_unlimited=True,
    )
    worker.driver = MagicMock()
    return worker


def test_suggested_cycle_transitions_to_shorts_when_target_reached(test_worker):
    """When cycle_long_vids_watched reaches target_long_vids_cycle after suggested video, _run_shorts_cycle is called."""
    test_worker.driver.current_url = "https://www.youtube.com/watch?v=start"
    test_worker._check_and_enter_night_sleep = MagicMock(return_value=False)
    test_worker._check_and_play_random_buffer_video = MagicMock(return_value=False)
    test_worker._check_account_login_before_play = MagicMock(return_value=True)

    rec_item = {"href": "/watch?v=sugg1", "title": "Suggested 1", "channel_name": "TestChannel"}
    test_worker.fetch_recommended_videos = MagicMock(return_value=[rec_item])
    target_match = (rec_item, test_worker.channels[0], "https://youtube.com/watch?v=sugg1", "Suggested 1", "TestChannel")
    test_worker._find_preferred_target_video = MagicMock(return_value=target_match)
    test_worker._play_target_video = MagicMock(return_value=WatchResult(completed=True, watched_seconds=100))

    shorts_called = threading.Event()

    def fake_shorts_cycle(session_watched_keys):
        shorts_called.set()
        test_worker.stop_event.set()  # Stop worker after shorts cycle runs
        return 5

    test_worker._run_shorts_cycle = MagicMock(side_effect=fake_shorts_cycle)

    with patch("src.automation.watching_worker.setup_driver", return_value=test_worker.driver), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.watching_worker.safe_get"), \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("src.automation.watching_worker.random.randint", return_value=1):  # Force cycle target = 1
        test_worker.run()

    assert shorts_called.is_set(), "_run_shorts_cycle should be called when suggested video reaches cycle target"
    test_worker._run_shorts_cycle.assert_called_once()


def test_search_cycle_transitions_to_shorts_when_target_reached(test_worker):
    """When cycle_long_vids_watched reaches target_long_vids_cycle after search video, _run_shorts_cycle is called."""
    test_worker.driver.current_url = "https://www.youtube.com/watch?v=start"
    test_worker._check_and_enter_night_sleep = MagicMock(return_value=False)
    test_worker._check_and_play_random_buffer_video = MagicMock(return_value=False)
    test_worker._check_account_login_before_play = MagicMock(return_value=True)

    # Simulate no video found in suggested section
    test_worker.fetch_recommended_videos = MagicMock(return_value=[])
    test_worker._find_preferred_target_video = MagicMock(return_value=None)

    # Search finds and plays a target video
    test_worker.search_and_play_target_video = MagicMock(return_value="TestChannel")

    shorts_called = threading.Event()

    def fake_shorts_cycle(session_watched_keys):
        shorts_called.set()
        test_worker.stop_event.set()
        return 3

    test_worker._run_shorts_cycle = MagicMock(side_effect=fake_shorts_cycle)

    with patch("src.automation.watching_worker.setup_driver", return_value=test_worker.driver), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.watching_worker.safe_get"), \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("src.automation.watching_worker.random.randint", return_value=1):  # Force cycle target = 1
        test_worker.run()

    assert shorts_called.is_set(), "_run_shorts_cycle should be called when search fallback reaches cycle target"
    test_worker._run_shorts_cycle.assert_called_once()


def test_search_playback_resets_suggested_misses_to_continue_suggestions(test_worker):
    """When search plays a video and cycle limit is not reached, suggested_miss_count is reset to 0."""
    test_worker.driver.current_url = "https://www.youtube.com/watch?v=start"
    test_worker._check_and_enter_night_sleep = MagicMock(return_value=False)
    test_worker._check_and_play_random_buffer_video = MagicMock(return_value=False)
    test_worker._check_account_login_before_play = MagicMock(return_value=True)

    iteration_count = [0]
    suggested_scanned_after_search = threading.Event()

    def fake_fetch_recommended():
        iteration_count[0] += 1
        if iteration_count[0] == 1:
            # First iteration: no suggestion found -> leads to search
            return []
        else:
            # Second iteration: browser is on search video's watch page -> scanning suggested section!
            suggested_scanned_after_search.set()
            test_worker.stop_event.set()
            return []

    test_worker.fetch_recommended_videos = MagicMock(side_effect=fake_fetch_recommended)
    test_worker._find_preferred_target_video = MagicMock(return_value=None)
    test_worker.search_and_play_target_video = MagicMock(return_value="TestChannel")

    with patch("src.automation.watching_worker.setup_driver", return_value=test_worker.driver), \
         patch("src.automation.watching_worker.perform_google_login", return_value=True), \
         patch("src.automation.watching_worker.safe_get") as mock_safe_get, \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("src.automation.watching_worker.random.randint", return_value=5):  # High cycle target so it doesn't switch to shorts yet
        test_worker.run()

    assert suggested_scanned_after_search.is_set(), "Should scan suggested section of the video found via search on next iteration"
    # Verify it did not immediately navigate away to YouTube home
    assert not any("youtube.com/" in str(call) and "/watch" not in str(call) for call in mock_safe_get.call_args_list)


