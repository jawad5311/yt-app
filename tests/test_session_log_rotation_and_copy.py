"""Tests for session log rotation (max 5 sessions), 1000-line UI limit, and 'Copy Complete Logs'."""

import logging
import queue
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.logger import (
    MAX_SESSION_LOGS,
    emit,
    get_current_session_log_file,
    get_sessions_dir,
    prune_old_session_files,
    read_current_session_logs,
    start_session_logging,
    stop_session_logging,
)


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
