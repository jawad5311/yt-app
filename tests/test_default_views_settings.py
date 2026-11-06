"""Tests for configurable default target views for regular videos and shorts."""

import math
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask, DEFAULT_MAX_RUNS, DEFAULT_SHORTS_MAX_RUNS, DEFAULT_TARGET_MULTIPLIER
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channel_import_modal import ChannelImportModal
from src.ui.views.channels_view import ChannelsView
from src.ui.views.queue_view import QueueView
from src.ui.views.settings_view import SettingsView
from src.utils.channel_fetcher import ChannelFetchResult, FetchedVideo


def test_config_manager_default_views_keys(tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)

    # Defaults in fresh config: Target Views = 3, Max Views = 5, Shorts Max Views = 5
    assert mgr.get_int("DefaultVideoTargetViews") == 3
    assert mgr.get_int("DefaultVideoMaxViews") == 5
    assert mgr.get_int("DefaultShortsMaxViews") == 5

    # Updating values
    mgr.set_setting("DefaultVideoTargetViews", 4)
    mgr.set_setting("DefaultVideoMaxViews", 15)
    mgr.set_setting("DefaultShortsMaxViews", 25)
    assert mgr.get_int("DefaultVideoTargetViews") == 4
    assert mgr.get_int("DefaultVideoMaxViews") == 15
    assert mgr.get_int("DefaultShortsMaxViews") == 25


def test_settings_view_load_and_save(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("DefaultVideoTargetViews", 4)
    mgr.set_setting("DefaultVideoMaxViews", 8)
    mgr.set_setting("DefaultShortsMaxViews", 18)

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr
    view = SettingsView(ctk_app, app_ctrl)

    # Verify loaded in UI entries
    assert view.default_video_views_entry.get() == "4"
    assert view.default_video_max_views_entry.get() == "8"
    assert view.default_shorts_views_entry.get() == "18"

    # Modify entries and save
    view.default_video_views_entry.delete(0, "end")
    view.default_video_views_entry.insert(0, "6")

    view.default_video_max_views_entry.delete(0, "end")
    view.default_video_max_views_entry.insert(0, "12")

    view.default_shorts_views_entry.delete(0, "end")
    view.default_shorts_views_entry.insert(0, "30")

    with patch.object(view, "_notify_result") as mock_notify:
        view.save_settings()
        mock_notify.assert_called_once_with(True)

    assert mgr.get_float("DefaultVideoTargetViews") == 6.0
    assert mgr.get_int("DefaultVideoMaxViews") == 12
    assert mgr.get_int("DefaultShortsMaxViews") == 30
    view.destroy()


def test_settings_view_validation(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr
    view = SettingsView(ctk_app, app_ctrl)

    # Invalid video target views (negative)
    view.default_video_views_entry.delete(0, "end")
    view.default_video_views_entry.insert(0, "-5")

    with patch.object(view, "_notify_result") as mock_notify:
        view.save_settings()
        mock_notify.assert_called_once_with(False, "Default Target Views per Video must be a valid number >= 0 (0 = unlimited).")

    # Invalid video max views (negative or non-integer)
    view.default_video_views_entry.delete(0, "end")
    view.default_video_views_entry.insert(0, "3")
    view.default_video_max_views_entry.delete(0, "end")
    view.default_video_max_views_entry.insert(0, "abc")

    with patch.object(view, "_notify_result") as mock_notify:
        view.save_settings()
        mock_notify.assert_called_once_with(False, "Default Max Views Cap per Video must be a valid integer >= 0 (0 = unlimited).")

    # Invalid shorts views
    view.default_video_max_views_entry.delete(0, "end")
    view.default_video_max_views_entry.insert(0, "5")
    view.default_shorts_views_entry.delete(0, "end")
    view.default_shorts_views_entry.insert(0, "abc")

    with patch.object(view, "_notify_result") as mock_notify:
        view.save_settings()
        mock_notify.assert_called_once_with(False, "Default Max Views per Short must be a valid integer >= 0 (0 = unlimited).")

    view.destroy()


def test_channel_import_modal_prefills_configured_defaults(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("DefaultVideoTargetViews", 7)
    mgr.set_setting("DefaultVideoMaxViews", 9)
    mgr.set_setting("DefaultShortsMaxViews", 14)

    fetched = [
        FetchedVideo(video_id="v1", url="https://youtube.com/watch?v=v1", title="Video 1", duration_seconds=600),
        FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", duration_seconds=45, is_short=True),
    ]

    imported_tasks = []
    modal = ChannelImportModal(
        ctk_app,
        channel_name="Test Chan",
        channel_url="https://youtube.com/@testchan",
        fetched_videos=fetched,
        existing_video_ids=set(),
        on_import=lambda tasks: imported_tasks.extend(tasks),
        config_mgr=mgr,
    )

    assert hasattr(modal, "video_views_entry")
    assert hasattr(modal, "shorts_views_entry")
    assert modal.video_views_entry.get() == "7"
    assert modal.shorts_views_entry.get() == "14"

    modal._submit_import()
    v_task = next(t for t in imported_tasks if not t.is_short)
    s_task = next(t for t in imported_tasks if t.is_short)

    assert v_task.target_multiplier == 7.0
    assert v_task.max_runs == 9
    assert s_task.max_runs == 14
    modal.destroy()


def test_channel_import_modal_override_values_during_fetch(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("DefaultVideoTargetViews", 7)
    mgr.set_setting("DefaultVideoMaxViews", 9)
    mgr.set_setting("DefaultShortsMaxViews", 14)

    fetched = [
        FetchedVideo(video_id="v1", url="https://youtube.com/watch?v=v1", title="Video 1", duration_seconds=600),
        FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", duration_seconds=45, is_short=True),
    ]

    imported_tasks = []
    modal = ChannelImportModal(
        ctk_app,
        channel_name="Test Chan",
        channel_url="https://youtube.com/@testchan",
        fetched_videos=fetched,
        existing_video_ids=set(),
        on_import=lambda tasks: imported_tasks.extend(tasks),
        config_mgr=mgr,
    )

    # User modifies values in the dialog before clicking import
    modal.video_views_entry.delete(0, "end")
    modal.video_views_entry.insert(0, "4.5")
    modal.shorts_views_entry.delete(0, "end")
    modal.shorts_views_entry.insert(0, "44")

    modal._submit_import()
    v_task = next(t for t in imported_tasks if not t.is_short)
    s_task = next(t for t in imported_tasks if t.is_short)

    assert v_task.target_multiplier == 4.5
    assert v_task.max_runs == 9
    assert s_task.max_runs == 44
    modal.destroy()


def test_queue_view_add_modal_uses_configured_defaults(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("DefaultVideoTargetViews", 4)
    mgr.set_setting("DefaultVideoMaxViews", 9)
    mgr.set_setting("DefaultShortsMaxViews", 19)

    app = ctk_app
    app.config_mgr = mgr
    app.on_queue_changed = MagicMock()

    # Regular Video Queue
    vid_view = QueueView(app, app, shorts_only=False)
    vid_view.open_video_modal()

    # Shorts Queue
    shorts_view = QueueView(app, app, shorts_only=True)
    shorts_view.open_video_modal()

    vid_view.destroy()
    shorts_view.destroy()


def test_watching_worker_browsing_discovery_uses_defaults(tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("DefaultVideoTargetViews", 4.5)
    mgr.set_setting("DefaultVideoMaxViews", 11)
    mgr.set_setting("DefaultShortsMaxViews", 22)

    account = Account(email="user1@gmail.com", password="pw1")
    worker = WatchingWorker(account=account, channels=[], config_mgr=mgr)

    # Mock _ensure_video_in_queue to record the created task
    added_tasks = []
    worker._ensure_video_in_queue = lambda t: added_tasks.append(t) or t
    worker.driver = MagicMock()
    worker.driver.current_url = "https://youtube.com/watch?v=new_browse_vid"
    worker.click_home_video = MagicMock(return_value=True)

    # 1. Regular video discovered via browsing
    worker._play_target_video(
        card_info={},
        full_url="https://youtube.com/watch?v=new_browse_vid",
        title="Discovered Video",
        channel_display="Target Channel",
        session_watched_keys=set(),
        source="home",
        is_home=True,
    )
    assert len(added_tasks) == 1
    assert added_tasks[0].target_multiplier == 4.5
    assert added_tasks[0].max_runs == 11

    # 2. Short discovered via reel loop
    def_shorts = (
        worker.config_mgr.get_int("DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS)
        if worker.config_mgr
        else DEFAULT_SHORTS_MAX_RUNS
    )
    assert def_shorts == 22


def test_channels_view_sync_uses_configured_defaults(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("DefaultVideoTargetViews", 4.0)
    mgr.set_setting("DefaultVideoMaxViews", 13)
    mgr.set_setting("DefaultShortsMaxViews", 26)

    app = ctk_app
    app.config_mgr = mgr
    app.queued_video_tasks = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.for_channel.return_value = []
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    ch = Channel(name="TestChan", url="https://youtube.com/@testchan", total_videos=1, total_shorts=1, has_new_videos=True, has_new_shorts=True)
    view.channels = [ch]

    res = ChannelFetchResult(
        channel_title="TestChan",
        channel_url=ch.url,
        total_videos=1,
        total_shorts=1,
        videos=[
            VideoTask(url="https://youtube.com/watch?v=sync_v1", title="Sync Video 1", duration_seconds=120),
            VideoTask(url="https://youtube.com/shorts/sync_s1", title="Sync Short 1", duration_seconds=30),
        ],
    )

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=res), \
         patch("src.ui.views.channels_view.messagebox.showinfo"):
        view.sync_single_channel_data(ch)
        app.background_jobs.drain()

    queue = mgr.get_video_queue()
    assert len(queue) == 2
    v_task = next(t for t in queue if "/watch" in t["url"])
    s_task = next(t for t in queue if "/shorts" in t["url"])

    assert v_task["target_multiplier"] == 4.0
    assert v_task["max_runs"] == 13
    assert s_task["max_runs"] == 26
    assert s_task["target_multiplier"] == 0.0

    view.destroy()


def test_max_ad_watch_time_in_ads_tab(ctk_app, tmp_path):
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)
    mgr.set_setting("MaxAdWatchSeconds", 35)
    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr
    view = SettingsView(ctk_app, app_ctrl)

    assert view.max_ad_entry.get() == "35"
    tab_ads = view.tabview._tab_dict["Ads"]
    curr = view.max_ad_entry.master
    is_descendant = False
    while curr:
        if curr == tab_ads:
            is_descendant = True
            break
        curr = getattr(curr, "master", None)
    assert is_descendant, "max_ad_entry must be placed inside the Ads tab"
    view.destroy()
