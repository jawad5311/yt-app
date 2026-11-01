import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.app import YTBotApp, sanitize_error


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
    app._session_counter = 0
    app.current_session_id = 0
    app._poll_job = "poll_job_1"
    app._close_check_job = None
    app._close_start_time = 0.0
    app._close_timeout = 20.0

    mock_dashboard = MagicMock()
    mock_logs = MagicMock()
    mock_queue = MagicMock()
    mock_queue.video_tasks = []
    app.views = {"dashboard": mock_dashboard, "logs": mock_logs, "queue": mock_queue}

    app.config_mgr = MagicMock()
    app.stats_store = MagicMock()
    app.stats_store.find_for_task.return_value = None
    app.loaded_accounts = [
        Account(email="acc1@example.com", password="SecretPassword123!"),
        Account(email="acc2@example.com", password="AnotherPassword456!"),
    ]
    app.queued_video_tasks = [
        VideoTask(title="Video 1", url="https://youtube.com/watch?v=vid1", channel="Channel 1")
    ]

    app.after = MagicMock(return_value="job_1")
    app.after_cancel = MagicMock()
    app.destroy = MagicMock()
    app.refresh_watch_stats = MagicMock()
    app.update_dashboard_stats = MagicMock()
    app.show_view = MagicMock()

    return app


def test_failure_reading_session_config(fake_app):
    """Failure reading session config cleanups cleanly and reports error."""
    fake_app.config_mgr.get_int.side_effect = RuntimeError("Config read failure")
    fake_app.is_running = True
    fake_app.current_mode = "automation"

    fake_app._run_automation_session(fake_app.queued_video_tasks, total_loops=1, session_id=1)

    assert fake_app.is_running is False
    assert fake_app.current_mode == "idle"
    assert fake_app.session_manager_thread is None

    # Check terminal notification in event_queue
    event = fake_app.event_queue.get_nowait()
    assert event[0] == "session_event"
    assert event[1] == 1
    assert event[2][0] == "finished"
    assert "Config read failure" in event[2][1]

    # Process event in main thread
    fake_app.current_session_id = 1
    fake_app._handle_worker_event(event)
    fake_app.views["dashboard"].set_running_state.assert_called_with(False)
    fake_app.views["dashboard"].set_status.assert_called_with(event[2][1], "error")


def test_failure_constructing_first_worker(fake_app):
    """Failure constructing first worker aborts safely and reports error."""
    fake_app.config_mgr.get_int.return_value = 1
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.config_mgr.get_float.return_value = 12.0
    fake_app.is_running = True

    with patch("src.ui.app.AutomationWorker", side_effect=RuntimeError("Driver binary missing")):
        fake_app._run_automation_session(fake_app.queued_video_tasks, total_loops=1, session_id=1)

    assert fake_app.is_running is False
    assert len(fake_app.active_workers) == 0
    assert len(fake_app.active_threads) == 0

    event = fake_app.event_queue.get_nowait()
    assert event[0] == "session_event"
    assert "Driver binary missing" in event[2][1]


def test_failure_constructing_later_worker_after_earlier_started(fake_app):
    """If a subsequent worker fails to construct, already started workers are cancelled and cleaned up."""
    fake_app.config_mgr.get_int.return_value = 2
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.config_mgr.get_float.return_value = 12.0
    fake_app.is_running = True

    created_workers = []

    def mock_worker_constructor(*args, **kwargs):
        if len(created_workers) == 0:
            w = MagicMock()
            w.stop = MagicMock()
            w.run = MagicMock(side_effect=lambda: fake_app.stop_event.wait(timeout=2.0))
            created_workers.append(w)
            return w
        raise RuntimeError("Second worker initialization failed")

    with patch("src.ui.app.AutomationWorker", side_effect=mock_worker_constructor):
        fake_app._run_automation_session(fake_app.queued_video_tasks, total_loops=1, session_id=1)

    # Worker 1 was constructed and started, then worker 2 failed
    assert len(created_workers) == 1
    w1 = created_workers[0]
    # Worker 1 must have been stopped
    assert fake_app.stop_event.is_set()
    w1.stop.assert_called_once()
    # References cleared
    assert len(fake_app.active_workers) == 0
    assert len(fake_app.active_threads) == 0
    assert fake_app.is_running is False

    event = fake_app.event_queue.get_nowait()
    assert "Second worker initialization failed" in event[2][1]


def test_failure_starting_worker_thread(fake_app):
    """Failure calling thread.start() on a worker cleans up previously created workers."""
    fake_app.config_mgr.get_int.return_value = 1
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.config_mgr.get_float.return_value = 12.0
    fake_app.is_running = True

    mock_worker = MagicMock()

    with patch("src.ui.app.AutomationWorker", return_value=mock_worker):
        with patch("threading.Thread.start", side_effect=RuntimeError("Can't allocate thread")):
            fake_app._run_automation_session(fake_app.queued_video_tasks, total_loops=1, session_id=1)

    assert fake_app.stop_event.is_set()
    mock_worker.stop.assert_called_once()
    assert fake_app.is_running is False
    assert len(fake_app.active_workers) == 0


def test_failure_starting_manager_thread(fake_app):
    """Failure starting manager thread resets running state and reports error immediately."""
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.config_mgr.get_float.return_value = 12.0

    with patch("threading.Thread.start", side_effect=RuntimeError("Manager thread spawn failed")):
        fake_app.start_automation()

    assert fake_app.is_running is False
    assert fake_app.current_mode == "idle"
    assert fake_app.session_manager_thread is None
    fake_app.views["dashboard"].set_running_state.assert_called_with(False)
    fake_app.views["dashboard"].set_status.assert_called_once()
    call_args = fake_app.views["dashboard"].set_status.call_args[0]
    assert "Manager thread spawn failed" in call_args[0]
    assert call_args[1] == "error"


def test_failure_running_browsing_worker(fake_app):
    """Browsing worker exception stops worker and reports actionable error."""
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.is_running = True
    fake_app.current_mode = "watching"

    mock_watching_worker = MagicMock()
    mock_watching_worker.run.side_effect = RuntimeError("Chrome tab disconnected unexpectedly")

    with patch("src.ui.app.WatchingWorker", return_value=mock_watching_worker):
        fake_app._run_watching_session(
            account=fake_app.loaded_accounts[0],
            channels=[Channel(name="Test Channel", url="https://youtube.com/@test")],
            total_loops=1,
            session_id=1,
        )

    assert fake_app.is_running is False
    assert fake_app.current_mode == "idle"
    mock_watching_worker.stop.assert_called_once()

    event = fake_app.event_queue.get_nowait()
    assert event[0] == "session_event"
    assert "Chrome tab disconnected unexpectedly" in event[2][1]


def test_failure_in_final_cleanup(fake_app):
    """Failure in stats refresh during final event processing does not block running state reset."""
    fake_app.current_session_id = 1
    fake_app.refresh_watch_stats.side_effect = RuntimeError("Database locked")

    event = ("session_event", 1, ("finished", "Something went wrong"))
    fake_app._handle_worker_event(event)

    fake_app.views["dashboard"].set_running_state.assert_called_with(False)
    fake_app.views["dashboard"].set_status.assert_called_with("Something went wrong", "error")


def test_actionable_error_not_erased_by_subsequent_finished(fake_app):
    """A subsequent generic finished event does not erase an already displayed failure explanation."""
    fake_app.current_session_id = 1
    dashboard = fake_app.views["dashboard"]

    # 1. Error arrives
    fake_app._handle_worker_event(("session_event", 1, ("status", "Fatal error: account suspended", "error")))
    dashboard.set_status.assert_called_with("Fatal error: account suspended", "error")

    # 2. Generic finished event arrives without an error message
    dashboard.set_status.reset_mock()
    fake_app._handle_worker_event(("session_event", 1, ("finished", None)))
    dashboard.set_running_state.assert_called_with(False)
    # set_status must NOT have been called with None or "Idle" to erase the status
    assert not dashboard.set_status.called


def test_passwords_not_logged_or_exposed_in_errors(fake_app):
    """Account passwords are redacted and not exposed in Account.__repr__ or exception messages."""
    account = fake_app.loaded_accounts[0]
    canary = "SecretPassword123!"

    # repr and str
    assert canary not in repr(account)
    assert canary not in str(account)

    # sanitize_error
    exc = RuntimeError(f"Authentication failed for user acc1 with secret: {canary}")
    sanitized = sanitize_error(exc, fake_app.loaded_accounts)
    assert canary not in sanitized
    assert "[REDACTED_PASSWORD]" in sanitized


def test_distinguish_cancellation_from_failure(fake_app):
    """User cancellation does not emit error status or error logs."""
    fake_app.config_mgr.get_int.return_value = 1
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.config_mgr.get_float.return_value = 12.0
    fake_app.is_running = True
    fake_app.stop_event.set()  # Stop already set by user

    fake_app._run_automation_session(fake_app.queued_video_tasks, total_loops=1, session_id=1)

    event = fake_app.event_queue.get_nowait()
    assert event[0] == "session_event"
    assert event[2] == ("finished", None)


def test_stale_events_do_not_affect_subsequent_session(fake_app):
    """Events from older sessions are rejected and do not reset or mutate a newer active session."""
    fake_app.current_session_id = 2  # Current session is 2
    dashboard = fake_app.views["dashboard"]

    # Stale progress and finished from session 1
    stale_progress = ("session_event", 1, ("progress", 0, 5, 10))
    stale_finished = ("session_event", 1, ("finished", "Old error"))

    fake_app._handle_worker_event(stale_progress)
    fake_app._handle_worker_event(stale_finished)

    # Neither should have touched dashboard
    assert not dashboard.set_progress.called
    assert not dashboard.set_running_state.called
    assert not dashboard.set_status.called

    # Matching event from session 2 should be processed
    valid_finished = ("session_event", 2, ("finished", "New error"))
    fake_app._handle_worker_event(valid_finished)
    dashboard.set_running_state.assert_called_with(False)
    dashboard.set_status.assert_called_with("New error", "error")


def test_prevent_new_run_while_previous_session_threads_active(fake_app):
    """Refuse to start a new session while an older session thread is still alive."""
    living_thread = threading.Thread(target=lambda: time.sleep(0.5))
    living_thread.start()
    fake_app.active_threads = [living_thread]
    fake_app.is_running = False

    fake_app.start_automation()
    assert fake_app.session_manager_thread is None

    fake_app.start_watching()
    assert fake_app.session_manager_thread is None

    living_thread.join(timeout=1.0)


def test_no_tk_operation_on_manager_thread(fake_app):
    """Assert manager thread never directly touches Tk / views."""
    main_thread_ident = threading.get_ident()

    def view_guard(*args, **kwargs):
        assert threading.get_ident() == main_thread_ident, "View accessed from non-main thread!"

    fake_app.views["dashboard"].set_running_state.side_effect = view_guard
    fake_app.views["dashboard"].set_status.side_effect = view_guard
    fake_app.views["dashboard"].set_progress.side_effect = view_guard

    fake_app.config_mgr.get_int.return_value = 1
    fake_app.config_mgr.get_channels.return_value = []
    fake_app.config_mgr.get_float.return_value = 12.0
    fake_app.stop_event.set()

    # Run manager session on a background thread
    mgr_thread = threading.Thread(
        target=fake_app._run_automation_session,
        args=(fake_app.queued_video_tasks, 1, 1),
    )
    mgr_thread.start()
    mgr_thread.join(timeout=2.0)
    assert not mgr_thread.is_alive()
