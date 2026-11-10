"""Account warm-up progression management, ladder tracking, and color gradient computation."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

from ..models.account import normalize_account_id


def get_warmup_gradient_color(current_step: int, total_steps: int, is_warmed_up: bool) -> Tuple[str, str, str]:
    """Calculate the status color and description for warm-up progression:
    - Red at the start / far from target (Stage 1 / Day 1)
    - Gradient from Red to Yellow in the middle
    - Gradient from Yellow to Light Green when one step before reaching its limit (total_steps - 1)
    - Dark Green when target achieved / fully warmed up

    Returns:
        (hex_color, label_text, text_contrast_color)
    """
    if is_warmed_up or current_step >= total_steps:
        # Dark green when target achieved or fully warmed up
        return "#15803d", "Fully Warmed Up", "#ffffff"

    if total_steps <= 1:
        return "#ef4444", "Warming Up", "#ffffff"

    if current_step == total_steps - 1:
        # Light green when one step before it reaches its limits
        return "#4ade80", f"Day {current_step}/{total_steps} (1 Step to Limit)", "#0f172a"

    # Steps from 1 to total_steps - 2
    max_mid_step = max(1, total_steps - 2)
    # Ratio ranges from 0.0 at Day 1 to 1.0 at Day (total_steps - 2)
    ratio = min(1.0, max(0.0, (current_step - 1) / max_mid_step))

    if ratio <= 0.5:
        # Interpolate from Red (#EF4444: 239, 68, 68) to Yellow (#EAB308: 234, 179, 8)
        sub_ratio = ratio / 0.5
        r = int(239 + (234 - 239) * sub_ratio)
        g = int(68 + (179 - 68) * sub_ratio)
        b = int(68 + (8 - 68) * sub_ratio)
        text_fg = "#ffffff" if ratio < 0.3 else "#0f172a"
    else:
        # Interpolate from Yellow (#EAB308: 234, 179, 8) to Light Green (#4ADE80: 74, 222, 128)
        sub_ratio = (ratio - 0.5) / 0.5
        r = int(234 + (74 - 234) * sub_ratio)
        g = int(179 + (222 - 179) * sub_ratio)
        b = int(8 + (128 - 8) * sub_ratio)
        text_fg = "#0f172a"

    hex_color = f"#{r:02x}{g:02x}{b:02x}"
    label_text = f"Day {current_step}/{total_steps}"
    return hex_color, label_text, text_fg


@dataclass
class WarmupAccountConfig:
    email: str
    enabled: bool = True
    target_hours: float = 15.0
    daily_increment_hours: float = 1.0
    start_hours: float = 1.0
    current_day: int = 1
    is_warmed_up: bool = False
    last_active_date: str = ""
    watched_today_seconds: float = 0.0
    total_watched_seconds: float = 0.0

    def __post_init__(self) -> None:
        self.email = normalize_account_id(self.email)
        self.target_hours = max(0.5, float(self.target_hours))
        self.daily_increment_hours = max(0.1, float(self.daily_increment_hours))
        self.start_hours = max(0.1, float(self.start_hours))
        self.current_day = max(1, int(self.current_day))
        if not self.last_active_date:
            self.last_active_date = datetime.now().strftime("%Y-%m-%d")

    @property
    def total_steps(self) -> int:
        """Calculate total number of daily ladder steps needed to reach target hours."""
        if self.target_hours <= self.start_hours:
            return 1
        steps = math.ceil((self.target_hours - self.start_hours) / self.daily_increment_hours) + 1
        return max(1, steps)

    def get_daily_allowed_seconds(self) -> float:
        """Calculate watch time limit in seconds allowed for the current day."""
        if self.is_warmed_up:
            return self.target_hours * 3600.0
        allowed_hours = min(self.target_hours, self.start_hours + (self.current_day - 1) * self.daily_increment_hours)
        return allowed_hours * 3600.0

    def check_and_rollover(self, today_str: Optional[str] = None) -> bool:
        """Check if date has rolled over past midnight.
        If a new day started, advances current_day if watch quota was progressed,
        resets watched_today_seconds to 0, and checks for graduation.
        """
        if today_str is None:
            today_str = datetime.now().strftime("%Y-%m-%d")

        if self.last_active_date != today_str:
            # New calendar day detected (midnight rollover)
            if self.watched_today_seconds > 0 and not self.is_warmed_up:
                # Advance step to next day
                self.current_day += 1
                if self.current_day >= self.total_steps:
                    self.is_warmed_up = True
            self.watched_today_seconds = 0.0
            self.last_active_date = today_str
            return True
        return False

    def record_watch_seconds(self, seconds: float, today_str: Optional[str] = None) -> None:
        """Add watch seconds to today's count and total count."""
        if seconds <= 0:
            return
        self.check_and_rollover(today_str)
        self.watched_today_seconds += seconds
        self.total_watched_seconds += seconds

        # Check if today's progress has achieved target
        daily_limit = self.get_daily_allowed_seconds()
        if not self.is_warmed_up and self.current_day >= self.total_steps and self.watched_today_seconds >= daily_limit:
            self.is_warmed_up = True

    def is_daily_quota_reached(self) -> bool:
        """Return True if warm-up is enabled, account is not fully warmed up, and today's limit is met."""
        if not self.enabled:
            return False
        self.check_and_rollover()
        if self.is_warmed_up:
            # Fully warmed up accounts operate under normal continuous browsing rules
            return False
        return self.watched_today_seconds >= self.get_daily_allowed_seconds()

    def mark_as_warmed_up(self) -> None:
        """Manual override: mark account as fully warmed up."""
        self.is_warmed_up = True
        self.current_day = self.total_steps

    def reset_warmup(self) -> None:
        """Manual override: reset warm-up progress back to Day 1."""
        self.is_warmed_up = False
        self.current_day = 1
        self.watched_today_seconds = 0.0
        self.last_active_date = datetime.now().strftime("%Y-%m-%d")

    def get_status_info(self) -> Dict[str, Any]:
        """Return comprehensive status metrics and styling properties."""
        self.check_and_rollover()
        tot_steps = self.total_steps
        color, stage_label, text_fg = get_warmup_gradient_color(self.current_day, tot_steps, self.is_warmed_up)
        daily_allowed_sec = self.get_daily_allowed_seconds()
        watched_sec = self.watched_today_seconds
        remaining_sec = max(0.0, daily_allowed_sec - watched_sec)

        pct = 100.0 if self.is_warmed_up else min(99.0, max(0.0, ((self.current_day - 1) / max(1, tot_steps - 1)) * 100.0))

        return {
            "email": self.email,
            "enabled": self.enabled,
            "is_warmed_up": self.is_warmed_up,
            "current_day": self.current_day,
            "total_steps": tot_steps,
            "target_hours": self.target_hours,
            "daily_increment_hours": self.daily_increment_hours,
            "start_hours": self.start_hours,
            "daily_allowed_seconds": daily_allowed_sec,
            "watched_today_seconds": watched_sec,
            "remaining_today_seconds": remaining_sec,
            "daily_allowed_hours": daily_allowed_sec / 3600.0,
            "watched_today_hours": watched_sec / 3600.0,
            "total_watched_hours": self.total_watched_seconds / 3600.0,
            "color": color,
            "text_fg": text_fg,
            "stage_label": stage_label,
            "progress_percent": pct,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], email: str = "") -> "WarmupAccountConfig":
        em = data.get("email") or email or ""
        return cls(
            email=em,
            enabled=bool(data.get("enabled", True)),
            target_hours=float(data.get("target_hours", 15.0)),
            daily_increment_hours=float(data.get("daily_increment_hours", 1.0)),
            start_hours=float(data.get("start_hours", 1.0)),
            current_day=int(data.get("current_day", 1)),
            is_warmed_up=bool(data.get("is_warmed_up", False)),
            last_active_date=str(data.get("last_active_date", "")),
            watched_today_seconds=float(data.get("watched_today_seconds", 0.0)),
            total_watched_seconds=float(data.get("total_watched_seconds", 0.0)),
        )


def calculate_seconds_until_midnight(now: Optional[datetime] = None) -> float:
    """Calculate remaining seconds until midnight (00:00:00) + 60s buffer for daily rollover."""
    if now is None:
        now = datetime.now()
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, time.min)
    seconds_left = (midnight - now).total_seconds() + 60.0
    return max(60.0, seconds_left)


class WarmupManager:
    """Manages warm-up persistence and querying against ConfigManager."""

    def __init__(self, config_mgr: Any):
        self.config_mgr = config_mgr

    def get_account_config(self, email: str) -> WarmupAccountConfig:
        """Retrieve warm-up configuration for an account, initializing defaults if absent."""
        norm_email = normalize_account_id(email)
        raw = self.config_mgr.get_account_warmup(norm_email) if hasattr(self.config_mgr, "get_account_warmup") else None
        if raw:
            return WarmupAccountConfig.from_dict(raw, email=norm_email)

        # Fallback to defaults from global config
        target_h = float(self.config_mgr.get_setting("WarmupDefaultTargetHours", 15.0)) if hasattr(self.config_mgr, "get_setting") else 15.0
        inc_h = float(self.config_mgr.get_setting("WarmupDefaultDailyIncrementHours", 1.0)) if hasattr(self.config_mgr, "get_setting") else 1.0
        start_h = float(self.config_mgr.get_setting("WarmupDefaultStartHours", 1.0)) if hasattr(self.config_mgr, "get_setting") else 1.0
        auto_enabled = bool(self.config_mgr.get_bool("WarmupAutoEnableNewAccounts", True)) if hasattr(self.config_mgr, "get_bool") else True

        cfg = WarmupAccountConfig(
            email=norm_email,
            enabled=auto_enabled,
            target_hours=target_h,
            daily_increment_hours=inc_h,
            start_hours=start_h,
            current_day=1,
            is_warmed_up=False,
        )
        if hasattr(self.config_mgr, "set_account_warmup"):
            self.config_mgr.set_account_warmup(norm_email, cfg.to_dict())
        return cfg

    def save_account_config(self, cfg: WarmupAccountConfig) -> None:
        """Persist account warm-up configuration to settings."""
        if hasattr(self.config_mgr, "set_account_warmup"):
            self.config_mgr.set_account_warmup(cfg.email, cfg.to_dict())

    def record_watch_time(self, email: str, seconds: float) -> Optional[WarmupAccountConfig]:
        """Record watched seconds for the account and save state."""
        cfg = self.get_account_config(email)
        if not cfg.enabled:
            return cfg
        cfg.record_watch_seconds(seconds)
        self.save_account_config(cfg)
        return cfg

    def is_daily_quota_reached(self, email: str) -> bool:
        """Check if active account reached its warm-up daily quota."""
        cfg = self.get_account_config(email)
        return cfg.is_daily_quota_reached()
