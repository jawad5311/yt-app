"""Channel model: named YouTube channels used to group queued video tasks."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    enabled: bool = True
    archived: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": self.name, "url": self.url}
        if not self.enabled:
            data["enabled"] = False
        if self.archived:
            data["archived"] = True
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "Channel":
        if not isinstance(data, dict):
            return cls()
        enabled_val = data.get("enabled", True)
        if isinstance(enabled_val, str):
            enabled_val = enabled_val.lower() in ("true", "1", "yes")
        archived_val = data.get("archived", False)
        if isinstance(archived_val, str):
            archived_val = archived_val.lower() in ("true", "1", "yes")
        return cls(
            name=str(data.get("name") or "").strip(),
            url=normalize_channel_url(str(data.get("url") or "")),
            enabled=bool(enabled_val),
            archived=bool(archived_val),
        )

    @classmethod
    def parse(
        cls,
        name: str,
        url: str,
        existing: Sequence["Channel"] = (),
        enabled: bool = True,
        archived: bool = False,
    ) -> "Channel":
        """Build a validated channel. Raises ValueError with a user-facing message."""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Channel name is required.")
        for channel in existing:
            if channel.name.lower() == clean_name.lower():
                raise ValueError(f"A channel named '{channel.name}' already exists.")
        return cls(name=clean_name, url=normalize_channel_url(url), enabled=enabled, archived=archived)


def is_video_task_runnable(
    task: VideoTask,
    channels: Sequence[Channel],
    stats_store: Optional[Any] = None,
) -> bool:
    """Determine whether a video task is enabled and runnable for automation.
    A task runs only if:
    1. task.enabled is True
    2. (if assigned to a channel) the corresponding channel is enabled and not archived
    3. (if max_runs > 0 and stats_store is provided) current runs have not reached max_runs.
    """
    if not task.enabled:
        return False
    if stats_store is not None and task.max_runs > 0:
        record = stats_store.find_for_task(task)
        current_runs = record.runs if record is not None else 0
        if current_runs >= task.max_runs:
            return False
    if task.channel:
        for ch in channels:
            if ch.name.lower() == task.channel.lower():
                if ch.archived:
                    return False
                return ch.enabled
    return True


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
    include_archived: bool = False,
) -> List[VideoGroup]:
    """Bucket queued videos by their channel, preserving channel order.

    Every channel is returned (even an empty one) so the UI can show channels
    that do not have videos yet. Videos pointing at a deleted or unknown
    channel fall into a trailing "Uncategorized" group.

    If include_archived is False, archived channels and their videos are excluded
    from the active queue.
    """
    ordered: List[VideoGroup] = []
    by_name: Dict[str, VideoGroup] = {}
    archived_names = {ch.name.lower() for ch in channels if ch.archived}

    for channel in channels:
        if not channel.name or channel.name in by_name:
            continue  # skip blank / duplicate names coming from disk
        if not include_archived and channel.archived:
            continue
        group = VideoGroup(channel=channel, videos=[])
        by_name[channel.name] = group
        ordered.append(group)

    uncategorized = VideoGroup(channel=Channel(), videos=[])
    for index, task in enumerate(videos):
        name = (task.channel or "").strip()
        if not include_archived and name and name.lower() in archived_names:
            # Video belongs to an archived channel: keep stored, omit from active queue
            continue
        matched = by_name.get(name) if name else None
        if matched is None:
            uncategorized.videos.append((index, task))
        else:
            matched.videos.append((index, task))

    if uncategorized.videos:
        ordered.append(uncategorized)

    return ordered
