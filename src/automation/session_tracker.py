"""Session Channel Watch Tracker.

Tracks per-channel video watch time for each session run:
- Maintains a separate counter for each channel.
- Only increments the counter for the channel whose video is currently playing.
- Increments by each minute of video watch time.
- As soon as the counter reaches 60 minutes, converts to hours and updates channel watch time in the app.
- When the script is stopped, converts all accumulated minutes in the counter to hours and updates the channel total watch time in the app.
"""

import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from ..models.channel import Channel
from ..models.watch_stats import format_hours
from ..utils.logger import emit


class SessionChannelTracker:
    """Manages per-channel minute counters and hourly conversion for an automation session."""

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

            if mins > 0:
                current_mins = self.minute_counters.get(key, 0) + mins
                self.minute_counters[key] = current_mins

                if self.log_queue:
                    emit(
                        self.log_queue,
                        f"Session watch counter: Channel '{channel_name}' +{mins}m (session counter: {current_mins}m).",
                    )

                # As soon as the counter reaches 60 min, convert to hours and update
                if current_mins >= 60:
                    hours_to_commit = current_mins // 60
                    self.minute_counters[key] = current_mins % 60
                    self._commit_hours(channel_name, float(hours_to_commit))

            # Check if this channel has achieved its target watch time
            if self.is_channel_target_achieved(channel_name):
                # When target is reached, commit uncommitted minutes immediately to settings
                uncommitted = self.minute_counters.get(key, 0)
                if uncommitted > 0:
                    self._commit_seconds(channel_name, uncommitted * 60.0)
                    self.minute_counters[key] = 0
                if self.log_queue:
                    total_h = self.get_current_total_watch_seconds(channel_name) / 3600.0
                    emit(
                        self.log_queue,
                        f"Target watch time achieved for channel '{channel_name}'! Total watch time reached {total_h:.1f} hours.",
                    )

    def get_current_total_watch_seconds(self, channel_name: str) -> float:
        """Calculate total watch seconds for a channel (persisted watch_seconds + active session delta)."""
        if not channel_name:
            return 0.0
        key = self._canon_name(channel_name)
        base = 0.0
        for ch in self.channels:
            if self._canon_name(ch.name) == key:
                base = getattr(ch, "watch_seconds", 0.0)
                break
        if base <= 0.0 and self.config_mgr:
            for ch_dict in self.config_mgr.get_channels():
                if self._canon_name(ch_dict.get("name", "")) == key:
                    base = float(ch_dict.get("watch_seconds", 0.0) or 0.0)
                    break
        with self._lock:
            session_sec = self.minute_counters.get(key, 0) * 60.0 + self.second_accumulators.get(key, 0.0)
        return base + session_sec

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

    def on_stop(self) -> None:
        """Called when script is stopped: converts all remaining minutes to hours and updates channel total watch time."""
        with self._lock:
            for key, mins in list(self.minute_counters.items()):
                if mins > 0:
                    hours = mins / 60.0
                    channel_name = self.original_names.get(key, key)
                    self._commit_seconds(channel_name, mins * 60.0)
                    if self.log_queue:
                        emit(
                            self.log_queue,
                            f"Script stopped: converted {mins}m in session counter for Channel '{channel_name}' to {hours:.1f} hours and updated total watch time.",
                        )
                self.minute_counters[key] = 0
                self.second_accumulators[key] = 0.0
            self.active_channel_name = None

    def get_counter_minutes(self, channel_name: str) -> int:
        """Return the current session minute counter for a channel."""
        with self._lock:
            return self.minute_counters.get(self._canon_name(channel_name), 0)

    def _commit_hours(self, channel_name: str, hours: float) -> None:
        if hours <= 0:
            return
        seconds = hours * 3600.0
        self._commit_seconds(channel_name, seconds)
        if self.log_queue:
            emit(
                self.log_queue,
                f"Channel '{channel_name}' reached 60m session watch time! Converted to {hours:.1f} hour(s) and updated total watch time in app.",
            )

    def _commit_seconds(self, channel_name: str, seconds: float) -> None:
        if seconds <= 0:
            return
        key = self._canon_name(channel_name)

        # 1. Update in-memory Channel object
        for ch in self.channels:
            if self._canon_name(ch.name) == key:
                ch.watch_seconds = getattr(ch, "watch_seconds", 0.0) + seconds

        # 2. Update settings.json via config_mgr
        if self.config_mgr:
            try:
                ch_list = self.config_mgr.get_channels()
                updated = False
                for ch_dict in ch_list:
                    if self._canon_name(ch_dict.get("name", "")) == key:
                        curr = float(ch_dict.get("watch_seconds", 0.0) or 0.0)
                        ch_dict["watch_seconds"] = round(curr + seconds, 3)
                        updated = True
                        break
                if updated:
                    self.config_mgr.set_channels(ch_list)
            except Exception as e:
                logging.debug(f"Failed updating channel in config_mgr: {e}")

        # 3. Update stats_store
        if self.stats_store:
            try:
                if hasattr(self.stats_store, "add_channel_watch_seconds"):
                    self.stats_store.add_channel_watch_seconds(channel_name, seconds)
            except Exception as e:
                logging.debug(f"Failed updating stats_store: {e}")

        # 4. Notify app UI to refresh watch stats
        if self.emit_event:
            try:
                self.emit_event(("channel_watch_time_updated", channel_name, seconds))
            except Exception as e:
                logging.debug(f"Failed emitting channel_watch_time_updated: {e}")

    @staticmethod
    def _canon_name(name: str) -> str:
        return (name or "").strip().lower().lstrip("@")
