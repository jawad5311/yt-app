"""Unit tests for search matching, channel scoping, candidate eligibility filtering,
and play-key throttling fixes."""

import queue
import unittest
from unittest.mock import MagicMock, patch

from src.automation.navigation import find_and_click_search_result
from src.automation.watching_worker import WatchingWorker
from src.automation.youtube_actions import ensure_video_playing
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import WatchResult


class TestSearchPlaybackLoopFixes(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.log_queue = queue.Queue()
        self.delay_patch = patch("src.automation.navigation.human_like_delay")
        self.delay_patch.start()
        self.account = Account(email="test@example.com", password="password123")
        self.channel1 = Channel(name="Lifestyle Mapping", enabled=True)
        self.channel2 = Channel(name="The Kidney Nutrition Institute", enabled=True)
        self.mock_config = MagicMock()
        self.mock_config.get_int.return_value = 5
        self.mock_config.get_float.return_value = 18.0
        self.mock_config.get_bool.return_value = True

    def tearDown(self):
        self.delay_patch.stop()

    def test_find_and_click_search_result_rejects_non_target_channel_matching_title(self):
        """When searching for a title query (e.g. 'Blessing'), a search result from an unrelated

        channel (e.g. 'Elevation Worship') must NOT be matched or clicked.
        """
        task = VideoTask(title="Blessing", channel="Lifestyle Mapping")
        results_payload = [
            {
                "title": "The Blessing with Kari Jobe | Elevation Worship",
                "url": "https://www.youtube.com/watch?v=ELEVATION_123",
                "channel": "Elevation Worship",
                "channelHref": "https://www.youtube.com/@elevationworship",
            },
            {
                "title": "Blessing: How Gratitude Shifts Mindset",
                "url": "https://www.youtube.com/watch?v=TARGET_LIFESTYLE_456",
                "channel": "Lifestyle Mapping",
                "channelHref": "https://www.youtube.com/@lifestylemapping",
            },
        ]
        self.driver.execute_script.side_effect = [
            results_payload,
            True,  # scroll
            True,  # click
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            matched = find_and_click_search_result(
                self.driver,
                task=task,
                target_channels=["Lifestyle Mapping", "The Kidney Nutrition Institute"],
                log_queue=self.log_queue,
            )
            self.assertIsNotNone(matched)
            # Must match the Lifestyle Mapping result, NOT Elevation Worship!
            self.assertEqual(matched["url"], "https://www.youtube.com/watch?v=TARGET_LIFESTYLE_456")
            self.assertEqual(matched["channel"], "Lifestyle Mapping")

    def test_find_and_click_search_result_priority3_does_not_hijack_unmatched_title_task(self):
        """When searching for a specific video title ('Brown'), if the title doesn't match,

        Priority 3 must NOT arbitrarily pick another video from the same channel ('Beyond the Renal Diet').
        """
        task = VideoTask(title="Brown", channel="The Kidney Nutrition Institute")
        results_payload = [
            {
                "title": "Beyond the Renal Diet",
                "url": "https://www.youtube.com/watch?v=RENAL_DIET_111",
                "channel": "The Kidney Nutrition Institute",
                "channelHref": "https://www.youtube.com/@kidneynutrition",
            },
        ]
        self.driver.execute_script.side_effect = [
            results_payload,  # scan attempt 1
            [],               # scan attempt 2 (after scroll)
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"):
            matched = find_and_click_search_result(
                self.driver,
                task=task,
                target_channels=["The Kidney Nutrition Institute"],
                max_scrolls=1,
                log_queue=self.log_queue,
            )
            # Should NOT match Beyond the Renal Diet when task explicitly searched for 'Brown'
            self.assertIsNone(matched)

    def test_search_and_play_target_video_filters_out_completed_and_cooldown_tasks(self):
        """Videos that already reached target multiplier or are on cooldown must be excluded

        from search candidates to prevent infinite restart loops.
        """
        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel1, self.channel2],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = self.driver
        self.mock_config.get_video_queue.return_value = [
            {
                "title": "Beyond the Renal Diet",
                "url": "https://www.youtube.com/watch?v=COMPLETED_1",
                "channel": "The Kidney Nutrition Institute",
                "enabled": True,
            },
            {
                "title": "Fresh Eligible Video",
                "url": "https://www.youtube.com/watch?v=FRESH_2",
                "channel": "Lifestyle Mapping",
                "enabled": True,
            },
        ]

        def mock_is_eligible(url, title, channel):
            if "COMPLETED" in url:
                return False, None, "already reached target multiplier reached"
            return True, None, "eligible"

        searched_queries = []

        def mock_search(query):
            searched_queries.append(query)
            return True

        with patch.object(worker, "is_candidate_eligible", side_effect=mock_is_eligible):
            with patch.object(worker, "search_youtube_query", side_effect=mock_search):
                with patch("src.automation.watching_worker.find_and_click_search_result") as mock_find:
                    mock_find.return_value = {
                        "title": "Fresh Eligible Video",
                        "url": "https://www.youtube.com/watch?v=FRESH_2",
                        "channel": "Lifestyle Mapping",
                    }
                    with patch.object(worker, "_play_target_video") as mock_play:
                        mock_play.return_value = WatchResult(completed=True, watched_seconds=30.0, video_duration=100.0)
                        session_keys = set()
                        played_ch = worker.search_and_play_target_video(session_keys)
                        self.assertEqual(played_ch, "Lifestyle Mapping")
                        # Beyond the Renal Diet must NEVER have been searched
                        for q in searched_queries:
                            self.assertNotIn("Beyond the Renal Diet", q)
                        # Channel name should be scoped
                        self.assertTrue(any("Lifestyle Mapping" in q for q in searched_queries))

    def test_search_and_play_target_video_zero_seconds_does_not_return_success(self):
        """If a search result video immediately stops (0.0s watched, e.g. target achieved or channel disabled),

        search_and_play_target_video must not report it as a successfully played channel.
        """
        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel1],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = self.driver
        self.mock_config.get_video_queue.return_value = [
            {"title": "Candidate 1", "url": "https://www.youtube.com/watch?v=CAND1", "channel": "Lifestyle Mapping", "enabled": True},
        ]

        with patch.object(worker, "is_candidate_eligible", return_value=(True, None, "eligible")):
            with patch.object(worker, "search_youtube_query", return_value=True):
                with patch("src.automation.watching_worker.find_and_click_search_result") as mock_find:
                    mock_find.return_value = {"title": "Candidate 1", "url": "https://www.youtube.com/watch?v=CAND1", "channel": "Lifestyle Mapping"}
                    with patch.object(worker, "_play_target_video") as mock_play:
                        # 0.0s watched, completed=False (immediate cutoff)
                        mock_play.return_value = WatchResult(completed=False, watched_seconds=0.0, reason="video_target_achieved")
                        session_keys = set()
                        res = worker.search_and_play_target_video(session_keys)
                        # Must return None so channel countdown is NOT incremented!
                        self.assertIsNone(res)

    def test_ensure_video_playing_verbose_false_suppresses_logs(self):
        """When verbose=False, ensure_video_playing must not emit play key shortcut messages."""
        self.driver.execute_script.side_effect = [
            {"is_ad": False},  # ad_check
            {"found": True, "playing": False},  # JS probe
            False,  # HTML5 play
            True,   # is_paused check before k
            False,  # is_still_paused after k (success)
        ]
        self.driver.find_elements.return_value = []
        body_el = MagicMock()
        self.driver.find_element.return_value = body_el

        ok = ensure_video_playing(self.driver, log_queue=self.log_queue, verbose=False)
        self.assertTrue(ok)
        self.assertTrue(self.log_queue.empty())


if __name__ == "__main__":
    unittest.main()
