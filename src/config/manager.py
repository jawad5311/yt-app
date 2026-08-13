import configparser
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConfigManager:
    """Thread-safe configuration manager for reading/writing config.ini."""

    DEFAULT_SETTINGS = {
        "AccountsFile": "accounts.txt",
        "AccessKey": "access",
        "DirectLinkFallback": "True",
        "AIProvider": "Gemini",
        "OpenAIKey": "",
        "GeminiKey": "",
        "CommentType": "AI",
        "WatchtimeDuration": "5 Minutes",
        "ParallelBrowsers": "1",
        "AppearanceMode": "Dark",
    }

    def __init__(self, config_file: Optional[Path] = None):
        if config_file is None:
            config_file = Path(__file__).resolve().parent.parent.parent / "config.ini"
        self.config_file = Path(config_file)
        self._lock = threading.Lock()
        self.parser = configparser.ConfigParser()
        self.load()

    def load(self) -> None:
        """Load configuration from config.ini file."""
        with self._lock:
            if not self.config_file.exists():
                self._create_default_config()

            try:
                self.parser.read(str(self.config_file), encoding="utf-8")
            except Exception as e:
                logging.error(f"Error reading config file {self.config_file}: {e}")
                self._create_default_config()

            # Ensure required sections exist
            if "Settings" not in self.parser:
                self.parser["Settings"] = {}
            if "Videos" not in self.parser:
                self.parser["Videos"] = {}

            # Fill missing defaults
            for key, val in self.DEFAULT_SETTINGS.items():
                if key not in self.parser["Settings"]:
                    self.parser["Settings"][key] = str(val)

            if "VideoList" not in self.parser["Videos"]:
                self.parser["Videos"]["VideoList"] = "[]"

    def _create_default_config(self) -> None:
        """Create a new default config.ini file."""
        self.parser["Settings"] = self.DEFAULT_SETTINGS
        self.parser["Videos"] = {"VideoList": "[]"}
        self._save_unlocked()

    def save(self) -> None:
        """Save current configuration state to disk."""
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        temp_path = self.config_file.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            self.parser.write(f)
        temp_path.replace(self.config_file)

    def get_setting(self, key: str, default: Any = "") -> str:
        with self._lock:
            return self.parser.get("Settings", key, fallback=str(default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get_setting(key, str(default)).strip().lower()
        return val in ("true", "1", "yes", "on")

    def get_int(self, key: str, default: int = 1) -> int:
        try:
            return int(self.get_setting(key, str(default)))
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            if "Settings" not in self.parser:
                self.parser["Settings"] = {}
            self.parser["Settings"][key] = str(value)
            self._save_unlocked()

    def get_video_queue(self) -> List[Dict[str, Any]]:
        with self._lock:
            raw = self.parser.get("Videos", "VideoList", fallback="[]")
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
            except Exception:
                pass
            return []

    def set_video_queue(self, video_list: List[Dict[str, Any]]) -> None:
        with self._lock:
            if "Videos" not in self.parser:
                self.parser["Videos"] = {}
            self.parser["Videos"]["VideoList"] = json.dumps(video_list, indent=2)
            self._save_unlocked()
