import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.models.watch_stats import WatchResult
from src.ui.views.dashboard_view import DashboardView, STATUS_INFO_COLOR


def make_dummy_worker(target_limit=1, is_unlimited=False, total_loops=None):
    account = Account(email="test@example.com", password="pw")
    channels = [Channel(name="TechChannel", url="https://youtube.com/@techchannel", enabled=True, archived=False)]
    config_mgr = MagicMock()
    config_mgr.get_bool.return_value = False
    config_mgr.get_int.return_value = 1
    config_mgr.get_channels.return_value = [c.to_dict() for c in channels]
    config_mgr.get_video_queue.return_value = []
    log_queue = queue.Queue()
    stop_event = threading.Event()
    worker = WatchingWorker(
        account=account,
        channels=channels,
        config_mgr=config_mgr,
        log_queue=log_queue,
        stop_event=stop_event,
        target_limit=target_limit,
        is_unlimited=is_unlimited,
        total_loops=total_loops,
        open_videos=True,
    )
    return worker


@patch("src.automation.watching_worker.dismiss_youtube_dialogs")
@patch("src.automation.watching_worker.safe_get")
@patch("src.automation.watching_worker.close_driver")
@patch("src.automation.watching_worker.human_like_delay")
@patch("src.automation.watching_worker.perform_google_login", return_value=True)
@patch("src.automation.watching_worker.setup_driver")
def test_browsing_limit_one_permits_one_target_attempt(mock_setup, mock_login, mock_delay, mock_close, mock_safe_get, mock_dialogs):
    worker = make_dummy_worker(target_limit=1, is_unlimited=False)
    mock_driver = MagicMock()
    mock_driver.current_url = "https://www.youtube.com/watch?v=12345"
    mock_driver.find_elements.return_value = [MagicMock()]
    mock_setup.return_value = mock_driver

    target_candidate = (
        {"title": "Tech Video", "href": "/watch?v=abc"},
        worker.channels[0],
        "https://www.youtube.com/watch?v=abc",
        "Tech Video",
        "TechChannel",
    )
    worker.fetch_recommended_videos = MagicMock(return_value=[{"title": "Tech Video", "href": "/watch?v=abc"}])
    worker._find_target_video = MagicMock(return_value=target_candidate)
    worker._play_target_video = MagicMock(return_value=WatchResult(completed=True))
    worker._watch_interleaving_buffer_video = MagicMock()

    worker.run()

    assert worker._play_target_video.call_count == 1
    assert worker.target_attempts == 1
    # Limit of 1 must NOT start an unnecessary buffer video after the final target attempt
    assert worker._watch_interleaving_buffer_video.call_count == 0


@patch("src.automation.watching_worker.dismiss_youtube_dialogs")
@patch("src.automation.watching_worker.safe_get")
@patch("src.automation.watching_worker.close_driver")
@patch("src.automation.watching_worker.human_like_delay")
@patch("src.automation.watching_worker.perform_google_login", return_value=True)
@patch("src.automation.watching_worker.setup_driver")
def test_browsing_limit_two_permits_two_attempts_across_discovery_paths(mock_setup, mock_login, mock_delay, mock_close, mock_safe_get, mock_dialogs):
    worker = make_dummy_worker(target_limit=2, is_unlimited=False)
    mock_driver = MagicMock()
    mock_driver.current_url = "https://www.youtube.com/watch?v=12345"
    mock_driver.find_elements.return_value = [MagicMock()]
    mock_setup.return_value = mock_driver

    target_candidate = (
        {"title": "Tech Video", "href": "/watch?v=abc"},
        worker.channels[0],
        "https://www.youtube.com/watch?v=abc",
        "Tech Video",
        "TechChannel",
    )
    worker.fetch_recommended_videos = MagicMock(return_value=[{"title": "Tech Video", "href": "/watch?v=abc"}])
    worker._find_target_video = MagicMock(return_value=target_candidate)
    worker._play_target_video = MagicMock(return_value=WatchResult(completed=True))
    worker._watch_interleaving_buffer_video = MagicMock(return_value=True)

    worker.run()

    assert worker._play_target_video.call_count == 2
    assert worker.target_attempts == 2
    # Only 1 buffer video interleaved between attempt 1 and 2; none after attempt 2
    assert worker._watch_interleaving_buffer_video.call_count == 1


@patch("src.automation.watching_worker.dismiss_youtube_dialogs")
@patch("src.automation.watching_worker.safe_get")
@patch("src.automation.watching_worker.close_driver")
@patch("src.automation.watching_worker.human_like_delay")
@patch("src.automation.watching_worker.perform_google_login", return_value=True)
@patch("src.automation.watching_worker.setup_driver")
def test_failed_or_partial_attempt_does_not_exceed_cap(mock_setup, mock_login, mock_delay, mock_close, mock_safe_get, mock_dialogs):
    worker = make_dummy_worker(target_limit=1, is_unlimited=False)
    mock_driver = MagicMock()
    mock_driver.current_url = "https://www.youtube.com/watch?v=12345"
    mock_driver.find_elements.return_value = [MagicMock()]
    mock_setup.return_value = mock_driver

    target_candidate = (
        {"title": "Tech Video", "href": "/watch?v=abc"},
        worker.channels[0],
        "https://www.youtube.com/watch?v=abc",
        "Tech Video",
        "TechChannel",
    )
    worker.fetch_recommended_videos = MagicMock(return_value=[{"title": "Tech Video", "href": "/watch?v=abc"}])
    worker._find_target_video = MagicMock(return_value=target_candidate)
    # Simulate partial / interrupted playback
    worker._play_target_video = MagicMock(return_value=WatchResult(completed=False, interrupted=True, reason="channel_target_achieved"))
    worker._watch_interleaving_buffer_video = MagicMock()

    worker.run()

    # Even though interrupted, the target attempt was consumed and did not run a second time
    assert worker._play_target_video.call_count == 1
    assert worker.target_attempts == 1
    assert worker._watch_interleaving_buffer_video.call_count == 0


def test_buffer_videos_do_not_consume_target_attempt_count():
    worker = make_dummy_worker(target_limit=2, is_unlimited=False)
    assert not worker._is_target_limit_reached(1)
    # Simulate interleaved buffer video call
    with patch.object(worker, "_watch_interleaving_buffer_video", return_value=True) as mock_buf:
        res = worker._after_target_video_watched(set(), 1)
        assert res is True
        assert mock_buf.call_count == 1
        # target_attempts still 0 on worker instance
        assert worker.target_attempts == 0


def test_no_buffer_starts_after_final_target_attempt():
    worker = make_dummy_worker(target_limit=1, is_unlimited=False)
    # If attempt reached limit (1), buffer call returns False without watching buffer
    with patch.object(worker, "_watch_interleaving_buffer_video") as mock_buf:
        res = worker._after_target_video_watched(set(), target_attempts=1)
        assert res is False
        assert mock_buf.call_count == 0


def test_unlimited_mode_is_explicit_and_stoppable():
    worker = make_dummy_worker(is_unlimited=True)
    assert worker.is_unlimited is True
    assert worker.target_limit is None
    # Cannot reach limit through counter
    assert worker._is_target_limit_reached(100) is False

    # Stopping is available and sets stop_event
    worker.stop()
    assert worker.stop_event.is_set()


def test_queue_and_browsing_settings_persist_independently(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("AutomationLoops", 4)
    mgr.update_settings({
        "BrowsingTargetLimit": 2,
        "BrowsingUnlimited": True,
    })

    assert mgr.get_int("AutomationLoops") == 4
    assert mgr.get_int("BrowsingTargetLimit") == 2
    assert mgr.get_bool("BrowsingUnlimited") is True

    # Changing AutomationLoops does not change browsing settings
    mgr.set_setting("AutomationLoops", 1)
    assert mgr.get_int("BrowsingTargetLimit") == 2
    assert mgr.get_bool("BrowsingUnlimited") is True

    # Changing browsing settings does not change AutomationLoops
    mgr.update_settings({"BrowsingTargetLimit": 5, "BrowsingUnlimited": False})
    assert mgr.get_int("AutomationLoops") == 1
    assert mgr.get_int("BrowsingTargetLimit") == 5
    assert mgr.get_bool("BrowsingUnlimited") is False


def test_progress_labels_and_summaries_match_selected_mode():
    view = DashboardView.__new__(DashboardView)
    view.progress_bar = MagicMock()
    view.progress_label = MagicMock()

    # Automation mode
    view.set_progress(done=2, total=5, mode="automation")
    view.progress_label.configure.assert_called_with(text="Completed 2 / 5 tasks", text_color=STATUS_INFO_COLOR)

    # Browsing mode with finite limit
    view.set_progress(done=1, total=2, mode="watching")
    view.progress_label.configure.assert_called_with(text="Browsing target attempts: 1 / 2", text_color=STATUS_INFO_COLOR)

    # Browsing mode with limit reached
    view.set_progress(done=2, total=2, mode="watching")
    view.progress_label.configure.assert_called_with(text="Browsing target attempts: 2 / 2 — limit reached", text_color=STATUS_INFO_COLOR)

    # Browsing mode with unlimited
    view.set_progress(done=3, total=0, mode="watching")
    view.progress_label.configure.assert_called_with(text="Browsing target attempts: 3 (unlimited)", text_color=STATUS_INFO_COLOR)


def test_invalid_values_cannot_start_session_with_ambiguous_limits():
    view = DashboardView.__new__(DashboardView)
    view.loop_entry = MagicMock()
    view.browsing_limit_entry = MagicMock()
    view.browsing_unlimited_cb = MagicMock()
    view.browsing_unlimited_cb.get.return_value = 0

    # Test invalid queue passes
    invalid_passes = ["0", "-1", "abc", "1.5", ""]
    for inv in invalid_passes:
        view.loop_entry.get.return_value = inv
        with pytest.raises(ValueError):
            view.get_queue_passes()

    # Test valid queue pass
    view.loop_entry.get.return_value = "3"
    assert view.get_queue_passes() == 3

    # Test invalid browsing limit
    invalid_limits = ["0", "-2", "xyz", "NaN", ""]
    for inv in invalid_limits:
        view.browsing_limit_entry.get.return_value = inv
        with pytest.raises(ValueError):
            view.get_browsing_limit()

    # Test valid browsing limit
    view.browsing_limit_entry.get.return_value = "2"
    assert view.get_browsing_limit() == (2, False)

    # Test explicit unlimited checkbox
    view.browsing_unlimited_cb.get.return_value = 1
    assert view.get_browsing_limit() == (None, True)


def test_one_queue_pass_runs_one_pass():
    view = DashboardView.__new__(DashboardView)
    view.loop_entry = MagicMock()
    view.loop_entry.get.return_value = "1"
    assert view.get_queue_passes() == 1


def test_multiple_queue_passes_behave_as_configured():
    view = DashboardView.__new__(DashboardView)
    view.loop_entry = MagicMock()
    view.loop_entry.get.return_value = "4"
    assert view.get_queue_passes() == 4
