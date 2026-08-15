from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class VideoTask:
    """Represents a YouTube Video task in the execution queue."""
    url: str = ""
    title: str = ""
    keywords: str = ""
    watchtime_duration: str = "5 Minutes"
    comment_type: str = "AI"  # 'AI', 'Custom', or 'None'
    custom_comment: str = ""
    like: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "keywords": self.keywords,
            "watchtime_duration": self.watchtime_duration,
            "comment_type": self.comment_type,
            "custom_comment": self.custom_comment,
            "like": self.like,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoTask":
        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            keywords=data.get("keywords", data.get("title", "")),
            watchtime_duration=data.get("watchtime_duration", "5 Minutes"),
            comment_type=data.get("comment_type", "AI"),
            custom_comment=data.get("custom_comment", ""),
            like=bool(data.get("like", True)),
        )

    def parse_duration_seconds(self) -> int:
        """Parse watchtime_duration string e.g. '24 Hours', '1 Hour', '15 Minutes', '30 Seconds' into integer seconds."""
        val = self.watchtime_duration.lower().strip()
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
