"""Channel model: named YouTube channels used to group queued video tasks."""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .video_task import VideoTask

# Sentinel shown by the UI when a video belongs to no channel.
NO_CHANNEL_VALUE = "(None)"
# Group label for videos whose channel is missing or was deleted.
UNCATEGORIZED_LABEL = "Uncategorized"


def normalize_channel_url(url: str) -> str:
    """Trim a channel URL and add a scheme when the user typed a bare address."""
    clean = (url or "").strip()
    if not clean:
        return ""
    if "://" not in clean:
        clean = f"https://{clean}"
    return clean


@dataclass
class Channel:
    """A YouTube channel (name + URL) that queued videos can be attached to."""

    name: str = ""
    url: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "url": self.url}

    @classmethod
    def from_dict(cls, data: Any) -> "Channel":
        if not isinstance(data, dict):
            return cls()
        return cls(
            name=str(data.get("name") or "").strip(),
            url=normalize_channel_url(str(data.get("url") or "")),
        )

    @classmethod
    def parse(cls, name: str, url: str, existing: Sequence["Channel"] = ()) -> "Channel":
        """Build a validated channel. Raises ValueError with a user-facing message."""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Channel name is required.")
        for channel in existing:
            if channel.name.lower() == clean_name.lower():
                raise ValueError(f"A channel named '{channel.name}' already exists.")
        return cls(name=clean_name, url=normalize_channel_url(url))


@dataclass
class VideoGroup:
    """A channel together with the queued videos assigned to it."""

    channel: Channel
    videos: List[Tuple[int, VideoTask]]  # (index in the original queue, task)

    @property
    def name(self) -> str:
        return self.channel.name or UNCATEGORIZED_LABEL

    @property
    def url(self) -> str:
        return self.channel.url

    @property
    def is_uncategorized(self) -> bool:
        return not self.channel.name

    @property
    def count(self) -> int:
        return len(self.videos)


def group_videos_by_channel(
    videos: Sequence[VideoTask],
    channels: Sequence[Channel],
) -> List[VideoGroup]:
    """Bucket queued videos by their channel, preserving channel order.

    Every channel is returned (even an empty one) so the UI can show channels
    that do not have videos yet. Videos pointing at a deleted or unknown
    channel fall into a trailing "Uncategorized" group.
    """
    ordered: List[VideoGroup] = []
    by_name: Dict[str, VideoGroup] = {}

    for channel in channels:
        if not channel.name or channel.name in by_name:
            continue  # skip blank / duplicate names coming from disk
        group = VideoGroup(channel=channel, videos=[])
        by_name[channel.name] = group
        ordered.append(group)

    uncategorized = VideoGroup(channel=Channel(), videos=[])
    for index, task in enumerate(videos):
        name = (task.channel or "").strip()
        matched = by_name.get(name) if name else None
        if matched is None:
            uncategorized.videos.append((index, task))
        else:
            matched.videos.append((index, task))

    if uncategorized.videos:
        ordered.append(uncategorized)

    return ordered
