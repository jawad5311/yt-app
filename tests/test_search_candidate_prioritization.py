"""Tests for YouTube search candidate selection:
- Excludes YouTube Shorts from search candidate pool
- Prioritizes 0-watch-time videos first
- Prioritizes least-watch-time videos second (in ascending order)
- Verifies rawText channel discovery in find_and_click_search_result
"""

import queue
import unittest
from unittest.mock import MagicMock, patch

from src.automation.navigation import find_and_click_search_result
from src.automation.watching_worker import WatchingWorker
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord, WatchResult


class TestSearchCandidatePrioritization(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.log_queue = queue.Queue()
        self.account = Account(email="test@example.com", password="password123")
        self.channel1 = Channel(name="Channel A", enabled=True)
        self.channel2 = Channel(name="Channel B", enabled=True)
        self.mock_config = MagicMock()
        self.mock_config.get_int.return_value = 5
        self.mock_config.get_float.return_value = 18.0
        self.mock_config.get_bool.return_value = True

    def test_search_candidates_filter_out_shorts(self):
        """YouTube Shorts tasks must be strictly excluded from the search candidates pool."""
        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel1, self.channel2],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = self.driver

        self.mock_config.get_video_queue.return_value = [
            {
                "title": "Short Video 1",
                "url": "https://www.youtube.com/shorts/SHORT_11111",
                "channel": "Channel A",
                "enabled": True,
                "is_short": True,
            },
            {
                "title": "Short Video 2 (by url)",
                "url": "https://www.youtube.com/shorts/SHORT_22222",
                "channel": "Channel B",
                "enabled": True,
            },
            {
                "title": "Long Form Video",
                "url": "https://www.youtube.com/watch?v=LONG_111111",
                "channel": "Channel A",
                "enabled": True,
            },
        ]

        worker.is_candidate_eligible = MagicMock(return_value=(True, None, "eligible"))
        worker.search_youtube_query = MagicMock(return_value=True)

        found_tasks = []

        def mock_find(driver, task, **kwargs):
            found_tasks.append(task)
            return {
                "title": task.title,
                "url": task.url,
                "channel": task.channel,
            }

        with patch("src.automation.watching_worker.find_and_click_search_result", side_effect=mock_find):
            with patch.object(worker, "_play_target_video") as mock_play:
                mock_play.return_value = WatchResult(completed=True, watched_seconds=30.0, video_duration=100.0)
                worker.search_and_play_target_video(set())

        # Only the long form video should have been searched
        self.assertEqual(len(found_tasks), 1)
        self.assertEqual(found_tasks[0].title, "Long Form Video")
        self.assertNotIn("SHORT_11111", found_tasks[0].url)
        self.assertNotIn("SHORT_22222", found_tasks[0].url)

    def test_search_prioritizes_zero_watch_time_before_least_watch_time(self):
        """Tasks with 0 watch time must be searched before tasks with existing watch time,

        and watched tasks must be searched in ascending order of watch time.
        """
        worker = WatchingWorker(
            account=self.account,
            channels=[self.channel1, self.channel2],
            config_mgr=self.mock_config,
            log_queue=self.log_queue,
        )
        worker.driver = self.driver

        mock_stats = MagicMock()
        # Video 1: 500s watched
        rec1 = StatRecord(watch_seconds=500.0, runs=1)
        # Video 2: 0s watched (never played)
        rec2 = StatRecord(watch_seconds=0.0, runs=0)
        # Video 3: 120s watched
        rec3 = StatRecord(watch_seconds=120.0, runs=1)
        # Video 4: No record at all (never played)
        rec4 = None

        def mock_find_for_task(t: VideoTask):
            if "VID_500" in t.url:
                return rec1
            elif "VID_ZERO" in t.url:
                return rec2
            elif "VID_120" in t.url:
                return rec3
            elif "VID_NONE" in t.url:
                return rec4
            return None

        mock_stats.find_for_task.side_effect = mock_find_for_task
        worker.stats_store = mock_stats

        self.mock_config.get_video_queue.return_value = [
            {"title": "High Watch Time", "url": "https://www.youtube.com/watch?v=VID_5001111", "channel": "Channel A", "enabled": True},
            {"title": "Zero Watch Time", "url": "https://www.youtube.com/watch?v=VID_ZERO111", "channel": "Channel B", "enabled": True},
            {"title": "Medium Watch Time", "url": "https://www.youtube.com/watch?v=VID_1201111", "channel": "Channel A", "enabled": True},
            {"title": "No Record Video", "url": "https://www.youtube.com/watch?v=VID_NONE111", "channel": "Channel B", "enabled": True},
        ]

        worker.is_candidate_eligible = MagicMock(return_value=(True, None, "eligible"))
        worker.search_youtube_query = MagicMock(return_value=True)

        searched_titles = []

        def mock_find(driver, task, **kwargs):
            searched_titles.append(task.title)
            # Fail all to observe the full candidate order
            return None

        with patch("src.automation.watching_worker.find_and_click_search_result", side_effect=mock_find):
            worker.search_and_play_target_video(set())

        # Each task attempts up to 3 stages of search results scanning, so deduce the task sequence:
        unique_tasks = list(dict.fromkeys(searched_titles))

        # The zero-watch tasks ('Zero Watch Time' and 'No Record Video') must come before watched tasks!
        first_two = set(unique_tasks[:2])
        self.assertEqual(first_two, {"Zero Watch Time", "No Record Video"})

        # The watched tasks must follow in ascending order: 120s before 500s!
        last_two = unique_tasks[2:]
        self.assertEqual(last_two, ["Medium Watch Time", "High Watch Time"])

    def test_find_and_click_search_result_matches_channel_in_raw_text(self):
        """When YouTube's modern DOM renders channel name in a view model metadata row

        or raw text rather than legacy ytd-channel-name a, find_and_click_search_result
        must match via rawText.
        """
        task = VideoTask(title="Renal Diet Outcomes", channel="The Kidney Nutrition Institute")
        results_payload = [
            {
                "title": "Renal Diet Outcomes: Rethinking Restrictions",
                "url": "https://www.youtube.com/watch?v=TARGET_1234",
                "channel": "",  # Empty legacy channel element
                "channelHref": "",
                "rawText": "Renal Diet Outcomes: Rethinking Restrictions\nThe Kidney Nutrition Institute\n1.2K views • 2 weeks ago",
            },
        ]
        self.driver.execute_script.side_effect = [
            results_payload,
            True,  # scroll
            True,  # click
        ]
        with patch("src.automation.navigation.dismiss_youtube_dialogs"), \
             patch("src.automation.navigation.human_like_delay"):
            matched = find_and_click_search_result(
                self.driver,
                task=task,
                target_channels=["The Kidney Nutrition Institute"],
                log_queue=self.log_queue,
            )
            self.assertIsNotNone(matched)
            self.assertEqual(matched["url"], "https://www.youtube.com/watch?v=TARGET_1234")


if __name__ == "__main__":
    unittest.main()
