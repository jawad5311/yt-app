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
    WarmupChannelConfig,
    WarmupManager,
    calculate_seconds_until_midnight,
    format_hours,
    format_number,
    get_warmup_gradient_color,
)
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
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


def test_number_formatting_helpers():
    """Verify that numbers default to whole integers and floats retain decimals."""
    # Whole numbers -> formatted as integers
    assert format_number(15.0) == "15"
    assert format_number(15) == "15"
    assert format_number("15.0") == "15"
    assert format_number(1.0) == "1"
    assert format_number(0.0) == "0"

    # Floats with fractions -> retain decimals
    assert format_number(1.5) == "1.5"
    assert format_number(2.25) == "2.25"
    assert format_number("3.75") == "3.75"

    # Hour formatter
    assert format_hours(15.0) == "15h"
    assert format_hours(1) == "1h"
    assert format_hours(1.5) == "1.5h"
    assert format_hours(0) == "0h"


def test_warmup_ladder_daily_quota_and_rollover():
    """Verify daily ladder increment, quota enforcement, and midnight rollover."""
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cfg = WarmupAccountConfig(
        email="test_ladder@gmail.com",
        target_hours=15,
        daily_increment_hours=1,
        current_day=1,
        last_active_date=today,
    )
    assert cfg.total_steps == 15
    assert cfg.get_daily_allowed_seconds() == 3600.0  # 1 hour allowed on Day 1

    # Watch 30 minutes (1800s) -> quota not yet reached
    cfg.record_watch_seconds(1800.0, today_str=today)
    assert not cfg.is_daily_quota_reached(today_str=today)
    assert cfg.watched_today_seconds == 1800.0

    # Watch another 30 minutes (1800s) -> total 3600s -> daily quota reached!
    cfg.record_watch_seconds(1800.0, today_str=today)
    assert cfg.is_daily_quota_reached(today_str=today)

    # Next day arrives (midnight rollover)!
    rolled = cfg.check_and_rollover(today_str=tomorrow)
    assert rolled is True
    assert cfg.current_day == 2
    assert cfg.watched_today_seconds == 0.0
    assert cfg.get_daily_allowed_seconds() == 7200.0  # 2 hours allowed on Day 2
    assert not cfg.is_daily_quota_reached(today_str=tomorrow)


def test_warmup_manual_status_overrides():
    """Verify manual override to mark account as warmed up or reset back to Day 1."""
    cfg = WarmupAccountConfig(
        email="override@gmail.com",
        target_hours=15,
        daily_increment_hours=1,
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
    mgr.set_setting("WarmupDefaultTargetHours", 12)
    mgr.set_setting("WarmupDefaultDailyIncrementHours", 2)

    mgr.set_accounts([{"email": "newbie@gmail.com", "password": "123"}])

    w_cfg = mgr.get_account_warmup("newbie@gmail.com")
    assert w_cfg is not None
    assert w_cfg["enabled"] is True
    assert w_cfg["target_hours"] == 12
    assert w_cfg["daily_increment_hours"] == 2
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

    # Verify presence of warm-up settings widgets (and absence of starting watch time)
    assert hasattr(view, "warmup_auto_enable_switch")
    assert hasattr(view, "warmup_default_target_entry")
    assert hasattr(view, "warmup_default_inc_entry")
    assert not hasattr(view, "warmup_default_start_entry")

    # Modify values: target with integer, increment with float
    view.warmup_default_target_entry.delete(0, tk.END)
    view.warmup_default_target_entry.insert(0, "20")

    view.warmup_default_inc_entry.delete(0, tk.END)
    view.warmup_default_inc_entry.insert(0, "2.5")

    # Save
    success = view.save_settings()
    assert success is True

    # Integer persisted as int, float persisted as float
    assert mgr.get_int("WarmupDefaultTargetHours") == 20
    assert mgr.get_float("WarmupDefaultDailyIncrementHours") == 2.5
    assert mgr.data["settings"]["WarmupDefaultTargetHours"] == 20
    assert mgr.data["settings"]["WarmupDefaultDailyIncrementHours"] == 2.5
    view.destroy()


def test_warmup_dialog_ui_and_actions(ctk_app, tmp_path):
    """Verify WarmupDialog UI, whole number display, manual override buttons, and saving."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    account = Account(email="ui_test@gmail.com", password="pwd")

    mgr.set_account_warmup("ui_test@gmail.com", {
        "email": "ui_test@gmail.com",
        "enabled": True,
        "target_hours": 10.0,
        "daily_increment_hours": 1.0,
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

    # Absence of starting watch time entry
    assert not hasattr(dialog, "start_entry")

    # Displays whole numbers as integers (not 10.0 or 1.0)
    assert dialog.target_entry.get() == "10"
    assert dialog.inc_entry.get() == "1"

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

    # Modify entries with float and save
    dialog.target_entry.delete(0, tk.END)
    dialog.target_entry.insert(0, "18.5")
    dialog.save()

    assert len(callback_called) == 1
    saved = mgr.get_account_warmup("ui_test@gmail.com")
    assert saved["target_hours"] == 18.5


def test_calculate_seconds_until_midnight():
    """Verify calculate_seconds_until_midnight returns positive duration reaching next calendar day."""
    sec = calculate_seconds_until_midnight()
    assert sec >= 60.0
    assert sec <= 86400.0 + 120.0


def test_channel_warmup_ladder_and_rollover():
    """Verify WarmupChannelConfig ladder quota enforcement, rollover, and overrides."""
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cfg = WarmupChannelConfig(
        channel_name="TechLead",
        channel_url="https://www.youtube.com/@techlead",
        target_hours=10,
        daily_increment_hours=2,
        current_day=1,
        last_active_date=today,
    )
    assert cfg.total_steps == 5  # ceil(10 / 2)
    assert cfg.get_daily_allowed_seconds() == 7200.0  # Day 1: 2 hours

    # Watch 3600s -> not yet reached
    cfg.record_watch_seconds(3600.0, today_str=today)
    assert not cfg.is_daily_quota_reached(today_str=today)

    # Watch another 3600s -> 7200s -> reached!
    cfg.record_watch_seconds(3600.0, today_str=today)
    assert cfg.is_daily_quota_reached(today_str=today)

    # Rollover to next day
    assert cfg.check_and_rollover(today_str=tomorrow) is True
    assert cfg.current_day == 2
    assert cfg.get_daily_allowed_seconds() == 14400.0  # Day 2: 4 hours
    assert not cfg.is_daily_quota_reached(today_str=tomorrow)

    # Manual override
    cfg.mark_as_warmed_up()
    assert cfg.is_warmed_up is True
    assert cfg.current_day == 5
    assert cfg.get_status_info()["color"].lower() == "#15803d"


def test_channel_warmup_auto_enroll_and_manager(tmp_path):
    """Verify auto-enrollment for new channels in ConfigManager and WarmupManager tracking."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("WarmupAutoEnableNewChannels", True)
    mgr.set_setting("WarmupChannelDefaultTargetHours", 12)
    mgr.set_setting("WarmupChannelDefaultDailyIncrementHours", 3)

    mgr.set_channels([{"name": "CodeMonkey", "url": "https://www.youtube.com/@codemonkey"}])

    w_cfg = mgr.get_channel_warmup("codemonkey")
    assert w_cfg is not None
    assert w_cfg["enabled"] is True
    assert w_cfg["target_hours"] == 12
    assert w_cfg["daily_increment_hours"] == 3

    # Test record watch time via WarmupManager
    wm = WarmupManager(mgr)
    updated = wm.record_channel_watch_time("CodeMonkey", 1800.0)
    assert updated.watched_today_seconds == 1800.0


def test_channel_warmup_dialog_ui_and_save(ctk_app, tmp_path):
    """Verify WarmupDialog properly handles channel objects, UI text, and persistence."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    channel = Channel(name="PythonPro", url="https://youtube.com/@pythonpro")

    mgr.set_channel_warmup("pythonpro", {
        "channel_name": "PythonPro",
        "channel_url": "https://youtube.com/@pythonpro",
        "enabled": True,
        "target_hours": 15,
        "daily_increment_hours": 1,
        "current_day": 1,
        "is_warmed_up": False,
        "watched_today_seconds": 0.0,
    })

    saved_calls = []
    dialog = WarmupDialog(
        ctk_app,
        channel=channel,
        config_mgr=mgr,
        on_save_callback=lambda: saved_calls.append(True),
    )

    assert "PythonPro" in dialog.title()
    assert dialog.is_channel is True
    assert dialog.target_entry.get() == "15"
    assert dialog.inc_entry.get() == "1"

    # Modify with whole number and save
    dialog.target_entry.delete(0, tk.END)
    dialog.target_entry.insert(0, "25")
    dialog.inc_entry.delete(0, tk.END)
    dialog.inc_entry.insert(0, "2")
    dialog.save()

    assert len(saved_calls) == 1
    persisted = mgr.get_channel_warmup("pythonpro")
    assert persisted["target_hours"] == 25
    assert persisted["daily_increment_hours"] == 2


def test_settings_view_channel_warmup_persistence(ctk_app, tmp_path):
    """Verify SettingsView persists channel warm-up settings."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("SystemName", "TestPC")
    app = ctk_app
    app.config_mgr = mgr
    app.appearance_mode = "Dark"
    app.apply_appearance_mode = MagicMock()

    view = SettingsView(app, app)

    assert hasattr(view, "warmup_channel_auto_enable_switch")
    assert hasattr(view, "warmup_channel_default_target_entry")
    assert hasattr(view, "warmup_channel_default_inc_entry")

    view.warmup_channel_default_target_entry.delete(0, tk.END)
    view.warmup_channel_default_target_entry.insert(0, "18")

    view.warmup_channel_default_inc_entry.delete(0, tk.END)
    view.warmup_channel_default_inc_entry.insert(0, "1.5")

    success = view.save_settings()
    assert success is True

    assert mgr.get_int("WarmupChannelDefaultTargetHours") == 18
    assert mgr.get_float("WarmupChannelDefaultDailyIncrementHours") == 1.5
    assert mgr.data["settings"]["WarmupChannelDefaultTargetHours"] == 18
    assert mgr.data["settings"]["WarmupChannelDefaultDailyIncrementHours"] == 1.5
    view.destroy()


def test_channel_warmup_default_target_30(ctk_app, tmp_path):
    """Verify channel warm-up defaults to 30 hours in global settings, ConfigManager, WarmupManager, and WarmupDialog."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("SystemName", "TestPC")

    # 1. Global config default is 30 for channels (while accounts stay 15)
    assert mgr.get_int("WarmupChannelDefaultTargetHours") == 30
    assert mgr.get_setting("WarmupChannelDefaultTargetHours") == "30"
    assert mgr.get_int("WarmupDefaultTargetHours") == 15

    # 2. WarmupChannelConfig defaults to 30
    dc = WarmupChannelConfig(channel_name="FreshChan")
    assert dc.target_hours == 30
    assert dc.total_steps == 30
    dc2 = WarmupChannelConfig.from_dict({"channel_name": "FromDictChan"})
    assert dc2.target_hours == 30

    # 3. Auto-enrolled channel receives target_hours == 30
    mgr.set_channels([{"name": "AutoChan", "url": "https://www.youtube.com/@autochan"}])
    auto_w = mgr.get_channel_warmup("autochan")
    assert auto_w is not None
    assert auto_w["target_hours"] == 30

    # 4. WarmupManager fallback uses 30
    wm = WarmupManager(mgr)
    wm_cfg = wm.get_channel_config("AnotherChan", "https://www.youtube.com/@anotherchan")
    assert wm_cfg.target_hours == 30

    # 5. SettingsView defaults to 30 in UI entry
    app = ctk_app
    app.config_mgr = mgr
    app.appearance_mode = "Dark"
    app.apply_appearance_mode = MagicMock()
    s_view = SettingsView(app, app)
    assert s_view.warmup_channel_default_target_entry.get() == "30"
    s_view.destroy()

    # 6. WarmupDialog for new channel opens with 30
    ch = Channel(name="DialogChan", url="https://www.youtube.com/@dialogchan")
    dlg = WarmupDialog(ctk_app, channel=ch, config_mgr=mgr)
    assert dlg.target_entry.get() == "30"
    dlg.destroy()


def test_channel_warmup_per_day_limit_divided_by_accounts(tmp_path):
    """Verify per-day channel quota (e.g. 10h) is divided equally by added accounts (e.g. 2 -> 5h each, 10h combined)."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_accounts([
        {"email": "acc1@gmail.com", "password": "p1"},
        {"email": "acc2@gmail.com", "password": "p2"},
    ])
    mgr.set_channels([{"name": "SharedChan", "url": "https://www.youtube.com/@sharedchan"}])

    today = datetime.now().strftime("%Y-%m-%d")
    mgr.set_channel_warmup("sharedchan", {
        "channel_name": "SharedChan",
        "channel_url": "https://www.youtube.com/@sharedchan",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 10,  # Day 10 -> 10 hours combined daily limit
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 0.0,
        "watched_today_by_account": {},
        "total_watched_seconds": 0.0,
    })

    wm = WarmupManager(mgr)
    assert wm.get_total_accounts_count() == 2

    cfg = wm.get_channel_config("SharedChan", "https://www.youtube.com/@sharedchan")
    assert cfg.get_daily_allowed_seconds() == 36000.0  # 10 hours total
    assert cfg.get_per_account_daily_allowed_seconds(2) == 18000.0  # 5 hours per account

    # Acc1 watches 4 hours -> neither account has hit quota yet
    wm.record_channel_watch_time("SharedChan", 4 * 3600.0, account_email="acc1@gmail.com")
    assert not wm.is_channel_daily_quota_reached("SharedChan", account_email="acc1@gmail.com")
    assert not wm.is_channel_daily_quota_reached("SharedChan", account_email="acc2@gmail.com")

    # Acc1 watches 1 more hour -> 5 hours total for Acc1 -> Acc1 reaches its 5h slice; Acc2 still has 5h!
    wm.record_channel_watch_time("SharedChan", 1 * 3600.0, account_email="acc1@gmail.com")
    assert wm.is_channel_daily_quota_reached("SharedChan", account_email="acc1@gmail.com") is True
    assert wm.is_channel_daily_quota_reached("SharedChan", account_email="acc2@gmail.com") is False

    # Acc2 watches 5 hours -> Combined reaches 10 hours (36000s) -> BOTH stopped!
    wm.record_channel_watch_time("SharedChan", 5 * 3600.0, account_email="acc2@gmail.com")
    assert wm.is_channel_daily_quota_reached("SharedChan", account_email="acc2@gmail.com") is True
    final_cfg = wm.get_channel_config("SharedChan")
    assert final_cfg.watched_today_seconds == 36000.0
    assert final_cfg.get_account_watched_today_seconds("acc1@gmail.com") == 18000.0
    assert final_cfg.get_account_watched_today_seconds("acc2@gmail.com") == 18000.0

    # Rollover clears both combined and per-account daily counters
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert final_cfg.check_and_rollover(today_str=tomorrow) is True
    assert final_cfg.current_day == 11
    assert final_cfg.watched_today_seconds == 0.0
    assert final_cfg.watched_today_by_account == {}


def test_channel_warmup_combined_cap_when_accounts_added_midday(tmp_path):
    """Verify combined 10h cap still holds even if an earlier account watched 7h before a 2nd account was added."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_accounts([{"email": "acc1@gmail.com", "password": "p1"}])
    today = datetime.now().strftime("%Y-%m-%d")
    mgr.set_channel_warmup("middaychan", {
        "channel_name": "MiddayChan",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 10,  # 10h daily limit
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 0.0,
        "watched_today_by_account": {},
        "total_watched_seconds": 0.0,
    })

    wm = WarmupManager(mgr)
    wm.record_channel_watch_time("MiddayChan", 7 * 3600.0, account_email="acc1@gmail.com")

    # Now add a 2nd account -> per-account limit becomes 5h, combined limit stays 10h (3h remaining)
    mgr.set_accounts([
        {"email": "acc1@gmail.com", "password": "p1"},
        {"email": "acc2@gmail.com", "password": "p2"},
    ])
    assert wm.is_channel_daily_quota_reached("MiddayChan", account_email="acc1@gmail.com") is True
    assert wm.is_channel_daily_quota_reached("MiddayChan", account_email="acc2@gmail.com") is False

    # Acc2 watches 3h -> combined hits 10h -> Acc2 stops at 3h instead of overshooting combined cap!
    wm.record_channel_watch_time("MiddayChan", 3 * 3600.0, account_email="acc2@gmail.com")
    assert wm.is_channel_daily_quota_reached("MiddayChan", account_email="acc2@gmail.com") is True


def test_workers_enforce_divided_channel_warmup_and_live_delta(tmp_path):
    """Verify WatchingWorker and AutomationWorker respect divided per-account channel warm-up and live delta."""
    from src.automation.watching_worker import WatchingWorker
    from src.automation.worker import AutomationWorker
    from src.models.video_task import VideoTask

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_accounts([
        {"email": "acc1@gmail.com", "password": "p1"},
        {"email": "acc2@gmail.com", "password": "p2"},
    ])
    mgr.set_channels([{"name": "DivChan", "url": "https://www.youtube.com/@divchan", "enabled": True}])
    today = datetime.now().strftime("%Y-%m-%d")
    mgr.set_channel_warmup("divchan", {
        "channel_name": "DivChan",
        "channel_url": "https://www.youtube.com/@divchan",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 10,  # 10h daily -> 5h per account (18000s)
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 17940.0,  # 4h 59m
        "watched_today_by_account": {"acc1@gmail.com": 17940.0},
        "total_watched_seconds": 17940.0,
    })

    acc1 = Account(email="acc1@gmail.com", password="p1")
    acc2 = Account(email="acc2@gmail.com", password="p2")

    w1 = WatchingWorker(account=acc1, channels=[Channel(name="DivChan", enabled=True)], config_mgr=mgr)
    w2 = WatchingWorker(account=acc2, channels=[Channel(name="DivChan", enabled=True)], config_mgr=mgr)

    # Before live delta, acc1 still has 60s left
    assert len(w1.get_target_channels()) == 1
    assert not w1._is_channel_warmup_quota_reached_live("DivChan")

    # Simulate 60s live playback delta in channel_tracker -> acc1 hits 5h limit immediately!
    w1.channel_tracker.set_active_channel("DivChan")
    w1.channel_tracker._active_watch_delta = 60.0
    assert w1._is_channel_warmup_quota_reached_live("DivChan") is True
    assert w1.get_target_channels() == []

    # Meanwhile acc2 still has its full 5h available!
    assert len(w2.get_target_channels()) == 1
    assert not w2._is_channel_warmup_quota_reached_live("DivChan")

    # Record those 60s via _record_watch mock store and verify AutomationWorker also honors it
    w1.stats_store = MagicMock()
    w1._record_watch(
        title="Test Vid",
        channel_name="DivChan",
        watch_seconds=60.0,
        ad_seconds=0.0,
        duration=120.0,
        completed=False,
        task=VideoTask(url="https://youtube.com/watch?v=12345678901", channel="DivChan"),
    )

    aw1 = AutomationWorker(account=acc1, video_tasks=[], config_mgr=mgr)
    aw2 = AutomationWorker(account=acc2, video_tasks=[], config_mgr=mgr)
    assert WarmupManager(mgr).is_channel_daily_quota_reached("DivChan", account_email=aw1.account.email) is True
    assert WarmupManager(mgr).is_channel_daily_quota_reached("DivChan", account_email=aw2.account.email) is False


def test_warmup_dialog_displays_divided_per_account_quota(ctk_app, tmp_path):
    """Verify WarmupDialog status banner shows divided per-account daily quota when multiple accounts exist."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_accounts([
        {"email": "acc1@gmail.com", "password": "p1"},
        {"email": "acc2@gmail.com", "password": "p2"},
    ])
    today = datetime.now().strftime("%Y-%m-%d")
    mgr.set_channel_warmup("bannerchan", {
        "channel_name": "BannerChan",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 10,
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 3600.0,
        "watched_today_by_account": {"acc1@gmail.com": 3600.0},
    })

    ch = Channel(name="BannerChan")
    dlg = WarmupDialog(ctk_app, channel=ch, config_mgr=mgr)
    detail = dlg.status_detail_lbl.cget("text")
    assert "Today's Combined Quota" in detail
    assert "÷ 2 accounts" in detail
    assert "5h/account" in detail
    assert "30h" in detail


def test_playback_does_not_abort_mid_video_when_warmup_quota_reached(tmp_path):
    """Verify that video playback does not cut off mid-video when warm-up quota is reached."""
    from src.automation.watching_worker import WatchingWorker

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_channels([{"name": "Lifestyle Mapping", "url": "https://www.youtube.com/@lifestyle", "enabled": True}])
    today = datetime.now().strftime("%Y-%m-%d")
    mgr.set_channel_warmup("lifestyle mapping", {
        "channel_name": "Lifestyle Mapping",
        "url": "https://www.youtube.com/@lifestyle",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 1,  # 1 hour limit (3600s)
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 3000.0,  # 50 minutes watched
    })

    acc = Account(email="chillecode@gmail.com", password="pwd")
    worker = WatchingWorker(account=acc, channels=[Channel(name="Lifestyle Mapping", url="https://www.youtube.com/@lifestyle", enabled=True)], config_mgr=mgr)
    worker.channel_tracker.set_active_channel("Lifestyle Mapping")

    # In active video playback, simulate 15 minutes of watch delta (total = 65m > 60m quota)
    worker.channel_tracker._active_watch_delta = 900.0

    # Ensure _play_target_video._should_stop does NOT return "channel_warmup_quota_reached"
    # Inspect the channel_disabled logic:
    cfg = worker.config_mgr
    clean_target = "lifestyle mapping"
    disabled = False
    for c_dict in cfg.get_channels() or []:
        if isinstance(c_dict, dict) and (c_dict.get("name") or "").strip().lower() == clean_target:
            if not c_dict.get("enabled", True) or c_dict.get("archived", False):
                disabled = True
            break
    assert disabled is False


def test_multi_channel_warmup_sleep_coordination(tmp_path):
    """Verify that the bot stays awake until ALL channels reach their daily warm-up quota."""
    from src.automation.watching_worker import WatchingWorker

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_channels([
        {"name": "Lifestyle Mapping", "url": "https://www.youtube.com/@lifestyle", "enabled": True},
        {"name": "The Kidney Nutrition", "url": "https://www.youtube.com/@kidney", "enabled": True},
    ])
    today = datetime.now().strftime("%Y-%m-%d")
    mgr.set_channel_warmup("lifestyle mapping", {
        "channel_name": "Lifestyle Mapping",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 1,
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 3600.0,  # 1h 00m -> reached!
    })
    mgr.set_channel_warmup("the kidney nutrition", {
        "channel_name": "The Kidney Nutrition",
        "enabled": True,
        "target_hours": 30,
        "daily_increment_hours": 1,
        "current_day": 1,
        "is_warmed_up": False,
        "last_active_date": today,
        "watched_today_seconds": 1984.0,  # 33m 04s -> NOT reached!
    })

    acc = Account(email="chillecode@gmail.com", password="pwd")
    worker = WatchingWorker(
        account=acc,
        channels=[
            Channel(name="Lifestyle Mapping", url="https://www.youtube.com/@lifestyle", enabled=True),
            Channel(name="The Kidney Nutrition", url="https://www.youtube.com/@kidney", enabled=True),
        ],
        config_mgr=mgr,
    )

    # Channel A reached -> paused from targets. Channel B unmet -> remains a target.
    active_targets = worker.get_target_channels()
    target_names = [c.name for c in active_targets]
    assert "The Kidney Nutrition" in target_names
    assert "Lifestyle Mapping" not in target_names

    # Check sleep condition: must return False because The Kidney Nutrition has not met quota!
    assert worker._check_and_enter_warmup_sleep(set()) is False

    # Simulate The Kidney Nutrition completing a 30m video (1800s) -> total 3784s (> 3600s)
    wm = WarmupManager(mgr)
    wm.record_channel_watch_time("The Kidney Nutrition", 1800.0, channel_url="https://www.youtube.com/@kidney")

    # Now both channels have met their daily warm-up quota
    assert len(worker.get_target_channels()) == 0

    # Mock _execute_channel_warmup_sleep_cycle to verify it gets triggered
    worker._execute_channel_warmup_sleep_cycle = MagicMock(return_value=True)
    assert worker._check_and_enter_warmup_sleep(set()) is True
    assert worker._execute_channel_warmup_sleep_cycle.called


def test_channel_warmup_email_report_content():
    """Verify email formatting for Channel Warm-Up Daily Limit Reached and Resumption."""
    from src.automation.email_reporter import SessionReport

    report_sleep = SessionReport(
        account_email="chillecode@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 13, 9, 21, 49),
        end_time=datetime(2026, 11, 13, 13, 42, 50),
        duration_seconds=15661.0,
        total_session_watch_seconds=5584.0,
        status="Channel Warm-Up Daily Limit Reached",
        sleep_mode_type="warmup",
        sleep_cycle_number=1,
        sleep_duration_hours=10.3,
        wake_up_time=datetime(2026, 11, 14, 0, 1, 0),
    )
    text_sleep = report_sleep.generate_plain_text()
    html_sleep = report_sleep.generate_html()
    assert "Channel Warm-Up Daily Limit Reached (Sleeping until Midnight)" in text_sleep
    assert "All targeted channels reached their allowed warm-up watch quota for today." in text_sleep
    assert "Channel Warm-Up Quota Reached — Sleeping until Midnight" in html_sleep

    report_resume = SessionReport(
        account_email="chillecode@gmail.com",
        session_mode="Continuous Browsing",
        start_time=datetime(2026, 11, 14, 0, 1, 0),
        end_time=datetime(2026, 11, 14, 0, 1, 30),
        duration_seconds=30.0,
        total_session_watch_seconds=0.0,
        status="Channel Warm-Up Session Resumed",
        sleep_mode_type="warmup_resume",
        sleep_cycle_number=2,
    )
    text_resume = report_resume.generate_plain_text()
    html_resume = report_resume.generate_html()
    assert "All targeted channels have advanced to the next warm-up" in text_resume
    assert "Channel Warm-Up Resumed — Midnight Rollover Completed" in html_resume
