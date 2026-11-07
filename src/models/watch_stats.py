"""Watch statistics: pure models for tracking automation runs and watch time.

This module holds no I/O: persistence lives in ``src.config.stats_store``.
Everything here is plain data + formatting so it can be unit tested without
a browser, a settings file or Tk.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from ..utils.helpers import extract_video_id
from .video_task import VideoTask

RESULT_SUCCESS = "success"
RESULT_FAILED = "failed"
RESULT_INTERRUPTED = "interrupted"

RESULT_ICONS: Dict[str, str] = {
    RESULT_SUCCESS: "✅",
    RESULT_FAILED: "❌",
    RESULT_INTERRUPTED: "⏸",
}


@dataclass
class WatchResult:
    """Outcome of a single ``watch_video`` call.

    ``__bool__`` maps to ``completed`` so legacy ``if watch_video(...)`` call
    sites keep behaving exactly like the old ``bool`` return value.

    ``completed`` means the task's budget or cutoff was met. Only an actual
    player end sets ``playback_finished``; partial tasks are not full runs.
    """

    completed: bool = False
    interrupted: bool = False
    watched_seconds: float = 0.0
    ad_seconds: float = 0.0
    reason: str = ""
    video_duration: float = 0.0
    final_current_time: float = 0.0
    playback_finished: bool = False
    views_counted: int = 0

    def __post_init__(self) -> None:
        if self.reason == "ended":
            self.playback_finished = True

    def __bool__(self) -> bool:
        return self.completed


@dataclass
class TaskOutcome:
    """What one queued task produced, recorded once per task processing.

    Retries fold into a single outcome: ``attempts`` counts the tries while
    ``watched_seconds`` accumulates the watch time actually delivered across
    them. ``video_key`` is the canonical identity (resolved video ID when one
    is known) and ``task_key`` the task-derived one used to look the record up
    from a queue card.
    """

    success: bool = False
    interrupted: bool = False
    attempts: int = 0
    watched_seconds: float = 0.0
    ad_seconds: float = 0.0
    video_key: str = ""
    task_key: str = ""
    label: str = ""
    channel: str = ""
    video_duration: float = 0.0
    playback_finished: bool = False
    runs: Optional[int] = None

    @property
    def result(self) -> str:
        if self.interrupted:
            return RESULT_INTERRUPTED
        return RESULT_SUCCESS if self.success else RESULT_FAILED


def calculate_length_equivalent_runs(watch_seconds: float, duration_seconds: float) -> int:
    """Calculate run count based on cumulative watch time relative to video length:
    - 0 watch time: 0 runs
    - 0 < watch time < 2 * duration: 1 run
    - watch time >= 2 * duration: int(watch_seconds // duration_seconds)
    """
    if watch_seconds <= 0:
        return 0
    if duration_seconds <= 0:
        return 1
    if watch_seconds < 2.0 * duration_seconds:
        return 1
    return int(watch_seconds // duration_seconds)


from datetime import datetime


def is_within_cooldown(
    last_timestamp: Optional[str],
    cooldown_hours: float,
    current_time: Optional[datetime] = None,
) -> Tuple[bool, float]:
    """Check if a timestamp is within the cooldown window.

    Args:
        last_timestamp: ISO format string of when the video was last watched/completed.
        cooldown_hours: Cooldown duration in hours (0 = disabled).
        current_time: Optional reference datetime for deterministic testing.

    Returns:
        (is_in_cooldown, remaining_hours)
    """
    if not last_timestamp or cooldown_hours <= 0:
        return False, 0.0

    try:
        last_dt = datetime.fromisoformat(last_timestamp)
        now_dt = current_time or datetime.now()
        if last_dt.tzinfo is not None and now_dt.tzinfo is None:
            last_dt = last_dt.astimezone().replace(tzinfo=None)
        elif last_dt.tzinfo is None and now_dt.tzinfo is not None:
            now_dt = now_dt.astimezone().replace(tzinfo=None)

        elapsed_seconds = (now_dt - last_dt).total_seconds()
        cooldown_seconds = cooldown_hours * 3600.0

        if -300.0 <= elapsed_seconds < cooldown_seconds:
            remaining_seconds = max(0.0, cooldown_seconds - max(0.0, elapsed_seconds))
            remaining_hours = remaining_seconds / 3600.0
            return True, remaining_hours
    except Exception:
        pass

    return False, 0.0


@dataclass
class StatRecord:
    """Lifetime counters; runs count successful player-end events, not attempts."""

    label: str = ""
    channel: str = ""
    runs: int = 0
    successes: int = 0
    failures: int = 0
    attempts: int = 0
    watch_seconds: float = 0.0
    ad_seconds: float = 0.0
    last_run: str = ""
    last_result: str = ""
    last_completed: str = ""
    # Video length/duration in seconds (detected from HTML5 player)
    video_duration: float = 0.0
    # Task-derived keys that resolve to this video (queue-card lookup alias).
    task_keys: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: object) -> "StatRecord":
        """Build a record from persisted data, ignoring junk values."""
        if not isinstance(data, dict):
            return cls()
        raw = data

        def _int(key: str) -> int:
            value = raw.get(key, 0)
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        def _float(key: str) -> float:
            value = raw.get(key, 0.0)
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return 0.0

        def _str(key: str) -> str:
            value = raw.get(key, "")
            return value if isinstance(value, str) else ""

        def _str_list(key: str) -> List[str]:
            value = raw.get(key, [])
            if not isinstance(value, list):
                return []
            return [item for item in value if isinstance(item, str) and item]

        rec = cls(
            label=_str("label"),
            channel=_str("channel"),
            runs=_int("runs"),
            successes=_int("successes"),
            failures=_int("failures"),
            attempts=_int("attempts"),
            watch_seconds=_float("watch_seconds"),
            ad_seconds=_float("ad_seconds"),
            last_run=_str("last_run"),
            last_result=_str("last_result"),
            last_completed=_str("last_completed"),
            video_duration=_float("video_duration"),
            task_keys=_str_list("task_keys"),
        )
        return rec

    def is_on_cooldown(self, cooldown_hours: float, current_time: Optional[datetime] = None) -> Tuple[bool, float]:
        """Check if this video record is currently within cooldown period."""
        timestamp = self.last_completed or self.last_run
        return is_within_cooldown(timestamp, cooldown_hours, current_time)

    def to_dict(self) -> Dict[str, object]:
        return {
            "label": self.label,
            "channel": self.channel,
            "runs": self.runs,
            "successes": self.successes,
            "failures": self.failures,
            "attempts": self.attempts,
            "watch_seconds": round(self.watch_seconds, 3),
            "ad_seconds": round(self.ad_seconds, 3),
            "last_run": self.last_run,
            "last_result": self.last_result,
            "last_completed": self.last_completed,
            "video_duration": round(self.video_duration, 3),
            "task_keys": list(self.task_keys),
        }

    def merge(self, outcome: TaskOutcome, timestamp: str) -> None:
        """Fold one task outcome into this record."""
        self.attempts += max(1, outcome.attempts)
        self.watch_seconds += max(0.0, outcome.watched_seconds)
        self.ad_seconds += max(0.0, outcome.ad_seconds)
        if outcome.video_duration > 0:
            self.video_duration = outcome.video_duration

        if outcome.runs is not None:
            self.runs += max(0, outcome.runs)
        elif outcome.playback_finished and outcome.success and not outcome.interrupted:
            self.runs += 1

        if outcome.success:
            self.successes += 1
        elif not outcome.interrupted:
            self.failures += 1
        if outcome.label:
            self.label = outcome.label
        if outcome.task_key and outcome.task_key not in self.task_keys:
            self.task_keys.append(outcome.task_key)
        # Snapshot: the channel as it is at record time, kept even if the
        # video is later reassigned or deleted from the queue.
        self.channel = outcome.channel
        self.last_run = timestamp
        if outcome.playback_finished or outcome.success or outcome.watched_seconds > 0:
            self.last_completed = timestamp
        self.last_result = outcome.result


def fold_records(primary: StatRecord, secondary: Optional[StatRecord]) -> StatRecord:
    """Merge ``secondary``'s counters into ``primary`` (mutates ``primary``).

    Used when a task first records under its task-derived key and later
    records under the resolved video ID: the two records become one.
    """
    if secondary is None or secondary is primary:
        return primary
    primary.successes += secondary.successes
    primary.failures += secondary.failures
    primary.attempts += secondary.attempts
    primary.watch_seconds += secondary.watch_seconds
    primary.ad_seconds += secondary.ad_seconds
    if secondary.video_duration > 0:
        primary.video_duration = secondary.video_duration
    primary.runs += secondary.runs
    if secondary.label and not primary.label:
        primary.label = secondary.label
    if secondary.last_run > primary.last_run:
        primary.last_run = secondary.last_run
        primary.last_result = secondary.last_result
    for alias in secondary.task_keys:
        if alias not in primary.task_keys:
            primary.task_keys.append(alias)
    return primary


@dataclass
class Totals:
    """Summed counters across a set of records."""

    runs: int = 0
    successes: int = 0
    failures: int = 0
    watch_seconds: float = 0.0
    ad_seconds: float = 0.0
    videos: int = 0


def resolve_video_id(task: VideoTask, resolved_url: str = "") -> Optional[str]:
    """Best-effort video ID: resolved page URL first, then the task URL.

    ``resolved_url`` (``driver.current_url`` after navigation) is what makes
    keyword-only tasks share one identity across runs.
    """
    for candidate in (task.url, resolved_url):
        video_id = extract_video_id(candidate)
        if video_id:
            return video_id
    return None


def resolve_video_key(task: VideoTask, resolved_url: str = "") -> str:
    """Stable key identifying a queued video across runs and task edits."""
    video_id = resolve_video_id(task, resolved_url)
    if video_id:
        return video_id
    url = (task.url or "").strip()
    if url:
        return f"url:{url}"
    label = (task.title or task.keywords or "").strip()
    return f"task:{label}" if label else "task:unnamed"


def totals_for(records: Iterable[StatRecord]) -> Totals:
    """Sum a collection of records (e.g. everything under one channel)."""
    totals = Totals()
    for record in records:
        totals.videos += 1
        totals.runs += record.runs
        totals.successes += record.successes
        totals.failures += record.failures
        totals.watch_seconds += record.watch_seconds
        totals.ad_seconds += record.ad_seconds
    return totals


def format_hours(seconds: float) -> str:
    """Format duration in hours with 1 digit floating point: e.g. '0.1 hours', '1.5 hours'."""
    hours = max(0.0, float(seconds)) / 3600.0
    return f"{hours:.1f} hours"


def format_duration(seconds: float) -> str:
    """Human duration: ``58s`` · ``1m 05s`` · ``4h 32m``."""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def format_watch_multiplier(watch_seconds: float, duration_seconds: float) -> str:
    """Format watch time generated relative to video length into 1x, 2x, 1.5x format.

    Examples:
    - 1 hour video watched for 1 hour -> '1x'
    - 1 hour video watched for 2 hours -> '2x'
    - 1 hour video watched for 1h 30m -> '1.5x'
    - 1 hour video watched for 45m -> '0.8x'
    - 0s watch time -> '0x'
    - unknown duration (<= 0) -> ''
    """
    if duration_seconds <= 0:
        return ""
    if watch_seconds <= 0:
        return "0x"
    ratio = round(watch_seconds / duration_seconds, 1)
    if ratio.is_integer():
        return f"{int(ratio)}x"
    return f"{ratio:.1f}x"


def format_video_watch_ratio(watch_seconds: float, target_seconds: float) -> str:
    """Format achieved watch time / target watch time ratio (e.g. '7 / 24 min (29%)' or '1.2 / 3.0 hours (40%)')."""
    w_sec = max(0.0, float(watch_seconds or 0.0))
    t_sec = max(0.0, float(target_seconds or 0.0))

    if t_sec <= 0.0:
        if w_sec >= 3600.0:
            return f"{w_sec / 3600.0:.1f} hours"
        mins = int(round(w_sec / 60.0))
        return f"{mins} min" if mins > 0 else f"{int(w_sec)}s"

    percent = int(round((w_sec / t_sec) * 100))
    if t_sec >= 3600.0 or w_sec >= 3600.0:
        w_hrs = w_sec / 3600.0
        t_hrs = t_sec / 3600.0
        return f"{w_hrs:.1f} / {t_hrs:.1f} hours ({percent}%)"
    elif t_sec >= 60.0:
        w_min = int(round(w_sec / 60.0))
        t_min = max(1, int(round(t_sec / 60.0)))
        return f"{w_min} / {t_min} min ({percent}%)"
    else:
        w_s = int(round(w_sec))
        t_s = max(1, int(round(t_sec)))
        return f"{w_s} / {t_s}s ({percent}%)"


def format_watch_time(watch_seconds: float, duration_seconds: float = 0.0) -> str:
    """Format watch time:
    - If total video length > 60 min (> 3600s) OR watch time >= 60 min (>= 3600s): format in hours ('X.X hours watched').
    - If watch time < 60 min (< 3600s) and video length <= 60 min: format in minutes ('XX min watched').
    """
    if duration_seconds > 3600.0 or watch_seconds >= 3600.0:
        return f"{format_hours(watch_seconds)} watched"
    mins = max(1, int(round(watch_seconds / 60.0))) if watch_seconds > 0 else 0
    return f"{mins} min watched"


def format_watch_line(
    record: Optional[StatRecord],
    max_runs: int = 0,
    use_hours: bool = False,
    task_duration: float = 0.0,
    show_multiplier: bool = True,
    unit: str = "runs",
    last_played: Optional[str] = None,
) -> str:
    """Queue-card line: ``x/x runs -- xx watched (multiplier)``."""
    current_runs = record.runs if record is not None else 0
    unit_singular = unit.rstrip("s") if unit.endswith("s") else unit
    if max_runs > 0:
        runs_str = f"{current_runs}/{max_runs} {unit}"
    else:
        runs_str = f"1 {unit_singular}" if current_runs == 1 else f"{current_runs} {unit}"

    eff_duration = task_duration
    if eff_duration <= 0 and record is not None and record.video_duration > 0:
        eff_duration = record.video_duration

    mult = format_watch_multiplier(record.watch_seconds if record else 0.0, eff_duration) if (show_multiplier and eff_duration > 0) else ""

    watch_sec = record.watch_seconds if record else 0.0
    if use_hours:
        time_str = format_watch_time(watch_sec, eff_duration)
    else:
        time_str = f"{format_duration(watch_sec)} watched"

    if last_played:
        time_str = f"{time_str} · Last Played: {last_played}"

    mult_suffix = f" ({mult})" if mult else ""
    return f"{runs_str} -- {time_str}{mult_suffix}"


def format_totals_line(totals: Totals) -> str:
    """Compact totals: ``27 full runs · 12h 04m watched``."""
    runs = f"{totals.runs} full run" if totals.runs == 1 else f"{totals.runs} full runs"
    return f"{runs} · {format_duration(totals.watch_seconds)} watched"


def group_records_by_channel(records: Dict[str, StatRecord]) -> Dict[str, List[StatRecord]]:
    """Group records by their snapshotted channel name.

    Handy for rendering a per-channel breakdown in one pass.
    """
    groups: Dict[str, List[StatRecord]] = {}
    for record in records.values():
        groups.setdefault(record.channel, []).append(record)
    return groups


__all__ = [
    "RESULT_FAILED",
    "RESULT_ICONS",
    "RESULT_INTERRUPTED",
    "RESULT_SUCCESS",
    "StatRecord",
    "TaskOutcome",
    "Totals",
    "WatchResult",
    "calculate_length_equivalent_runs",
    "fold_records",
    "format_duration",
    "format_hours",
    "format_totals_line",
    "format_watch_line",
    "format_watch_time",
    "format_watch_multiplier",
    "format_video_watch_ratio",
    "group_records_by_channel",
    "resolve_video_id",
    "resolve_video_key",
    "totals_for",
]
