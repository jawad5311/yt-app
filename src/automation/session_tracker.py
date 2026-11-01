"""Session Channel Watch Tracker.

Tracks per-channel video watch time for each session run:
- Maintains a separate counter for each channel.
- Only increments the counter for the channel whose video is currently playing.
- Increments by each minute of video watch time.
- Checkpoints each measured playback delta, including fractional seconds.
- Session minute counters are display-only and are never added to totals again.
- Notes baseline channel watch times at session start and calculates per-channel
  and combined session watch times for reporting.
"""

import logging
import queue
import threading
from datetime import datetime
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

        # Video & Shorts counts for session reporting
        self.total_regular_videos: int = 0
        self.total_shorts: int = 0
        self.channel_regular_videos: Dict[str, int] = {}
        self.channel_shorts: Dict[str, int] = {}

        # Discovery source counts for session reporting (suggested, search, home, random)
        self.suggested_videos_count: int = 0
        self.search_videos_count: int = 0
        self.home_videos_count: int = 0
        self.random_videos_count: int = 0
        self.search_queries_count: int = 0

        # Session tracking for reports
        self.session_start_time: datetime = datetime.now()
        self.session_end_time: Optional[datetime] = None
        # Baseline watch time noted separately for each channel at session start
        self.initial_channel_watch_seconds: Dict[str, float] = {}
        # Exact cumulative watch seconds generated in this session per channel
        self.session_watch_seconds: Dict[str, float] = {}

        # Cache known channel names and snapshot baseline watch time
        for ch in self.channels:
            if ch.name:
                key = self._canon_name(ch.name)
                self.original_names[key] = ch.name
                self.initial_channel_watch_seconds[key] = float(getattr(ch, "watch_seconds", 0.0) or 0.0)
                self.session_watch_seconds[key] = 0.0

        if self.config_mgr:
            try:
                for c_dict in self.config_mgr.get_channels():
                    name = c_dict.get("name", "")
                    if name:
                        key = self._canon_name(name)
                        self.original_names.setdefault(key, name)
                        if key not in self.initial_channel_watch_seconds:
                            self.initial_channel_watch_seconds[key] = float(c_dict.get("watch_seconds", 0.0) or 0.0)
                        self.session_watch_seconds.setdefault(key, 0.0)
            except Exception as e:
                logging.debug(f"Failed snapshotting initial channel watch times: {e}")

    def set_active_channel(self, channel_name: Optional[str]) -> None:
        """Set the channel whose video is currently playing."""
        with self._lock:
            self._active_watch_delta = 0.0
            self.active_channel_name = (channel_name or "").strip() if channel_name else None
            if self.active_channel_name:
                key = self._canon_name(self.active_channel_name)
                self.original_names[key] = self.active_channel_name
                self.session_watch_seconds.setdefault(key, 0.0)
                if key not in self.initial_channel_watch_seconds:
                    self.initial_channel_watch_seconds[key] = self.get_current_total_watch_seconds(self.active_channel_name)

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

            # Accumulate exact watch seconds for this session
            self.session_watch_seconds[key] = self.session_watch_seconds.get(key, 0.0) + delta_seconds

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

    def get_session_channel_watch_seconds(self, channel_name: str) -> float:
        """Return the watch seconds generated for a specific channel during this session."""
        if not channel_name:
            return 0.0
        with self._lock:
            return float(self.session_watch_seconds.get(self._canon_name(channel_name), 0.0))

    def get_session_total_watch_seconds(self) -> float:
        """Return combined watch seconds generated across all channels during this session."""
        with self._lock:
            return float(sum(self.session_watch_seconds.values()))

    def record_search_query(self) -> None:
        """Record that a YouTube search was performed."""
        with self._lock:
            self.search_queries_count += 1

    def record_video_watched(
        self,
        is_short: bool = False,
        channel_name: Optional[str] = None,
        source: Optional[str] = None,
        is_random: bool = False,
    ) -> None:
        """Record a completed or watched video / short, its discovery source, and random/non-target status."""
        with self._lock:
            if is_short:
                self.total_shorts += 1
            else:
                self.total_regular_videos += 1

            if is_random:
                self.random_videos_count += 1

            if source:
                src_clean = source.strip().lower()
                if "suggest" in src_clean or "recommend" in src_clean:
                    self.suggested_videos_count += 1
                elif "search" in src_clean:
                    self.search_videos_count += 1
                elif "home" in src_clean:
                    self.home_videos_count += 1

            target_channel = channel_name or self.active_channel_name
            if target_channel:
                key = self._canon_name(target_channel)
                if is_short:
                    self.channel_shorts[key] = self.channel_shorts.get(key, 0) + 1
                else:
                    self.channel_regular_videos[key] = self.channel_regular_videos.get(key, 0) + 1

    def get_session_report_data(self) -> Dict[str, Any]:
        """Compile a complete session report with per-channel breakdown and combined totals."""
        with self._lock:
            end_t = self.session_end_time or datetime.now()
            duration = max(0.0, (end_t - self.session_start_time).total_seconds())

            # Gather all channels that either had initial seconds, session seconds, or are known
            all_keys = set(self.original_names) | set(self.initial_channel_watch_seconds) | set(self.session_watch_seconds)
            channel_items: List[Dict[str, Any]] = []

            for key in sorted(all_keys):
                name = self.original_names.get(key, key)
                session_sec = float(self.session_watch_seconds.get(key, 0.0))
                initial_sec = float(self.initial_channel_watch_seconds.get(key, 0.0))
                current_total = self.get_current_total_watch_seconds(name)
                # If current_total is 0 or less than initial+session, reconcile
                final_sec = max(current_total, initial_sec + session_sec)

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

                if target_sec > 0.0:
                    pct = min(100.0, (final_sec / target_sec) * 100.0)
                    achieved = final_sec >= target_sec
                    cur_h = final_sec / 3600.0
                    tgt_h = target_sec / 3600.0
                    target_goal_text = f"{cur_h:.1f}h / {tgt_h:.1f}h ({pct:.0f}%)"
                    if achieved:
                        target_goal_text += " [ACHIEVED]"
                else:
                    achieved = False
                    target_goal_text = "No target set"

                reg_count = self.channel_regular_videos.get(key, 0)
                short_count = self.channel_shorts.get(key, 0)

                channel_items.append({
                    "name": name,
                    "canon_name": key,
                    "session_watch_seconds": session_sec,
                    "initial_watch_seconds": initial_sec,
                    "final_watch_seconds": final_sec,
                    "target_watch_seconds": target_sec,
                    "target_goal_text": target_goal_text,
                    "target_achieved": achieved,
                    "regular_videos_count": reg_count,
                    "shorts_count": short_count,
                    "total_videos_count": reg_count + short_count,
                })

            total_session_sec = float(sum(self.session_watch_seconds.values()))
            total_vids = self.total_regular_videos + self.total_shorts

            return {
                "start_time": self.session_start_time,
                "end_time": end_t,
                "duration_seconds": duration,
                "total_session_watch_seconds": total_session_sec,
                "total_videos_watched": total_vids,
                "regular_videos_count": self.total_regular_videos,
                "shorts_count": self.total_shorts,
                "suggested_videos_count": self.suggested_videos_count,
                "search_videos_count": self.search_videos_count,
                "home_videos_count": self.home_videos_count,
                "random_videos_count": self.random_videos_count,
                "search_queries_count": self.search_queries_count,
                "channels": channel_items,
            }

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
            self.session_watch_seconds.clear()
            self.initial_channel_watch_seconds.clear()
            self.session_start_time = datetime.now()
            self.session_end_time = None
            self.total_regular_videos = 0
            self.total_shorts = 0
            self.channel_regular_videos.clear()
            self.channel_shorts.clear()
            for ch in self.channels:
                ch.watch_seconds = 0.0

    def on_stop(self) -> None:
        """Retry pending checkpoints without adding already saved session time."""
        with self._lock:
            if self.session_end_time is None:
                self.session_end_time = datetime.now()
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
