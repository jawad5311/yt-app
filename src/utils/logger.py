import logging
import queue
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .helpers import get_project_root

SECRET_PATTERNS = [
    re.compile(r"key=([A-Za-z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    re.compile(r"AIzaSy[A-Za-z0-9_\-]+"),
]

MAX_SESSION_LOGS = 5
_CURRENT_SESSION_FILE: Optional[Path] = None
_CURRENT_SESSION_HANDLER: Optional[logging.FileHandler] = None
_SESSION_LOCK = threading.Lock()


def redact_secrets(text: Any) -> str:
    """Mask sensitive API keys and tokens in log strings."""
    val = str(text)
    for pattern in SECRET_PATTERNS:
        val = pattern.sub("[REDACTED_API_KEY]", val)
    return val


def init_logging(log_dir: Optional[Path] = None) -> Path:
    """Initialize logging handler to file logs/automation.log."""
    if log_dir is None:
        log_dir = get_project_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "automation.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate file handlers
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == str(log_file):
            return log_file

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    root_logger.addHandler(file_handler)

    return log_file


def get_sessions_dir(base_dir: Optional[Path] = None) -> Path:
    """Get or create the directory used for storing per-session log files."""
    if base_dir is None:
        base_dir = get_project_root() / "logs"
    sessions_dir = base_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def prune_old_session_files(session_dir: Path, max_files: int = MAX_SESSION_LOGS) -> None:
    """Enforce max session files limit (FIFO rotation: oldest deleted when reaching max_files)."""
    try:
        files = sorted(session_dir.glob("session_*.log"), key=lambda p: (p.stat().st_mtime, p.name))
        while len(files) >= max_files:
            oldest = files.pop(0)
            try:
                oldest.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def start_session_logging(log_dir: Optional[Path] = None, max_sessions: int = MAX_SESSION_LOGS) -> Path:
    """Start recording a new session log file with rotation keeping at most max_sessions files.
    Every session starts with 'Start Browsing' and ends after 'Stop' is pressed.
    """
    global _CURRENT_SESSION_FILE, _CURRENT_SESSION_HANDLER
    with _SESSION_LOCK:
        root_logger = logging.getLogger()

        # Detach previous session handler if still attached
        if _CURRENT_SESSION_HANDLER is not None:
            try:
                _CURRENT_SESSION_HANDLER.flush()
                _CURRENT_SESSION_HANDLER.close()
                root_logger.removeHandler(_CURRENT_SESSION_HANDLER)
            except Exception:
                pass
            _CURRENT_SESSION_HANDLER = None

        s_dir = get_sessions_dir(log_dir)
        prune_old_session_files(s_dir, max_files=max_sessions)

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_file = s_dir / f"session_{timestamp_str}.log"
        counter = 1
        while session_file.exists():
            session_file = s_dir / f"session_{timestamp_str}_{counter}.log"
            counter += 1

        handler = logging.FileHandler(session_file, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

        _CURRENT_SESSION_FILE = session_file
        _CURRENT_SESSION_HANDLER = handler
        return session_file


def stop_session_logging() -> Optional[Path]:
    """Stop the current session logging handler upon session stop/completion."""
    global _CURRENT_SESSION_HANDLER
    with _SESSION_LOCK:
        if _CURRENT_SESSION_HANDLER is not None:
            root_logger = logging.getLogger()
            try:
                _CURRENT_SESSION_HANDLER.flush()
                _CURRENT_SESSION_HANDLER.close()
                root_logger.removeHandler(_CURRENT_SESSION_HANDLER)
            except Exception:
                pass
            _CURRENT_SESSION_HANDLER = None
        return _CURRENT_SESSION_FILE


def get_current_session_log_file(log_dir: Optional[Path] = None) -> Optional[Path]:
    """Return path to active or most recent session log file."""
    global _CURRENT_SESSION_FILE
    with _SESSION_LOCK:
        if log_dir is None:
            if _CURRENT_SESSION_FILE and _CURRENT_SESSION_FILE.exists():
                return _CURRENT_SESSION_FILE
            s_dir = get_sessions_dir()
        else:
            s_dir = get_sessions_dir(log_dir)
            if _CURRENT_SESSION_FILE and _CURRENT_SESSION_FILE.exists():
                try:
                    if _CURRENT_SESSION_FILE.resolve().is_relative_to(s_dir.resolve()):
                        return _CURRENT_SESSION_FILE
                except Exception:
                    pass
        files = sorted(s_dir.glob("session_*.log"), key=lambda p: (p.stat().st_mtime, p.name))
        if files:
            _CURRENT_SESSION_FILE = files[-1]
            return _CURRENT_SESSION_FILE
        return None


def read_current_session_logs(log_dir: Optional[Path] = None) -> str:
    """Read and return all logs of the current session stored in the file."""
    with _SESSION_LOCK:
        if _CURRENT_SESSION_HANDLER is not None:
            try:
                _CURRENT_SESSION_HANDLER.flush()
            except Exception:
                pass

    file_path = get_current_session_log_file(log_dir)
    if not file_path or not file_path.exists():
        return ""

    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
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
