import logging
from .helpers import get_project_root
import queue
import re
from pathlib import Path
from typing import Any, Optional

SECRET_PATTERNS = [
    re.compile(r"key=([A-Za-z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    re.compile(r"AIzaSy[A-Za-z0-9_\-]+"),
]


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


def emit(log_queue: Optional[queue.Queue], message: str, level: int = logging.INFO) -> None:
    """Log a message to python logging system and UI queue safely."""
    safe_msg = redact_secrets(message)
    logging.log(level, safe_msg)
    if log_queue is not None:
        try:
            log_queue.put_nowait(safe_msg)
        except queue.Full:
            pass
