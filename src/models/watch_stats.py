"""Watch statistics: pure models for tracking automation runs and watch time.

This module holds no I/O: persistence lives in ``src.config.stats_store``.
Everything here is plain data + formatting so it can be unit tested without
a browser, a settings file or Tk.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

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
    """

    completed: bool = False
    interrupted: bool = False
    watched_seconds: float = 0.0
    ad_seconds: float = 0.0
    reason: str = ""
    video_duration: float = 0.0
    final_current_time: float = 0.0
    playback_finished: bool = False

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


@dataclass
class StatRecord:
    """Aggregated lifetime counters for one video."""

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
            video_duration=_float("video_duration"),
            task_keys=_str_list("task_keys"),
        )
        return rec

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

        if outcome.playback_finished:
            self.runs += 1
        elif self.video_duration == 0:
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


def format_watch_line(
    record: Optional[StatRecord],
    max_runs: int = 0,
    use_hours: bool = False,
    task_duration: float = 0.0,
) -> str:
    """Queue-card line: ``▶ 12 full runs · 4.4 hours watched (1.5x) · last ✅`` or with limit progress."""
    current_runs = record.runs if record is not None else 0
    if max_runs > 0:
        if current_runs >= max_runs:
            runs_str = f"{current_runs}/{max_runs} full runs (Limit reached)"
        else:
            remaining = max_runs - current_runs
            rem_str = "1 remaining" if remaining == 1 else f"{remaining} remaining"
            runs_str = f"{current_runs}/{max_runs} full runs ({rem_str})"
    else:
        if current_runs == 0:
            runs_str = ""
        elif current_runs == 1:
            runs_str = "1 full run"
        else:
            runs_str = f"{current_runs} full runs"

    eff_duration = task_duration
    if eff_duration <= 0 and record is not None and record.video_duration > 0:
        eff_duration = record.video_duration

    mult = format_watch_multiplier(record.watch_seconds if record else 0.0, eff_duration) if eff_duration > 0 else ""

    if record is None or current_runs == 0:
        if max_runs > 0:
            return f"▶ {runs_str} ({mult})" if mult else f"▶ {runs_str}"
        return f"▶ Not run yet ({mult})" if mult else "▶ Not run yet"

    time_str = format_hours(record.watch_seconds) if use_hours else format_duration(record.watch_seconds)
    mult_suffix = f" ({mult})" if mult else ""
    line = f"▶ {runs_str} · {time_str} watched{mult_suffix}"
    if record is not None and record.last_result:
        icon = RESULT_ICONS.get(record.last_result)
        if icon:
            line += f" · last {icon}"
    return line


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
    "format_watch_multiplier",
    "group_records_by_channel",
    "resolve_video_id",
    "resolve_video_key",
    "totals_for",
]
