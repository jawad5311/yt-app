import json
from unittest.mock import MagicMock, patch
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


def test_ad_settings_defaults_in_config_manager_definition():
    """ConfigManager DEFAULT_DATA must have BlockAds=True, SkipAds=True, and MaxAdWatchSeconds=9."""
    defaults = ConfigManager.DEFAULT_DATA["settings"]
    assert defaults["BlockAds"] is True
    assert defaults["SkipAds"] is True
    assert defaults["MaxAdWatchSeconds"] == 9


def test_fresh_clone_creates_ad_settings_with_defaults(tmp_path):
    """When a new user clones the app and starts it, settings.json is created with ad defaults enabled and 9s max watch time."""
    settings_file = tmp_path / "settings.json"
    assert not settings_file.exists()

    mgr = ConfigManager(config_file=settings_file)
    assert settings_file.exists()

    assert mgr.get_bool("SkipAds") is True
    assert mgr.get_bool("BlockAds") is True
    assert mgr.get_int("MaxAdWatchSeconds") == 9

    # Verify directly from written disk JSON
    disk_data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert disk_data["settings"]["BlockAds"] is True
    assert disk_data["settings"]["SkipAds"] is True
    assert disk_data["settings"]["MaxAdWatchSeconds"] == 9


def test_settings_view_displays_ad_defaults_on_fresh_setup(ctk_app, tmp_path):
    """SettingsView Ads tab displays Skip / Block switch ON and Max Ad Watch Time entry as 9 by default."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=settings_file)

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr

    view = SettingsView(ctk_app, app_ctrl)

    # 1. Skip / Block switch is ON by default
    assert view.skip_ads_switch.get() == 1

    # 2. Max Ad Watch Time is 9 by default
    assert view.max_ad_entry.get() == "9"

    view.destroy()


def test_workers_read_default_ad_settings(tmp_path):
    """Workers default to skip_ads=True and max_ad_seconds=9 when settings are newly initialized."""
    from src.automation.watching_worker import WatchingWorker
    from src.automation.browsing_worker import BrowsingWorker
    from src.automation.worker import AutomationWorker
    from src.models.account import Account

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=settings_file)
    account = Account(email="test@gmail.com", password="pwd")

    # WatchingWorker
    w_worker = WatchingWorker(
        account=account,
        channels=[],
        config_mgr=mgr,
        log_queue=MagicMock(),
        stop_event=MagicMock(),
    )
    skip_ads, max_ad = w_worker._ad_settings()
    assert skip_ads is True
    assert max_ad == 9

    # BrowsingWorker
    b_worker = BrowsingWorker(
        account=account,
        video_tasks=[],
        channels=[],
        config_mgr=mgr,
        log_queue=MagicMock(),
        stop_event=MagicMock(),
    )
    skip_ads, max_ad = b_worker._ad_settings()
    assert skip_ads is True
    assert max_ad == 9

    # AutomationWorker
    base_worker = AutomationWorker(
        account=account,
        video_tasks=[],
        config_mgr=mgr,
        log_queue=MagicMock(),
        stop_event=MagicMock(),
    )
    skip_ads, max_ad = base_worker._ad_settings()
    assert skip_ads is True
    assert max_ad == 9
