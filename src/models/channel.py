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

from datetime import datetime
import re
"""Channel model: named YouTube channels used to group queued video tasks."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .video_task import VideoTask

# Sentinel shown by the UI when a video belongs to no channel.
NO_CHANNEL_VALUE = "(None)"
# Group label for videos whose channel is missing or was deleted.
UNCATEGORIZED_LABEL = "Uncategorized"


def clean_channel_url(url: str) -> str:
    """Clean a user-entered channel URL/handle to the base handle or channel format (e.g. https://www.youtube.com/@channelhandle)."""
    clean = (url or "").strip()
    if not clean:
        return ""

    # Check for handle format (@handle)
    match = re.search(r"(@[a-zA-Z0-9_\-\.]+)", clean)
    if match:
        return f"https://www.youtube.com/{match.group(1)}"

    # Strip subpaths like /videos, /shorts, /featured, /streams, /community
    for suffix in ("/videos", "/shorts", "/featured", "/streams", "/community"):
        if clean.lower().endswith(suffix):
            clean = clean[:-len(suffix)]
            break

    clean = clean.rstrip("/")
    if clean.startswith("@"):
        return f"https://www.youtube.com/{clean}"
    if clean and "://" not in clean:
        clean = f"https://{clean}"
    return clean

def normalize_channel_url(url: str) -> str:
    """Trim a channel URL and add a scheme when the user typed a bare address."""
    clean = (url or "").strip()
    if not clean:
        return ""
    if "://" not in clean:
        clean = f"https://{clean}"
    return clean


def canonical_channel_url(url: str) -> str:
    """Normalize a channel URL or handle into a canonical key for duplicate checking."""
    clean = (url or "").strip().lower()
    if not clean:
        return ""
    if "://" in clean:
        clean = clean.split("://", 1)[1]
    if clean.startswith("www."):
        clean = clean[4:]
    if clean.startswith("youtube.com/"):
        clean = clean[12:]
    elif clean.startswith("m.youtube.com/"):
        clean = clean[14:]
    clean = clean.rstrip("/")
    for suffix in ("/videos", "/shorts", "/featured", "/streams", "/community"):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
    return clean.rstrip("/")


def parse_channel_identity(url_or_handle: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse a channel URL or handle into an explicit identity form: (type, value).

    Supported identity forms:
    - ('handle', '@canonicalhandle'): YouTube handle, case-folded.
    - ('id', 'UC...'): YouTube channel ID, preserving original identifier case.
    - ('custom', 'canonicalname'): /c/customname, case-folded.
    - ('user', 'canonicalname'): /user/username, case-folded.

    Returns (None, None) if no usable identity is detected.
    """
    raw = (url_or_handle or "").strip()
    if not raw:
        return None, None

    # Strip query parameters and fragments
    raw = raw.split("?")[0].split("#")[0].strip()

    # 1. Handle match (@handle)
    handle_match = re.search(r"@([a-zA-Z0-9_\-\.]+)", raw)
    if handle_match:
        handle_val = handle_match.group(1).rstrip("/").split("/")[0]
        if handle_val:
            return "handle", f"@{handle_val.casefold()}"

    # Strip scheme and hostname
    path = raw
    if "://" in path:
        path = path.split("://", 1)[1]
    if path.startswith("www."):
        path = path[4:]
    if path.startswith("m.youtube.com/"):
        path = path[14:]
    elif path.startswith("youtube.com/"):
        path = path[12:]

    path = "/" + path.lstrip("/")

    # 2. Channel ID: /channel/UC... (preserve identifier case)
    channel_id_match = re.search(r"^/channel/([a-zA-Z0-9_\-]+)", path)
    if channel_id_match:
        cid = channel_id_match.group(1).split("/")[0]
        if cid:
            return "id", cid

    # 3. Custom: /c/name
    custom_match = re.search(r"^/c/([a-zA-Z0-9_\-\.]+)", path)
    if custom_match:
        cname = custom_match.group(1).split("/")[0]
        if cname:
            return "custom", cname.casefold()

    # 4. User: /user/name
    user_match = re.search(r"^/user/([a-zA-Z0-9_\-\.]+)", path)
    if user_match:
        uname = user_match.group(1).split("/")[0]
        if uname:
            return "user", uname.casefold()

    return None, None


@dataclass
class Channel:
    """A YouTube channel (name + URL) that queued videos can be attached to."""

    name: str = ""
    url: str = ""
    enabled: bool = True
    archived: bool = False
    watch_seconds: float = 0.0
    target_watch_seconds: float = 0.0
    total_videos: Optional[int] = None
    total_shorts: Optional[int] = None
    has_new_videos: bool = False

    @property
    def target_watch_hours(self) -> float:
        """Configured target watch time in hours."""
        return round(self.target_watch_seconds / 3600.0, 1)

    @property
    def target_achieved(self) -> bool:
        """Whether this channel has achieved its configured target watch time."""
        if self.target_watch_seconds <= 0.0:
            return False
        return self.watch_seconds >= self.target_watch_seconds

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": self.name, "url": self.url}
        if not self.enabled:
            data["enabled"] = False
        if self.archived:
            data["archived"] = True
        if self.watch_seconds > 0.0:
            data["watch_seconds"] = round(self.watch_seconds, 3)
        if self.target_watch_seconds > 0.0:
            data["target_watch_seconds"] = round(self.target_watch_seconds, 3)
        if self.total_videos is not None:
            data["total_videos"] = self.total_videos
        if self.total_shorts is not None:
            data["total_shorts"] = self.total_shorts
        if self.has_new_videos:
            data["has_new_videos"] = True
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
        watch_sec_val = safe_float(data.get("watch_seconds"), default=0.0, min_val=0.0)
        target_watch_sec_val = safe_float(data.get("target_watch_seconds"), default=0.0, min_val=0.0)
        raw_tv = data.get("total_videos")
        total_videos = safe_int(raw_tv, default=-1) if raw_tv is not None else -1
        raw_ts = data.get("total_shorts")
        total_shorts = safe_int(raw_ts, default=-1) if raw_ts is not None else -1
        raw_hnv = data.get("has_new_videos", False)
        if isinstance(raw_hnv, str):
            raw_hnv = raw_hnv.lower() in ("true", "1", "yes")
        return cls(
            name=str(data.get("name") or "").strip(),
            url=normalize_channel_url(str(data.get("url") or "")),
            enabled=bool(enabled_val),
            archived=bool(archived_val),
            watch_seconds=watch_sec_val,
            target_watch_seconds=target_watch_sec_val,
            total_videos=total_videos if total_videos >= 0 else None,
            total_shorts=total_shorts if total_shorts >= 0 else None,
            has_new_videos=bool(raw_hnv),
        )

    @classmethod
    def parse(
        cls,
        name: str,
        url: str,
        existing: Sequence["Channel"] = (),
        enabled: bool = True,
        archived: bool = False,
        watch_seconds: float = 0.0,
        target_watch_seconds: float = 0.0,
        total_videos: Optional[int] = None,
        total_shorts: Optional[int] = None,
        has_new_videos: bool = False,
    ) -> "Channel":
        """Build a validated channel. Raises ValueError with a user-facing message."""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Channel name is required.")
        for channel in existing:
            if channel.name.lower() == clean_name.lower():
                raise ValueError(f"A channel named '{channel.name}' already exists.")

        clean_url = clean_channel_url(url)
        canon_url = canonical_channel_url(clean_url)
        if canon_url:
            for channel in existing:
                if canonical_channel_url(channel.url) == canon_url:
                    raise ValueError(
                        f"A channel with this URL already exists ('{channel.name}')."
                    )

        return cls(
            name=clean_name,
            url=clean_url,
            enabled=enabled,
            archived=archived,
            watch_seconds=watch_seconds,
            target_watch_seconds=target_watch_seconds,
            total_videos=total_videos,
            total_shorts=total_shorts,
            has_new_videos=has_new_videos,
        )


def check_video_task_eligibility(
    task: VideoTask,
    channels: Sequence[Channel],
    stats_store: Optional[Any] = None,
    cooldown_hours: float = 0.0,
    current_time: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """Determine whether a video task is enabled and runnable for automation.

    Returns:
        (is_eligible, reason_msg)
    """
    if not task.enabled:
        return False, "task is disabled in queue"

    if stats_store is not None:
        record = stats_store.find_for_task(task)
        if record is not None:
            if cooldown_hours > 0 and hasattr(record, "is_on_cooldown"):
                is_cool, remaining_hours = record.is_on_cooldown(cooldown_hours, current_time=current_time)
                if is_cool:
                    rem_str = f"{remaining_hours:.1f}h" if remaining_hours >= 1.0 else f"{int(remaining_hours * 60)}m"
                    return False, f"within {cooldown_hours:g}h cooldown period ({rem_str} remaining)"

            current_runs = record.runs
            watch_sec = record.watch_seconds
            v_dur = (record.video_duration if record.video_duration > 0 else 0.0) or getattr(task, "duration_seconds", 0.0)
            if task.is_target_reached(current_runs, watch_sec, v_dur):
                reason = f"target multiplier {task.target_multiplier:.1f}x reached" if (getattr(task, "target_multiplier", 0.0) > 0 and v_dur > 0 and watch_sec / v_dur >= task.target_multiplier) else f"max views limit of {task.max_runs} reached"
                return False, reason

    if task.channel:
        clean_ch = task.channel.strip().lower().lstrip("@")
        matching = [c for c in channels if (c.name or "").strip().lower().lstrip("@") == clean_ch]
        if not matching:
            if channels:
                return False, f"channel '{task.channel}' is not found or inactive"
        else:
            ch = matching[0]
            if ch.archived:
                return False, f"channel '{task.channel}' is archived"
            if not ch.enabled:
                return False, f"channel '{task.channel}' is disabled"
            if ch.target_achieved:
                return False, f"channel '{task.channel}' target watch time achieved"

    return True, "eligible"


def is_video_task_runnable(
    task: VideoTask,
    channels: Sequence[Channel],
    stats_store: Optional[Any] = None,
    cooldown_hours: float = 0.0,
    current_time: Optional[datetime] = None,
) -> bool:
    """Determine whether a video task is enabled and runnable for automation."""
    eligible, _ = check_video_task_eligibility(
        task, channels, stats_store=stats_store, cooldown_hours=cooldown_hours, current_time=current_time
    )
    return eligible


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
