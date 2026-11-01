"""Unit tests for YouTube search, home feed channel discovery, and suggested video navigation."""

import queue
import unittest
from unittest.mock import MagicMock, patch

from src.automation.navigation import (
    find_and_click_search_result,
    find_and_click_suggested_video,
    find_channel_video_on_home,
    perform_youtube_search,
)
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask


class TestBrowsingSearch(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.log_queue = queue.Queue()
        self.delay_patch = patch("src.automation.navigation.human_like_delay")
        self.delay_patch.start()

    def tearDown(self):
        self.delay_patch.stop()

    def test_perform_youtube_search_success(self):
        """Verify perform_youtube_search finds input, clears, types query, and waits for results."""
        self.driver.current_url = "https://www.youtube.com/"
        mock_input = MagicMock()
        with patch("src.automation.navigation.WebDriverWait") as mock_wait:
            mock_wait.return_value.until.return_value = mock_input
            with patch("src.automation.navigation.dismiss_youtube_dialogs"):
                with patch("src.automation.navigation.slow_type") as mock_slow_type:
                    ok = perform_youtube_search(self.driver, "test query", log_queue=self.log_queue)
                    self.assertTrue(ok)
                    mock_slow_type.assert_called_once_with(mock_input, "test query")

    def test_perform_youtube_search_empty_query(self):
        """Verify empty query returns False immediately."""
        ok = perform_youtube_search(self.driver, "   ", log_queue=self.log_queue)
        self.assertFalse(ok)

    def test_find_and_click_search_result_match_video_id(self):
        """Verify matching search result by target video ID."""
        task = VideoTask(url="https://www.youtube.com/watch?v=TARGET12345", title="Sample Title")
        results_payload = [
            {"title": "Other Video", "url": "https://www.youtube.com/watch?v=OTHER11111", "channel": "OtherCh"},
            {"title": "Target Video", "url": "https://www.youtube.com/watch?v=TARGET12345", "channel": "MyCh"},
        ]
        self.driver.execute_script.side_effect = [
            results_payload,  # SCAN_RESULTS_JS
            True,  # CLICK_RESULT_JS
            True,  # CLICK_EXEC_JS
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            matched = find_and_click_search_result(self.driver, task=task, log_queue=self.log_queue)
            self.assertIsNotNone(matched)
            self.assertEqual(matched["url"], "https://www.youtube.com/watch?v=TARGET12345")

    def test_find_and_click_search_result_match_title(self):
        """Verify matching search result by candidate title if video ID is absent."""
        task = VideoTask(title="Learn Python Fast", keywords="learn python")
        results_payload = [
            {"title": "Different Video", "url": "https://www.youtube.com/watch?v=DIFF11111", "channel": "Ch1"},
            {"title": "Best Guide: Learn Python Fast for Beginners", "url": "https://www.youtube.com/watch?v=PYTH12345", "channel": "PythonCh"},
        ]
        self.driver.execute_script.side_effect = [
            results_payload,
            True,
            True,
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            matched = find_and_click_search_result(self.driver, task=task, log_queue=self.log_queue)
            self.assertIsNotNone(matched)
            self.assertEqual(matched["url"], "https://www.youtube.com/watch?v=PYTH12345")

    def test_find_and_click_search_result_match_channel(self):
        """Verify matching search result by target channel name."""
        results_payload = [
            {"title": "Random Video", "url": "https://www.youtube.com/watch?v=RAND11111", "channel": "Random Creator"},
            {"title": "New Upload", "url": "https://www.youtube.com/watch?v=CHAN12345", "channel": "Tech Channel"},
        ]
        self.driver.execute_script.side_effect = [
            results_payload,
            True,
            True,
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            matched = find_and_click_search_result(
                self.driver,
                target_channels=["Tech Channel"],
                log_queue=self.log_queue,
            )
            self.assertIsNotNone(matched)
            self.assertEqual(matched["channel"], "Tech Channel")

    def test_find_and_click_search_result_not_found(self):
        """Verify returning None if no search results match after scrolling."""
        self.driver.execute_script.return_value = []
        matched = find_and_click_search_result(
            self.driver,
            task=VideoTask(title="Unfindable Video"),
            max_scrolls=1,
            log_queue=self.log_queue,
        )
        self.assertIsNone(matched)

    def test_find_channel_video_on_home_found(self):
        """Verify finding target channel video on home feed."""
        self.driver.current_url = "https://www.youtube.com/"
        home_cards = [
            {"title": "Unrelated", "url": "https://www.youtube.com/watch?v=UNREL11111", "channel": "Other"},
            {"title": "My Favorite Tech Video", "url": "https://www.youtube.com/watch?v=FAV12345678", "channel": "FavoriteTech"},
        ]
        self.driver.execute_script.side_effect = [
            home_cards,
            True,  # CLICK_HOME_JS
            True,
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            found = find_channel_video_on_home(
                self.driver,
                target_channels=["FavoriteTech"],
                log_queue=self.log_queue,
            )
            self.assertIsNotNone(found)
            self.assertEqual(found["channel"], "FavoriteTech")

    def test_find_and_click_suggested_video_prioritize_target_channel(self):
        """Verify suggested video selection prioritizes target channel if available."""
        suggested_items = [
            {"title": "Viral Meme", "url": "https://www.youtube.com/watch?v=MEME1111111", "channel": "MemeLord"},
            {"title": "Target Recommended", "url": "https://www.youtube.com/watch?v=TARG1111111", "channel": "TargetChannel"},
        ]
        self.driver.execute_script.side_effect = [
            suggested_items,
            True,
            True,
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            chosen = find_and_click_suggested_video(
                self.driver,
                target_channels=["TargetChannel"],
                prefer_target_channels=True,
                log_queue=self.log_queue,
            )
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen["channel"], "TargetChannel")

    def test_find_and_click_suggested_video_random_when_no_target(self):
        """Verify picking random suggested video when target channel is not in suggestions."""
        suggested_items = [
            {"title": "Viral 1", "url": "https://www.youtube.com/watch?v=V1111111111", "channel": "Ch1"},
            {"title": "Viral 2", "url": "https://www.youtube.com/watch?v=V2222222222", "channel": "Ch2"},
        ]
        self.driver.execute_script.side_effect = [
            suggested_items,
            True,
            True,
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            chosen = find_and_click_suggested_video(
                self.driver,
                target_channels=["NonExistentChannel"],
                prefer_target_channels=True,
                log_queue=self.log_queue,
            )
            self.assertIsNotNone(chosen)
            self.assertIn(chosen["title"], ["Viral 1", "Viral 2"])


class TestWatchingWorkerSearchIntegration(unittest.TestCase):
    def setUp(self):
        self.account = Account(email="test@gmail.com", password="password123")
        self.channel = Channel(name="MyTargetChannel", enabled=True)
        self.mock_config = MagicMock()
        self.log_queue = queue.Queue()

    def test_search_and_play_target_video_finds_and_plays(self):
        """Verify search_and_play_target_video tries queued candidate, finds it, and plays."""
        from src.automation.watching_worker import WatchingWorker

        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = MagicMock()
        worker.driver.current_url = "https://www.youtube.com/watch?v=TEST_LOG_VID"
        self.mock_config.get_int.return_value = 5

        self.mock_config.get_video_queue.return_value = [
            {"title": "Queued Video 1", "url": "https://www.youtube.com/watch?v=QUEUED12345", "enabled": True}
        ]

        with patch.object(worker, "search_youtube_query", return_value=True):
            with patch("src.automation.watching_worker.find_and_click_search_result") as mock_find:
                mock_find.return_value = {
                    "title": "Queued Video 1",
                    "url": "https://www.youtube.com/watch?v=QUEUED12345",
                    "channel": "MyTargetChannel",
                }
                with patch.object(worker, "_play_target_video") as mock_play:
                    mock_play.return_value = MagicMock(completed=True, interrupted=False)
                    session_keys = set()
                    ok = worker.search_and_play_target_video(session_keys)
                    self.assertTrue(ok)
                    self.assertIn("QUEUED12345", session_keys)
                    mock_play.assert_called_once()

    def test_search_and_play_target_video_tries_next_on_failure(self):
        """Verify search_and_play_target_video tries next candidate when the first is not found."""
        from src.automation.watching_worker import WatchingWorker

        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = MagicMock()
        worker.driver.current_url = "https://www.youtube.com/watch?v=TEST_LOG_VID"
        self.mock_config.get_int.return_value = 5

        self.mock_config.get_video_queue.return_value = [
            {"title": "Video Not Found", "url": "https://www.youtube.com/watch?v=MISSING1111", "enabled": True},
            {"title": "Video Found", "url": "https://www.youtube.com/watch?v=FOUND222222", "enabled": True},
        ]

        search_calls = []

        def mock_search(query):
            search_calls.append(query)
            return True

        with patch.object(worker, "search_youtube_query", side_effect=mock_search):
            with patch("src.automation.watching_worker.find_and_click_search_result") as mock_find:
                # First returns None, second returns found dict
                mock_find.side_effect = [
                    None,
                    {"title": "Video Found", "url": "https://www.youtube.com/watch?v=FOUND222222", "channel": "MyTargetChannel"},
                ]
                with patch.object(worker, "_play_target_video") as mock_play:
                    mock_play.return_value = MagicMock(completed=True, interrupted=False)
                    session_keys = set()
                    ok = worker.search_and_play_target_video(session_keys)
                    self.assertTrue(ok)
                    self.assertEqual(len(search_calls), 2)
                    self.assertIn("FOUND222222", session_keys)



    def test_log_formatting_url_title_channel_and_found_messages(self):
        """Verify the log messages for Scanning, Found video, and URL -> Title -> Channel ordering."""
        from src.automation.watching_worker import WatchingWorker

        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = MagicMock()
        worker.driver.current_url = "https://www.youtube.com/watch?v=TEST_LOG_VID"
        self.mock_config.get_int.return_value = 5

        # 1. Test _play_target_video log structure
        with patch("src.automation.watching_worker.watch_video") as mock_watch:
            mock_watch.return_value = MagicMock(completed=True, watched_seconds=10.0, video_duration=100.0, playback_finished=True)
            worker._play_target_video(
                card_info={},
                full_url="https://www.youtube.com/watch?v=TEST_LOG_VID",
                title="Super Awesome AI Video",
                channel_display="AI Channel",
                session_watched_keys=set(),
            )

            logged = []
            while not self.log_queue.empty():
                logged.append(self.log_queue.get_nowait())
            log_str = "\n".join(logged)

            # Strict order: URL -> Title -> Channel
            expected_block = (
                "URL: https://www.youtube.com/watch?v=TEST_LOG_VID\n"
                "Title: Super Awesome AI Video\n"
                "Channel: AI Channel"
            )
            self.assertIn(expected_block, log_str)
            # Old order must NOT be present
            self.assertNotIn("Channel: AI Channel\nURL:", log_str)

        # 2. Test search_and_play_target_video logs
        self.mock_config.get_video_queue.return_value = [
            {"title": "Search AI Video", "url": "https://www.youtube.com/watch?v=SEARCH_VID", "enabled": True},
        ]
        with patch.object(worker, "search_youtube_query", return_value=True):
            with patch("src.automation.watching_worker.find_and_click_search_result") as mock_find:
                mock_find.return_value = {
                    "title": "Search AI Video",
                    "url": "https://www.youtube.com/watch?v=SEARCH_VID",
                    "channel": "AI Channel",
                }
                with patch.object(worker, "_play_target_video") as mock_play:
                    mock_play.return_value = MagicMock(completed=True)
                    worker.search_and_play_target_video(set())

                    search_logs = []
                    while not self.log_queue.empty():
                        search_logs.append(self.log_queue.get_nowait())
                    search_str = "\n".join(search_logs)

                    self.assertIn("Scanning YouTube search...", search_str)
                    self.assertIn("Found video in YouTube search result", search_str)
                    # Video title must NOT be in the Found line
                    for line in search_logs:
                        if "Found video in YouTube search result" in line:
                            self.assertNotIn("Search AI Video", line)

if __name__ == "__main__":
    unittest.main()
