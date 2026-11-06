import queue
import threading
from unittest.mock import MagicMock, patch

from src.models.channel import Channel
from src.automation.watching_worker import WatchingWorker


def test_is_unknown_channel_short():
    # Unknown cases
    assert WatchingWorker._is_unknown_channel_short(None, "Valid Title") is True
    assert WatchingWorker._is_unknown_channel_short("", "Valid Title") is True
    assert WatchingWorker._is_unknown_channel_short("Unknown", "Valid Title") is True
    assert WatchingWorker._is_unknown_channel_short("Unknown Channel", "Valid Title") is True
    assert WatchingWorker._is_unknown_channel_short("none", "Valid Title") is True
    assert WatchingWorker._is_unknown_channel_short("@ValidChannel", None) is True
    assert WatchingWorker._is_unknown_channel_short("@ValidChannel", "") is True
    assert WatchingWorker._is_unknown_channel_short("@ValidChannel", "Short") is True
    assert WatchingWorker._is_unknown_channel_short("@ValidChannel", "Untargeted Short") is True
    assert WatchingWorker._is_unknown_channel_short("@ValidChannel", "unknown") is True

    # Actual known channel and title
    assert WatchingWorker._is_unknown_channel_short("@BOLNewsofficial", "Breaking News Pakistan") is False
    assert WatchingWorker._is_unknown_channel_short("BBC News", "Global Climate Summit Report") is False
    assert WatchingWorker._is_unknown_channel_short("@shewasmy", "Emotional Story Episode 1") is False


def test_reel_loop_unknown_channel_watch_duration():
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock()
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []

    short_unknown = {
        "title": "Short",
        "channel_name": "Unknown Channel",
        "channel_url": "",
        "video_id": "unk123",
        "url": "https://youtube.com/shorts/unk123",
        "found": True,
    }
    worker.get_active_short_info = MagicMock(return_value=short_unknown)

    uniform_calls = []
    def mock_uniform(a, b):
        uniform_calls.append((a, b))
        return a

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=1), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert reason == "reel_limit_reached"
        assert (1.0, 2.0) in uniform_calls

        emitted = []
        while not log_q.empty():
            emitted.append(log_q.get_nowait())

        assert any("Short from unknown channel" in msg and "1.0s" in msg for msg in emitted)


def test_reel_loop_actual_nontarget_channel_watch_duration():
    log_q = queue.Queue()
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = log_q
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker.scroll_to_next_short = MagicMock()
    worker.config_mgr = MagicMock()
    worker.config_mgr.get_setting.return_value = "10"
    worker.config_mgr.get_int.return_value = 10
    worker.config_mgr.get_video_queue.return_value = []

    short_actual = {
        "title": "Breaking News from Islamabad",
        "channel_name": "@BOLNewsofficial",
        "channel_url": "https://youtube.com/@BOLNewsofficial",
        "video_id": "bol456",
        "url": "https://youtube.com/shorts/bol456",
        "found": True,
    }
    worker.get_active_short_info = MagicMock(return_value=short_actual)

    uniform_calls = []
    def mock_uniform(a, b):
        uniform_calls.append((a, b))
        return 3.5

    with patch("random.uniform", side_effect=mock_uniform), \
         patch("random.randint", return_value=1), \
         patch("time.sleep"):

        watched, non_targets, reason = worker._run_shorts_reel_loop(
            session_watched_keys=set(),
            current_target_watched=0,
            cycle_target_shorts=5,
        )

        assert non_targets == 1
        assert reason == "reel_limit_reached"
        assert (2.0, 6.0) in uniform_calls

        emitted = []
        while not log_q.empty():
            emitted.append(log_q.get_nowait())

        assert any("Short from '@BOLNewsofficial' is not in targeted channels list. Watching for 3.5s randomly before skipping" in msg for msg in emitted)
