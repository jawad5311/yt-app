"""Tests for WatchingWorker, channel matching, and Start Watching feature."""

import queue
import threading
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
        config_mgr.set_channels([c.to_dict() for c in channels])
        log_q = queue.Queue()
        stop_ev = threading.Event()
        event_q = queue.Queue()

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
        mock_driver.find_elements.return_value = [MagicMock()]
        mock_setup.return_value = mock_driver
        mock_login.return_value = True

        home_videos = [
            {"index": 0, "title": "Great Video", "href": "/watch?v=123", "channel_name": "Test Channel", "channel_url": ""}
        ]
        mock_driver.execute_script.return_value = home_videos

        def click_and_update_url(*args, **kwargs):
            mock_driver.current_url = "https://www.youtube.com/watch?v=123"
            return True

        worker.click_home_video = MagicMock(side_effect=click_and_update_url)

        def finish_and_stop(*args, **kwargs):
            worker.stop_event.set()
            return WatchResult(completed=True, watched_seconds=60.0, playback_finished=True)

        mock_watch.side_effect = finish_and_stop

        worker.open_videos = True
        worker.run()

        assert worker.click_home_video.called
        assert mock_watch.called
        mock_close.assert_called_once()

    @patch("src.automation.watching_worker.human_like_delay")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.watch_video")
    @patch("src.automation.watching_worker.safe_get")
    @patch("src.automation.watching_worker.close_driver")
    def test_first_video_click_watch_queue_and_stats(
        self, mock_close, mock_safe_get, mock_watch, mock_login, mock_setup, mock_delay, worker
    ):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_driver.find_elements.return_value = [MagicMock()]
        mock_setup.return_value = mock_driver
        mock_login.return_value = True

        home_videos = [
            {"index": 0, "title": "First Matched Video", "href": "/watch?v=match123", "channel_name": "Test Channel", "channel_url": ""},
            {"index": 1, "title": "Second Video", "href": "/watch?v=match456", "channel_name": "Test Channel", "channel_url": ""},
        ]
        mock_driver.execute_script.return_value = home_videos

        def click_side_effect(card_info):
            mock_driver.current_url = f"https://www.youtube.com{card_info.get('href')}"
            return True

        worker.click_home_video = MagicMock(side_effect=click_side_effect)

        def watch_side_effect(*args, **kwargs):
            assert kwargs.get("duration_seconds") == 60
            worker.stop_event.set()
            return WatchResult(completed=True, watched_seconds=190.0, video_duration=190.0, playback_finished=True)

        mock_watch.side_effect = watch_side_effect

        worker.open_videos = True
        worker.run()

        # 0. Ensured no duplicate navigation to YouTube Home when already on the page
        home_nav_calls = [
            call for call in mock_safe_get.call_args_list
            if len(call[0]) > 1 and call[0][1] == "https://www.youtube.com/"
        ]
        assert len(home_nav_calls) == 0

        # 1. First video clicked
        assert worker.click_home_video.called
        clicked_card = worker.click_home_video.call_args[0][0]
        assert clicked_card["href"] == "/watch?v=match123"

        # 2. Added to queue in settings under channel
        queue_items = worker.config_mgr.get_video_queue()
        assert len(queue_items) == 1
        assert queue_items[0]["title"] == "First Matched Video"
        assert queue_items[0]["channel"] == "Test Channel"
        assert queue_items[0]["max_runs"] == 3
        assert queue_items[0]["duration_seconds"] == 190
        assert queue_items[0]["duration_text"] == "3:10"

        # 3. Stats recorded in stats_store
        stats = worker.stats_store.records
        assert len(stats) == 1
        stat = next(iter(stats.values()))
        assert stat.watch_seconds == 190.0
        assert stat.runs == 1
        assert stat.channel == "Test Channel"

        # 4. Strict log formatting
        logged = []
        while not worker.log_queue.empty():
            logged.append(worker.log_queue.get_nowait())
        log_str = "\n".join(logged)
        assert "Channel: Test Channel\nURL: https://www.youtube.com/watch?v=match123\nTitle: First Matched Video" in log_str

    @patch("src.automation.watching_worker.human_like_delay")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.watch_video")
    @patch("src.automation.watching_worker.safe_get")
    @patch("src.automation.watching_worker.close_driver")
    def test_run_loop_link_grabbing_only(
        self, mock_close, mock_safe_get, mock_watch, mock_login, mock_setup, mock_delay, worker
    ):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_driver.find_elements.return_value = [MagicMock()]
        mock_setup.return_value = mock_driver
        mock_login.return_value = True

        home_videos = [
            {"index": 0, "title": "Target Video", "href": "/watch?v=target1", "channel_name": "Test Channel", "channel_url": ""},
            {"index": 1, "title": "Other Video", "href": "/watch?v=other2", "channel_name": "Other Channel", "channel_url": ""}
        ]
        # First iteration returns items, then set stop_event
        def execute_side_effect(script, *args):
            worker.stop_event.set()
            return home_videos

        mock_driver.execute_script.side_effect = execute_side_effect
        worker.open_videos = False
        worker.run()

        # Videos should NOT be opened or watched
        assert not mock_watch.called
        mock_close.assert_called_once()

        # Check that logs were emitted ONLY for matched target channel in the specified format
        logged = []
        while not worker.log_queue.empty():
            logged.append(worker.log_queue.get_nowait())
        log_str = "\n".join(logged)
        assert "Channel: Test Channel" in log_str
        assert "URL: https://www.youtube.com/watch?v=target1" in log_str
        assert "Title: Target Video" in log_str
        assert "other2" not in log_str


    def test_is_candidate_eligible_new_and_stored(self, worker):
        from src.models.video_task import VideoTask
        from src.models.watch_stats import TaskOutcome

        vid_new = "newvid12345"
        vid_stored = "oldvid12345"
        url_new = f"https://www.youtube.com/watch?v={vid_new}"
        url_stored = f"https://www.youtube.com/watch?v={vid_stored}"

        # 1. New video -> Eligible
        eligible, task, reason = worker.is_candidate_eligible(url_new, "New Video", "Test Channel")
        assert eligible is True
        assert task is None
        assert "new video" in reason

        # 2. Stored video with 1/3 runs -> Eligible
        stored_task = VideoTask(url=url_stored, title="Stored Video", channel="Test Channel", max_runs=3)
        worker.config_mgr.set_video_queue([stored_task.to_dict()])
        worker.stats_store.record(TaskOutcome(
            video_key=vid_stored,
            task_key=vid_stored,
            label="Stored Video",
            channel="Test Channel",
            attempts=1,
            success=True,
            watched_seconds=100.0,
            video_duration=100.0,
            playback_finished=True,
        ))

        eligible, task, reason = worker.is_candidate_eligible(url_stored, "Stored Video", "Test Channel")
        assert eligible is True
        assert task is not None
        assert "eligible (1/3 runs completed)" in reason

        # 3. Stored video with 3/3 runs -> Ineligible (skipped)
        worker.stats_store.record(TaskOutcome(
            video_key=vid_stored,
            task_key=vid_stored,
            label="Stored Video",
            channel="Test Channel",
            attempts=1,
            success=True,
            watched_seconds=100.0,
            video_duration=100.0,
            playback_finished=True,
        ))
        worker.stats_store.record(TaskOutcome(
            video_key=vid_stored,
            task_key=vid_stored,
            label="Stored Video",
            channel="Test Channel",
            attempts=1,
            success=True,
            watched_seconds=100.0,
            video_duration=100.0,
            playback_finished=True,
        ))
        assert worker.stats_store.get(vid_stored).runs == 3

        eligible, task, reason = worker.is_candidate_eligible(url_stored, "Stored Video", "Test Channel")
        assert eligible is False
        assert "already reached run limit (3/3 runs completed)" in reason

    @patch("src.automation.watching_worker.human_like_delay")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.watch_video")
    @patch("src.automation.watching_worker.safe_get")
    @patch("src.automation.watching_worker.close_driver")
    def test_run_skips_ineligible_video_and_plays_next(
        self, mock_close, mock_safe_get, mock_watch, mock_login, mock_setup, mock_delay, worker
    ):
        from src.models.video_task import VideoTask
        from src.models.watch_stats import TaskOutcome

        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_driver.find_elements.return_value = [MagicMock()]
        mock_setup.return_value = mock_driver
        mock_login.return_value = True

        vid_done = "done1234567"
        vid_next = "next1234567"

        # Pre-store video 1 in queue with 3/3 runs completed
        task1 = VideoTask(url=f"https://www.youtube.com/watch?v={vid_done}", title="Done Video", channel="Test Channel", max_runs=3)
        worker.config_mgr.set_video_queue([task1.to_dict()])
        for _ in range(3):
            worker.stats_store.record(TaskOutcome(
                video_key=vid_done,
                task_key=vid_done,
                label="Done Video",
                channel="Test Channel",
                attempts=1,
                success=True,
                watched_seconds=100.0,
                video_duration=100.0,
                playback_finished=True,
            ))

        # Home feed has Done Video (first) and Next Eligible Video (second)
        home_videos = [
            {"index": 0, "title": "Done Video", "href": f"/watch?v={vid_done}", "channel_name": "Test Channel", "channel_url": ""},
            {"index": 1, "title": "Next Video", "href": f"/watch?v={vid_next}", "channel_name": "Test Channel", "channel_url": ""},
        ]
        mock_driver.execute_script.return_value = home_videos

        def click_side_effect(card_info):
            mock_driver.current_url = f"https://www.youtube.com{card_info.get('href')}"
            return True

        worker.click_home_video = MagicMock(side_effect=click_side_effect)

        def watch_side_effect(*args, **kwargs):
            worker.stop_event.set()
            return WatchResult(completed=True, watched_seconds=60.0, video_duration=120.0, playback_finished=True)

        mock_watch.side_effect = watch_side_effect

        worker.open_videos = True
        worker.run()

        # Done Video should be skipped, and Next Video clicked and watched!
        assert worker.click_home_video.called
        clicked_card = worker.click_home_video.call_args[0][0]
        assert clicked_card["href"] == f"/watch?v={vid_next}"

        logged = []
        while not worker.log_queue.empty():
            logged.append(worker.log_queue.get_nowait())
        log_str = "\n".join(logged)
        assert "Skipping video 'Done Video' from channel 'Test Channel': already reached run limit (3/3 runs completed)." in log_str
        assert f"Channel: Test Channel\nURL: https://www.youtube.com/watch?v={vid_next}\nTitle: Next Video" in log_str


    def test_click_recommended_video_lockup(self, worker):
        mock_driver = MagicMock()
        mock_driver.find_elements.return_value = []
        worker.driver = mock_driver

        with patch("src.automation.watching_worker.safe_get") as mock_safe_get:
            res = worker.click_recommended_video({"index": 0, "href": "/watch?v=rec12345678"})
            assert res is True
            mock_safe_get.assert_called_once_with(
                mock_driver, "https://www.youtube.com/watch?v=rec12345678", timeout=30, log_queue=worker.log_queue
            )

    @patch("src.automation.watching_worker.human_like_delay")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.watch_video")
    @patch("src.automation.watching_worker.safe_get")
    @patch("src.automation.watching_worker.close_driver")
    def test_run_chained_continuous_watching_loop(
        self, mock_close, mock_safe_get, mock_watch, mock_login, mock_setup, mock_delay, worker
    ):
        """Verify back-to-back loop: Home -> Watch -> Recommended (skip ineligible, play eligible) -> Watch."""
        from src.models.video_task import VideoTask
        from src.models.watch_stats import TaskOutcome

        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_driver.find_elements.return_value = [MagicMock()]  # Fast WebDriverWait
        mock_setup.return_value = mock_driver
        mock_login.return_value = True

        home_vid = "home1234567"
        done_vid = "done1234567"
        rec_vid = "rec12345678"

        # Pre-store done_vid with 3/3 runs in stats
        worker.stats_store.record(TaskOutcome(
            video_key=done_vid,
            task_key=done_vid,
            label="Ineligible Rec Video",
            channel="Test Channel",
            attempts=1,
            success=True,
            watched_seconds=120.0,
            video_duration=120.0,
            playback_finished=True,
        ))
        worker.stats_store.record(TaskOutcome(
            video_key=done_vid,
            task_key=done_vid,
            label="Ineligible Rec Video",
            channel="Test Channel",
            attempts=1,
            success=True,
            watched_seconds=120.0,
            video_duration=120.0,
            playback_finished=True,
        ))
        worker.stats_store.record(TaskOutcome(
            video_key=done_vid,
            task_key=done_vid,
            label="Ineligible Rec Video",
            channel="Test Channel",
            attempts=1,
            success=True,
            watched_seconds=120.0,
            video_duration=120.0,
            playback_finished=True,
        ))

        # 1. Home feed has home_vid
        home_videos = [
            {"index": 0, "title": "Home Video", "href": f"/watch?v={home_vid}", "channel_name": "Test Channel", "channel_url": ""},
        ]
        # 2. Watch page recommendations has done_vid (ineligible) then rec_vid (eligible)
        rec_videos = [
            {"index": 0, "title": "Ineligible Rec Video", "href": f"/watch?v={done_vid}", "channel_name": "Test Channel", "channel_url": ""},
            {"index": 1, "title": "Eligible Rec Video", "href": f"/watch?v={rec_vid}", "channel_name": "Test Channel", "channel_url": ""},
        ]

        def exec_script_side_effect(script, *args):
            if "HOME_VIDEOS_JS" in script or "ytd-rich-item-renderer" in script:
                return home_videos
            if "RECOMMENDED_VIDEOS_JS" in script or "yt-lockup-view-model" in script:
                return rec_videos
            if "v.duration" in script:
                return 180.0
            return []

        mock_driver.execute_script.side_effect = exec_script_side_effect

        def click_home_side_effect(card_info):
            mock_driver.current_url = f"https://www.youtube.com{card_info.get('href')}"
            return True

        def click_rec_side_effect(card_info):
            mock_driver.current_url = f"https://www.youtube.com{card_info.get('href')}"
            return True

        worker.click_home_video = MagicMock(side_effect=click_home_side_effect)
        worker.click_recommended_video = MagicMock(side_effect=click_rec_side_effect)

        watched_urls = []
        def watch_side_effect(*args, **kwargs):
            watched_urls.append(mock_driver.current_url)
            # After 2 videos watched (home + rec), stop the session
            if len(watched_urls) >= 2:
                worker.stop_event.set()
            return WatchResult(completed=True, watched_seconds=60.0, video_duration=180.0, playback_finished=True)

        mock_watch.side_effect = watch_side_effect

        worker.open_videos = True
        worker.run()

        # Both home video and recommended video were clicked!
        assert worker.click_home_video.called
        assert worker.click_recommended_video.called
        assert len(watched_urls) == 2

        clicked_rec = worker.click_recommended_video.call_args[0][0]
        assert clicked_rec["href"] == f"/watch?v={rec_vid}"

        # Both videos registered in queue with max_runs=3 and updated duration
        queue_items = worker.config_mgr.get_video_queue()
        queue_urls = [q["url"] for q in queue_items]
        assert f"https://www.youtube.com/watch?v={home_vid}" in queue_urls
        assert f"https://www.youtube.com/watch?v={rec_vid}" in queue_urls
        for q in queue_items:
            assert q["max_runs"] == 3
            assert q["duration_seconds"] == 180

        # Verify logs
        logged = []
        while not worker.log_queue.empty():
            logged.append(worker.log_queue.get_nowait())
        log_str = "\n".join(logged)
        assert "Skipping suggested video 'Ineligible Rec Video' from channel 'Test Channel': already reached run limit" in log_str
        assert "Eligible Rec Video" in log_str
        assert "Updated video time for 'Eligible Rec Video': watched 60.0s" in log_str


    def test_is_clean_youtube_home_url(self):
        from src.automation.watching_worker import is_clean_youtube_home_url
        assert is_clean_youtube_home_url("https://www.youtube.com/") is True
        assert is_clean_youtube_home_url("https://www.youtube.com") is True
        assert is_clean_youtube_home_url("https://www.youtube.com/@iamcarhacker") is False
        assert is_clean_youtube_home_url("https://www.youtube.com/@SummiaJamshed/videos") is False
        assert is_clean_youtube_home_url("https://www.youtube.com/watch?v=123") is False
        assert is_clean_youtube_home_url("https://www.youtube.com/?bp=123") is False
        assert is_clean_youtube_home_url("") is False

class TestDashboardButtonsAndState:
    def test_dashboard_view_buttons(self, tmp_path):
        # Test widget structure without rendering display
        from src.ui.views.dashboard_view import DashboardView

        parent = MagicMock()
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

            view.pause_btn = MagicMock()

            # Test running state: watching mode
            view.set_running_state(True, mode="watching")
            view.start_btn.configure.assert_called_with(state="disabled")
            view.start_watching_btn.configure.assert_called_with(state="disabled")
            view.stop_btn.configure.assert_any_call(state="normal")
            view.stop_btn.configure.assert_any_call(text="⏹ Stop Browsing")
            view.pause_btn.configure.assert_any_call(state="normal", text="⏸ Pause", fg_color="#D97706", hover_color="#B45309")

            # Test pause toggling
            view.set_paused_state(True)
            view.pause_btn.configure.assert_called_with(state="normal", text="▶ Resume", fg_color="#2563EB", hover_color="#1D4ED8")
            view.set_paused_state(False)
            view.pause_btn.configure.assert_called_with(state="normal", text="⏸ Pause", fg_color="#D97706", hover_color="#B45309")

            # Test idle state
            view.set_running_state(False)
            view.start_btn.configure.assert_called_with(state="normal")
            view.start_watching_btn.configure.assert_called_with(state="normal")
            view.stop_btn.configure.assert_called_with(state="disabled", text="⏹ Stop")
            view.pause_btn.configure.assert_called_with(state="disabled", text="⏸ Pause", fg_color="#D97706")
