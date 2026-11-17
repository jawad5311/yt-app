"""Tests for scrolling Shorts reel within 1 second on ads, unknown channel handles, or missing channel handles."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.models.channel import Channel
from src.automation.watching_worker import (
    GET_ACTIVE_SHORT_INFO_JS,
    WatchingWorker,
)


def test_is_short_ad_detection():
    # Direct is_ad flag
    assert WatchingWorker.is_short_ad({"is_ad": True}) is True
    assert WatchingWorker.is_short_ad({"is_ad": False}) is False
    assert WatchingWorker.is_short_ad(None) is False
    assert WatchingWorker.is_short_ad({}) is False

    # Ad redirect URLs
    assert WatchingWorker.is_short_ad({"url": "https://www.googleadservices.com/pagead/aclk?..."}) is True
    assert WatchingWorker.is_short_ad({"href": "https://adclick.g.doubleclick.net/..."}) is True

    # Title indicators
    assert WatchingWorker.is_short_ad({"title": "Sponsored - Buy Now!"}) is True
    assert WatchingWorker.is_short_ad({"title": "Ad: Special Offer"}) is True
    assert WatchingWorker.is_short_ad({"title": "Ad Special Offer"}) is True
    assert WatchingWorker.is_short_ad({"title": "ad"}) is True

    # Channel name / handle indicators
    assert WatchingWorker.is_short_ad({"channel_name": "Sponsored Brand"}) is True
    assert WatchingWorker.is_short_ad({"channel_handle": "@sponsored"}) is True
    assert WatchingWorker.is_short_ad({"channel_handle": "ad"}) is True

    # Legitimate non-ad short
    assert WatchingWorker.is_short_ad({
        "title": "Funny Dog Tricks",
        "channel_name": "DogLovers",
        "channel_handle": "@doglovers",
        "url": "https://www.youtube.com/shorts/abc12345",
    }) is False


def test_is_unknown_channel_short_handles():
    # Ad short
    assert WatchingWorker._is_unknown_channel_short("Brand", "Title", is_ad=True) is True
    assert WatchingWorker._is_unknown_channel_short("Brand", "Title", short_info={"is_ad": True}) is True

    # Missing handle
    assert WatchingWorker._is_unknown_channel_short("Some Creator", "Cool Video", ch_handle="") is True

    # Unknown handle
    assert WatchingWorker._is_unknown_channel_short("Some Creator", "Cool Video", ch_handle="@unknown") is True
    assert WatchingWorker._is_unknown_channel_short("Some Creator", "Cool Video", ch_handle="unknown channel") is True
    assert WatchingWorker._is_unknown_channel_short("Some Creator", "Cool Video", ch_handle="none") is True

    # If the channel contains a channel handle, then it is NOT an ad
    assert WatchingWorker._is_unknown_channel_short("", "Cool Video", ch_handle="@creator") is False
    assert WatchingWorker._is_unknown_channel_short("Unknown Channel", "Cool Video", ch_handle="@creator") is False

    # Valid creator and handle
    assert WatchingWorker._is_unknown_channel_short("Creator Name", "Cool Video", ch_handle="@creator") is False


def test_shorts_reel_scrolls_ad_within_one_second():
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock(side_effect=lambda **kwargs: worker.stop_event.set())
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []

    ad_short_info = {
        "title": "Install Now",
        "channel_name": "Game Studio",
        "channel_handle": "",
        "channel_url": "",
        "video_id": "ad_vid_1",
        "url": "https://youtube.com/shorts/ad_vid_1",
        "is_ad": True,
        "found": True,
    }
    worker.get_active_short_info = MagicMock(return_value=ad_short_info)

    uniform_delays = []
    def mock_uniform(a, b):
        uniform_delays.append((a, b))
        return a

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=5), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert worker.scroll_to_next_short.called

        # Verify that all watch duration uniform calls had max bound <= 1.0 second
        watch_calls = [call for call in uniform_delays if call == (0.4, 0.8)]
        assert len(watch_calls) >= 1
        for a, b in watch_calls:
            assert b <= 1.0, f"Watch time upper bound {b} exceeds 1.0s limit"


def test_shorts_reel_scrolls_unknown_channel_within_one_second():
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock(side_effect=lambda **kwargs: worker.stop_event.set())
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []

    # Displays Unknown Channel instead of channel handle
    unknown_short_info = {
        "title": "Short",
        "channel_name": "Unknown Channel",
        "channel_handle": "",
        "channel_url": "",
        "video_id": "unk_vid_2",
        "url": "https://youtube.com/shorts/unk_vid_2",
        "is_ad": False,
        "found": True,
    }
    worker.get_active_short_info = MagicMock(return_value=unknown_short_info)

    uniform_delays = []
    def mock_uniform(a, b):
        uniform_delays.append((a, b))
        return a

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=5), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert worker.scroll_to_next_short.called

        watch_calls = [call for call in uniform_delays if call == (0.4, 0.8)]
        assert len(watch_calls) >= 1
        for a, b in watch_calls:
            assert b <= 1.0


def test_shorts_reel_scrolls_no_handle_found_within_one_second():
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock(side_effect=lambda **kwargs: worker.stop_event.set())
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []

    # No handle found in short_info
    no_handle_short_info = {
        "title": "Random Meme Video",
        "channel_name": "Meme Page",
        "channel_handle": "",
        "channel_url": "",
        "video_id": "meme_vid_3",
        "url": "https://youtube.com/shorts/meme_vid_3",
        "is_ad": False,
        "found": True,
    }
    worker.get_active_short_info = MagicMock(return_value=no_handle_short_info)

    uniform_delays = []
    def mock_uniform(a, b):
        uniform_delays.append((a, b))
        return a

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=5), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert worker.scroll_to_next_short.called

        watch_calls = [call for call in uniform_delays if call == (0.4, 0.8)]
        assert len(watch_calls) >= 1
        for a, b in watch_calls:
            assert b <= 1.0


def test_shorts_reel_known_channel_and_handle_watches_normal_duration():
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock(side_effect=lambda **kwargs: worker.stop_event.set())
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []

    # Valid known non-target channel with handle
    known_short_info = {
        "title": "Top 10 Gadgets",
        "channel_name": "MKBHD",
        "channel_handle": "@mkbhd",
        "channel_url": "https://youtube.com/@mkbhd",
        "video_id": "mkbhd_vid_4",
        "url": "https://youtube.com/shorts/mkbhd_vid_4",
        "is_ad": False,
        "found": True,
    }
    worker.get_active_short_info = MagicMock(return_value=known_short_info)

    uniform_delays = []
    def mock_uniform(a, b):
        uniform_delays.append((a, b))
        return 3.0

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=5), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert worker.scroll_to_next_short.called
        assert (2.0, 6.0) in uniform_delays


def test_random_channel_with_handle_is_not_an_ad():
    """Verify that a random channel with a handle (even if title is 'Short' or channel_name is empty) is not an ad."""
    # Channel with handle, generic title 'Short', and empty or unknown channel_name
    info = {
        "title": "Short",
        "channel_name": "",
        "channel_handle": "@professionalwriterurdu",
        "channel_url": "https://youtube.com/@professionalwriterurdu",
        "video_id": "wm0jl3yQvcs",
        "url": "https://youtube.com/shorts/wm0jl3yQvcs",
        "is_ad": False,
        "found": True,
    }

    # Must NOT be considered an ad
    assert WatchingWorker.is_short_ad(info) is False
    assert WatchingWorker._is_unknown_channel_short(
        ch_name=info["channel_name"],
        title=info["title"],
        ch_handle=info["channel_handle"],
        short_info=info,
    ) is False

    # In reel loop, it should watch for 2-6s randomly, not 1s ad scroll
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock(side_effect=lambda **kwargs: worker.stop_event.set())
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []
    worker.get_active_short_info = MagicMock(return_value=info)

    uniform_delays = []
    def mock_uniform(a, b):
        uniform_delays.append((a, b))
        return 4.5

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=5), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert (2.0, 6.0) in uniform_delays

    logs = []
    while not log_q.empty():
        logs.append(log_q.get())
    joined_logs = "\n".join(logs)

    assert "Short is an ad or from unknown channel/handle" not in joined_logs
    assert "Short from '@professionalwriterurdu' is not in targeted channels list" in joined_logs
