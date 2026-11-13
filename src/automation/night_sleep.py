"""Scheduler and evaluator for Long Sleep / Night Mode based on local system time."""

from datetime import datetime, timedelta
import logging
import random
from typing import Any, Dict, Optional, Set, Tuple

from ..config.manager import ConfigManager, parse_duration_range, parse_sleep_hours, parse_time_of_day


class NightSleepScheduler:
    """Manages natural human-like nighttime sleep scheduling based on local system clock.

    Within a user-configured nighttime window (e.g. 12 hours from 22:00 to 10:00),
    the scheduler randomly selects a sleep window of the configured duration
    (e.g. 7-8 hours) and tracks completion so the bot sleeps once per night.
    """

    def __init__(
        self,
        config_mgr: Optional[ConfigManager] = None,
        session_start_time: Optional[datetime] = None,
    ) -> None:
        self.config_mgr = config_mgr
        self.active_window_id: Optional[str] = None
        self.scheduled_sleep_start: Optional[datetime] = None
        self.scheduled_sleep_end: Optional[datetime] = None
        self.scheduled_duration_hours: float = 8.0
        self.completed_window_ids: Set[str] = set()
        self.bypassed_window_ids: Set[str] = set()
        self.session_start_time: Optional[datetime] = session_start_time

    def is_enabled(self) -> bool:
        """Return True if Long Sleep / Night Mode is turned on in settings."""
        if not self.config_mgr:
            return False
        return bool(self.config_mgr.get_bool("NightSleepEnabled", False))

    def get_current_or_upcoming_window(self, now: Optional[datetime] = None) -> Tuple[datetime, datetime, bool, str]:
        """Calculate the active or upcoming night window based on system clock.

        Returns (window_start, window_end, is_currently_inside, window_id).
        """
        current_time = now if now is not None else datetime.now()
        raw_settings = self.config_mgr.get_night_sleep_settings() if self.config_mgr else {}
        settings = raw_settings if isinstance(raw_settings, dict) else {}

        try:
            sh = int(settings.get("start_hour", 22))
        except (TypeError, ValueError):
            sh = 22
        try:
            sm = int(settings.get("start_minute", 0))
        except (TypeError, ValueError):
            sm = 0
        try:
            wh = float(settings.get("window_hours", 12.0))
        except (TypeError, ValueError):
            wh = 12.0

        # Candidate 1: Window started yesterday
        w1_start = datetime(current_time.year, current_time.month, current_time.day, sh, sm) - timedelta(days=1)
        w1_end = w1_start + timedelta(hours=wh)
        if w1_start <= current_time < w1_end:
            win_id = w1_start.strftime("%Y-%m-%d_%H%M")
            return w1_start, w1_end, True, win_id

        # Candidate 2: Window started today
        w2_start = datetime(current_time.year, current_time.month, current_time.day, sh, sm)
        w2_end = w2_start + timedelta(hours=wh)
        if w2_start <= current_time < w2_end:
            win_id = w2_start.strftime("%Y-%m-%d_%H%M")
            return w2_start, w2_end, True, win_id

        # Candidate 3: Outside window (upcoming window in future)
        upcoming_start = w2_start if current_time < w2_start else w2_start + timedelta(days=1)
        upcoming_end = upcoming_start + timedelta(hours=wh)
        win_id = upcoming_start.strftime("%Y-%m-%d_%H%M")
        return upcoming_start, upcoming_end, False, win_id

    def schedule_for_window(
        self,
        w_start: datetime,
        w_end: datetime,
        window_id: str,
        now: Optional[datetime] = None,
        forced_duration: Optional[float] = None,
        forced_start_offset: Optional[float] = None,
    ) -> Tuple[datetime, datetime, float]:
        """Randomly choose a sleep window of configured duration fitting within [w_start, w_end]."""
        current_time = now if now is not None else datetime.now()
        raw_settings = self.config_mgr.get_night_sleep_settings() if self.config_mgr else {}
        settings = raw_settings if isinstance(raw_settings, dict) else {}

        try:
            d_min = float(settings.get("duration_min", 8.0))
        except (TypeError, ValueError):
            d_min = 8.0
        try:
            d_max = float(settings.get("duration_max", 8.0))
        except (TypeError, ValueError):
            d_max = 8.0
        total_window_hours = (w_end - w_start).total_seconds() / 3600.0

        if forced_duration is not None:
            dur = forced_duration
        else:
            dur = random.uniform(d_min, d_max) if d_max > d_min else d_min

        # Clamp duration to total window hours
        dur = min(dur, total_window_hours)
        dur = max(0.5, dur)

        if current_time <= w_start:
            # We are scheduling ahead of time: full window available
            slack_hours = max(0.0, total_window_hours - dur)
            offset = forced_start_offset if forced_start_offset is not None else random.uniform(0.0, slack_hours)
            sleep_start = w_start + timedelta(hours=offset)
            sleep_end = sleep_start + timedelta(hours=dur)
        else:
            # We are already inside the window (e.g. app launched at night)
            rem_window_hours = max(0.0, (w_end - current_time).total_seconds() / 3600.0)
            if rem_window_hours >= dur:
                slack_from_now = rem_window_hours - dur
                offset = forced_start_offset if forced_start_offset is not None else random.uniform(0.0, slack_from_now)
                sleep_start = current_time + timedelta(hours=offset)
                sleep_end = sleep_start + timedelta(hours=dur)
            else:
                # Less time remains in the window than full sleep duration
                sleep_start = current_time
                actual_dur = max(0.5, min(dur, rem_window_hours))
                dur = actual_dur
                sleep_end = current_time + timedelta(hours=dur)

        self.active_window_id = window_id
        self.scheduled_sleep_start = sleep_start
        self.scheduled_sleep_end = sleep_end
        self.scheduled_duration_hours = dur
        return sleep_start, sleep_end, dur

    def evaluate(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Evaluate current system time against night sleep configuration.

        Returns status dictionary with keys:
            - enabled: bool
            - inside_night_window: bool
            - is_sleep_time: bool
            - window_id: str
            - window_start: datetime
            - window_end: datetime
            - scheduled_sleep_start: datetime
            - scheduled_sleep_end: datetime
            - sleep_duration_hours: float
            - remaining_sleep_seconds: float
        """
        if not self.is_enabled():
            return {
                "enabled": False,
                "inside_night_window": False,
                "is_sleep_time": False,
            }

        current_time = now if now is not None else datetime.now()
        w_start, w_end, inside_window, win_id = self.get_current_or_upcoming_window(current_time)

        if not inside_window:
            return {
                "enabled": True,
                "inside_night_window": False,
                "is_sleep_time": False,
                "window_id": win_id,
                "window_start": w_start,
                "window_end": w_end,
            }

        # Check if this night's sleep was already completed
        if win_id in self.completed_window_ids:
            return {
                "enabled": True,
                "inside_night_window": True,
                "is_sleep_time": False,
                "completed_for_this_window": True,
                "window_id": win_id,
                "window_start": w_start,
                "window_end": w_end,
            }

        # Check if this night window is bypassed because session was started during nighttime
        if self.is_window_bypassed(win_id, w_start=w_start, w_end=w_end):
            return {
                "enabled": True,
                "inside_night_window": True,
                "is_sleep_time": False,
                "bypassed_for_this_window": True,
                "window_id": win_id,
                "window_start": w_start,
                "window_end": w_end,
            }

        # If not scheduled yet for this window, compute random schedule
        if self.active_window_id != win_id or not self.scheduled_sleep_start or not self.scheduled_sleep_end:
            self.schedule_for_window(w_start, w_end, win_id, now=current_time)

        # Check if scheduled period has ended
        if current_time >= self.scheduled_sleep_end:
            self.completed_window_ids.add(win_id)
            return {
                "enabled": True,
                "inside_night_window": True,
                "is_sleep_time": False,
                "completed_for_this_window": True,
                "window_id": win_id,
                "window_start": w_start,
                "window_end": w_end,
            }

        # Check if sleep time has arrived
        if current_time >= self.scheduled_sleep_start:
            rem_secs = max(0.0, (self.scheduled_sleep_end - current_time).total_seconds())
            return {
                "enabled": True,
                "inside_night_window": True,
                "is_sleep_time": True,
                "window_id": win_id,
                "window_start": w_start,
                "window_end": w_end,
                "scheduled_sleep_start": self.scheduled_sleep_start,
                "scheduled_sleep_end": self.scheduled_sleep_end,
                "sleep_duration_hours": self.scheduled_duration_hours,
                "remaining_sleep_seconds": rem_secs,
            }

        # Inside night window, but waiting for randomly chosen sleep start time
        return {
            "enabled": True,
            "inside_night_window": True,
            "is_sleep_time": False,
            "window_id": win_id,
            "window_start": w_start,
            "window_end": w_end,
            "scheduled_sleep_start": self.scheduled_sleep_start,
            "scheduled_sleep_end": self.scheduled_sleep_end,
            "sleep_duration_hours": self.scheduled_duration_hours,
        }

    def is_window_bypassed(
        self,
        window_id: str,
        w_start: Optional[datetime] = None,
        w_end: Optional[datetime] = None,
    ) -> bool:
        """Return True if the specified night window is bypassed (e.g. session started during it)."""
        if window_id in self.bypassed_window_ids:
            return True
        if self.session_start_time is not None:
            if w_start is None or w_end is None:
                calc_start, calc_end, inside, calc_id = self.get_current_or_upcoming_window(self.session_start_time)
                if inside and calc_id == window_id:
                    self.bypassed_window_ids.add(window_id)
                    return True
            elif w_start <= self.session_start_time < w_end:
                self.bypassed_window_ids.add(window_id)
                return True
        return False

    def mark_bypassed(self, window_id: str) -> None:
        """Mark the specified night window as bypassed so long sleep is skipped for it."""
        self.bypassed_window_ids.add(window_id)

    def set_session_start_time(self, start_time: Optional[datetime]) -> None:
        """Set or update the session start time used to detect if started during nighttime."""
        self.session_start_time = start_time

    def mark_completed(self, window_id: str) -> None:
        """Mark the specified night window as completed so it does not sleep again tonight."""
        self.completed_window_ids.add(window_id)
