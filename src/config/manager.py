import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConfigManager:
    """Thread-safe configuration manager using settings.json for settings, accounts, and video queue persistence."""

    REMOVED_SETTINGS = {
        "AIProvider",
        "OpenAIKey",
        "GeminiKey",
        "OpenRouterKey",
        "CommentType",
    }
    REMOVED_VIDEO_FIELDS = {"comment_type", "custom_comment", "like"}

    DEFAULT_DATA: Dict[str, Any] = {
        "settings": {
            "AccountsFile": "accounts.txt",
            "AccessKey": "access",
            "DirectLinkFallback": True,
            "WatchtimeDuration": "5 Minutes",
            "ParallelBrowsers": 1,
            "AppearanceMode": "Dark",
        },
        "accounts": [],
        "videos": [],
    }

    def __init__(self, config_file: Optional[Path] = None):
        if config_file is None:
            config_file = Path(__file__).resolve().parent.parent.parent / "settings.json"
        self.config_file = Path(config_file)
        self.credentials_file = self.config_file.with_name("credentials.json")
        self._lock = threading.Lock()
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load JSON configuration from settings.json file."""
        with self._lock:
            if not self.config_file.exists():
                self.data = json.loads(json.dumps(self.DEFAULT_DATA))
                self._save_unlocked()
                return

            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data = loaded
                    else:
                        self.data = json.loads(json.dumps(self.DEFAULT_DATA))
            except Exception as e:
                logging.error(f"Error reading settings file {self.config_file}: {e}")
                self.data = json.loads(json.dumps(self.DEFAULT_DATA))

            # Ensure required root sections exist
            if "settings" not in self.data or not isinstance(self.data["settings"], dict):
                self.data["settings"] = {}
            if "accounts" not in self.data or not isinstance(self.data["accounts"], list):
                self.data["accounts"] = []
            if "videos" not in self.data or not isinstance(self.data["videos"], list):
                self.data["videos"] = []

            # Fill missing defaults
            for key, val in self.DEFAULT_DATA["settings"].items():
                if key not in self.data["settings"]:
                    self.data["settings"][key] = val

            # Remove settings that belonged to the retired commenting feature,
            # including any API keys previously stored on disk.
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
            if removed:
                self._save_unlocked()

    def save(self) -> None:
        """Save current configuration state to disk."""
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        temp_path = self.config_file.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.config_file)

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
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            if "settings" not in self.data:
                self.data["settings"] = {}
            self.data["settings"][key] = value
            self._save_unlocked()

    def get_accounts(self) -> List[Dict[str, str]]:
        """Return account credentials from the separate local credentials file."""
        with self._lock:
            if self.credentials_file.exists():
                try:
                    with open(self.credentials_file, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    accounts = loaded.get("accounts", []) if isinstance(loaded, dict) else []
                    if isinstance(accounts, list):
                        legacy_accounts = self.data.get("accounts", [])
                        if not accounts and isinstance(legacy_accounts, list) and legacy_accounts:
                            self._save_credentials_unlocked(legacy_accounts)
                            self.data["accounts"] = []
                            self._save_unlocked()
                            return list(legacy_accounts)
                        return list(accounts)
                except Exception as exc:
                    logging.error(f"Error reading credentials file {self.credentials_file}: {exc}")

            # Migrate legacy accounts from settings.json on first use.
            accounts = list(self.data.get("accounts", []))
            self._save_credentials_unlocked(accounts)
            return accounts

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
            self.data["videos"] = video_list
            self._save_unlocked()
