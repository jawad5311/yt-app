"""Tests for single-session log retention, 1-minute batched disk writes, 1000-line UI limit, and 'Copy Complete Logs'."""

import queue
import time
from unittest.mock import patch

from src.utils.logger import (
    MAX_SESSION_LOGS,
    SESSION_LOG_FLUSH_INTERVAL_SECONDS,
    BatchedSessionFileHandler,
    emit,
    flush_session_logs,
    get_sessions_dir,
    init_logging,
    prune_old_session_files,
    read_current_session_logs,
    start_session_logging,
    stop_session_logging,
)


def test_default_keeps_only_one_recent_session_and_deletes_old_logs(tmp_path):
    """Default MAX_SESSION_LOGS is 1: starting a new session deletes previous session logs and legacy automation.log."""
    assert MAX_SESSION_LOGS == 1
    assert SESSION_LOG_FLUSH_INTERVAL_SECONDS == 60.0

    # Create a legacy automation.log that should be removed when a new session starts
    legacy_log = tmp_path / "automation.log"
    legacy_log.write_text("stale legacy log from earlier sessions\n", encoding="utf-8")

    s_dir = get_sessions_dir(tmp_path)

    # Session 1
    s1 = start_session_logging(log_dir=tmp_path)
    emit(None, "Session 1 log line")
    stop_session_logging()

    assert s1.exists()
    assert not legacy_log.exists()
    assert list(s_dir.glob("session_*.log")) == [s1]
    assert "Session 1 log line" in s1.read_text(encoding="utf-8")

    # Calling init_logging (simulating app restart) must preserve Session 1's log file until a new session starts
    returned_path = init_logging(log_dir=tmp_path)
    assert returned_path == s1
    assert s1.exists()
    assert not legacy_log.exists()

    time.sleep(0.02)

    # Session 2: must delete Session 1's log file so only Session 2's file exists
    s2 = start_session_logging(log_dir=tmp_path)
    emit(None, "Session 2 fresh log line")
    stop_session_logging()

    assert not s1.exists()
    assert s2.exists()
    remaining_files = list(s_dir.glob("session_*.log"))
    assert remaining_files == [s2]
    all_logs_under_dir = list(tmp_path.rglob("*.log"))
    assert all_logs_under_dir == [s2]
    s2_text = s2.read_text(encoding="utf-8")
    assert "Session 2 fresh log line" in s2_text
    assert "Session 1 log line" not in s2_text


def test_batched_flushing_defers_disk_writes_until_interval_or_stop(tmp_path):
    """Logs are buffered in memory between flushes and written in batches after the flush interval or on stop."""
    # Use a long interval (60s) to confirm emit() does not immediately hit disk on every line
    session_file = start_session_logging(log_dir=tmp_path, flush_interval=60.0)
    try:
        assert session_file.exists()
        emit(None, "Buffered line 1")
        emit(None, "Buffered line 2")

        # File on disk should still be empty before the 60-second batch flush (saving CPU/disk I/O)
        assert session_file.read_text(encoding="utf-8") == ""

        # Explicit flush persists the batch immediately
        flush_session_logs()
        flushed_text = session_file.read_text(encoding="utf-8")
        assert "Buffered line 1" in flushed_text
        assert "Buffered line 2" in flushed_text

        # Emit one more line and verify stop_session_logging() flushes it immediately
        emit(None, "Final line before stop")
        assert "Final line before stop" not in session_file.read_text(encoding="utf-8")
    finally:
        stop_session_logging()

    final_text = session_file.read_text(encoding="utf-8")
    assert "Final line before stop" in final_text


def test_batched_flushing_writes_automatically_after_interval(tmp_path):
    """Background timer automatically flushes buffered logs in a batch once flush_interval elapses."""
    session_file = start_session_logging(log_dir=tmp_path, flush_interval=0.15)
    try:
        emit(None, "Timed batch line A")
        emit(None, "Timed batch line B")
        # Not written immediately on emit
        assert session_file.read_text(encoding="utf-8") == ""

        # Wait for the periodic batch flush timer to fire
        deadline = time.monotonic() + 2.0
        content = ""
        while time.monotonic() < deadline:
            content = session_file.read_text(encoding="utf-8")
            if "Timed batch line B" in content:
                break
            time.sleep(0.05)

        assert "Timed batch line A" in content
        assert "Timed batch line B" in content
    finally:
        stop_session_logging()


def test_batched_handler_buffer_cap_triggers_early_flush(tmp_path):
    """If a burst reaches max_buffer_records before the timer fires, the handler flushes early."""
    import logging

    target = tmp_path / "sessions" / "session_cap_test.log"
    handler = BatchedSessionFileHandler(target, flush_interval=60.0, max_buffer_records=3)
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        for idx in range(2):
            rec = logging.LogRecord("test", logging.INFO, "", 0, f"burst {idx}", (), None)
            handler.emit(rec)
        # 2 records < cap of 3 -> still buffered
        assert target.read_text(encoding="utf-8") == ""

        rec3 = logging.LogRecord("test", logging.INFO, "", 0, "burst 2", (), None)
        handler.emit(rec3)
        # Reached cap of 3 -> flushed immediately
        text = target.read_text(encoding="utf-8")
        assert "burst 0" in text
        assert "burst 1" in text
        assert "burst 2" in text
    finally:
        handler.close()


def test_session_logging_creates_file_and_records_logs(tmp_path):
    """Verify start_session_logging creates a session log file and records emit logs."""
    session_file = start_session_logging(log_dir=tmp_path)
    assert session_file.exists()
    assert session_file.parent.name == "sessions"

    log_q = queue.Queue()
    emit(log_q, "Session Started: Testing logging output")
    emit(log_q, "Processing channel: Lifestyle Mapping")

    content = read_current_session_logs(log_dir=tmp_path)
    assert "Session Started: Testing logging output" in content
    assert "Processing channel: Lifestyle Mapping" in content

    stopped_file = stop_session_logging()
    assert stopped_file == session_file


def test_session_log_rotation_keeps_max_5_sessions(tmp_path):
    """Verify that when more than 5 sessions are created, the oldest files are removed to keep at most 5."""
    s_dir = get_sessions_dir(tmp_path)

    created_files = []
    for i in range(7):
        time.sleep(0.02)  # ensure different timestamps
        f = start_session_logging(log_dir=tmp_path, max_sessions=5)
        created_files.append(f)
        emit(None, f"Message in session {i + 1}")
        stop_session_logging()

    current_files = sorted(s_dir.glob("session_*.log"), key=lambda p: (p.stat().st_mtime, p.name))
    assert len(current_files) == 5

    # The first 2 files (session 1 and session 2) should have been pruned
    assert not created_files[0].exists()
    assert not created_files[1].exists()

    # The last 5 files should exist
    for f in created_files[2:]:
        assert f.exists()


def test_prune_old_session_files_directly(tmp_path):
    """Directly test prune_old_session_files logic with dummy files."""
    s_dir = tmp_path / "sessions"
    s_dir.mkdir(parents=True, exist_ok=True)

    dummy_files = []
    for i in range(8):
        f = s_dir / f"session_20261110_010{i}00.log"
        f.write_text(f"log {i}", encoding="utf-8")
        dummy_files.append(f)
        time.sleep(0.01)

    prune_old_session_files(s_dir, max_files=5)
    remaining = sorted(s_dir.glob("session_*.log"), key=lambda p: (p.stat().st_mtime, p.name))
    assert len(remaining) == 4  # prune_old_session_files leaves room for 1 new file so total <= 5


def test_read_current_session_logs_empty_when_no_sessions(tmp_path):
    """When no session logs exist, read_current_session_logs safely returns an empty string."""
    empty_dir = tmp_path / "empty_logs"
    assert read_current_session_logs(log_dir=empty_dir) == ""


def test_ui_log_truncation_limits_to_1000_lines(ctk_app):
    """Verify that LogsView trims output when line count exceeds 1000 lines."""
    from src.ui.views.logs_view import LogsView

    app = ctk_app
    view = LogsView(app, app)

    # Insert 1200 lines into log_textbox via append_log
    batch = "\n".join(f"Log line {i}" for i in range(1200))
    view.append_log(batch)

    line_count = int(view.log_textbox.index("end-1c").split(".")[0])
    assert line_count <= 1005  # within buffer range (at most ~1000 lines)


def test_copy_complete_logs_button_copies_file_content(ctk_app, tmp_path):
    """Verify that clicking Copy Complete Logs copies the complete session log file to clipboard."""
    from src.ui.views.logs_view import LogsView

    session_file = start_session_logging(log_dir=tmp_path)
    emit(None, "Full session line 1: Setup completed")
    emit(None, "Full session line 2: Watch countdown 1/3")

    app = ctk_app
    view = LogsView(app, app)
    assert hasattr(view, "copy_complete_btn")
    assert view.copy_complete_btn.cget("text") == "Copy Complete Logs"

    with patch.object(view, "clipboard_clear") as mock_clear, \
         patch.object(view, "clipboard_append") as mock_append:
        view.copy_complete_logs()
        mock_clear.assert_called_once()
        mock_append.assert_called_once()
        copied_text = mock_append.call_args[0][0]
        assert "Full session line 1: Setup completed" in copied_text
        assert "Full session line 2: Watch countdown 1/3" in copied_text

    stop_session_logging()


def test_copy_complete_logs_when_session_is_running_copies_current_session(ctk_app, tmp_path):
    """When a session is currently running, Copy Complete Logs copies the complete logs of that active session."""
    from src.ui.views.logs_view import LogsView

    # Create Session 1 and stop it
    s1 = start_session_logging(log_dir=tmp_path)
    emit(None, "Session 1 line A: Channel Alpha")
    stop_session_logging()

    time.sleep(0.02)

    # Create Session 2 and keep it actively running
    s2 = start_session_logging(log_dir=tmp_path)
    emit(None, "Session 2 line B: Channel Beta Active")

    app = ctk_app
    app.is_running = True
    view = LogsView(app, app)

    with patch.object(view, "clipboard_clear") as mock_clear, \
         patch.object(view, "clipboard_append") as mock_append, \
         patch("src.utils.logger.get_project_root", return_value=tmp_path):
        view.copy_complete_logs()
        mock_clear.assert_called_once()
        mock_append.assert_called_once()
        copied = mock_append.call_args[0][0]
        # Must contain Session 2's active logs
        assert "Session 2 line B: Channel Beta Active" in copied
        # Must NOT contain Session 1's logs
        assert "Session 1 line A: Channel Alpha" not in copied

    stop_session_logging()
    app.is_running = False


def test_copy_complete_logs_when_idle_copies_most_recent_session(ctk_app, tmp_path):
    """When no session is running, Copy Complete Logs copies the logs of the most recent session."""
    from src.ui.views.logs_view import LogsView

    # Create Session 1
    s1 = start_session_logging(log_dir=tmp_path)
    emit(None, "Session 1 log: Old run")
    stop_session_logging()

    time.sleep(0.02)

    # Create Session 2
    s2 = start_session_logging(log_dir=tmp_path)
    emit(None, "Session 2 log: Most recent run")
    stop_session_logging()

    # App is idle (no session running)
    app = ctk_app
    app.is_running = False
    view = LogsView(app, app)

    with patch.object(view, "clipboard_clear") as mock_clear, \
         patch.object(view, "clipboard_append") as mock_append, \
         patch("src.utils.logger.get_project_root", return_value=tmp_path):
        view.copy_complete_logs()
        mock_clear.assert_called_once()
        mock_append.assert_called_once()
        copied = mock_append.call_args[0][0]
        assert "Session 2 log: Most recent run" in copied
        assert "Session 1 log: Old run" not in copied


def test_copy_logs_only_copies_logs_showing_in_app(ctk_app):
    """Copy Logs button copies only the text currently showing in the console."""
    from src.ui.views.logs_view import LogsView

    app = ctk_app
    view = LogsView(app, app)

    # Insert specific text into log_textbox (simulating app output)
    view.append_log("Console line 1: Checking status")
    view.append_log("Console line 2: Ad skipped")

    with patch.object(view, "clipboard_clear") as mock_clear, \
         patch.object(view, "clipboard_append") as mock_append:
        view.copy_logs()
        mock_clear.assert_called_once()
        mock_append.assert_called_once()
        copied = mock_append.call_args[0][0]
        assert "Console line 1: Checking status" in copied
        assert "Console line 2: Ad skipped" in copied


def test_clear_logs_clears_console_without_deleting_session_log_file(ctk_app, tmp_path):
    """Clear Logs only clears the console in the app and does not clear or delete the session log file."""
    from src.ui.views.logs_view import LogsView

    # Start session and write file logs
    session_file = start_session_logging(log_dir=tmp_path)
    emit(None, "Session file log: Important diagnostic data")
    stop_session_logging()

    app = ctk_app
    app.is_running = False
    view = LogsView(app, app)

    # Put text in console
    view.append_log("Console message displayed to user")
    assert "Console message displayed to user" in view.log_textbox.get("1.0", "end")

    # Click Clear Logs
    view.clear_logs()

    # 1. Console text must be cleared
    assert view.log_textbox.get("1.0", "end-1c").strip() == ""

    # 2. Session log file on disk MUST still exist and retain its data
    assert session_file.exists()
    disk_content = session_file.read_text(encoding="utf-8")
    assert "Session file log: Important diagnostic data" in disk_content

    # 3. Copy Complete Logs still copies the complete session log from disk
    with patch.object(view, "clipboard_clear") as mock_clear, \
         patch.object(view, "clipboard_append") as mock_append, \
         patch("src.utils.logger.get_project_root", return_value=tmp_path):
        view.copy_complete_logs()
        mock_clear.assert_called_once()
        mock_append.assert_called_once()
        copied = mock_append.call_args[0][0]
        assert "Session file log: Important diagnostic data" in copied

    # 4. Copy Logs does not copy anything because console was cleared
    with patch.object(view, "clipboard_clear") as mock_clear2, \
         patch.object(view, "clipboard_append") as mock_append2:
        view.copy_logs()
        # Since console is empty, clipboard_append is not called with logs
        mock_append2.assert_not_called()


def test_copy_complete_logs_fallback_to_automation_log_when_no_session_files(ctk_app, tmp_path):
    """When sessions directory has no files, Copy Complete Logs extracts the last session from automation.log."""
    from src.ui.views.logs_view import LogsView

    # Ensure sessions dir is empty
    s_dir = tmp_path / "sessions"
    s_dir.mkdir(parents=True, exist_ok=True)

    # Create automation.log with two sessions
    auto_log = tmp_path / "automation.log"
    auto_log.write_text(
        "2026-11-09 10:00:00 [INFO] === Starting YouTube Automation Session ===\n"
        "2026-11-09 10:05:00 [INFO] Old session task completed\n"
        "2026-11-10 11:00:00 [INFO] === Starting Watch Session for user@example.com ===\n"
        "2026-11-10 11:15:00 [INFO] Recent session active watch 15m\n",
        encoding="utf-8",
    )

    app = ctk_app
    app.is_running = False
    view = LogsView(app, app)

    with patch.object(view, "clipboard_clear") as mock_clear, \
         patch.object(view, "clipboard_append") as mock_append, \
         patch("src.utils.logger.get_project_root", return_value=tmp_path):
        view.copy_complete_logs()
        mock_clear.assert_called_once()
        mock_append.assert_called_once()
        copied = mock_append.call_args[0][0]
        assert "=== Starting Watch Session for user@example.com ===" in copied
        assert "Recent session active watch 15m" in copied
        assert "Old session task completed" not in copied
