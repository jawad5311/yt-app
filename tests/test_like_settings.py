import json
from unittest.mock import MagicMock, patch
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView
from src.automation.worker import AutomationWorker
from src.automation.watching_worker import WatchingWorker
from src.automation.interactivity import HumanInteractivitySimulator
from src.models.account import Account
from src.models.video_task import VideoTask


def test_like_settings_in_defaults():
    settings = ConfigManager.DEFAULT_DATA["settings"]
    assert settings.get("LikeVideo") is True
    assert settings.get("LikeComments") is True
    assert "LikeVideo" not in ConfigManager.REMOVED_SETTINGS
    assert "LikeComments" not in ConfigManager.REMOVED_SETTINGS


def test_settings_view_has_like_switches_and_saves(tmp_path):
    cfg_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=cfg_file)

    view = SettingsView.__new__(SettingsView)
    view.config_mgr = mgr
    view.app_controller = MagicMock()

    # Create dummy switch widgets
    mock_like_video = MagicMock()
    mock_like_video.get.return_value = 0
    view.like_video_switch = mock_like_video

    mock_like_comments = MagicMock()
    mock_like_comments.get.return_value = 1
    view.like_comments_switch = mock_like_comments

    # Fill mandatory fields for save_settings
    view.fallback_switch = MagicMock(get=lambda: 0)
    view.skip_ads_switch = MagicMock(get=lambda: 1)
    view.threads_entry = MagicMock(get=lambda: "1")
    view.duration_menu = MagicMock(get=lambda: "Full Video")
    view.max_ad_entry = MagicMock(get=lambda: "32")
    view.full_timeout_entry = MagicMock(get=lambda: "5")
    view.stop_buffer_entry = MagicMock(get=lambda: "10")
    view.cooldown_entry = MagicMock(get=lambda: "12")
    view.loop_entry = MagicMock(get=lambda: "1")
    view.browsing_limit_entry = MagicMock(get=lambda: "1")
    view.browsing_unlimited_switch = MagicMock(get=lambda: 0)
    view.interactivity_switch = MagicMock(get=lambda: 1)
    view.scroll_switch = MagicMock(get=lambda: 1)
    view.caption_switch = MagicMock(get=lambda: 1)
    view.quality_switch = MagicMock(get=lambda: 1)
    view.quality_menu = MagicMock(get=lambda: "360p")
    view.micro_pause_switch = MagicMock(get=lambda: 1)
    view.appearance_menu = MagicMock(get=lambda: "Dark")

    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert mgr.get_bool("LikeVideo") is False
    assert mgr.get_bool("LikeComments") is True


def test_workers_and_simulator_read_like_settings(tmp_path):
    cfg_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=cfg_file)
    mgr.set_setting("LikeVideo", False)
    mgr.set_setting("LikeComments", True)

    acc = Account(email="user@gmail.com", password="pwd")
    task = VideoTask(url="https://youtube.com/watch?v=abcdef12345")

    worker = AutomationWorker(account=acc, video_tasks=[task], config_mgr=mgr)
    _, cfg = worker._interactivity_settings()
    assert cfg["like_video"] is False
    assert cfg["like_comments"] is True

    watching = WatchingWorker(account=acc, channels=[], config_mgr=mgr)
    _, w_cfg = watching._interactivity_settings()
    assert w_cfg["like_video"] is False
    assert w_cfg["like_comments"] is True

    sim = HumanInteractivitySimulator(
        like_video=w_cfg["like_video"],
        like_comments=w_cfg["like_comments"]
    )
    assert sim.like_video is False
    assert sim.like_comments is True
