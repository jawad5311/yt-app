import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.automation.driver import close_driver
from src.automation.session_tracker import SessionChannelTracker
from src.models.channel import Channel
from src.ui.app import YTBotApp


@pytest.fixture
def fake_app(tmp_path):
    app = YTBotApp.__new__(YTBotApp)
    app.stop_event = threading.Event()
    app.pause_event = threading.Event()
    app.is_running = False
    app.is_closing = False
    app.current_mode = "idle"
    app.current_view_name = "dashboard"
    app._queue_needs_refresh = False
    app._channels_needs_refresh = False
    app.session_manager_thread = None
    app.active_threads = []
    app.active_workers = []
    app.worker_progress = {}
    app.log_queue = queue.Queue()
    app.event_queue = queue.Queue()
    app._poll_job = "poll_job_1"
    app._close_check_job = None
    app._close_start_time = 0.0
    app._close_timeout = 20.0

    mock_dashboard = MagicMock()
    mock_logs = MagicMock()
    app.views = {"dashboard": mock_dashboard, "logs": mock_logs}

    app.after_jobs = {}
    app.after_id_counter = 0

    def fake_after(ms, func):
        app.after_id_counter += 1
        job_id = f"job_{app.after_id_counter}"
        app.after_jobs[job_id] = func
        return job_id

    def fake_after_cancel(job_id):
        app.after_jobs.pop(job_id, None)

    app.after = MagicMock(side_effect=fake_after)
    app.after_cancel = MagicMock(side_effect=fake_after_cancel)
    app.destroy = MagicMock()

    return app


def test_1_closing_while_idle_destroys_safely(fake_app):
    """1. Closing while idle cancels scheduled callbacks and destroys window immediately."""
    fake_app.is_running = False
    fake_app.active_threads = []
    fake_app.session_manager_thread = None

    fake_app.on_closing()

    assert fake_app.is_closing is True
    fake_app.after_cancel.assert_called_with("poll_job_1")
    assert fake_app._poll_job is None
    fake_app.destroy.assert_called_once()


def test_2_closing_during_automation_requests_stop_and_waits(fake_app):
    """2. Closing during queue automation requests Stop, notifies workers, and waits."""
    fake_app.is_running = True
    fake_worker = MagicMock()
    fake_app.active_workers = [fake_worker]

    worker_finished = threading.Event()

    def worker_target():
        fake_app.stop_event.wait(timeout=2.0)
        worker_finished.set()

    thread = threading.Thread(target=worker_target)
    fake_app.active_threads = [thread]
    fake_app.session_manager_thread = thread
    thread.start()

    fake_app.on_closing()

    # Must set stop_event and call stop() on active workers
    assert fake_app.is_closing is True
    assert fake_app.stop_event.is_set()
    fake_worker.stop.assert_called_once()
    # Must NOT have destroyed yet while thread is alive
    assert not fake_app.destroy.called
    assert fake_app._close_check_job is not None

    # Wait for thread to finish
    thread.join(timeout=2.0)
    assert not thread.is_alive()

    # Now step the shutdown progress check
    fake_app._check_shutdown_progress()
    fake_app.destroy.assert_called_once()


def test_3_closing_during_paused_browsing_releases_pause(fake_app):
    """3. Closing during paused browsing releases Pause and exits cleanly."""
    fake_app.is_running = True
    fake_app.pause_event.set()
    assert fake_app.pause_event.is_set()

    fake_app.on_closing()

    # Pause must be cleared so paused workers wake up and exit
    assert not fake_app.pause_event.is_set()
    assert fake_app.stop_event.is_set()


def test_4_final_measured_time_recorded_once(tmp_path):
    """4. Final measured watch time is saved without double counting on stop."""
    from src.config.manager import ConfigManager
    config_file = tmp_path / "settings.json"
    config_mgr = ConfigManager(config_file)
    channel = Channel(name="Test Channel", url="https://youtube.com/@test", watch_seconds=0.0)
    config_mgr.set_channels([channel.to_dict()])

    tracker = SessionChannelTracker(
        config_mgr=config_mgr,
        channels=[channel],
    )

    # Accumulate 45.5 seconds of measured watch time
    tracker.set_active_channel("Test Channel")
    tracker.add_watch_delta("Test Channel", 45.5)
    # Stop session (checkpoint flush)
    tracker.on_stop()

    assert abs(channel.watch_seconds - 45.5) < 0.1
    saved_channels = config_mgr.get_channels()
    assert abs(saved_channels[0]["watch_seconds"] - 45.5) < 0.1

    # Calling on_stop again must not duplicate
    tracker.on_stop()
    assert abs(channel.watch_seconds - 45.5) < 0.1
    saved_channels2 = config_mgr.get_channels()
    assert abs(saved_channels2[0]["watch_seconds"] - 45.5) < 0.1


def test_5_browser_cleanup_occurs_per_owned_driver():
    """5. Browser cleanup occurs once per owned driver via close_driver."""
    driver = MagicMock()
    driver.browser_pid = 99999

    with patch("subprocess.run") as mock_run:
        close_driver(driver)
        driver.quit.assert_called_once()
        # Verify it targeted only the specific driver PID
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "99999" in cmd


def test_6_repeated_close_requests_do_not_duplicate(fake_app):
    """6. Repeated close requests do not trigger multiple cleanups."""
    fake_app.is_running = True
    fake_worker = MagicMock()
    fake_app.active_workers = [fake_worker]

    thread = threading.Thread(target=lambda: fake_app.stop_event.wait(timeout=1.0))
    fake_app.active_threads = [thread]
    thread.start()

    fake_app.on_closing()
    assert fake_app.is_closing is True
    first_worker_stop_count = fake_worker.stop.call_count
    assert first_worker_stop_count == 1

    # Second call should be a no-op
    fake_app.on_closing()
    assert fake_worker.stop.call_count == first_worker_stop_count

    fake_app.stop_event.set()
    thread.join(timeout=1.0)


def test_7_delayed_worker_does_not_freeze_ui_thread(fake_app):
    """7. Delayed workers do not freeze the UI thread (non-blocking after-polling)."""
    fake_app.is_running = True
    fake_app._close_timeout = 1.0

    slow_thread = threading.Thread(target=lambda: time.sleep(0.5))
    fake_app.active_threads = [slow_thread]
    slow_thread.start()

    # Calling on_closing returns immediately
    start_t = time.monotonic()
    fake_app.on_closing()
    elapsed = time.monotonic() - start_t
    assert elapsed < 0.2  # Did not block

    # Simulate timeout reached
    fake_app._close_start_time = time.monotonic() - 10.0
    with patch.object(fake_app, "_prompt_stuck_workers", return_value="cancel") as mock_prompt:
        fake_app._check_shutdown_progress()
        mock_prompt.assert_called_once()
        # User canceled close, so is_closing reset to False
        assert fake_app.is_closing is False

    slow_thread.join(timeout=1.0)


def test_8_late_events_do_not_access_destroyed_widgets(fake_app):
    """8. Late events arriving during/after close do not touch destroyed views."""
    fake_app.is_closing = True
    event = ("progress", 0, "Working...")

    fake_app._handle_worker_event(event)
    # views['dashboard'] must not have been touched
    assert not fake_app.views["dashboard"].update_worker_progress.called

    # _poll_log_queue should not reschedule when closing
    fake_app._poll_log_queue()
    assert fake_app._poll_job is None


def test_9_unrelated_browser_processes_are_never_targeted():
    """9. Driver cleanup targets only the driver's own PID, never system-wide chrome."""
    driver = MagicMock()
    driver.browser_pid = 4321
    with patch("subprocess.run") as mock_run:
        close_driver(driver)
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        # Must be taskkill /PID 4321, not taskkill /IM chrome.exe
        assert "/PID" in cmd
        assert "4321" in cmd
        assert "/IM" not in cmd


def test_10_finished_session_references_are_removed(fake_app):
    """10. Finished session references are cleared in finally block."""
    fake_app.config_mgr = MagicMock()
    fake_app.config_mgr.get_int.return_value = 1
    fake_app.stop_event = threading.Event()
    fake_app.stop_event.set()  # Stop immediately

    fake_app.active_threads = [MagicMock()]
    fake_app.active_workers = [MagicMock()]
    fake_app.session_manager_thread = MagicMock()
    fake_app.is_running = True

    # Execute _run_automation_session
    fake_app._run_automation_session([], 1)

    assert fake_app.is_running is False
    assert fake_app.current_mode == "idle"
    assert len(fake_app.active_threads) == 0
    assert len(fake_app.active_workers) == 0
    assert fake_app.session_manager_thread is None
