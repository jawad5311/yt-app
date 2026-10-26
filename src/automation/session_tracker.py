"""Session Channel Watch Tracker.

Tracks per-channel video watch time for each session run:
- Maintains a separate counter for each channel.
- Only increments the counter for the channel whose video is currently playing.
- Increments by each minute of video watch time.
- Checkpoints each measured playback delta, including fractional seconds.
- Session minute counters are display-only and are never added to totals again.
"""

import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from ..models.channel import Channel
from ..utils.logger import emit


class SessionChannelTracker:
    """Checkpoint channel time while retaining session minute counters for display."""

    def __init__(
        self,
        config_mgr: Optional[Any] = None,
        stats_store: Optional[Any] = None,
        channels: Optional[List[Channel]] = None,
        emit_event: Optional[Callable[[Any], None]] = None,
        log_queue: Optional[queue.Queue] = None,
    ):
        self.config_mgr = config_mgr
        self.stats_store = stats_store
        self.channels = channels if channels is not None else []
        self.emit_event = emit_event
        self.log_queue = log_queue

        # Separate counter for each channel (in minutes) for the current session
        self.minute_counters: Dict[str, int] = {}
        # Sub-minute seconds accumulator per channel
        self.second_accumulators: Dict[str, float] = {}
        # Mapping from canon key to original display channel name
        self.original_names: Dict[str, str] = {}
        # Channel whose video is currently playing
        self.active_channel_name: Optional[str] = None
        self._active_watch_delta: float = 0.0
        self._pending_seconds: Dict[str, float] = {}
        self._lock = threading.RLock()

        # Cache known channel names
        for ch in self.channels:
            if ch.name:
                self.original_names[self._canon_name(ch.name)] = ch.name

    def set_active_channel(self, channel_name: Optional[str]) -> None:
        """Set the channel whose video is currently playing."""
        with self._lock:
            self._active_watch_delta = 0.0
            self.active_channel_name = (channel_name or "").strip() if channel_name else None
            if self.active_channel_name:
                self.original_names[self._canon_name(self.active_channel_name)] = self.active_channel_name

    def get_active_watch_delta(self) -> float:
        """Return watch seconds accumulated during current active video playback."""
        with self._lock:
            return self._active_watch_delta

    def add_watch_delta(self, channel_name: str, delta_seconds: float) -> None:
        """Add watch seconds. Only increments if this channel's video is currently playing."""
        if not channel_name or delta_seconds <= 0:
            return

        with self._lock:
            # Enforce: only that channel counter minutes are increasing whose video is currently playing
            if not self.active_channel_name:
                return
            active_clean = self._canon_name(self.active_channel_name)
            target_clean = self._canon_name(channel_name)
            if active_clean != target_clean:
                return

            self._active_watch_delta += delta_seconds
            key = active_clean
            self.original_names.setdefault(key, channel_name)

            sec = self.second_accumulators.get(key, 0.0) + delta_seconds
            mins = int(sec // 60.0)
            self.second_accumulators[key] = sec % 60.0

            self._commit_seconds(channel_name, delta_seconds, notify=mins > 0)

            if mins > 0:
                current_mins = self.minute_counters.get(key, 0) + mins
                self.minute_counters[key] = current_mins

                if self.log_queue:
                    emit(
                        self.log_queue,
                        f"Session watch counter: Channel '{channel_name}' +{mins}m (session counter: {current_mins}m).",
                    )

                # Keep the session display within the current hour. The time
                # itself was already checkpointed, so do not commit it again.
                if current_mins >= 60:
                    self.minute_counters[key] = current_mins % 60

            # Check if this channel has achieved its target watch time
            if self.is_channel_target_achieved(channel_name):
                if self.log_queue:
                    total_h = self.get_current_total_watch_seconds(channel_name) / 3600.0
                    emit(
                        self.log_queue,
                        f"Target watch time achieved for channel '{channel_name}'! Total watch time reached {total_h:.1f} hours.",
                    )

    def get_current_total_watch_seconds(self, channel_name: str) -> float:
        """Return checkpointed time plus any delta awaiting a successful write."""
        if not channel_name:
            return 0.0
        key = self._canon_name(channel_name)
        with self._lock:
            if self.config_mgr:
                for ch_dict in self.config_mgr.get_channels():
                    if self._canon_name(ch_dict.get("name", "")) == key:
                        return float(ch_dict.get("watch_seconds", 0.0) or 0.0) + self._pending_seconds.get(key, 0.0)
            for ch in self.channels:
                if self._canon_name(ch.name) == key:
                    return getattr(ch, "watch_seconds", 0.0)
        return 0.0

    def is_channel_target_achieved(self, channel_name: str) -> bool:
        """Check if channel has reached its configured target watch time (target_watch_seconds)."""
        if not channel_name:
            return False
        key = self._canon_name(channel_name)
        target_sec = 0.0
        for ch in self.channels:
            if self._canon_name(ch.name) == key:
                target_sec = getattr(ch, "target_watch_seconds", 0.0)
                break
        if target_sec <= 0.0 and self.config_mgr:
            for ch_dict in self.config_mgr.get_channels():
                if self._canon_name(ch_dict.get("name", "")) == key:
                    target_sec = float(ch_dict.get("target_watch_seconds", 0.0) or 0.0)
                    break
        if target_sec <= 0.0:
            return False
        return self.get_current_total_watch_seconds(channel_name) >= target_sec

    def reset(self) -> None:
        """Clear all active session counters, accumulators, pending checkpoints, and zero channel watch times."""
        with self._lock:
            self.minute_counters.clear()
            self.second_accumulators.clear()
            self._pending_seconds.clear()
            self._active_watch_delta = 0.0
            self.active_channel_name = None
            for ch in self.channels:
                ch.watch_seconds = 0.0

    def on_stop(self) -> None:
        """Retry pending checkpoints without adding already saved session time."""
        with self._lock:
            keys = set(self.minute_counters) | set(self.second_accumulators) | set(self._pending_seconds)
            for key in keys:
                self._commit_seconds(self.original_names.get(key, key), 0.0, notify=True)
                self.minute_counters[key] = 0
                self.second_accumulators[key] = 0.0
            self.active_channel_name = None

    def get_counter_minutes(self, channel_name: str) -> int:
        """Return the current session minute counter for a channel."""
        with self._lock:
            return self.minute_counters.get(self._canon_name(channel_name), 0)

    def _commit_seconds(self, channel_name: str, seconds: float, notify: bool = False) -> None:
        if seconds < 0:
            return
        key = self._canon_name(channel_name)

        # 1. Update in-memory Channel object
        for ch in self.channels:
            if self._canon_name(ch.name) == key:
                ch.watch_seconds = getattr(ch, "watch_seconds", 0.0) + seconds

        # 2. Checkpoint settings atomically. Keep failed writes pending so
        # another tick or Stop can retry without double-counting local time.
        if self.config_mgr:
            pending = self._pending_seconds.get(key, 0.0) + seconds
            self._pending_seconds[key] = pending
            try:
                if pending > 0:
                    total = self.config_mgr.increment_channel_watch_seconds(channel_name, pending)
                    if total is not None:
                        for ch in self.channels:
                            if self._canon_name(ch.name) == key:
                                ch.watch_seconds = total
                self._pending_seconds.pop(key, None)
            except Exception as e:
                logging.debug(f"Failed updating channel in config_mgr: {e}")

        # Video outcomes are recorded by the workers in StatsStore. Writing
        # these same seconds there as a channel record would count them twice.

        # 3. Notify app UI to refresh watch stats
        if notify and self.emit_event:
            try:
                self.emit_event(("channel_watch_time_updated", channel_name, seconds))
            except Exception as e:
                logging.debug(f"Failed emitting channel_watch_time_updated: {e}")

    @staticmethod
    def _canon_name(name: str) -> str:
        return (name or "").strip().lower().lstrip("@")
