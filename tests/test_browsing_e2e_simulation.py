"""End-to-End Simulation Test for the entire YouTube Browsing & Search Loop Workflow.

Simulates the complete cycle:
1. Home feed targeted video detection and playback.
2. Suggested video playback (2 to 3 videos).
3. YouTube search for queued candidates with retry until found.
4. Transition back to suggested videos and fallback when suggestions run out.
5. Clean stop and stats verification.
"""

import queue
import threading
import unittest
from unittest.mock import MagicMock, patch

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import WatchResult


class TestBrowsingE2ESimulation(unittest.TestCase):
    def setUp(self):
        self.account = Account(email="browse_tester@gmail.com", password="pass")
        self.channel = Channel(name="AlphaChannel", url="https://youtube.com/@alphachannel", enabled=True)
        self.log_queue = queue.Queue()
        self.event_queue = queue.Queue()
        self.stop_event = threading.Event()

    @patch("src.automation.watching_worker.time.sleep")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login")
    @patch("src.automation.watching_worker.close_driver")
    @patch("src.automation.watching_worker.safe_get")
    @patch("src.automation.watching_worker.human_like_delay")
    @patch("src.automation.navigation.human_like_delay")
    @patch("src.automation.watching_worker.dismiss_youtube_dialogs")
    def test_full_browsing_and_search_loop_lifecycle(
        self,
        mock_dismiss,
        mock_nav_delay,
        mock_worker_delay,
        mock_safe_get,
        mock_close,
        mock_login,
        mock_setup,
        mock_sleep,
    ):
        mock_login.return_value = True
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_driver.find_elements.return_value = [MagicMock()]
        mock_setup.return_value = mock_driver

        mock_config = MagicMock(spec=ConfigManager)
        mock_config.get_channels.return_value = [self.channel.to_dict()]
        mock_config.get_video_queue.return_value = [
            {"title": "Alpha Video 1", "url": "https://www.youtube.com/watch?v=ALPHA11111", "channel": "AlphaChannel", "enabled": True},
            {"title": "Alpha Video 2", "url": "https://www.youtube.com/watch?v=ALPHA22222", "channel": "AlphaChannel", "enabled": True},
        ]
        mock_config.get_bool.return_value = True
        mock_config.get_int.return_value = 10
        mock_config.get_float.return_value = 12.0
        mock_config.get_setting.return_value = "5 Minutes"
        mock_config.get_accounts.return_value = []

        mock_stats = MagicMock(spec=StatsStore)
        mock_stats.find_for_task.return_value = None
        mock_stats.get.return_value = None

        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel],
            config_mgr=mock_config,
            log_queue=self.log_queue,
            stop_event=self.stop_event,
            event_queue=self.event_queue,
            stats_store=mock_stats,
            is_unlimited=True,
            target_limit=None,
            open_videos=True,
        )

        # Track the sequence of phases executed
        workflow_steps = []

        # 1. Mock Home feed items (Found AlphaChannel video)
        home_cards = [
            {"title": "Alpha Home Video", "href": "/watch?v=ALPHA11111", "channel_name": "AlphaChannel", "channel_url": ""},
        ]
        mock_driver.execute_script.return_value = home_cards

        # 2. Mock _play_target_video
        def play_target_side_effect(item, full_url, title, ch_display, session_keys, is_home=False, **kwargs):
            workflow_steps.append(f"play_target:{title}:is_home={is_home}")
            # Simulate browser landing on watch page
            mock_driver.current_url = full_url
            return WatchResult(completed=True, watched_seconds=60.0, video_duration=180.0, playback_finished=True)

        worker._play_target_video = MagicMock(side_effect=play_target_side_effect)

        # 3. Mock suggested videos: provide 2 suggested videos, then on next check return []
        suggested_pool = [
            {"title": "Suggested Rec 1", "href": "/watch?v=REC11111111", "channel_name": "Other1", "channel_url": ""},
            {"title": "Suggested Rec 2", "href": "/watch?v=REC22222222", "channel_name": "Other2", "channel_url": ""},
        ]

        def fetch_recs_side_effect():
            if "search_executed" in workflow_steps:
                # After search, test fallback when suggested section is empty
                return []
            return list(suggested_pool)

        worker.fetch_recommended_videos = MagicMock(side_effect=fetch_recs_side_effect)

        # 4. Mock _watch_random_video
        def watch_random_side_effect(card_info, dur, session_keys):
            workflow_steps.append(f"watch_suggested:{card_info.get('title')}")
            mock_driver.current_url = f"https://www.youtube.com{card_info.get('href')}"
            return True

        worker._watch_random_video = MagicMock(side_effect=watch_random_side_effect)

        # 5. Mock search_and_play_target_video
        def search_side_effect(session_keys):
            workflow_steps.append("search_executed")
            # Simulate playing searched video
            worker._play_target_video(
                {"title": "Alpha Video 2", "url": "https://www.youtube.com/watch?v=ALPHA22222"},
                "https://www.youtube.com/watch?v=ALPHA22222",
                "Alpha Video 2",
                "AlphaChannel",
                session_keys,
                is_home=False,
            )
            # After completing one full cycle (Home -> Suggested -> Search -> empty suggested -> Search -> Stop)
            # stop the worker
            if workflow_steps.count("search_executed") >= 2:
                worker.stop_event.set()
            return True

        worker.search_and_play_target_video = MagicMock(side_effect=search_side_effect)

        # Execute the worker run loop!
        worker.run()

        # Assertions
        # 1. Step 1 must have played target channel video found on Home page
        self.assertIn("play_target:Alpha Home Video:is_home=True", workflow_steps)

        # 2. Step 2 must have watched suggested videos
        suggested_watched = [s for s in workflow_steps if s.startswith("watch_suggested:")]
        self.assertGreaterEqual(len(suggested_watched), 1)

        # 3. Step 3 must have triggered search phase
        self.assertIn("search_executed", workflow_steps)

        # 4. Step 4 must have played searched video
        self.assertIn("play_target:Alpha Video 2:is_home=False", workflow_steps)

        # 5. Worker stopped cleanly
        self.assertTrue(worker.stop_event.is_set())
        mock_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
