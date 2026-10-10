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

DEFAULT_WATCHTIME_DURATION = "5 Minutes"


@dataclass
class VideoTask:
    """Represents a YouTube Video task in the execution queue."""
    url: str = ""
    title: str = ""
    keywords: str = ""
    keywords_2: str = ""
    keywords_3: str = ""
    watchtime_duration: str = DEFAULT_WATCHTIME_DURATION
    # Name of the Channel this video belongs to ("" = uncategorized).
    channel: str = ""
    # Whether this video task is enabled for automation.
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "keywords": self.keywords,
            "keywords_2": self.keywords_2,
            "keywords_3": self.keywords_3,
            "watchtime_duration": self.watchtime_duration,
            "channel": self.channel,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoTask":
        raw_kw = str(data.get("keywords", data.get("title", "")) or "")
        kw_2 = str(data.get("keywords_2", "") or "")
        kw_3 = str(data.get("keywords_3", "") or "")
        if "\n" in raw_kw and not kw_2:
            lines = [line.strip() for line in raw_kw.splitlines() if line.strip()]
            raw_kw = lines[0] if lines else ""
            kw_2 = lines[1] if len(lines) > 1 else ""
            kw_3 = lines[2] if len(lines) > 2 else ""

        enabled_val = data.get("enabled", True)
        if isinstance(enabled_val, str):
            enabled_val = enabled_val.lower() in ("true", "1", "yes")

        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            keywords=raw_kw,
            keywords_2=kw_2,
            keywords_3=kw_3,
            watchtime_duration=data.get("watchtime_duration", DEFAULT_WATCHTIME_DURATION),
            channel=data.get("channel") or "",
            enabled=bool(enabled_val),
        )

    def get_keyword_queries(self) -> List[str]:
        """Return non-empty keyword search queries (up to 3 lines) for organic search attempts.
        Falls back to title if no keyword lines are provided.
        """
        candidates = [self.keywords.strip(), self.keywords_2.strip(), self.keywords_3.strip()]
        queries = [c for c in candidates if c]
        if not queries and self.title.strip():
            return [self.title.strip()]
        return queries

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
