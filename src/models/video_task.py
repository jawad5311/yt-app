from typing import Any, Dict, List, Optional, Sequence, Tuple
import math

def safe_float(val: Any, default: float = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        if not math.isfinite(f):
            return default
        if min_val is not None and f < min_val:
            return default
        if max_val is not None and f > max_val:
            return default
        return f
    except (TypeError, ValueError):
        return default

def safe_int(val: Any, default: int = 0, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    if val is None:
        return default
    try:
        f = float(val)
        if not math.isfinite(f):
            return default
        i = int(f)
        if min_val is not None and i < min_val:
            return default
        if max_val is not None and i > max_val:
            return default
        return i
    except (TypeError, ValueError):
        return default

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Duration choices offered by the UI (Add/Edit task modal and the Settings default).
WATCHTIME_OPTIONS: List[str] = [
    "Full Video",
    "30 Seconds",
    "1 Minute",
    "2 Minutes",
    "5 Minutes",
    "10 Minutes",
    "15 Minutes",
    "30 Minutes",
    "45 Minutes",
    "1 Hour",
    "2 Hours",
    "3 Hours",
    "6 Hours",
    "12 Hours",
    "24 Hours",
]

DEFAULT_WATCHTIME_DURATION = "Full Video"
DEFAULT_MAX_RUNS: int = 5
DEFAULT_TARGET_MULTIPLIER: float = 3.0


@dataclass
class VideoTask:
    """Represents a YouTube Video task in the execution queue."""
    url: str = ""
    title: str = ""
    title_2: str = ""
    title_3: str = ""
    keywords: str = ""
    keywords_2: str = ""
    keywords_3: str = ""
    watchtime_duration: str = DEFAULT_WATCHTIME_DURATION
    # Name of the Channel this video belongs to ("" = uncategorized).
    channel: str = ""
    # Whether this video task is enabled for automation.
    enabled: bool = True
    # Maximum lifetime automation runs/views allowed for this video (0 = unlimited).
    max_runs: int = 0
    # Target watch multiplier (e.g. 1.0, 1.5, 2.0; 0.0 = unlimited).
    # When cumulative watch time reaches (target_multiplier * duration), viewing stops.
    target_multiplier: float = 0.0
    # Length of the video in seconds (as reported by YouTube).
    duration_seconds: int = 0
    # Formatted length string (e.g., "14:25").
    duration_text: str = ""

    def __post_init__(self):
        # Synchronize titles and keywords so both properties remain interchangeable
        if not self.title and self.keywords:
            self.title = self.keywords
        elif not self.keywords and self.title:
            self.keywords = self.title

        if not self.title_2 and self.keywords_2:
            self.title_2 = self.keywords_2
        elif not self.keywords_2 and self.title_2:
            self.keywords_2 = self.title_2

        if not self.title_3 and self.keywords_3:
            self.title_3 = self.keywords_3
        elif not self.keywords_3 and self.title_3:
            self.keywords_3 = self.title_3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "title_2": self.title_2,
            "title_3": self.title_3,
            "keywords": self.keywords or self.title,
            "keywords_2": self.keywords_2 or self.title_2,
            "keywords_3": self.keywords_3 or self.title_3,
            "watchtime_duration": self.watchtime_duration,
            "channel": self.channel,
            "enabled": self.enabled,
            "max_runs": self.max_runs,
            "target_multiplier": round(self.target_multiplier, 2) if self.target_multiplier > 0 else 0.0,
            "duration_seconds": self.duration_seconds,
            "duration_text": self.duration_text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoTask":
        raw_t1 = str(data.get("title", "") or "")
        raw_t2 = str(data.get("title_2", "") or "")
        raw_t3 = str(data.get("title_3", "") or "")

        raw_kw1 = str(data.get("keywords", "") or "")
        raw_kw2 = str(data.get("keywords_2", "") or "")
        raw_kw3 = str(data.get("keywords_3", "") or "")

        if "\n" in raw_kw1 and not raw_kw2 and not raw_t2:
            lines = [line.strip() for line in raw_kw1.splitlines() if line.strip()]
            raw_kw1 = lines[0] if lines else ""
            raw_kw2 = lines[1] if len(lines) > 1 else ""
            raw_kw3 = lines[2] if len(lines) > 2 else ""
            raw_t1 = raw_kw1
            raw_t2 = raw_kw2
            raw_t3 = raw_kw3

        if not raw_t1 and raw_kw1:
            raw_t1 = raw_kw1
        if not raw_kw1 and raw_t1:
            raw_kw1 = raw_t1

        if not raw_t2 and raw_kw2:
            raw_t2 = raw_kw2
        if not raw_kw2 and raw_t2:
            raw_kw2 = raw_t2

        if not raw_t3 and raw_kw3:
            raw_t3 = raw_kw3
        if not raw_kw3 and raw_t3:
            raw_kw3 = raw_t3

        enabled_val = data.get("enabled", True)
        if isinstance(enabled_val, str):
            enabled_val = enabled_val.lower() in ("true", "1", "yes")

        raw_duration_sec = data.get("duration_seconds", 0)
        duration_seconds_val = safe_int(raw_duration_sec, default=0, min_val=0)

        duration_text_val = str(data.get("duration_text", "") or "")

        raw_max_runs = data.get("max_runs", 0)
        max_runs_val = max(0, safe_int(raw_max_runs, default=0, min_val=0))

        raw_mult = data.get("target_multiplier", 0.0)
        target_multiplier_val = safe_float(raw_mult, default=0.0, min_val=0.0)

        return cls(
            url=data.get("url", ""),
            title=raw_t1,
            title_2=raw_t2,
            title_3=raw_t3,
            keywords=raw_kw1,
            keywords_2=raw_kw2,
            keywords_3=raw_kw3,
            watchtime_duration=data.get("watchtime_duration", DEFAULT_WATCHTIME_DURATION),
            channel=data.get("channel") or "",
            enabled=bool(enabled_val),
            max_runs=max_runs_val,
            target_multiplier=target_multiplier_val,
            duration_seconds=duration_seconds_val,
            duration_text=duration_text_val,
        )

    @property
    def is_short(self) -> bool:
        """Return True if this task represents a YouTube Short."""
        return "/shorts/" in (self.url or "")

    def is_run_limit_reached(self, current_runs: int) -> bool:
        """Return True if positive max_runs limit is reached or exceeded."""
        return self.max_runs > 0 and current_runs >= self.max_runs

    def is_target_reached(
        self,
        current_runs: int = 0,
        watch_seconds: float = 0.0,
        video_duration: float = 0.0,
    ) -> bool:
        """Check if this task has achieved either its target multiplier or maximum views cap."""
        # 1. Check max views / runs cap
        if self.max_runs > 0 and current_runs >= self.max_runs:
            return True
        # 2. Check target multiplier
        eff_dur = float(video_duration if video_duration > 0 else self.duration_seconds)
        if self.target_multiplier > 0.0 and eff_dur > 0.0:
            if watch_seconds >= self.target_multiplier * eff_dur:
                return True
        return False

    def remaining_runs(self, current_runs: int) -> Optional[int]:
        """Return remaining runs until max_runs is reached, or None if unlimited."""
        if self.max_runs <= 0:
            return None
        return max(0, self.max_runs - current_runs)

    def get_title_queries(self) -> List[str]:
        """Return non-empty video title lines (up to 3) for search and matching attempts."""
        candidates = [self.title.strip(), self.title_2.strip(), self.title_3.strip()]
        queries = [c for c in candidates if c]
        if not queries and self.keywords.strip():
            queries = [self.keywords.strip()]
        return queries

    def get_keyword_queries(self) -> List[str]:
        """Alias for get_title_queries for backward compatibility."""
        return self.get_title_queries()

    def is_full_video(self) -> bool:
        """Check if task is set to watch full video until playback finishes."""
        return "full" in self.watchtime_duration.lower()

    def parse_duration_seconds(self) -> Optional[int]:
        """Parse watchtime_duration string into integer seconds, or None if Full Video."""
        val = self.watchtime_duration.lower().strip()
        if "full" in val:
            return None
        if "hour" in val:
            num = val.split("hour")[0].strip()
            try:
                return int(float(num) * 3600)
            except ValueError:
                return 3600
        elif "minute" in val:
            num = val.split("minute")[0].strip()
            try:
                return int(float(num) * 60)
            except ValueError:
                return 300
        elif "second" in val:
            num = val.split("second")[0].strip()
            try:
                return int(float(num))
            except ValueError:
                return 30
        else:
            try:
                return int(float(val))
            except ValueError:
                return 300
