"""Comprehensive unit tests for the account warm-up progression system:
- Color gradient calculation (Red -> Yellow -> Light Green -> Dark Green)
- Daily ladder watch quota tracking and midnight rollover
- Manual overrides (mark as warmed up, reset warm-up)
- Auto-enrollment for newly added accounts
- SettingsView Warm Up tab and persistence
- WarmupDialog interaction and status rendering
"""
import tkinter as tk
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

from src.automation.warmup_manager import (
    WarmupAccountConfig,
    WarmupManager,
    calculate_seconds_until_midnight,
    get_warmup_gradient_color,
)
from src.config.manager import ConfigManager
from src.models.account import Account
from src.ui.views.settings_view import SettingsView
from src.ui.warmup_dialog import WarmupDialog


def test_warmup_gradient_color_progression():
    """Verify color gradient progression:
    - Red at start (Day 1 / far from limit)
    - Yellow in the middle
    - Light Green when 1 step before limit
    - Dark Green when limit achieved or fully warmed up
    """
    total_steps = 15

    # Day 1: Starting phase -> Red
    c_day1, label1, _ = get_warmup_gradient_color(1, total_steps, False)
    assert c_day1.lower() == "#ef4444"
    assert "Day 1/15" in label1

    # Day 8: Middle phase -> Yellow / Amber
    c_mid, label_mid, _ = get_warmup_gradient_color(8, total_steps, False)
    # Check that Green component is high and Red component is high (Yellow)
    r_val = int(c_mid[1:3], 16)
    g_val = int(c_mid[3:5], 16)
    assert r_val > 150
    assert g_val > 150

    # Day 14 (1 step before 15): Light Green (#4ade80)
    c_penultimate, label_pen, _ = get_warmup_gradient_color(14, total_steps, False)
    assert c_penultimate.lower() == "#4ade80"
    assert "1 Step" in label_pen

    # Day 15 (Target limit reached): Dark Green (#15803d)
    c_target, label_target, _ = get_warmup_gradient_color(15, total_steps, False)
    assert c_target.lower() == "#15803d"
    assert "Fully Warmed Up" in label_target

    # Explicit is_warmed_up flag -> Dark Green (#15803d) regardless of step
    c_forced, _, _ = get_warmup_gradient_color(3, total_steps, True)
    assert c_forced.lower() == "#15803d"


def test_warmup_ladder_daily_quota_and_rollover():
    """Verify daily ladder increment, quota enforcement, and midnight rollover."""
    cfg = WarmupAccountConfig(
        email="test_ladder@gmail.com",
        target_hours=15.0,
        daily_increment_hours=1.0,
        start_hours=1.0,
        current_day=1,
        last_active_date="2026-11-10",
    )
    assert cfg.total_steps == 15
    assert cfg.get_daily_allowed_seconds() == 3600.0  # 1 hour allowed on Day 1

    # Watch 30 minutes (1800s) -> quota not yet reached
    cfg.record_watch_seconds(1800.0, today_str="2026-11-10")
    assert not cfg.is_daily_quota_reached()
    assert cfg.watched_today_seconds == 1800.0

    # Watch another 30 minutes (1800s) -> total 3600s -> daily quota reached!
    cfg.record_watch_seconds(1800.0, today_str="2026-11-10")
    assert cfg.is_daily_quota_reached()

    # Next day arrives (midnight rollover)!
    rolled = cfg.check_and_rollover(today_str="2026-11-11")
    assert rolled is True
    assert cfg.current_day == 2
    assert cfg.watched_today_seconds == 0.0
    assert cfg.get_daily_allowed_seconds() == 7200.0  # 2 hours allowed on Day 2
    assert not cfg.is_daily_quota_reached()


def test_warmup_manual_status_overrides():
    """Verify manual override to mark account as warmed up or reset back to Day 1."""
    cfg = WarmupAccountConfig(
        email="override@gmail.com",
        target_hours=15.0,
        daily_increment_hours=1.0,
        start_hours=1.0,
        current_day=5,
        watched_today_seconds=5000.0,
    )
    assert not cfg.is_warmed_up

    # 1. Mark as fully warmed up
    cfg.mark_as_warmed_up()
    assert cfg.is_warmed_up is True
    assert cfg.current_day == 15
    info = cfg.get_status_info()
    assert info["color"].lower() == "#15803d"

    # 2. Reset warm-up
    cfg.reset_warmup()
    assert cfg.is_warmed_up is False
    assert cfg.current_day == 1
    assert cfg.watched_today_seconds == 0.0
    info2 = cfg.get_status_info()
    assert info2["color"].lower() == "#ef4444"


def test_config_manager_auto_enroll_new_accounts(tmp_path):
    """Verify that adding accounts auto-enrolls them in warm-up when toggle is True."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("WarmupAutoEnableNewAccounts", True)
    mgr.set_setting("WarmupDefaultTargetHours", 12.0)
    mgr.set_setting("WarmupDefaultDailyIncrementHours", 2.0)

    mgr.set_accounts([{"email": "newbie@gmail.com", "password": "123"}])

    w_cfg = mgr.get_account_warmup("newbie@gmail.com")
    assert w_cfg is not None
    assert w_cfg["enabled"] is True
    assert w_cfg["target_hours"] == 12.0
    assert w_cfg["daily_increment_hours"] == 2.0
    assert w_cfg["current_day"] == 1
    assert w_cfg["is_warmed_up"] is False


def test_config_manager_skip_auto_enroll_when_disabled(tmp_path):
    """Verify that adding accounts does not auto-enroll when toggle is False."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("WarmupAutoEnableNewAccounts", False)

    mgr.set_accounts([{"email": "no_warmup@gmail.com", "password": "123"}])

    w_cfg = mgr.get_account_warmup("no_warmup@gmail.com")
    assert w_cfg is None


def test_settings_view_warmup_tab_exact_order_and_persistence(ctk_app, tmp_path):
    """Verify SettingsView has Warm Up tab in exact order and persists settings."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("SystemName", "TestPC")
    app = ctk_app
    app.config_mgr = mgr
    app.appearance_mode = "Dark"
    app.apply_appearance_mode = MagicMock()

    view = SettingsView(app, app)

    # Verify tab order
    tab_names = list(view.tabview._segmented_button._buttons_dict.keys())
    assert "Warm Up" in tab_names
    assert tab_names[-1] == "Warm Up"

    # Verify presence of warm-up settings widgets
    assert hasattr(view, "warmup_auto_enable_switch")
    assert hasattr(view, "warmup_default_target_entry")
    assert hasattr(view, "warmup_default_inc_entry")
    assert hasattr(view, "warmup_default_start_entry")

    # Modify values
    view.warmup_default_target_entry.delete(0, tk.END)
    view.warmup_default_target_entry.insert(0, "20.0")

    view.warmup_default_inc_entry.delete(0, tk.END)
    view.warmup_default_inc_entry.insert(0, "2.5")

    view.warmup_default_start_entry.delete(0, tk.END)
    view.warmup_default_start_entry.insert(0, "1.5")

    # Save
    success = view.save_settings()
    assert success is True

    assert mgr.get_float("WarmupDefaultTargetHours") == 20.0
    assert mgr.get_float("WarmupDefaultDailyIncrementHours") == 2.5
    assert mgr.get_float("WarmupDefaultStartHours") == 1.5
    view.destroy()


def test_warmup_dialog_ui_and_actions(ctk_app, tmp_path):
    """Verify WarmupDialog UI, manual override buttons, and saving."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    account = Account(email="ui_test@gmail.com", password="pwd")

    mgr.set_account_warmup("ui_test@gmail.com", {
        "email": "ui_test@gmail.com",
        "enabled": True,
        "target_hours": 10.0,
        "daily_increment_hours": 1.0,
        "start_hours": 1.0,
        "current_day": 2,
        "is_warmed_up": False,
        "watched_today_seconds": 1800.0,
    })

    callback_called = []
    dialog = WarmupDialog(
        ctk_app,
        account=account,
        config_mgr=mgr,
        on_save_callback=lambda: callback_called.append(True),
    )

    # Initial status reflects Day 2
    assert "Day 2" in dialog.status_badge.cget("text")

    # Test "Mark as Fully Warmed Up" button
    dialog._mark_as_warmed_up()
    assert "Fully Warmed Up" in dialog.status_badge.cget("text")
    assert dialog.status_badge.cget("bg").lower() == "#15803d"

    # Test "Reset Warm-Up" button
    dialog._reset_warmup()
    assert "Day 1" in dialog.status_badge.cget("text")
    assert dialog.status_badge.cget("bg").lower() == "#ef4444"

    # Modify entries and save
    dialog.target_entry.delete(0, tk.END)
    dialog.target_entry.insert(0, "18.0")
    dialog.save()

    assert len(callback_called) == 1
    saved = mgr.get_account_warmup("ui_test@gmail.com")
    assert saved["target_hours"] == 18.0


def test_calculate_seconds_until_midnight():
    """Verify calculate_seconds_until_midnight returns positive duration reaching next calendar day."""
    sec = calculate_seconds_until_midnight()
    assert sec >= 60.0
    assert sec <= 86400.0 + 120.0
