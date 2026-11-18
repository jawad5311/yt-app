import atexit
import logging
import queue
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from .helpers import get_project_root

SECRET_PATTERNS = [
    re.compile(r"key=([A-Za-z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    re.compile(r"AIzaSy[A-Za-z0-9_\-]+"),
]

# Keep only the single most recent session log file; old session logs are deleted on new session start.
MAX_SESSION_LOGS = 1
# Batch disk writes every 60 seconds (1 minute) to minimize CPU, disk I/O, and power consumption.
SESSION_LOG_FLUSH_INTERVAL_SECONDS = 60.0
# Safety cap to flush early if a burst produces thousands of log lines before the 1-minute mark.
SESSION_LOG_MAX_BUFFER_RECORDS = 5000

_CURRENT_SESSION_FILE: Optional[Path] = None
_CURRENT_SESSION_HANDLER: Optional["BatchedSessionFileHandler"] = None
_SESSION_LOCK = threading.Lock()


class BatchedSessionFileHandler(logging.Handler):
    """Memory-buffered file handler that persists logs in timed batches.

    Avoids per-line OS write/flush syscalls (which wake storage and consume extra CPU/power)
    by buffering formatted log lines in RAM and writing them in a single append operation
    every ``flush_interval`` seconds (default: 60s). Always flushes immediately on
    ``flush()``, ``close()``, session stop, or when reading complete logs.
    """

    def __init__(
        self,
        filepath: Path,
        flush_interval: float = SESSION_LOG_FLUSH_INTERVAL_SECONDS,
        max_buffer_records: int = SESSION_LOG_MAX_BUFFER_RECORDS,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self.filepath = Path(filepath)
        self.baseFilename = str(self.filepath.resolve())
        self.flush_interval = max(0.0, float(flush_interval))
        self.max_buffer_records = max(1, int(max_buffer_records))
        self.encoding = encoding

        self._buffer: List[str] = []
        self._buffer_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._closed = False
        self._flush_thread: Optional[threading.Thread] = None

        # Create the session log file immediately at session start so it is visible on disk.
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.filepath.touch(exist_ok=True)

        if self.flush_interval > 0.0:
            self._flush_thread = threading.Thread(
                target=self._flush_loop,
                name="session-log-flusher",
                daemon=True,
            )
            self._flush_thread.start()

    def _flush_loop(self) -> None:
        """Sleep with zero CPU overhead until the flush interval elapses or stop is signaled."""
        while not self._stop_event.wait(timeout=self.flush_interval):
            try:
                self.flush()
            except Exception:
                pass

    def emit(self, record: logging.LogRecord) -> None:
        """Format and append a log line to the in-memory batch buffer."""
        if self._closed:
            return
        try:
            msg = self.format(record)
            should_flush = False
            with self._buffer_lock:
                if self._closed:
                    return
                self._buffer.append(msg)
                if self.flush_interval <= 0.0 or len(self._buffer) >= self.max_buffer_records:
                    should_flush = True
            if should_flush:
                self.flush()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        """Flush all currently buffered log lines to disk in a single batched write."""
        with self._io_lock:
            with self._buffer_lock:
                if not self._buffer:
                    return
                batch = self._buffer
                self._buffer = []
            try:
                self.filepath.parent.mkdir(parents=True, exist_ok=True)
                with open(self.filepath, "a", encoding=self.encoding) as fh:
                    fh.write("\n".join(batch) + "\n")
                    fh.flush()
            except Exception:
                if not self._closed:
                    with self._buffer_lock:
                        self._buffer = batch + self._buffer

    def close(self) -> None:
        """Stop the background timer, flush all remaining buffered records, and mark closed."""
        if self._closed:
            super().close()
            return
        self._stop_event.set()
        thread = self._flush_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            self.flush()
        finally:
            with self._buffer_lock:
                self._closed = True
            super().close()


def redact_secrets(text: Any) -> str:
    """Mask sensitive API keys and tokens in log strings."""
    val = str(text)
    for pattern in SECRET_PATTERNS:
        val = pattern.sub("[REDACTED_API_KEY]", val)
    return val


def _detach_legacy_file_handlers(root_logger: logging.Logger, legacy_log_path: Path) -> None:
    """Close and remove any FileHandler writing to the legacy unbounded automation.log."""
    try:
        resolved_target = str(legacy_log_path.resolve())
    except Exception:
        resolved_target = str(legacy_log_path)

    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            try:
                h_path = str(Path(handler.baseFilename).resolve())
            except Exception:
                h_path = str(getattr(handler, "baseFilename", ""))
            if h_path == resolved_target or Path(h_path).name == "automation.log":
                try:
                    root_logger.removeHandler(handler)
                    handler.flush()
                    handler.close()
                except Exception:
                    pass


def init_logging(log_dir: Optional[Path] = None) -> Path:
    """Initialize logging subsystem for single-session log persistence.

    Does not attach a duplicate unbounded automation.log FileHandler, and preserves
    the most recent session log file across app restarts until a new session starts.
    """
    if log_dir is None:
        log_dir = get_project_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = get_sessions_dir(log_dir)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    _detach_legacy_file_handlers(root_logger, log_dir / "automation.log")

    recent = get_most_recent_session_log_file(log_dir)
    if recent is not None:
        return recent
    return sessions_dir / "session_latest.log"


def get_sessions_dir(base_dir: Optional[Path] = None) -> Path:
    """Get or create the directory used for storing per-session log files."""
    if base_dir is None:
        base_dir = get_project_root() / "logs"
    sessions_dir = base_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def prune_old_session_files(session_dir: Path, max_files: int = MAX_SESSION_LOGS) -> None:
    """Enforce max session files limit (FIFO rotation: oldest deleted when reaching max_files).

    With the default ``max_files=1``, all prior session log files are deleted so that
    creating the new session file leaves only one log file for the current session.
    """
    try:
        limit = max(1, int(max_files))
        files = sorted(session_dir.glob("session_*.log"), key=lambda p: (p.stat().st_mtime, p.name))
        while files and len(files) >= limit:
            oldest = files.pop(0)
            try:
                oldest.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def start_session_logging(
    log_dir: Optional[Path] = None,
    max_sessions: int = MAX_SESSION_LOGS,
    flush_interval: float = SESSION_LOG_FLUSH_INTERVAL_SECONDS,
) -> Path:
    """Start recording a new session log file.

    By default (``max_sessions=1``), deletes all previous session log files (and any
    legacy ``automation.log``) so only one log file exists containing the current run.
    Updates are written in batches every ``flush_interval`` seconds (default 60s) and
    immediately flushed on session stop or when reading logs.
    """
    global _CURRENT_SESSION_FILE, _CURRENT_SESSION_HANDLER
    with _SESSION_LOCK:
        root_logger = logging.getLogger()

        # Detach and close previous session handler if still attached
        if _CURRENT_SESSION_HANDLER is not None:
            prev_handler = _CURRENT_SESSION_HANDLER
            _CURRENT_SESSION_HANDLER = None
            try:
                root_logger.removeHandler(prev_handler)
            except Exception:
                pass
            try:
                prev_handler.close()
            except Exception:
                pass

        # Ensure no legacy automation.log handler or stale file remains
        base_dir = log_dir if log_dir is not None else (get_project_root() / "logs")
        legacy_auto_log = base_dir / "automation.log"
        _detach_legacy_file_handlers(root_logger, legacy_auto_log)
        if max_sessions <= 1 and legacy_auto_log.exists():
            try:
                legacy_auto_log.unlink(missing_ok=True)
            except Exception:
                pass

        s_dir = get_sessions_dir(log_dir)
        prune_old_session_files(s_dir, max_files=max_sessions)

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_file = s_dir / f"session_{timestamp_str}.log"
        counter = 1
        while session_file.exists():
            session_file = s_dir / f"session_{timestamp_str}_{counter}.log"
            counter += 1

        handler = BatchedSessionFileHandler(
            session_file,
            flush_interval=flush_interval,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

        _CURRENT_SESSION_FILE = session_file
        _CURRENT_SESSION_HANDLER = handler
        return session_file


def flush_session_logs() -> None:
    """Immediately flush any buffered log records for the active session to disk."""
    with _SESSION_LOCK:
        handler = _CURRENT_SESSION_HANDLER
    if handler is not None:
        try:
            handler.flush()
        except Exception:
            pass


def stop_session_logging() -> Optional[Path]:
    """Stop the current session logging handler, flushing all buffered logs to disk."""
    global _CURRENT_SESSION_HANDLER
    with _SESSION_LOCK:
        handler = _CURRENT_SESSION_HANDLER
        _CURRENT_SESSION_HANDLER = None
        if handler is not None:
            root_logger = logging.getLogger()
            try:
                root_logger.removeHandler(handler)
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass
        return _CURRENT_SESSION_FILE


def is_session_logging_active() -> bool:
    """Return True if session logging is currently active (a session is running)."""
    with _SESSION_LOCK:
        return _CURRENT_SESSION_HANDLER is not None


def get_most_recent_session_log_file(log_dir: Optional[Path] = None) -> Optional[Path]:
    """Return path to the most recent completed or active session log file on disk."""
    try:
        s_dir = get_sessions_dir(log_dir)
        files = sorted(s_dir.glob("session_*.log"), key=lambda p: (p.name, p.stat().st_mtime))
        if files:
            return files[-1]
        if log_dir is None:
            alt_dir = get_project_root() / "sessions"
            if alt_dir.exists() and alt_dir != s_dir:
                files2 = sorted(alt_dir.glob("session_*.log"), key=lambda p: (p.name, p.stat().st_mtime))
                if files2:
                    return files2[-1]
    except Exception:
        pass
    return None


def get_current_session_log_file(log_dir: Optional[Path] = None) -> Optional[Path]:
    """Return path to active session log file if running, or the most recent session log file if idle."""
    global _CURRENT_SESSION_FILE
    with _SESSION_LOCK:
        # If a session is currently running, return the active session's log file
        if _CURRENT_SESSION_HANDLER is not None and _CURRENT_SESSION_FILE and _CURRENT_SESSION_FILE.exists():
            if log_dir is None:
                return _CURRENT_SESSION_FILE
            try:
                s_dir = get_sessions_dir(log_dir)
                if _CURRENT_SESSION_FILE.resolve().is_relative_to(s_dir.resolve()):
                    return _CURRENT_SESSION_FILE
            except Exception:
                pass

    # If no session is running (or active file missing), return the most recent session log file
    recent_file = get_most_recent_session_log_file(log_dir)
    if recent_file:
        with _SESSION_LOCK:
            _CURRENT_SESSION_FILE = recent_file
        return recent_file

    return None


def read_current_session_logs(log_dir: Optional[Path] = None) -> str:
    """Read and return all logs of the current session (if running) or most recent session (if idle)."""
    flush_session_logs()

    file_path = get_current_session_log_file(log_dir)
    if file_path and file_path.exists():
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                return content
        except Exception:
            pass

    # Fallback if no session files exist: check automation.log for the most recent session
    try:
        base_dir = log_dir if log_dir is not None else (get_project_root() / "logs")
        auto_log = base_dir / "automation.log"
        if not auto_log.exists():
            auto_log = get_project_root() / "automation.log"
        if auto_log.exists():
            raw_text = auto_log.read_text(encoding="utf-8", errors="replace")
            if raw_text.strip():
                lines = raw_text.splitlines()
                last_idx = -1
                for idx, line in enumerate(lines):
                    for marker in [
                        "=== Starting YouTube Automation Session",
                        "=== Starting Watch Session for",
                        "Starting automation session for account:",
                        "=== Starting Browsing Session",
                        "=== Starting Shorts Cycle",
                    ]:
                        if marker in line:
                            last_idx = idx
                            break
                if last_idx >= 0:
                    return "\n".join(lines[last_idx:])
                return raw_text
    except Exception:
        pass

    return ""


def emit(log_queue: Optional[queue.Queue], message: str, level: int = logging.INFO) -> None:
    """Log a message to python logging system and UI queue safely."""
    safe_msg = redact_secrets(message)
    logging.log(level, safe_msg)
    if log_queue is not None:
        try:
            log_queue.put_nowait(safe_msg)
        except queue.Full:
            pass


atexit.register(stop_session_logging)
