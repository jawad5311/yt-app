"""Ad blocker stub (uBlock extension removed).

Extension-based ad blocking has been completely removed in favor of
native script-based ad skipping (no extension overhead or anti-adblock detection).
"""

from pathlib import Path
from typing import Any, Optional
import queue


def ensure_adblock(log_queue: Optional[queue.Queue] = None, force: bool = False) -> Optional[Path]:
    """Stub returning None; extensions are no longer provisioned or loaded."""
    return None


def load_extension_cdp(driver: Any, extension_dir: Path, log_queue: Optional[queue.Queue] = None) -> bool:
    """Stub returning False; extensions are no longer provisioned or loaded."""
    return False


def find_extension_root(folder: Path) -> Optional[Path]:
    """Return the directory containing manifest.json if present."""
    manifest = folder / "manifest.json"
    if manifest.is_file():
        return folder
    candidates = sorted(folder.rglob("manifest.json"))
    if not candidates:
        return None
    return candidates[0].parent
