import copy
import json
import os
import queue
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.automation.driver import close_driver, setup_driver
from src.automation.interactivity import HumanInteractivitySimulator, set_video_quality
from src.automation.profile_manager import resolve_profile_dir
from src.automation.worker import AutomationWorker
from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.views.settings_view import QUALITY_OPTIONS


# ---------------------------------------------------------------------------
# Issue 1: Automated likes and comments enabled and configurable
# ---------------------------------------------------------------------------

def test_issue_1_likes_enabled_in_defaults_and_persistable(tmp_path):
    settings = ConfigManager.DEFAULT_DATA["settings"]
    assert settings["LikeComments"] is True
    assert settings["LikeVideo"] is True
    assert "LikeComments" not in ConfigManager.REMOVED_SETTINGS
    assert "LikeVideo" not in ConfigManager.REMOVED_SETTINGS

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(json.dumps({
        "settings": {
            "LikeComments": False,
            "LikeVideo": False,
        },
        "videos": [],
        "channels": [],
    }), encoding="utf-8")

    mgr = ConfigManager(config_file=cfg_file)
    saved_settings = json.loads(cfg_file.read_text(encoding="utf-8"))["settings"]
    assert saved_settings["LikeComments"] is False
    assert saved_settings["LikeVideo"] is False


def test_issue_1_workers_respect_like_settings(tmp_path):
    mgr = ConfigManager(config_file=tmp_path / "settings.json")
    acc = Account(email="test@gmail.com", password="pwd")
    task = VideoTask(url="https://youtube.com/watch?v=12345678901")

    # Default is True
    worker = AutomationWorker(account=acc, video_tasks=[task], config_mgr=mgr)
    enabled, cfg = worker._interactivity_settings()
    assert cfg["like_comments"] is True
    assert cfg["like_video"] is True

    w_worker = WatchingWorker(account=acc, channels=[], config_mgr=mgr)
    w_enabled, w_cfg = w_worker._interactivity_settings()
    assert w_cfg["like_comments"] is True
    assert w_cfg["like_video"] is True

    sim = HumanInteractivitySimulator(like_comments=True, like_video=True)
    assert sim.like_comments is True
    assert sim.like_video is True

    # When toggled off in settings
    mgr.set_setting("LikeComments", False)
    mgr.set_setting("LikeVideo", False)
    _, cfg_off = worker._interactivity_settings()
    assert cfg_off["like_comments"] is False
    assert cfg_off["like_video"] is False


# ---------------------------------------------------------------------------
# Issue 2: Channel imports do not overwrite newer queue changes
# ---------------------------------------------------------------------------

def test_issue_2_channel_import_atomic_append(tmp_path):
    mgr = ConfigManager(config_file=tmp_path / "settings.json")
    mgr.set_video_queue([{"url": "https://youtube.com/watch?v=initial0001", "title": "Initial"}])

    # Simulate modal opened and user concurrently adding task2 to queue
    mgr.append_video_tasks([{"url": "https://youtube.com/watch?v=concurrent2", "title": "Concurrent"}])

    # Modal finishes and imports task3
    imported = [{"url": "https://youtube.com/watch?v=imported003", "title": "Imported"}]
    updated = mgr.append_video_tasks(imported)

    urls = [v["url"] for v in updated]
    assert "https://youtube.com/watch?v=initial0001" in urls
    assert "https://youtube.com/watch?v=concurrent2" in urls
    assert "https://youtube.com/watch?v=imported003" in urls


def test_issue_2_modal_does_not_destroy_on_save_error():
    from src.ui.views.channel_import_modal import ChannelImportModal

    modal = ChannelImportModal.__new__(ChannelImportModal)
    modal.fetched_videos = [MagicMock(video_id="vid1", url="https://yt.com/watch?v=vid1", title="Title", duration_seconds=60, duration_text="1:00")]
    modal.selected_vars = {"vid1": MagicMock(get=lambda: True)}
    modal.channel_name = "Test"
    modal.duration_option = MagicMock(get=lambda: "Full Video")
    modal.destroy = MagicMock()

    def fail_import(tasks):
        raise OSError("Permission denied")

    modal.on_import = fail_import

    with patch("tkinter.messagebox.showerror") as mock_err:
        modal._submit_import()
        assert mock_err.called
        assert not modal.destroy.called


# ---------------------------------------------------------------------------
# Issue 3: Profile collision protection in setup_driver
# ---------------------------------------------------------------------------

def test_issue_3_setup_driver_detects_collision(tmp_path):
    acc1 = Account(email="john.doe@gmail.com", password="pw1")
    acc2 = Account(email="johndoe@gmail.com", password="pw2")

    # With collision protection against known accounts, they receive distinct safe directories
    p1 = resolve_profile_dir(acc1, known_accounts=[acc1, acc2], runtime_dir=tmp_path)
    p2 = resolve_profile_dir(acc2, known_accounts=[acc1, acc2], runtime_dir=tmp_path)
    assert p1 != p2


# ---------------------------------------------------------------------------
# Issue 4: Browser cleanup terminates process even if driver.quit() raises
# ---------------------------------------------------------------------------

def test_issue_4_close_driver_cleans_up_on_quit_exception():
    mock_driver = MagicMock()
    mock_driver.browser_pid = 99999
    mock_driver.quit.side_effect = RuntimeError("Chrome disconnected")
    mock_lock = MagicMock()
    mock_driver._profile_lock = mock_lock

    with patch("os.name", "nt"), patch("subprocess.run") as mock_run:
        close_driver(mock_driver)
        assert mock_run.called
        assert "/PID" in mock_run.call_args[0][0]
        assert "99999" in mock_run.call_args[0][0]
        assert mock_lock.release.called


# ---------------------------------------------------------------------------
# Issue 5: Settings write failure does not leave Start Browsing stuck running
# ---------------------------------------------------------------------------

def test_issue_5_start_watching_settings_write_failure_resets_state():
    from src.ui.app import YTBotApp

    app = YTBotApp.__new__(YTBotApp)
    app.is_running = False
    app.current_mode = "idle"
    app.loaded_accounts = [Account(email="test@gmail.com", password="pwd")]
    app.log_queue = queue.Queue()
    mock_dashboard = MagicMock()
    mock_dashboard.get_browsing_limit.return_value = (1, False)
    app.views = {"dashboard": mock_dashboard, "channels": MagicMock()}
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = [{"name": "Ch", "url": "https://yt.com/@ch", "enabled": True, "archived": False}]
    app.config_mgr.update_settings.side_effect = OSError("Disk full")

    app.start_watching()

    assert app.is_running is False
    assert app.current_mode == "idle"
    assert app.views["dashboard"].set_status.called
    assert "Failed to save browsing settings" in app.views["dashboard"].set_status.call_args[0][0]


# ---------------------------------------------------------------------------
# Issue 6: Atomic queue updates across workers
# ---------------------------------------------------------------------------

def test_issue_6_get_video_queue_deep_copy(tmp_path):
    mgr = ConfigManager(config_file=tmp_path / "settings.json")
    mgr.set_video_queue([{"url": "https://youtube.com/watch?v=test0000001", "title": "Test"}])

    q = mgr.get_video_queue()
    q[0]["title"] = "Modified Locally"

    fresh_q = mgr.get_video_queue()
    assert fresh_q[0]["title"] == "Test"


def test_issue_6_concurrent_queue_appends(tmp_path):
    mgr = ConfigManager(config_file=tmp_path / "settings.json")
    mgr.set_video_queue([])

    def worker_add(idx):
        mgr.update_video_queue(lambda q: q.append({"url": f"https://youtube.com/watch?v=vid{idx:05d}"}))

    threads = [threading.Thread(target=worker_add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_queue = mgr.get_video_queue()
    assert len(final_queue) == 20


# ---------------------------------------------------------------------------
# Issue 7: Quality documentation matches implementation
# ---------------------------------------------------------------------------

def test_issue_7_quality_options_and_docs():
    assert QUALITY_OPTIONS == ["360p", "480p", "Random (360p / 480p)"]

    repo_root = Path(__file__).resolve().parent.parent
    features = (repo_root / "FEATURES.md").read_text(encoding="utf-8")
    todo = (repo_root / "TODO.md").read_text(encoding="utf-8")

    assert "720p" not in features
    assert "720p" not in todo


# ---------------------------------------------------------------------------
# Issue 8: Untracked scratch files ignored
# ---------------------------------------------------------------------------

def test_issue_8_scratch_ignored():
    repo_root = Path(__file__).resolve().parent.parent
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert "scratch/" in gitignore

    spec = (repo_root / "yt_tool.spec").read_text(encoding="utf-8")
    assert '"scratch"' in spec

def test_legacy_profile_with_dictionary_accounts_no_attribute_error(tmp_path):
    acc = Account(email="single.user@gmail.com", password="pw")
    legacy_dir = tmp_path / acc.get_legacy_profile_dir_name()
    legacy_dir.mkdir(parents=True, exist_ok=True)

    # ConfigManager.get_accounts() returns dictionaries
    dict_accounts = [{"email": "single.user@gmail.com", "password": "pw"}]

    # Must resolve legacy profile without AttributeError: 'dict' object has no attribute 'get_legacy_profile_dir_name'
    resolved = resolve_profile_dir(acc, known_accounts=dict_accounts, runtime_dir=tmp_path)
    assert resolved == legacy_dir


def test_legacy_profile_collision_with_dictionary_accounts(tmp_path):
    acc1 = Account(email="john.doe@gmail.com", password="pw1")
    acc2 = Account(email="john_doe@gmail.com", password="pw2")

    # If legacy directory exists and both accounts are dicts, it must not crash and assign safe dir
    legacy_dir = tmp_path / acc1.get_legacy_profile_dir_name()
    legacy_dir.mkdir(parents=True, exist_ok=True)

    dict_accounts = [
        {"email": "john.doe@gmail.com", "password": "pw1"},
        {"email": "john_doe@gmail.com", "password": "pw2"},
    ]

    resolved = resolve_profile_dir(acc1, known_accounts=dict_accounts, runtime_dir=tmp_path)
    assert resolved != legacy_dir
    assert resolved == (tmp_path / acc1.get_profile_dir_name()).resolve()


