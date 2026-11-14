import copy
import json
import logging
import math
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from ..models.account import normalize_account_id
from ..models.channel import Channel
from ..models.video_task import DEFAULT_WATCHTIME_DURATION
from ..utils.helpers import get_project_root


class ConfigurationError(Exception):
    """Base exception for configuration errors."""
    pass


class CorruptConfigurationError(ConfigurationError):
    """Raised when a settings file is corrupt or unreadable."""
    pass


class CorruptCredentialsError(ConfigurationError):
    """Raised when a credentials file is corrupt or unreadable."""
    pass


class CredentialConflictError(ConfigurationError):
    """Raised when account migration detects conflicting credentials for the same identifier."""
    pass


def safe_float(val: Any, default: float = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    if val is None:
        return default
    try:
        if isinstance(val, str):
            parts = val.strip().split()
            if not parts or parts[0].lower() in ("disabled", "none", "off", "false"):
                return 0.0
            f = float(parts[0])
        else:
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


def parse_sleep_hours(val: Any, default: float = 2.0) -> float:
    """Parse a sleep duration string or numeric value into hours.
    
    Handles '30 Minutes' -> 0.5, '2 Hours' -> 2.0, 'Disabled' -> 0.0, etc.
    """
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val) if val >= 0 else default
    s = str(val).strip()
    if not s or s.lower() in ("disabled", "none", "off", "false", "0"):
        return 0.0
    parts = s.split()
    try:
        num = float(parts[0])
        if len(parts) > 1 and "min" in parts[1].lower():
            return num / 60.0
        return num
    except (ValueError, TypeError):
        return default


def parse_time_of_day(val: Any, default_hour: int = 22, default_minute: int = 0) -> tuple:
    """Parse time string like '22:00 (10:00 PM)', '22:00', '10:00 PM', or '22' into (hour, minute)."""
    if not val:
        return default_hour, default_minute
    s = str(val).strip()
    if "(" in s:
        s = s.split("(")[0].strip()
    is_pm = "pm" in s.lower()
    is_am = "am" in s.lower()
    clean_s = s.lower().replace("pm", "").replace("am", "").strip()
    try:
        if ":" in clean_s:
            h_str, m_str = clean_s.split(":", 1)
            h = int(h_str)
            m = int(m_str)
        else:
            h = int(clean_s)
            m = 0
        if is_pm and h < 12:
            h += 12
        elif is_am and h == 12:
            h = 0
        h = max(0, min(23, h))
        m = max(0, min(59, m))
        return h, m
    except Exception:
        return default_hour, default_minute


def parse_duration_range(val: Any, default_min: float = 8.0, default_max: float = 8.0) -> tuple:
    """Parse duration like '8 Hours', '7 - 8 Hours', '6 - 8 Hours (Random)' into (min_hours, max_hours)."""
    if not val:
        return default_min, default_max
    if isinstance(val, (int, float)):
        v = float(val)
        return v, v
    s = str(val).strip()
    if "(" in s:
        s = s.split("(")[0].strip()
    if "-" in s:
        parts = s.split("-")
        try:
            p1 = float(parts[0].strip().split()[0])
            p2 = float(parts[1].strip().split()[0])
            return min(p1, p2), max(p1, p2)
        except Exception:
            pass
    try:
        h = parse_sleep_hours(s, default=default_min)
        return h, h
    except Exception:
        return default_min, default_max


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
            "SystemName": "",
            "DefaultVideoTargetViews": 3,
            "DefaultVideoMaxViews": 5,
            "DefaultShortsMaxViews": 5,
            "SleepNapEnabled": True,
            "NoTargetFoundSleepHours": "2 Hours",
            "ShortSleepHours": "2 Hours",
            "NightSleepEnabled": False,
            "NightSleepStartTime": "22:00 (10:00 PM)",
            "NightSleepWindowHours": "12 Hours",
            "NightSleepDurationHours": "8 Hours",
            "AccountsFile": "accounts.txt",
            "DirectLinkFallback": False,
            "WatchtimeDuration": DEFAULT_WATCHTIME_DURATION,
            "ParallelBrowsers": 1,
            "AppearanceMode": "Dark",
            "FullVideoTimeoutMinutes": 0,
            "VideoCooldownHours": 18,
            "BlockAds": True,
            "SkipAds": True,
            "MaxAdWatchSeconds": 9,
            "AutoplayEndBufferSeconds": 10,
            "AutomationLoops": 1,
            "BrowsingTargetLimit": 1,
            "BrowsingUnlimited": True,
            "EnableHumanInteractivity": True,
            "RandomScrolling": True,
            "VariableSpeed": False,
            "ToggleCaptions": True,
            "ExpandDescription": True,
            "LikeVideo": True,
            "LikeComments": True,
            "AdjustQuality": True,
            "QualityLevel": "360p",
            "RandomMicroPauses": True,
            "FullscreenTheaterMode": True,
            "TargetShortsPerCycle": 10,
            "NonTargetReelsLimit": 12,
            "TotalNonTargetShortsLimit": 33,
            "NonTargetShortsLimit": 33,
            "EmailReportingEnabled": True,
            "ReportRecipientEmail": "",
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
            "SmtpHost": "smtp.gmail.com",
            "SmtpPort": 587,
            "SmtpUsername": "",
            "SmtpPassword": "",
            "SenderEmail": "",
            "RdpUsername": "",
            "RdpPassword": "",
            "WarmupAutoEnableNewAccounts": True,
            "WarmupDefaultTargetHours": 15,
            "WarmupDefaultDailyIncrementHours": 1,
            "WarmupAutoEnableNewChannels": True,
            "WarmupChannelDefaultTargetHours": 30,
            "WarmupChannelDefaultDailyIncrementHours": 1,
        },
        "accounts": [],
        "videos": [],
        "channels": [],
        "account_warmup": {},
        "channel_warmup": {},
    }

    def __init__(self, config_file: Optional[Path] = None):
        if config_file is None:
            config_file = get_project_root() / "settings.json"
        self.config_file = Path(config_file)
        self.credentials_file = self.config_file.with_name("credentials.json")
        self._lock = threading.RLock()
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
            # Safety limits: parallel browsers, loops, and browsing limit must not be <= 0
            if key in ("ParallelBrowsers", "AutomationLoops", "BrowsingTargetLimit", "NonTargetShortsLimit", "TargetShortsPerCycle", "NonTargetReelsLimit", "TotalNonTargetShortsLimit"):
                if res <= 0:
                    return default
            elif key in ("FullVideoTimeoutMinutes", "VideoCooldownHours", "MaxAdWatchSeconds", "AutoplayEndBufferSeconds", "DefaultVideoTargetViews", "DefaultVideoMaxViews", "DefaultShortsMaxViews", "NoTargetFoundSleepHours", "ShortSleepHours", "NightSleepWindowHours"):
                if res < 0:
                    return default
            return res

    def get_float(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            val = self.data.get("settings", {}).get(key, default)
            res = safe_float(val, default=default)
            if key in ("ParallelBrowsers", "AutomationLoops", "BrowsingTargetLimit", "NonTargetShortsLimit", "TargetShortsPerCycle", "NonTargetReelsLimit", "TotalNonTargetShortsLimit"):
                if res <= 0.0:
                    return default
            elif key in ("FullVideoTimeoutMinutes", "VideoCooldownHours", "MaxAdWatchSeconds", "AutoplayEndBufferSeconds", "DefaultVideoTargetViews", "DefaultVideoMaxViews", "DefaultShortsMaxViews", "NoTargetFoundSleepHours", "ShortSleepHours", "NightSleepWindowHours"):
                if res < 0.0:
                    return default
            return res

    def get_sleep_nap_enabled(self, default: bool = True) -> bool:
        """Return True if Sleep & Nap mode toggle is enabled."""
        return self.get_bool("SleepNapEnabled", default)

    def get_short_sleep_hours(self, default: float = 2.0) -> float:
        """Return configured hours to sleep when no target videos/shorts are found (Short Sleep / Nap)."""
        with self._lock:
            val = self.data.get("settings", {}).get("ShortSleepHours")
            if val is None:
                val = self.data.get("settings", {}).get("NoTargetFoundSleepHours")
            return parse_sleep_hours(val, default=default)

    def get_no_target_sleep_hours(self, default: float = 2.0) -> float:
        """Return configured hours to sleep when no target videos/shorts are found (0.0 if disabled)."""
        return self.get_short_sleep_hours(default=default)

    def get_night_sleep_settings(self) -> Dict[str, Any]:
        """Return configured settings for Long Sleep / Night Mode."""
        enabled = self.get_bool("NightSleepEnabled", False)
        start_raw = str(self.get_setting("NightSleepStartTime", "22:00 (10:00 PM)"))
        sh, sm = parse_time_of_day(start_raw, default_hour=22, default_minute=0)
        window_raw = str(self.get_setting("NightSleepWindowHours", "12 Hours"))
        window_hours = parse_sleep_hours(window_raw, default=12.0)
        dur_raw = str(self.get_setting("NightSleepDurationHours", "8 Hours"))
        dur_min, dur_max = parse_duration_range(dur_raw, default_min=8.0, default_max=8.0)
        return {
            "enabled": enabled,
            "start_time_str": start_raw,
            "start_hour": sh,
            "start_minute": sm,
            "window_hours": max(1.0, window_hours),
            "duration_str": dur_raw,
            "duration_min": max(0.5, dur_min),
            "duration_max": max(dur_min, dur_max),
        }

    def update_settings(self, new_settings: Dict[str, Any]) -> None:
        """Atomically update multiple settings in a single disk-persisted transaction.

        Preserves existing queue tasks, channels, and counters without overwriting them.
        Restores previous in-memory settings if disk persistence fails.
        """
        with self._lock:
            if "settings" not in self.data or not isinstance(self.data["settings"], dict):
                self.data["settings"] = {}
            old_settings = dict(self.data["settings"])
            try:
                for k, v in new_settings.items():
                    self.data["settings"][k] = v
                self._save_unlocked()
            except Exception:
                self.data["settings"] = old_settings
                raise

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            if "settings" not in self.data:
                self.data["settings"] = {}
            old_val = self.data["settings"].get(key)
            self.data["settings"][key] = value
            if key == "NoTargetFoundSleepHours":
                self.data["settings"]["ShortSleepHours"] = value
            elif key == "ShortSleepHours":
                self.data["settings"]["NoTargetFoundSleepHours"] = value
            try:
                self._save_unlocked()
            except Exception:
                if old_val is not None:
                    self.data["settings"][key] = old_val
                else:
                    self.data["settings"].pop(key, None)
                raise

    def migrate_credentials(self) -> None:
        """Explicit, ordered, idempotent migration of legacy accounts in settings to credentials.json."""
        dest_accounts: List[Dict[str, str]] = []
        if self.credentials_file.exists():
            try:
                with open(self.credentials_file, "r", encoding="utf-8") as f:
                    content = f.read()
                loaded = json.loads(content)
                if not isinstance(loaded, dict):
                    raise ValueError(f"Root of credentials file {self.credentials_file.name} is {type(loaded).__name__}, expected object")
                raw_dest = loaded.get("accounts")
                if not isinstance(raw_dest, list):
                    raise ValueError(f"'accounts' in {self.credentials_file.name} must be a list")
                for a in raw_dest:
                    if isinstance(a, dict) and a.get("email"):
                        dest_accounts.append({
                            "email": str(a.get("email") or "").strip(),
                            "password": str(a.get("password") or ""),
                        })
            except Exception as exc:
                self.is_credentials_corrupt = True
                self.credentials_load_error = f"Malformed credentials file {self.credentials_file.name}: {exc}"
                logging.error(self.credentials_load_error)
                raise CorruptCredentialsError(self.credentials_load_error)

        raw_legacy = self.data.get("accounts", [])
        valid_legacy: List[Dict[str, str]] = []
        if isinstance(raw_legacy, list):
            for a in raw_legacy:
                if isinstance(a, dict) and a.get("email"):
                    valid_legacy.append({
                        "email": str(a.get("email") or "").strip(),
                        "password": str(a.get("password") or ""),
                    })

        # If no legacy accounts to migrate:
        if not valid_legacy:
            if not self.credentials_file.exists():
                self._save_credentials_unlocked([])
            if isinstance(raw_legacy, list) and raw_legacy:
                self.data["accounts"] = []
                self._save_unlocked()
            return

        # Merge and deduplicate
        merged_accounts = list(dest_accounts)
        dest_by_email = {a["email"].lower(): a for a in dest_accounts}

        for leg in valid_legacy:
            email_key = leg["email"].lower()
            if email_key in dest_by_email:
                existing = dest_by_email[email_key]
                if existing.get("password", "") != leg.get("password", ""):
                    err_msg = f"Credential conflict for account '{leg['email']}': conflicting passwords in settings and credentials.json"
                    logging.error(err_msg)
                    raise CredentialConflictError(err_msg)
            else:
                merged_accounts.append(leg)
                dest_by_email[email_key] = leg

        # 1. Persist destination credentials
        self._save_credentials_unlocked(merged_accounts)

        # 2. Confirm destination can represent migrated accounts correctly
        with open(self.credentials_file, "r", encoding="utf-8") as f:
            verified = json.load(f)
        if not isinstance(verified, dict) or verified.get("accounts") != merged_accounts:
            raise ConfigurationError(f"Verification of saved credentials file {self.credentials_file.name} failed")

        # 3. Only then remove legacy accounts from settings
        old_accounts = self.data.get("accounts")
        self.data["accounts"] = []
        try:
            self._save_unlocked()
            logging.info(f"Successfully migrated {len(valid_legacy)} account(s) to {self.credentials_file.name}")
        except Exception as e:
            self.data["accounts"] = old_accounts
            err_msg = f"Migration incomplete: credentials saved, but settings cleanup failed: {e}"
            logging.warning(err_msg)
            raise ConfigurationError(err_msg)

    def get_accounts(self) -> List[Dict[str, str]]:
        """Return account credentials from credentials.json, migrating legacy accounts if needed."""
        with self._lock:
            legacy_present = bool(self.data.get("accounts"))
            if not self.credentials_file.exists() or legacy_present:
                self.migrate_credentials()

            if not self.credentials_file.exists():
                return []

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

                seen_accounts: Dict[str, str] = {}
                clean_accounts: List[Dict[str, str]] = []
                for a in accounts:
                    if isinstance(a, dict) and a.get("email"):
                        norm_email = normalize_account_id(str(a.get("email") or ""))
                        pwd = str(a.get("password") or "")
                        if norm_email in seen_accounts:
                            if seen_accounts[norm_email] != pwd:
                                raise CredentialConflictError(
                                    f"Conflicting passwords for duplicate account '{norm_email}' in {self.credentials_file.name}"
                                )
                            continue
                        seen_accounts[norm_email] = pwd
                        clean_accounts.append({"email": norm_email, "password": pwd})
                return clean_accounts
            except CredentialConflictError:
                raise
            except Exception as exc:
                self.is_credentials_corrupt = True
                self.credentials_load_error = f"Malformed credentials file {self.credentials_file.name}: {exc}"
                logging.error(self.credentials_load_error)
                raise CorruptCredentialsError(self.credentials_load_error)

    def set_accounts(self, accounts_list: List[Dict[str, str]]) -> None:
        """Persist account credentials in credentials.json."""
        with self._lock:
            seen: Dict[str, str] = {}
            clean_list: List[Dict[str, str]] = []
            for a in accounts_list:
                if isinstance(a, dict) and a.get("email"):
                    norm_email = normalize_account_id(str(a.get("email") or ""))
                    pwd = str(a.get("password") or "")
                    if norm_email in seen:
                        if seen[norm_email] != pwd:
                            raise CredentialConflictError(f"Conflicting passwords for account '{norm_email}'")
                        continue
                    seen[norm_email] = pwd
                    clean_list.append({"email": norm_email, "password": pwd})
            self._save_credentials_unlocked(clean_list)
            save_needed = False
            if self.data.get("accounts"):
                self.data["accounts"] = []
                save_needed = True

            # Auto-enable warm-up for new accounts if configured
            settings_dict = self.data.get("settings", {})
            auto_val = settings_dict.get("WarmupAutoEnableNewAccounts", True)
            if isinstance(auto_val, bool):
                auto_enabled = auto_val
            else:
                auto_enabled = str(auto_val).strip().lower() in ("true", "1", "yes", "on")

            if auto_enabled:
                if "account_warmup" not in self.data or not isinstance(self.data["account_warmup"], dict):
                    self.data["account_warmup"] = {}
                raw_target = settings_dict.get("WarmupDefaultTargetHours", 15)
                raw_inc = settings_dict.get("WarmupDefaultDailyIncrementHours", 1)
                th = safe_float(raw_target, default=15.0)
                target_h = int(th) if th.is_integer() else th
                inc = safe_float(raw_inc, default=1.0)
                inc_h = int(inc) if inc.is_integer() else inc
                today_str = datetime.now().strftime("%Y-%m-%d")
                for item in clean_list:
                    em = item["email"]
                    if em not in self.data["account_warmup"]:
                        self.data["account_warmup"][em] = {
                            "email": em,
                            "enabled": True,
                            "target_hours": target_h,
                            "daily_increment_hours": inc_h,
                            "current_day": 1,
                            "is_warmed_up": False,
                            "last_active_date": today_str,
                            "watched_today_seconds": 0.0,
                            "total_watched_seconds": 0.0,
                        }
                        save_needed = True

            if save_needed:
                self._save_unlocked()

    def get_account_warmup(self, email: str) -> Optional[Dict[str, Any]]:
        """Return warm-up settings dictionary for the normalized email, or None."""
        norm_email = normalize_account_id(email)
        with self._lock:
            warmup_store = self.data.get("account_warmup", {})
            if isinstance(warmup_store, dict):
                val = warmup_store.get(norm_email)
                return copy.deepcopy(val) if val else None
            return None

    def set_account_warmup(self, email: str, warmup_data: Dict[str, Any]) -> None:
        """Persist warm-up configuration for an account into settings.json."""
        norm_email = normalize_account_id(email)
        with self._lock:
            if "account_warmup" not in self.data or not isinstance(self.data["account_warmup"], dict):
                self.data["account_warmup"] = {}
            clean_data = copy.deepcopy(warmup_data)
            clean_data["email"] = norm_email
            self.data["account_warmup"][norm_email] = clean_data
            self._save_unlocked()

    def get_all_account_warmup(self) -> Dict[str, Dict[str, Any]]:
        """Return all stored account warm-up profiles."""
        with self._lock:
            warmup_store = self.data.get("account_warmup", {})
            return copy.deepcopy(warmup_store) if isinstance(warmup_store, dict) else {}

    def get_channel_warmup(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Return warm-up settings dictionary for the normalized channel identifier, or None."""
        key = (channel_id or "").strip().lower().lstrip("@")
        with self._lock:
            warmup_store = self.data.get("channel_warmup", {})
            if isinstance(warmup_store, dict):
                val = warmup_store.get(key)
                return copy.deepcopy(val) if val else None
            return None

    def set_channel_warmup(self, channel_id: str, warmup_data: Dict[str, Any]) -> None:
        """Persist warm-up configuration for a channel into settings.json."""
        key = (channel_id or "").strip().lower().lstrip("@")
        with self._lock:
            if "channel_warmup" not in self.data or not isinstance(self.data["channel_warmup"], dict):
                self.data["channel_warmup"] = {}
            clean_data = copy.deepcopy(warmup_data)
            self.data["channel_warmup"][key] = clean_data
            self._save_unlocked()

    def get_all_channel_warmup(self) -> Dict[str, Dict[str, Any]]:
        """Return all stored channel warm-up profiles."""
        with self._lock:
            warmup_store = self.data.get("channel_warmup", {})
            return copy.deepcopy(warmup_store) if isinstance(warmup_store, dict) else {}

    def _save_credentials_unlocked(self, accounts_list: List[Dict[str, str]]) -> None:
        self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.credentials_file.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump({"accounts": accounts_list}, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.credentials_file)

    def get_video_queue(self) -> List[Dict[str, Any]]:
        """Return deep copy of video task dictionary list."""
        with self._lock:
            return copy.deepcopy(self.data.get("videos", []))

    def set_video_queue(self, video_list: List[Dict[str, Any]]) -> None:
        """Persist video task dictionary list."""
        with self._lock:
            old_videos = self.data.get("videos")
            self.data["videos"] = copy.deepcopy(video_list)
            try:
                self._save_unlocked()
            except Exception:
                self.data["videos"] = old_videos
                raise

    def update_video_queue(
        self,
        updater: Callable[[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]
    ) -> List[Dict[str, Any]]:
        """Atomically read, mutate, and save the video queue under lock."""
        with self._lock:
            current_queue = copy.deepcopy(self.data.get("videos", []))
            res = updater(current_queue)
            new_queue = res if res is not None else current_queue
            old_videos = self.data.get("videos")
            self.data["videos"] = new_queue
            try:
                self._save_unlocked()
            except Exception:
                self.data["videos"] = old_videos
                raise
            return copy.deepcopy(new_queue)

    def append_video_tasks(self, tasks: List[Dict[str, Any]], deduplicate: bool = True) -> List[Dict[str, Any]]:
        """Atomically append tasks to the video queue under lock, preventing duplicate video tasks."""
        if not tasks:
            return self.get_video_queue()
        with self._lock:
            current_queue = list(self.data.get("videos", []))
            if deduplicate:
                from ..utils.helpers import extract_video_id

                def _canonical_key(v: Dict[str, Any]) -> str:
                    u = str(v.get("url", "") or "").strip()
                    vid = extract_video_id(u)
                    if vid:
                        return f"id:{vid}"
                    if u:
                        return f"url:{u.lower()}"
                    lbl = str(v.get("title", "") or v.get("keywords", "") or "").strip().lower()
                    return f"label:{lbl}" if lbl else ""

                existing_keys: Set[str] = set()
                for item in current_queue:
                    if isinstance(item, dict):
                        k = _canonical_key(item)
                        if k:
                            existing_keys.add(k)

                to_add: List[Dict[str, Any]] = []
                for item in copy.deepcopy(tasks):
                    if not isinstance(item, dict):
                        continue
                    k = _canonical_key(item)
                    if k:
                        if k in existing_keys:
                            continue
                        existing_keys.add(k)
                    to_add.append(item)
                current_queue.extend(to_add)
            else:
                current_queue.extend(copy.deepcopy(tasks))

            old_videos = self.data.get("videos")
            self.data["videos"] = current_queue
            try:
                self._save_unlocked()
            except Exception:
                self.data["videos"] = old_videos
                raise
            return copy.deepcopy(current_queue)

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

            # Auto-enable warm-up for newly added channels if configured
            settings_dict = self.data.get("settings", {})
            auto_val = settings_dict.get("WarmupAutoEnableNewChannels", True)
            if isinstance(auto_val, bool):
                auto_enabled = auto_val
            else:
                auto_enabled = str(auto_val).strip().lower() in ("true", "1", "yes", "on")

            if auto_enabled:
                if "channel_warmup" not in self.data or not isinstance(self.data["channel_warmup"], dict):
                    self.data["channel_warmup"] = {}
                raw_target = settings_dict.get("WarmupChannelDefaultTargetHours", 30)
                raw_inc = settings_dict.get("WarmupChannelDefaultDailyIncrementHours", 1)
                th = safe_float(raw_target, default=30.0)
                target_h = int(th) if th.is_integer() else th
                inc = safe_float(raw_inc, default=1.0)
                inc_h = int(inc) if inc.is_integer() else inc
                today_str = datetime.now().strftime("%Y-%m-%d")

                for ch_item in self.data["channels"]:
                    if isinstance(ch_item, dict):
                        ch_name = (ch_item.get("name") or "").strip()
                        ch_url = (ch_item.get("url") or "").strip()
                        key = ch_name.lower().lstrip("@")
                        if key and key not in self.data["channel_warmup"]:
                            self.data["channel_warmup"][key] = {
                                "channel_name": ch_name,
                                "channel_url": ch_url,
                                "enabled": True,
                                "target_hours": target_h,
                                "daily_increment_hours": inc_h,
                                "current_day": 1,
                                "is_warmed_up": False,
                                "last_active_date": today_str,
                                "watched_today_seconds": 0.0,
                                "watched_today_by_account": {},
                                "total_watched_seconds": 0.0,
                            }

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
