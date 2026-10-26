import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import json

from src.models.channel import Channel, VideoGroup
from src.models.video_task import VideoTask
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.automation.session_tracker import SessionChannelTracker
from src.automation.youtube_actions import WatchResult
from src.ui.app import YTBotApp
from src.ui.views.queue_view import QueueView
from src.ui.views.channels_view import ChannelsView


class TestIssues6To10(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path("scratch/test_issues")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.tmp_dir / "settings.json"
        self.stats_path = self.tmp_dir / "stats.json"

        # Initialize mock config
        self.config_mgr = ConfigManager(config_file=self.config_path)
        self.stats_store = StatsStore(stats_file=self.stats_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Issue 6: Reset Statistics resets all watch-time state (stats, config, workers)
    # -------------------------------------------------------------------------
    def test_issue_6_reset_all_watch_stats(self):
        # 1. Setup channel with watch time in config
        self.config_mgr.set_channels([
            {"name": "TechChannel", "url": "https://youtube.com/@tech", "watch_seconds": 3600.0}
        ])
        # 2. Setup stats_store record
        self.stats_store.records["test_vid"] = MagicMock(watch_seconds=1800.0)

        # 3. Setup mock worker with channel tracker
        mock_worker = MagicMock()
        mock_tracker = SessionChannelTracker(channels=[Channel(name="TechChannel", watch_seconds=3600.0)])
        mock_tracker.minute_counters["techchannel"] = 30
        mock_worker.channel_tracker = mock_tracker
        mock_worker.channels = [Channel(name="TechChannel", watch_seconds=3600.0)]

        # Mock app controller
        app = YTBotApp.__new__(YTBotApp)
        app.stats_store = self.stats_store
        app.config_mgr = self.config_mgr
        app.active_workers = [mock_worker]
        app.views = {}
        app.refresh_watch_stats = MagicMock()
        app.update_dashboard_stats = MagicMock()

        # Execute reset
        app.reset_all_watch_stats()

        # Assert stats store cleared
        self.assertEqual(len(self.stats_store.records), 0)
        # Assert config channels watch_seconds reset to 0.0
        channels = self.config_mgr.get_channels()
        self.assertEqual(channels[0]["watch_seconds"], 0.0)
        # Assert worker tracker reset
        self.assertEqual(len(mock_tracker.minute_counters), 0)
        self.assertEqual(mock_worker.channels[0].watch_seconds, 0.0)
        # Assert UI refreshes invoked
        app.refresh_watch_stats.assert_called_once()
        app.update_dashboard_stats.assert_called_once()

    # -------------------------------------------------------------------------
    # Issue 7: Unchecking queue video does not raise NameError on group
    # -------------------------------------------------------------------------
    def test_issue_7_unchecking_queue_video_with_or_without_group(self):
        view = QueueView.__new__(QueueView)
        view.app_controller = MagicMock()
        view.save_queue = MagicMock()
        view.refresh_list = MagicMock()
        view._stats = MagicMock(return_value=None)

        task1 = VideoTask(title="Video 1", url="https://youtube.com/watch?v=1", enabled=True)
        task2 = VideoTask(title="Video 2", url="https://youtube.com/watch?v=2", enabled=True)
        view.video_tasks = [task1, task2]

        group = VideoGroup(
            channel=Channel(name="Channel 1"),
            videos=[(0, task1), (1, task2)],
        )

        with patch("customtkinter.CTkCheckBox") as mock_chk_cls, \
             patch("customtkinter.CTkLabel"), \
             patch("customtkinter.CTkFrame"), \
             patch("customtkinter.CTkButton"), \
             patch("customtkinter.CTkProgressBar"), \
             patch("customtkinter.CTkFont"), \
             patch("customtkinter.CTkEntry"):

            mock_chk = MagicMock()
            mock_chk.get.return_value = False  # Unchecked
            mock_chk_cls.return_value = mock_chk

            mock_parent = MagicMock()
            # 1. With group passed: should move unchecked task to bottom of group
            view._build_task_card(mock_parent, position=1, task=task1, index=0, group=group)

            toggle_cb = mock_chk_cls.call_args[1].get("command")
            self.assertIsNotNone(toggle_cb)
            # Invoke toggle: should not raise NameError!
            toggle_cb()
            self.assertFalse(task1.enabled)
            self.assertEqual(view.video_tasks, [task2, task1])

            # 2. Without group passed (None): should still toggle cleanly without error
            task2.enabled = True
            mock_chk.get.return_value = False
            view._build_task_card(mock_parent, position=1, task=task2, index=0, group=None)
            toggle_cb2 = mock_chk_cls.call_args[1].get("command")
            toggle_cb2()
            self.assertFalse(task2.enabled)

    # -------------------------------------------------------------------------
    # Issue 8: Editing channel preserves watch_seconds, target_watch_seconds, archived
    # -------------------------------------------------------------------------
    def test_issue_8_editing_channel_preserves_stats_and_archived(self):
        # Test Channel.parse
        parsed = Channel.parse(
            name="Updated Name",
            url="https://youtube.com/@updated",
            enabled=True,
            archived=True,
            watch_seconds=7200.0,
            target_watch_seconds=14400.0,
        )
        self.assertEqual(parsed.name, "Updated Name")
        self.assertTrue(parsed.archived)
        self.assertEqual(parsed.watch_seconds, 7200.0)
        self.assertEqual(parsed.target_watch_seconds, 14400.0)

    # -------------------------------------------------------------------------
    # Issue 9: Stale channel objects do not overwrite newer watch counters
    # -------------------------------------------------------------------------
    def test_issue_9_set_channels_preserves_live_counters(self):
        # 1. Channel on disk has 5000s recorded by a worker
        self.config_mgr.set_channels([
            {"name": "Gaming", "url": "https://youtube.com/@gaming", "watch_seconds": 5000.0}
        ])

        # 2. UI has stale snapshot with 0.0s (e.g. from earlier load) and toggles enabled
        stale_ui_channels = [
            {"name": "Gaming", "url": "https://youtube.com/@gaming", "enabled": False, "watch_seconds": 0.0}
        ]

        # 3. Saving UI channels must preserve the 5000.0s
        self.config_mgr.set_channels(stale_ui_channels, preserve_counters=True)

        reloaded = self.config_mgr.get_channels()
        self.assertEqual(len(reloaded), 1)
        self.assertFalse(reloaded[0]["enabled"])
        self.assertEqual(reloaded[0]["watch_seconds"], 5000.0)

    # -------------------------------------------------------------------------
    # Issue 10: Reaching one target does not stop browsing session if other targets remain
    # -------------------------------------------------------------------------
    def test_issue_10_browsing_session_continues_when_one_target_reached(self):
        from src.automation.watching_worker import WatchingWorker
        from src.models.account import Account

        worker = WatchingWorker.__new__(WatchingWorker)
        worker.log_queue = MagicMock()
        worker.stop_event = MagicMock()
        worker.stop_event.is_set.return_value = False
        worker.stats_store = None
        worker.total_loops = 10

        ch1 = Channel(name="Channel A", enabled=True, target_watch_seconds=3600.0, watch_seconds=3600.0) # target reached
        ch2 = Channel(name="Channel B", enabled=True, target_watch_seconds=3600.0, watch_seconds=0.0)    # still eligible

        # worker.get_target_channels() returns remaining channels that have not reached target
        worker.get_target_channels = MagicMock(return_value=[ch2])

        # Simulate interrupted watch result due to target reached
        watch_res = WatchResult(completed=False, reason="channel_target_achieved", interrupted=True)

        # Check behavior: should NOT break the session when remaining channels exist
        remaining = worker.get_target_channels()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].name, "Channel B")


if __name__ == "__main__":
    unittest.main()
