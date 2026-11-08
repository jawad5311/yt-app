"""Daily API Quota Tracker for YouTube Data API.

Tracks daily quota consumption per calendar day according to the local system clock.
Automatically resets to 0 whenever the system clock day changes (midnight rollover).
Persists state across application restarts in ``quota.json``.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .helpers import get_project_root

logger = logging.getLogger("quota_tracker")


class QuotaTracker:
    """Thread-safe manager for tracking and persisting daily YouTube API quota usage."""

    def __init__(self, storage_file: Optional[Path] = None):
        if storage_file is None:
            storage_file = get_project_root() / "quota.json"
        self.storage_file = Path(storage_file)
        self._lock = threading.RLock()
        self._date: str = self._get_current_system_date()
        self._used: int = 0
        self._listeners: List[Callable[[int], None]] = []
        self.load()

    @staticmethod
    def _get_current_system_date() -> str:
        """Return the current local calendar date string (YYYY-MM-DD)."""
        return datetime.now().strftime("%Y-%m-%d")

    def register_listener(self, callback: Callable[[int], None]) -> None:
        """Register a callback that receives the updated quota used count."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[int], None]) -> None:
        """Remove a previously registered listener callback."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify_listeners_unlocked(self, used: int) -> None:
        """Call all registered listener callbacks with the new quota value."""
        for cb in list(self._listeners):
            try:
                cb(used)
            except Exception as exc:
                logger.debug("Error in quota listener callback: %s", exc)

    def load(self) -> None:
        """Load stored quota from disk and check for calendar day rollover."""
        with self._lock:
            current_date = self._get_current_system_date()
            if not self.storage_file.exists():
                self._date = current_date
                self._used = 0
                self._save_unlocked()
                return

            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    saved_date = str(data.get("date", "")).strip()
                    saved_used = data.get("used", 0)
                    try:
                        saved_used = int(saved_used)
                        if saved_used < 0:
                            saved_used = 0
                    except (ValueError, TypeError):
                        saved_used = 0

                    if saved_date == current_date:
                        self._date = saved_date
                        self._used = saved_used
                    else:
                        # Day has rolled over
                        self._date = current_date
                        self._used = 0
                        self._save_unlocked()
                else:
                    self._date = current_date
                    self._used = 0
                    self._save_unlocked()
            except Exception as exc:
                logger.warning("Could not read quota file %s: %s; resetting quota", self.storage_file, exc)
                self._date = current_date
                self._used = 0
                self._save_unlocked()

    def _save_unlocked(self) -> None:
        """Persist current date and quota usage to disk atomically."""
        payload: Dict[str, Any] = {
            "date": self._date,
            "used": self._used,
        }
        try:
            temp_file = self.storage_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            temp_file.replace(self.storage_file)
        except Exception as exc:
            logger.error("Failed to save quota to %s: %s", self.storage_file, exc)

    def _check_day_rollover_unlocked(self) -> bool:
        """Check if system clock calendar day has advanced and reset if needed."""
        today = self._get_current_system_date()
        if today != self._date:
            self._date = today
            self._used = 0
            self._save_unlocked()
            return True
        return False

    def check_day_rollover(self) -> bool:
        """Public check called by periodic UI timer or worker.
        
        Returns True if day rolled over and quota was reset to 0.
        """
        with self._lock:
            rolled_over = self._check_day_rollover_unlocked()
            if rolled_over:
                self._notify_listeners_unlocked(self._used)
            return rolled_over

    def get_used(self) -> int:
        """Get the total quota used today, checking for calendar day rollover first."""
        with self._lock:
            if self._check_day_rollover_unlocked():
                self._notify_listeners_unlocked(self._used)
            return self._used

    def get_date(self) -> str:
        """Get the date string (YYYY-MM-DD) currently tracked."""
        with self._lock:
            self._check_day_rollover_unlocked()
            return self._date

    def increment(self, amount: int = 1) -> int:
        """Increment today's quota usage by amount and notify listeners."""
        with self._lock:
            self._check_day_rollover_unlocked()
            if amount > 0:
                self._used += amount
                self._save_unlocked()
            val = self._used
            self._notify_listeners_unlocked(val)
            return val

    def reset(self) -> None:
        """Explicitly reset quota to 0 for today."""
        with self._lock:
            self._date = self._get_current_system_date()
            self._used = 0
            self._save_unlocked()
            self._notify_listeners_unlocked(0)


_global_tracker: Optional[QuotaTracker] = None
_global_lock = threading.Lock()


def get_quota_tracker(storage_file: Optional[Path] = None) -> QuotaTracker:
    """Return the global QuotaTracker singleton instance."""
    global _global_tracker
    with _global_lock:
        if _global_tracker is None:
            _global_tracker = QuotaTracker(storage_file=storage_file)
        return _global_tracker


def set_quota_tracker(tracker: Optional[QuotaTracker]) -> None:
    """Set or reset the global QuotaTracker singleton (useful in unit tests)."""
    global _global_tracker
    with _global_lock:
        _global_tracker = tracker


def record_api_quota_usage(amount: int = 1) -> int:
    """Convenience helper to record API quota units consumed."""
    return get_quota_tracker().increment(amount=amount)
