from datetime import datetime
import json
import logging
import math
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.channel import Channel
from ..utils.helpers import get_project_root
from ..models.video_task import DEFAULT_WATCHTIME_DURATION, VideoTask


class ConfigurationError(Exception):
    """Base exception for configuration errors."""
    pass


class CorruptConfigurationError(ConfigurationError):
    """Raised when a settings file is corrupt or unreadable."""
    pass


class CorruptCredentialsError(ConfigurationError):
    """Raised when a credentials file is corrupt or unreadable."""
    pass


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


class ConfigManager:
    """Thread-safe configuration manager using settings.json for settings, accounts, and video queue persistence."""

    REMOVED_SETTINGS = {
        "AIProvider",
        "OpenAIKey",
        "GeminiKey",
        "OpenRouterKey",
        "CommentType",
        "AccessKey",
    }
    REMOVED_VIDEO_FIELDS = {"comment_type", "custom_comment", "like"}

    DEFAULT_DATA: Dict[str, Any] = {
        "settings": {
            "AccountsFile": "accounts.txt",
            "DirectLinkFallback": False,
            "WatchtimeDuration": DEFAULT_WATCHTIME_DURATION,
            "ParallelBrowsers": 1,
            "AppearanceMode": "Dark",
            "FullVideoTimeoutMinutes": 5,
            "VideoCooldownHours": 12,
            "BlockAds": False,
            "SkipAds": True,
            "MaxAdWatchSeconds": 32,
            "AutoplayEndBufferSeconds": 10,
            "AutomationLoops": 1,
            "EnableHumanInteractivity": True,
            "RandomScrolling": True,
            "VariableSpeed": False,
            "ToggleCaptions": True,
            "ExpandDescription": True,
            "LikeComments": True,
            "LikeVideo": True,
            "AdjustQuality": True,
            "QualityLevel": "360p",
            "RandomMicroPauses": True,
        },
        "accounts": [],
        "videos": [],
        "channels": [],
    }

    def __init__(self, config_file: Optional[Path] = None):
        if config_file is None:
            config_file = get_project_root() / "settings.json"
        self.config_file = Path(config_file)
        self.credentials_file = self.config_file.with_name("credentials.json")
        self._lock = threading.Lock()
        self.data: Dict[str, Any] = {}
        self.is_settings_corrupt: bool = False
        self.settings_load_error: Optional[str] = None
        self.settings_backup_path: Optional[Path] = None
        self.is_credentials_corrupt: bool = False
        self.credentials_load_error: Optional[str] = None
        self._save_disabled: bool = False
        self.load()

    @staticmethod
    def _create_unique_backup(source_path: Path) -> Path:
        """Create a uniquely named backup of source_path, never overwriting earlier backups."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        counter = 1
        while True:
            backup_name = f"{source_path.name}.backup_{timestamp}_{counter}.bak"
            backup_path = source_path.with_name(backup_name)
            if not backup_path.exists():
                break
            counter += 1

        shutil.copy2(source_path, backup_path)
        return backup_path

    def load(self) -> None:
        """Load JSON configuration from settings.json file."""
        with self._lock:
            if not self.config_file.exists():
                self.data = json.loads(json.dumps(self.DEFAULT_DATA))
                self._save_unlocked()
                return

            is_corrupt = False
            corrupt_reason = ""
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    content = f.read()
                loaded = json.loads(content)
                if not isinstance(loaded, dict):
                    is_corrupt = True
                    corrupt_reason = f"Root of settings file {self.config_file.name} is {type(loaded).__name__}, expected object"
                else:
                    self.data = loaded
            except Exception as e:
                is_corrupt = True
                corrupt_reason = f"Error reading settings file {self.config_file.name}: {e}"

            if is_corrupt:
                self.is_settings_corrupt = True
                self.settings_load_error = corrupt_reason
                logging.error(corrupt_reason)
                # Preserve corrupt file in a unique backup before recovery
                try:
                    backup_path = self._create_unique_backup(self.config_file)
                    self.settings_backup_path = backup_path
                    logging.info(f"Preserved corrupt settings backup at {backup_path}")
                except Exception as backup_exc:
                    self._save_disabled = True
                    self.data = json.loads(json.dumps(self.DEFAULT_DATA))
                    raise CorruptConfigurationError(
                        f"Failed to backup corrupt settings file {self.config_file.name}: {backup_exc}"
                    )

                # Protect the original invalid file on disk from automatic startup saves
                self._save_disabled = True
                self.data = json.loads(json.dumps(self.DEFAULT_DATA))
                return

            # Ensure required root sections exist
            if "settings" not in self.data or not isinstance(self.data["settings"], dict):
                self.data["settings"] = {}
            if "accounts" not in self.data or not isinstance(self.data["accounts"], list):
                self.data["accounts"] = []
            if "videos" not in self.data or not isinstance(self.data["videos"], list):
                self.data["videos"] = []

            # Clean and sanitize videos: retain valid dict entries
            if "videos" not in self.data or not isinstance(self.data["videos"], list):
                self.data["videos"] = []
            else:
                self.data["videos"] = [v for v in self.data["videos"] if isinstance(v, dict)]

            # Channels are stored in settings.json next to the video queue.
            channels_changed = False
            if "channels" not in self.data or not isinstance(self.data["channels"], list):
                self.data["channels"] = []
                channels_changed = True
            sanitized_channels = self._sanitize_channels(self.data["channels"])
            if sanitized_channels != self.data["channels"]:
                self.data["channels"] = sanitized_channels
                channels_changed = True

            # Fill missing defaults
            for key, val in self.DEFAULT_DATA["settings"].items():
                if key not in self.data["settings"]:
                    self.data["settings"][key] = val

            # Remove settings that belonged to the retired commenting feature
            removed = False
            for key in self.REMOVED_SETTINGS:
                if key in self.data["settings"]:
                    del self.data["settings"][key]
                    removed = True
            for video in self.data["videos"]:
                if not isinstance(video, dict):
                    continue
                for key in self.REMOVED_VIDEO_FIELDS:
                    if key in video:
                        del video[key]
                        removed = True
            if removed or channels_changed:
                self._save_unlocked()

    @staticmethod
    def _sanitize_channels(raw: Any) -> List[Dict[str, Any]]:
        """Drop unusable channel entries and duplicate names coming from disk."""
        if not isinstance(raw, list):
            return []
        cleaned: List[Dict[str, Any]] = []
        seen: set = set()
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            channel = Channel.from_dict(entry)
            key = channel.name.lower()
            if not channel.name or key in seen:
                continue
            seen.add(key)
            cleaned.append(channel.to_dict())
        return cleaned

    def save(self, force: bool = False) -> None:
        """Save current configuration state to disk."""
        with self._lock:
            self._save_unlocked(force=force)

    def _save_unlocked(self, force: bool = False) -> None:
        if self._save_disabled and not force:
            return
        temp_path = self.config_file.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.config_file)
        if force:
            self._save_disabled = False
            self.is_settings_corrupt = False

    def get_setting(self, key: str, default: Any = "") -> str:
        with self._lock:
            val = self.data.get("settings", {}).get(key, default)
            return str(val) if val is not None else str(default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        with self._lock:
            val = self.data.get("settings", {}).get(key, default)
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("true", "1", "yes", "on")

    def get_int(self, key: str, default: int = 1) -> int:
        with self._lock:
            val = self.data.get("settings", {}).get(key, default)
            res = safe_int(val, default=default)
            # Safety limits: timeout, parallel browsers, loops must not be <= 0
            if key in ("FullVideoTimeoutMinutes", "ParallelBrowsers", "AutomationLoops"):
                if res <= 0:
                    return default
            elif key in ("VideoCooldownHours", "MaxAdWatchSeconds"):
                if res < 0:
                    return default
            return res

    def get_float(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            val = self.data.get("settings", {}).get(key, default)
            res = safe_float(val, default=default)
            if key == "FullVideoTimeoutMinutes":
                if res <= 0.0:
                    return default
            elif key in ("VideoCooldownHours", "MaxAdWatchSeconds"):
                if res < 0.0:
                    return default
            return res

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            if "settings" not in self.data:
                self.data["settings"] = {}
            old_val = self.data["settings"].get(key)
            self.data["settings"][key] = value
            try:
                self._save_unlocked()
            except Exception:
                if old_val is not None:
                    self.data["settings"][key] = old_val
                else:
                    self.data["settings"].pop(key, None)
                raise

    def get_accounts(self) -> List[Dict[str, str]]:
        """Return account credentials from the separate local credentials file."""
        with self._lock:
            if self.credentials_file.exists():
                try:
                    with open(self.credentials_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    loaded = json.loads(content)
                    if not isinstance(loaded, dict):
                        raise ValueError(f"Root of credentials file {self.credentials_file.name} is {type(loaded).__name__}, expected object")
                    accounts = loaded.get("accounts")
                    if not isinstance(accounts, list):
                        raise ValueError(f"'accounts' in {self.credentials_file.name} must be a list")
                    self.is_credentials_corrupt = False
                    self.credentials_load_error = None
                    return [
                        {"email": str(a.get("email") or ""), "password": str(a.get("password") or "")}
                        for a in accounts if isinstance(a, dict) and a.get("email")
                    ]
                except Exception as exc:
                    self.is_credentials_corrupt = True
                    self.credentials_load_error = f"Malformed credentials file {self.credentials_file.name}: {exc}"
                    logging.error(self.credentials_load_error)
                    # Do not fall through or overwrite credentials file!
                    raise CorruptCredentialsError(self.credentials_load_error)

            # Migrate legacy accounts from settings.json on first use if credentials_file does not exist.
            legacy = [
                {"email": str(a.get("email") or ""), "password": str(a.get("password") or "")}
                for a in self.data.get("accounts", []) if isinstance(a, dict) and a.get("email")
            ]
            self._save_credentials_unlocked(legacy)
            return legacy

    def set_accounts(self, accounts_list: List[Dict[str, str]]) -> None:
        """Persist account credentials in credentials.json."""
        with self._lock:
            self._save_credentials_unlocked(accounts_list)

    def _save_credentials_unlocked(self, accounts_list: List[Dict[str, str]]) -> None:
        self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.credentials_file.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump({"accounts": accounts_list}, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.credentials_file)

    def get_video_queue(self) -> List[Dict[str, Any]]:
        """Return video task dictionary list."""
        with self._lock:
            return list(self.data.get("videos", []))

    def set_video_queue(self, video_list: List[Dict[str, Any]]) -> None:
        """Persist video task dictionary list."""
        with self._lock:
            old_videos = self.data.get("videos")
            self.data["videos"] = video_list
            try:
                self._save_unlocked()
            except Exception:
                self.data["videos"] = old_videos
                raise

    def get_channels(self) -> List[Dict[str, Any]]:
        """Return the saved channel list (name + url dictionaries)."""
        with self._lock:
            return [dict(entry) for entry in self.data.get("channels", [])]

    def set_channels(self, channel_list: List[Dict[str, Any]], preserve_counters: bool = True) -> None:
        """Persist the channel list, sanitized of blanks and duplicate names."""
        with self._lock:
            old_channels = self.data.get("channels")
            if preserve_counters:
                existing_map = {
                    (c.get("name") or "").strip().lower().lstrip("@"): float(c.get("watch_seconds", 0.0) or 0.0)
                    for c in self.data.get("channels", []) if isinstance(c, dict)
                }
                for incoming in channel_list:
                    if isinstance(incoming, dict):
                        key = (incoming.get("name") or "").strip().lower().lstrip("@")
                        curr_sec = existing_map.get(key)
                        if curr_sec is not None and curr_sec > float(incoming.get("watch_seconds", 0.0) or 0.0):
                            incoming["watch_seconds"] = curr_sec
            self.data["channels"] = self._sanitize_channels(channel_list)
            try:
                self._save_unlocked()
            except Exception:
                self.data["channels"] = old_channels
                raise

    def reset_channel_watch_times(self) -> None:
        """Zero out watch_seconds for all channels stored in settings."""
        with self._lock:
            old_channels = [dict(c) for c in self.data.get("channels", [])]
            for channel in self.data.get("channels", []):
                if isinstance(channel, dict):
                    channel["watch_seconds"] = 0.0
            try:
                self._save_unlocked()
            except Exception:
                self.data["channels"] = old_channels
                raise

    def increment_channel_watch_seconds(self, channel_name: str, seconds: float) -> Optional[float]:
        """Checkpoint a playback delta under one lock; never recreate a deleted channel."""
        key = (channel_name or "").strip().lower().lstrip("@")
        with self._lock:
            for channel in self.data.get("channels", []):
                if (channel.get("name") or "").strip().lower().lstrip("@") == key:
                    previous = float(channel.get("watch_seconds", 0.0) or 0.0)
                    total = round(previous + seconds, 3)
                    channel["watch_seconds"] = total
                    try:
                        self._save_unlocked()
                    except Exception:
                        channel["watch_seconds"] = previous
                        raise
                    return total
        return None
