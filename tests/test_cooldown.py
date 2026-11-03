"""Unit tests for the video rewatch cooldown feature."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel, is_video_task_runnable
from src.models.video_task import VideoTask
from src.models.watch_stats import (
    StatRecord,
    TaskOutcome,
    is_within_cooldown,
)


class TestVideoCooldown:
    def test_is_within_cooldown(self):
        now = datetime(2026, 10, 26, 12, 0, 0)

        # 1. Watched 2 hours ago with 12h cooldown -> in cooldown, 10h remaining
        two_hours_ago = (now - timedelta(hours=2)).isoformat()
        is_cool, remaining = is_within_cooldown(two_hours_ago, 12.0, current_time=now)
        assert is_cool is True
        assert round(remaining, 1) == 10.0

        # 2. Watched 12.5 hours ago with 12h cooldown -> cooldown expired
        expired = (now - timedelta(hours=12.5)).isoformat()
        is_cool, remaining = is_within_cooldown(expired, 12.0, current_time=now)
        assert is_cool is False
        assert remaining == 0.0

        # 3. Exactly 12 hours ago
        exact = (now - timedelta(hours=12.0)).isoformat()
        is_cool, remaining = is_within_cooldown(exact, 12.0, current_time=now)
        assert is_cool is False

        # 4. Cooldown set to 0 -> disabled
        is_cool, _ = is_within_cooldown(two_hours_ago, 0.0, current_time=now)
        assert is_cool is False

        # 5. Empty or None timestamp
        assert is_within_cooldown("", 12.0)[0] is False
        assert is_within_cooldown(None, 12.0)[0] is False

    def test_stat_record_last_completed_and_is_on_cooldown(self):
        now = datetime(2026, 10, 26, 15, 0, 0)
        rec = StatRecord(label="Test Video")

        outcome = TaskOutcome(
            video_key="vid123",
            success=True,
            playback_finished=True,
            watched_seconds=180.0,
        )
        timestamp = (now - timedelta(hours=3)).isoformat()
        rec.merge(outcome, timestamp)

        assert rec.last_completed == timestamp
        assert rec.last_run == timestamp

        # Check cooldown
        is_cool, remaining = rec.is_on_cooldown(12.0, current_time=now)
        assert is_cool is True
        assert round(remaining, 1) == 9.0

        # Check serialization
        d = rec.to_dict()
        assert d["last_completed"] == timestamp
        restored = StatRecord.from_dict(d)
        assert restored.last_completed == timestamp

    def test_is_candidate_eligible_skips_within_cooldown(self, tmp_path):
        config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
        config_mgr.set_setting("VideoCooldownHours", "12")

        stats_store = MagicMock()
        now = datetime.now()
        recent_ts = (now - timedelta(hours=4)).isoformat()

        mock_rec = StatRecord(
            label="Throttle adaptation",
            runs=1,
            watch_seconds=120.0,
            video_duration=120.0,
            last_completed=recent_ts,
        )
        stats_store.find_for_task.return_value = mock_rec
        stats_store.get.return_value = mock_rec

        worker = WatchingWorker(
            account=Account(email="test@gmail.com", password="pwd"),
            channels=[Channel(name="Iamcarhacker", url="")],
            config_mgr=config_mgr,
            stats_store=stats_store,
        )

        eligible, task, reason = worker.is_candidate_eligible(
            full_url="https://www.youtube.com/watch?v=umjVEhetrzA",
            title="Throttle adaptation",
            channel_name="Iamcarhacker",
        )

        # Video completed 4 hours ago -> within 12h cooldown, skipped!
        assert eligible is False
        assert "within 12h cooldown period" in reason
        assert "remaining" in reason

    def test_is_candidate_eligible_allows_after_cooldown_expires(self, tmp_path):
        config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
        config_mgr.set_setting("VideoCooldownHours", "12")

        stats_store = MagicMock()
        now = datetime.now()
        old_ts = (now - timedelta(hours=14)).isoformat()

        mock_rec = StatRecord(
            label="Throttle adaptation",
            runs=1,
            watch_seconds=120.0,
            video_duration=120.0,
            last_completed=old_ts,
        )
        stats_store.find_for_task.return_value = mock_rec
        stats_store.get.return_value = mock_rec

        worker = WatchingWorker(
            account=Account(email="test@gmail.com", password="pwd"),
            channels=[Channel(name="Iamcarhacker", url="")],
            config_mgr=config_mgr,
            stats_store=stats_store,
        )

        eligible, task, reason = worker.is_candidate_eligible(
            full_url="https://www.youtube.com/watch?v=umjVEhetrzA",
            title="Throttle adaptation",
            channel_name="Iamcarhacker",
        )

        # Completed 14 hours ago -> cooldown passed -> eligible to rewatch!
        assert eligible is True
        assert "eligible" in reason or "runs" in reason

    def test_is_video_task_runnable_respects_cooldown(self):
        stats_store = MagicMock()
        now = datetime.now()
        recent_ts = (now - timedelta(hours=1)).isoformat()

        mock_rec = StatRecord(
            label="Video 1",
            runs=1,
            last_completed=recent_ts,
        )
        stats_store.find_for_task.return_value = mock_rec

        task = VideoTask(url="https://youtube.com/watch?v=11111111111", title="Video 1", channel="Channel A")
        ch = Channel(name="Channel A", url="")

        # With 12h cooldown, task is not runnable
        assert is_video_task_runnable(task, [ch], stats_store, cooldown_hours=12.0) is False

        # With 0h cooldown (disabled), task is runnable
        assert is_video_task_runnable(task, [ch], stats_store, cooldown_hours=0.0) is True

    def test_cooldown_updates_last_completed_on_watch(self):
        now = datetime(2026, 11, 4, 12, 0, 0)
        rec = StatRecord(label="Rewatch Video")
        outcome = TaskOutcome(
            video_key="vid123",
            success=False,
            playback_finished=False,
            watched_seconds=111.5,
        )
        timestamp = (now - timedelta(hours=1)).isoformat()
        rec.merge(outcome, timestamp)

        assert rec.last_completed == timestamp
        assert rec.last_run == timestamp

        # Under 12h cooldown, should be on cooldown based on the recent watch 1h ago!
        is_cool, remaining = rec.is_on_cooldown(12.0, current_time=now)
        assert is_cool is True
        assert round(remaining, 1) == 11.0

    def test_is_within_cooldown_timezone_awareness(self):
        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).isoformat()

        # Should not raise TypeError when subtracting timezone aware and naive
        is_cool, remaining = is_within_cooldown(one_hour_ago, 12.0, current_time=datetime.now())
        assert is_cool is True
        assert 10.0 <= remaining <= 11.5

    def test_home_shorts_filter_by_cooldown(self, tmp_path):
        config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
        config_mgr.set_setting("VideoCooldownHours", "12")
        config_mgr.update_video_queue(lambda q: [
            {"url": "https://www.youtube.com/shorts/cool_short_1", "title": "Cool Short 1", "enabled": True, "is_short": True},
            {"url": "https://www.youtube.com/shorts/cool_short_2", "title": "Cool Short 2", "enabled": True, "is_short": True},
        ])

        now = datetime.now()
        stats_store = MagicMock()
        # cool_short_1 was watched 1 hour ago -> within 12h cooldown
        rec1 = StatRecord(label="Cool Short 1", last_run=(now - timedelta(hours=1)).isoformat())
        # cool_short_2 was watched 20 hours ago -> expired
        rec2 = StatRecord(label="Cool Short 2", last_run=(now - timedelta(hours=20)).isoformat())

        def mock_find(task):
            if "cool_short_1" in task.url:
                return rec1
            elif "cool_short_2" in task.url:
                return rec2
            return None

        stats_store.find_for_task.side_effect = mock_find

        worker = WatchingWorker(
            account=Account(email="test@gmail.com", password="pwd"),
            channels=[Channel(name="Target Channel", url="")],
            config_mgr=config_mgr,
            stats_store=stats_store,
        )

        # get_target_shorts with filter_cooldown=True should only return cool_short_2!
        available_shorts = worker.get_target_shorts(filter_cooldown=True)
        assert len(available_shorts) == 1
        assert "cool_short_2" in available_shorts[0].url

    def test_shorts_reel_skips_target_short_on_cooldown(self, tmp_path):
        import queue
        import threading
        from unittest.mock import patch

        config_mgr = ConfigManager(config_file=tmp_path / "settings.json")
        config_mgr.set_setting("VideoCooldownHours", "12")
        config_mgr.set_setting("NonTargetReelsLimit", 2)

        now = datetime.now()
        stats_store = MagicMock()
        # On cooldown (watched 1h ago)
        rec_cool = StatRecord(label="Cool Short", last_completed=(now - timedelta(hours=1)).isoformat())
        stats_store.get.return_value = rec_cool
        stats_store.find_for_task.return_value = rec_cool

        worker = WatchingWorker.__new__(WatchingWorker)
        worker.config_mgr = config_mgr
        worker.stats_store = stats_store
        worker.stop_event = threading.Event()
        worker.pause_event = threading.Event()
        worker.log_queue = queue.Queue()
        worker.check_pause = MagicMock()
        worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="")])
        worker.get_target_shorts = MagicMock(return_value=[])
        worker.channel_tracker = MagicMock()

        # Reel returns the target short, which is on cooldown
        worker.get_active_short_info = MagicMock(return_value={
            "video_id": "cool_vid_123",
            "url": "https://www.youtube.com/shorts/cool_vid_123",
            "title": "Cool Short",
            "channel_name": "Target Channel",
        })
        worker._skip_non_target_reel = MagicMock(side_effect=lambda: worker.stop_event.set())

        with patch("src.automation.watching_worker.watch_short") as mock_ws:
            worker._run_shorts_reel_loop(session_watched_keys=set())
            # watch_short must NOT be called for short on cooldown
            mock_ws.assert_not_called()
            # _skip_non_target_reel was invoked to advance past the cooldown short
            worker._skip_non_target_reel.assert_called_once()

