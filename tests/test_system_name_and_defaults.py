import json
from unittest.mock import MagicMock, patch
import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView
from src.ui.views.system_name_modal import SystemNamePromptModal


def test_config_manager_updated_defaults():
    """Verify that ConfigManager DEFAULT_DATA has all requested default settings."""
    defaults = ConfigManager.DEFAULT_DATA["settings"]
    assert defaults["VideoCooldownHours"] == 18
    assert defaults["TargetShortsPerCycle"] == 10
    assert defaults["NonTargetReelsLimit"] == 12
    assert defaults["TotalNonTargetShortsLimit"] == 33
    assert defaults["NonTargetShortsLimit"] == 33
    assert defaults["DefaultShortsMaxViews"] == 5
    assert defaults["BlockAds"] is True
    assert defaults["SkipAds"] is True
    assert defaults["MaxAdWatchSeconds"] == 9


def test_fresh_settings_disk_persistence(tmp_path):
    """Verify fresh settings file gets populated with updated defaults on initial save."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=settings_file)
    assert mgr.get_float("VideoCooldownHours") == 18.0
    assert mgr.get_int("TargetShortsPerCycle") == 10
    assert mgr.get_int("NonTargetReelsLimit") == 12
    assert mgr.get_int("TotalNonTargetShortsLimit") == 33
    assert mgr.get_int("DefaultShortsMaxViews") == 5
    assert mgr.get_bool("BlockAds") is True
    assert mgr.get_bool("SkipAds") is True
    assert mgr.get_int("MaxAdWatchSeconds") == 9

    disk = json.loads(settings_file.read_text(encoding="utf-8"))["settings"]
    assert disk["VideoCooldownHours"] == 18
    assert disk["TargetShortsPerCycle"] == 10
    assert disk["NonTargetReelsLimit"] == 12
    assert disk["TotalNonTargetShortsLimit"] == 33
    assert disk["DefaultShortsMaxViews"] == 5


def test_settings_view_mandatory_system_name_validation(ctk_app, tmp_path):
    """SettingsView must reject empty/whitespace SystemName and accept non-empty name."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=settings_file)
    mgr.set_setting("SystemName", "Init-PC")

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr

    view = SettingsView(ctk_app, app_ctrl)
    assert view.system_name_entry.get() == "Init-PC"

    # Attempt to save with blank system name
    view.system_name_entry.delete(0, "end")
    view.system_name_entry.insert(0, "   ")

    notified = []
    view._notify_result = lambda success, msg=None: notified.append((success, msg))

    view.save_settings()
    assert len(notified) == 1
    assert notified[0][0] is False
    assert "System Name is required" in notified[0][1]
    # Config remains unchanged
    assert mgr.get_setting("SystemName") == "Init-PC"

    # Save with valid system name
    view.system_name_entry.delete(0, "end")
    view.system_name_entry.insert(0, "Worker-Desk-09")
    view.save_settings()
    assert notified[-1][0] is True
    assert mgr.get_setting("SystemName") == "Worker-Desk-09"
    view.destroy()


def test_system_name_prompt_modal_save_and_skip(ctk_app):
    """SystemNamePromptModal enforces non-empty input on save and allows skipping."""
    saved_names = []

    # 1. Test empty validation
    modal = SystemNamePromptModal(ctk_app, current_name="", on_save=lambda name: saved_names.append(name))
    assert modal.name_entry.get() == ""
    modal._on_save()
    assert "cannot be empty" in modal.error_label.cget("text")
    assert len(saved_names) == 0

    # 2. Test saving valid name
    modal.name_entry.insert(0, "Rig-Alpha-01")
    modal._on_save()
    assert modal.saved_name == "Rig-Alpha-01"
    assert saved_names == ["Rig-Alpha-01"]

    # 3. Test skipping
    modal_skip = SystemNamePromptModal(ctk_app, current_name="", on_save=lambda name: saved_names.append(name))
    modal_skip._on_skip()
    assert modal_skip.skipped is True
    assert modal_skip.saved_name is None


def test_app_startup_prompt_system_name_logic(ctk_app, tmp_path):
    """YTBotApp._check_and_prompt_system_name triggers modal when SystemName is empty."""
    from src.ui.app import YTBotApp

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=settings_file)
    mgr.set_setting("SystemName", "")

    # Mock YTBotApp instance without running full mainloop
    app = MagicMock(spec=YTBotApp)
    app.config_mgr = mgr
    app.show_system_name_prompt_modal = MagicMock()
    app.views = {}

    # Call method directly
    YTBotApp._check_and_prompt_system_name(app)
    app.show_system_name_prompt_modal.assert_called_once_with(current_name="")

    # When SystemName is configured, it should not prompt unless forced
    mgr.set_setting("SystemName", "MyLaptop")
    app.show_system_name_prompt_modal.reset_mock()
    YTBotApp._check_and_prompt_system_name(app, force=False)
    app.show_system_name_prompt_modal.assert_not_called()

    # When forced, it prompts even if configured
    YTBotApp._check_and_prompt_system_name(app, force=True)
    app.show_system_name_prompt_modal.assert_called_once_with(current_name="MyLaptop")


def test_watching_worker_defaults_and_fallbacks():
    """WatchingWorker uses 18h cooldown, 33 total non-target shorts, 12 reels limit, and 9s ad watch by default."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.config_mgr = None

    # Cooldown fallback is 18.0
    assert worker._get_cooldown_hours() == 18.0

    # Ad settings default is (True, 9)
    worker.config_mgr = ConfigManager.__new__(ConfigManager)
    worker.config_mgr._lock = MagicMock()
    worker.config_mgr.data = {"settings": {}}
    skip_ads, max_ad_seconds = worker._ad_settings()
    assert skip_ads is True
    assert max_ad_seconds == 9
