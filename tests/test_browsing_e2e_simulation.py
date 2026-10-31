"""End-to-End Simulation Test for the entire YouTube Browsing & Search Loop Workflow.

Simulates the complete cycle:
1. Home feed targeted video detection and playback.
2. Suggested video scanning prioritizing alternative target channels.
3. YouTube search fallback when suggestions have no target videos (NOT random videos).
4. Countdown tracking and non-target random buffer video playback.
5. Fallback back to Home feed after consecutive suggested misses.
6. Clean stop and stats verification.
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
        self.channel1 = Channel(name="AlphaChannel", url="https://youtube.com/@alphachannel", enabled=True)
        self.channel2 = Channel(name="BetaChannel", url="https://youtube.com/@betachannel", enabled=True)
        self.channel3 = Channel(name="GammaChannel", url="https://youtube.com/@gammachannel", enabled=True)
        self.log_queue = queue.Queue()
        self.event_queue = queue.Queue()
        self.stop_event = threading.Event()

    @patch("src.automation.watching_worker.time.sleep")
    @patch("src.automation.watching_worker.setup_driver")
    @patch("src.automation.watching_worker.perform_google_login", return_value=True)
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
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.youtube.com/"
        mock_driver.find_elements.return_value = [MagicMock()]
        mock_setup.return_value = mock_driver

        mock_config = MagicMock(spec=ConfigManager)
        mock_config.get_channels.return_value = [self.channel1.to_dict(), self.channel2.to_dict()]
        mock_config.get_video_queue.return_value = [
            {"title": "Alpha Video 1", "url": "https://www.youtube.com/watch?v=ALPHA111111", "channel": "AlphaChannel", "enabled": True},
            {"title": "Beta Video 1", "url": "https://www.youtube.com/watch?v=BETA1111111", "channel": "BetaChannel", "enabled": True},
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
            channels=[self.channel1, self.channel2],
            config_mgr=mock_config,
            log_queue=self.log_queue,
            stop_event=self.stop_event,
            event_queue=self.event_queue,
            stats_store=mock_stats,
            is_unlimited=True,
            target_limit=None,
            open_videos=True,
        )
        worker._send_session_report = MagicMock()
        worker._send_start_notification = MagicMock()
        worker._send_critical_alert = MagicMock()

        workflow_steps = []

        # 1. Mock Home feed scan
        home_cards = [
            {"title": "Alpha Home Video", "href": "/watch?v=ALPHA111111", "channel_name": "AlphaChannel", "channel_url": ""},
        ]
        mock_driver.execute_script.return_value = home_cards

        # 2. Mock _play_target_video
        def play_target_side_effect(item, full_url, title, ch_display, session_keys, is_home=False, **kwargs):
            workflow_steps.append(f"play_target:{title}:is_home={is_home}:channel={ch_display}")
            mock_driver.current_url = full_url
            return WatchResult(completed=True, watched_seconds=60.0, video_duration=180.0, playback_finished=True)

        worker._play_target_video = MagicMock(side_effect=play_target_side_effect)

        # 3. Mock suggested videos:
        # First iteration returns BetaChannel (alternate channel preference)
        # Second iteration returns no target channel videos
        suggested_pool = [
            {"title": "Beta Suggested Video", "href": "/watch?v=BETA1111111", "channel_name": "BetaChannel", "channel_url": ""},
        ]

        def fetch_recs_side_effect():
            if any(s.startswith("play_target:Beta Suggested Video") for s in workflow_steps):
                # Now return suggestions with only non-target videos
                return [{"title": "Unrelated Video", "href": "/watch?v=UNRELATED99", "channel_name": "RandomCreator", "channel_url": ""}]
            return list(suggested_pool)

        worker.fetch_recommended_videos = MagicMock(side_effect=fetch_recs_side_effect)

        # 4. Mock _watch_random_video
        def watch_random_side_effect(card_info, dur, session_keys):
            workflow_steps.append(f"watch_random_buffer:{card_info.get('title')}")
            return True

        worker._watch_random_video = MagicMock(side_effect=watch_random_side_effect)

        # 5. Mock search_and_play_target_video
        def search_side_effect(session_watched_keys=None, preferred_channel_to_avoid=None, **kwargs):
            workflow_steps.append(f"search_executed:avoid={preferred_channel_to_avoid}")
            worker._play_target_video(
                {"title": "Alpha Searched Video", "url": "https://www.youtube.com/watch?v=ALPHA222222"},
                "https://www.youtube.com/watch?v=ALPHA222222",
                "Alpha Searched Video",
                "AlphaChannel",
                session_watched_keys or set(),
                is_home=False,
            )
            # Stop worker after search step
            worker.stop_event.set()
            return "AlphaChannel"

        worker.search_and_play_target_video = MagicMock(side_effect=search_side_effect)

        # Set buffer threshold high for this specific test so search transition is reached
        with patch("random.randint", return_value=5):
            worker.run()

        # Assertions
        # 1. Home feed target video was played
        self.assertIn("play_target:Alpha Home Video:is_home=True:channel=AlphaChannel", workflow_steps)

        # 2. Suggested section prioritized alternate channel BetaChannel
        self.assertIn("play_target:Beta Suggested Video:is_home=False:channel=BetaChannel", workflow_steps)

        # 3. When suggestions had no target channel videos, search was executed (NOT random video)
        search_calls = [s for s in workflow_steps if s.startswith("search_executed")]
        self.assertEqual(len(search_calls), 1)

        # 4. Searched video was played
        self.assertIn("play_target:Alpha Searched Video:is_home=False:channel=AlphaChannel", workflow_steps)

        # 5. Clean stop
        self.assertTrue(worker.stop_event.is_set())
        mock_close.assert_called_once()

    def test_find_preferred_target_video_channel_alternation(self):
        """Verify _find_preferred_target_video prioritizes another channel and falls back only when under limit."""
        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel1, self.channel2],
            config_mgr=MagicMock(get_video_queue=MagicMock(return_value=[])),
        )
        worker.is_candidate_eligible = MagicMock(return_value=(True, None, "eligible"))

        candidates = [
            {"title": "Alpha 1", "href": "/watch?v=ALPHA111111", "channel_name": "AlphaChannel", "channel_url": ""},
            {"title": "Beta 1", "href": "/watch?v=BETA1111111", "channel_name": "BetaChannel", "channel_url": ""},
        ]

        # 1. If last watched was AlphaChannel, it must pick BetaChannel first
        match = worker._find_preferred_target_video(
            candidates=candidates,
            target_channels=[self.channel1, self.channel2],
            last_channel_name="AlphaChannel",
            session_watched_keys=set(),
            channel_consecutive_counts={"alphachannel": 1},
        )
        self.assertIsNotNone(match)
        self.assertEqual(match[4], "BetaChannel")

        # 2. If only AlphaChannel is in candidates and countdown is 2 (< 3), fall back to AlphaChannel
        match_fallback = worker._find_preferred_target_video(
            candidates=[candidates[0]],
            target_channels=[self.channel1, self.channel2],
            last_channel_name="AlphaChannel",
            session_watched_keys=set(),
            channel_consecutive_counts={"alphachannel": 2},
        )
        self.assertIsNotNone(match_fallback)
        self.assertEqual(match_fallback[4], "AlphaChannel")

        # 3. If countdown reaches 3, reject same channel
        match_rejected = worker._find_preferred_target_video(
            candidates=[candidates[0]],
            target_channels=[self.channel1, self.channel2],
            last_channel_name="AlphaChannel",
            session_watched_keys=set(),
            channel_consecutive_counts={"alphachannel": 3},
        )
        self.assertIsNone(match_rejected)

    def test_random_buffer_video_trigger_and_duration_scaling(self):
        """Verify _check_and_play_random_buffer_video triggers on countdown or threshold and scales duration."""
        mock_config = MagicMock()
        mock_config.get_channels.return_value = [self.channel1.to_dict(), self.channel2.to_dict(), self.channel3.to_dict()]
        mock_config.get_float.return_value = 12.0
        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel1, self.channel2, self.channel3],
            config_mgr=mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = MagicMock()
        worker.driver.current_url = "https://www.youtube.com/watch?v=CURR1111111"
        worker.fetch_recommended_videos = MagicMock(return_value=[
            {"title": "Random NonTarget Video", "href": "/watch?v=RND11111111", "channel_name": "Stranger", "channel_url": ""}
        ])

        recorded_durations = []
        def mock_watch_random(card_info, dur, keys):
            recorded_durations.append(dur)
            return True

        worker._watch_random_video = MagicMock(side_effect=mock_watch_random)

        # 1. Condition not met (consecutive < threshold and count < 3)
        res = worker._check_and_play_random_buffer_video(
            session_watched_keys=set(),
            consecutive_target_watched=1,
            random_buffer_threshold=3,
            channel_consecutive_counts={"alphachannel": 1},
        )
        self.assertFalse(res)
        self.assertEqual(len(recorded_durations), 0)

        # 2. Condition met via countdown reaching 3
        res = worker._check_and_play_random_buffer_video(
            session_watched_keys=set(),
            consecutive_target_watched=1,
            random_buffer_threshold=3,
            channel_consecutive_counts={"alphachannel": 3},
        )
        self.assertTrue(res)
        self.assertEqual(len(recorded_durations), 1)
        # With 3 channels, duration must scale up to 60.0 - 120.0s
        self.assertGreaterEqual(recorded_durations[0], 60.0)
        self.assertLessEqual(recorded_durations[0], 120.0)


if __name__ == "__main__":
    unittest.main()
