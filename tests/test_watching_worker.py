"""Tests for WatchingWorker, channel matching, and Start Watching feature."""

import queue
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import (
    WatchingWorker,
    match_channel,
    normalize_channel_text,
)
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.models.watch_stats import WatchResult


class TestChannelMatching:
    def test_normalize_channel_text(self):
        assert normalize_channel_text("Mr. Whose The Boss!") == "mrwhosetheboss"
        assert normalize_channel_text("  @Veritasium  ") == "veritasium"
        assert normalize_channel_text("") == ""

    def test_exact_match(self):
        channels = [Channel(name="Veritasium", url="https://youtube.com/@veritasium")]
        match = match_channel("Veritasium", "", channels)
        assert match is not None
        assert match.name == "Veritasium"

    def test_case_insensitive_and_whitespace(self):
        channels = [Channel(name="Mrwhosetheboss", url="")]
        match = match_channel("  MRWHOSETHEBOSS  \n", "", channels)
        assert match is not None
        assert match.name == "Mrwhosetheboss"

    def test_normalized_punctuation(self):
        channels = [Channel(name="Mr. Whose The Boss", url="")]
        match = match_channel("Mr Whose The Boss", "", channels)
        assert match is not None

    def test_fuzzy_match_user_typo(self):
        # User entered "MR Whose The Bose" in channels, YouTube has "Mrwhosetheboss"
        channels = [Channel(name="MR Whose The Bose", url="")]
        match = match_channel("Mrwhosetheboss", "", channels)
        assert match is not None
        assert match.name == "MR Whose The Bose"

    def test_canonical_url_match(self):
        channels = [Channel(name="TechChannel", url="https://www.youtube.com/@mkbhd/videos")]
        match = match_channel("Marques Brownlee", "/@mkbhd", channels)
        assert match is not None
        assert match.name == "TechChannel"

    def test_disabled_or_archived_channels_ignored(self):
        channels = [
            Channel(name="DisabledChannel", url="", enabled=False),
            Channel(name="ArchivedChannel", url="", enabled=True, archived=True),
        ]
        assert match_channel("DisabledChannel", "", channels) is None
        assert match_channel("ArchivedChannel", "", channels) is None

    def test_no_match(self):
        channels = [Channel(name="PewDiePie", url="")]
        assert match_channel("T-Series", "/@tseries", channels) is None


class TestWatchingWorker:
    @pytest.fixture
    def worker(self, tmp_path):
        config_file = tmp_path / "settings.json"
        config_mgr = ConfigManager(config_file=config_file)
        stats_file = tmp_path / "stats.json"
        stats_store = StatsStore(stats_file=stats_file)

        account = Account(email="test@gmail.com", password="pwd")
        channels = [Channel(name="Test Channel", url="https://youtube.com/@testchannel")]
        log_q: queue.Queue[Any] = queue.Queue()
        stop_ev = threading.Event()
        event_q: queue.Queue[Any] = queue.Queue()

        return WatchingWorker(
            account=account,
            channels=channels,
            config_mgr=config_mgr,
            log_queue=log_q,
            stop_event=stop_ev,
            event_queue=event_q,
            stats_store=stats_store,
        )

    def test_settings_readers(self, worker):
        worker.config_mgr.set_setting("BlockAds", True)
        worker.config_mgr.set_setting("MaxAdWatchSeconds", 25)
        worker.config_mgr.set_setting("FullVideoTimeoutMinutes", 10)
        worker.config_mgr.set_setting("AutoplayEndBufferSeconds", 12)

        skip_ads, max_ad = worker._ad_settings()
        assert skip_ads is True
        assert max_ad == 25
        assert worker._full_video_timeout_seconds() == 600
        assert worker._end_buffer_seconds() == 12.0

        interactivity_enabled, cfg = worker._interactivity_settings()
        assert interactivity_enabled is True
        assert "scroll_comments" in cfg

    def test_duration_budget_parsing(self, worker):
        worker.config_mgr.set_setting("WatchtimeDuration", "Full Video")
        assert worker._watchtime_duration_seconds() is None

        worker.config_mgr.set_setting("WatchtimeDuration", "5 Minutes")
        assert worker._watchtime_duration_seconds() == 300

        worker.config_mgr.set_setting("WatchtimeDuration", "45 Seconds")
        assert worker._watchtime_duration_seconds() == 45

    def test_fetch_home_videos_script_mock(self, worker):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_items = [
            {"index": i, "title": f"Video {i}", "href": f"/watch?v={i}", "channel_name": f"Ch {i}", "channel_url": ""}
            for i in range(25)
        ]
        mock_driver.execute_script.return_value = mock_items
        worker.driver = mock_driver

        results = worker.fetch_home_videos(target_count=20)
        assert len(results) == 20
        assert results[0]["title"] == "Video 0"

    def test_fetch_recommended_videos_script_mock(self, worker):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/watch?v=abc"
        mock_items = [
            {"index": 0, "title": "Rec 1", "href": "/watch?v=rec1", "channel_name": "Test Channel", "channel_url": ""},
            {"index": 1, "title": "Rec 2", "href": "/watch?v=rec2", "channel_name": "Other Channel", "channel_url": ""},
        ]
        mock_driver.execute_script.return_value = mock_items
        worker.driver = mock_driver

        results = worker.fetch_recommended_videos()
        assert len(results) == 2
        assert results[0]["title"] == "Rec 1"

    def test_record_watch(self, worker):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/watch?v=sample123"
        worker.driver = mock_driver

        worker._record_watch(
            title="Sample Video",
            channel_name="Test Channel",
            watch_seconds=120.0,
            ad_seconds=15.0,
            duration=300.0,
            completed=True,
        )

        records = worker.stats_store.records
        assert len(records) == 1
        rec = next(iter(records.values()))
        assert rec.watch_seconds == 120.0
        assert rec.runs == 1

    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.close_driver")
    def test_run_stops_when_login_fails(self, mock_close, mock_login, mock_setup, worker):
        mock_driver = MagicMock()
        mock_setup.return_value = mock_driver
        mock_login.return_value = False

        worker.run()

        mock_setup.assert_called_once()
        mock_login.assert_called_once()
        mock_close.assert_called_once_with(mock_driver, log_queue=worker.log_queue)

    @patch("src.automation.watching_worker.human_like_delay")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.watch_video")
    @patch("src.automation.watching_worker.safe_get")
    @patch("src.automation.watching_worker.close_driver")
    def test_run_loop_and_stop_event(
        self, mock_close, mock_safe_get, mock_watch, mock_login, mock_setup, mock_delay, worker
    ):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_setup.return_value = mock_driver
        mock_login.return_value = True

        home_videos = [
            {"index": 0, "title": "Great Video", "href": "/watch?v=123", "channel_name": "Test Channel", "channel_url": ""}
        ]
        worker.fetch_home_videos = MagicMock(return_value=home_videos)

        def click_and_update_url(*args, **kwargs):
            mock_driver.current_url = "https://www.youtube.com/watch?v=123"
            return True

        worker.click_home_video = MagicMock(side_effect=click_and_update_url)

        def finish_and_stop(*args, **kwargs):
            worker.stop_event.set()
            return WatchResult(completed=True, watched_seconds=60.0, playback_finished=True)

        mock_watch.side_effect = finish_and_stop

        worker.run()

        assert worker.click_home_video.called
        assert mock_watch.called
        mock_close.assert_called_once()


class TestDashboardButtonsAndState:
    def test_dashboard_view_buttons(self, tmp_path):
        # Test widget structure without rendering display
        from src.ui.views.dashboard_view import DashboardView

        _parent = MagicMock()
        controller = MagicMock()
        controller.config_mgr.get_int.return_value = 1

        with patch("customtkinter.CTkFrame.__init__", return_value=None), \
             patch("customtkinter.CTkLabel.__init__", return_value=None), \
             patch("customtkinter.CTkButton.__init__", return_value=None), \
             patch("customtkinter.CTkEntry.__init__", return_value=None), \
             patch("customtkinter.CTkProgressBar.__init__", return_value=None):

            view = DashboardView.__new__(DashboardView)
            view.app_controller = controller
            view.start_btn = MagicMock()
            view.start_watching_btn = MagicMock()
            view.stop_btn = MagicMock()
            view.loop_entry = MagicMock()
            view.card_status = MagicMock()

            # Test running state: watching mode
            view.set_running_state(True, mode="watching")
            view.start_btn.configure.assert_called_with(state="disabled")
            view.start_watching_btn.configure.assert_called_with(state="disabled")
            view.stop_btn.configure.assert_any_call(state="normal")
            view.stop_btn.configure.assert_any_call(text="⏹ Stop Watching")

            # Test idle state
            view.set_running_state(False)
            view.start_btn.configure.assert_called_with(state="normal")
            view.start_watching_btn.configure.assert_called_with(state="normal")
            view.stop_btn.configure.assert_called_with(state="disabled", text="⏹ Stop Automation")
