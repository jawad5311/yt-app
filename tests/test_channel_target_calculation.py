from unittest.mock import MagicMock
import pytest

from src.automation.email_reporter import SessionReport
from src.automation.session_tracker import SessionChannelTracker
from src.config.manager import ConfigManager
from src.models.channel import Channel, calculate_channel_target_seconds
from src.models.video_task import DEFAULT_SHORTS_MAX_RUNS, DEFAULT_TARGET_MULTIPLIER, VideoTask


def test_calculate_channel_target_regular_videos():
    """Regular videos contribute (duration_seconds * target_multiplier) to target watch time."""
    ch = Channel(name="Tech Channel", url="https://youtube.com/@techchannel")
    # Video 1: 600s * 2.0 = 1200s
    v1 = VideoTask(channel="Tech Channel", duration_seconds=600, target_multiplier=2.0)
    # Video 2: 1800s * 1.5 = 2700s
    v2 = VideoTask(channel="@techchannel", duration_seconds=1800, target_multiplier=1.5)
    # Total = 3900s
    target = calculate_channel_target_seconds(ch, [v1, v2])
    assert target == 3900.0


def test_calculate_channel_target_shorts_view_count():
    """Shorts contribute (duration_seconds * number_of_views_per_short) to target watch time."""
    ch = Channel(name="Shorts Creator", url="https://youtube.com/@shortscreator")
    # Short 1: 60s * 5 views = 300s
    s1 = VideoTask(channel="Shorts Creator", url="https://youtube.com/shorts/s1", duration_seconds=60, max_runs=5)
    # Short 2: 30s * 10 views = 300s
    s2 = VideoTask(channel="@shortscreator", url="https://youtube.com/shorts/s2", duration_seconds=30, max_runs=10)
    # Total = 600s
    target = calculate_channel_target_seconds(ch, [s1, s2])
    assert target == 600.0


def test_calculate_channel_target_mixed_videos_and_shorts():
    """Channel with both regular videos and shorts combines their calculated watch times."""
    ch = Channel(name="Creator Hub", url="https://youtube.com/@creatorhub")
    # Regular: 1000s * 3.0 = 3000s
    v = VideoTask(channel="Creator Hub", duration_seconds=1000, target_multiplier=3.0)
    # Short: 45s * 20 views = 900s
    s = VideoTask(channel="Creator Hub", url="https://youtube.com/shorts/ch1", duration_seconds=45, max_runs=20)
    # Total = 3900s
    target = calculate_channel_target_seconds(ch, [v, s])
    assert target == 3900.0


def test_calculate_channel_target_defaults():
    """When multiplier or views are <= 0, default constants (3.0x and 5 views) apply."""
    ch = Channel(name="Default Chan", url="https://youtube.com/@defaultchan")
    # Regular video with 0.0 multiplier: uses DEFAULT_TARGET_MULTIPLIER (3.0) -> 200s * 3.0 = 600s
    v = VideoTask(channel="Default Chan", duration_seconds=200, target_multiplier=0.0)
    # Short with 0 max_runs: uses DEFAULT_SHORTS_MAX_RUNS (5) -> 40s * 5 = 200s
    s = VideoTask(channel="Default Chan", url="https://youtube.com/shorts/def1", duration_seconds=40, max_runs=0)
    target = calculate_channel_target_seconds(ch, [v, s])
    assert target == (200.0 * DEFAULT_TARGET_MULTIPLIER) + (40.0 * DEFAULT_SHORTS_MAX_RUNS)


def test_calculate_channel_target_fallback_durations():
    """When durations are 0, fallback durations (300s for regular, 30s for shorts) apply."""
    ch = Channel(name="Zero Dur Chan")
    v = VideoTask(channel="Zero Dur Chan", duration_seconds=0, target_multiplier=2.0)
    s = VideoTask(channel="Zero Dur Chan", url="https://youtube.com/shorts/z1", duration_seconds=0, max_runs=5)
    # Regular: 300s * 2.0 = 600s
    # Short: 30s * 5 = 150s
    target = calculate_channel_target_seconds(ch, [v, s])
    assert target == 750.0


def test_session_tracker_report_data_uses_calculated_targets(tmp_path):
    """SessionChannelTracker compiles target progress text from videos & shorts."""
    cfg = ConfigManager(tmp_path / "settings.json")
    ch = Channel(name="Summia Jamshed", url="https://youtube.com/@SummiaJamshed", watch_seconds=1800.0)
    cfg.set_channels([ch.to_dict()])

    # Queue: 1 regular video (3600s * 2.0 = 7200s), 1 short (60s * 10 views = 600s) -> Total target = 7800s (2.166h -> 2.2h)
    tasks = [
        {"channel": "Summia Jamshed", "title": "Vid 1", "duration_seconds": 3600, "target_multiplier": 2.0, "is_short": False},
        {"channel": "Summia Jamshed", "title": "Short 1", "url": "https://youtube.com/shorts/s1", "duration_seconds": 60, "max_runs": 10, "is_short": True},
    ]
    cfg.set_video_queue(tasks)

    tracker = SessionChannelTracker(config_mgr=cfg, channels=[ch])
    tracker.set_active_channel("Summia Jamshed")
    tracker.add_watch_delta("Summia Jamshed", 1800.0) # total 3600s watched (1.0h)

    report_data = tracker.get_session_report_data()
    ch_report = report_data["channels"][0]
    assert ch_report["name"] == "Summia Jamshed"
    assert ch_report["target_watch_seconds"] == 7800.0
    assert ch_report["target_achieved"] is False
    # 3600s = 1.0h, 7800s = 2.2h, (3600/7800)*100 = 46%
    assert "1.0h / 2.2h (46%)" in ch_report["target_goal_text"]


def test_session_tracker_target_achieved(tmp_path):
    """SessionChannelTracker notes target achieved when watch time equals or exceeds target."""
    cfg = ConfigManager(tmp_path / "settings.json")
    ch = Channel(name="Iamcarhacker", url="https://youtube.com/@iamcarhacker", watch_seconds=3600.0)
    cfg.set_channels([ch.to_dict()])

    # Queue: 1 regular video of 1200s with 1.0x multiplier -> Target = 1200s (0.3h)
    tasks = [
        {"channel": "Iamcarhacker", "title": "Hacker Vid", "duration_seconds": 1200, "target_multiplier": 1.0, "is_short": False},
    ]
    cfg.set_video_queue(tasks)

    tracker = SessionChannelTracker(config_mgr=cfg, channels=[ch])
    # Channel already has 3600s watch time >= 1200s target
    assert tracker.is_channel_target_achieved("Iamcarhacker") is True

    report_data = tracker.get_session_report_data()
    ch_report = report_data["channels"][0]
    assert ch_report["target_achieved"] is True
    assert "[ACHIEVED]" in ch_report["target_goal_text"]


def test_email_reporter_renders_target_progress_in_table(tmp_path):
    """Email report generates plain text and HTML displaying the calculated target progress."""
    cfg = ConfigManager(tmp_path / "settings.json")
    ch1 = Channel(name="Summia Jamshed", url="https://youtube.com/@SummiaJamshed", watch_seconds=3600.0)
    ch2 = Channel(name="Iamcarhacker", url="https://youtube.com/@iamcarhacker", watch_seconds=7200.0)
    cfg.set_channels([ch1.to_dict(), ch2.to_dict()])

    # Queue for Summia: 3600s * 3.0 = 10800s = 3.0h
    # Queue for Iamcarhacker: 3600s * 2.0 = 7200s = 2.0h
    cfg.set_video_queue([
        {"channel": "Summia Jamshed", "title": "V1", "duration_seconds": 3600, "target_multiplier": 3.0},
        {"channel": "Iamcarhacker", "title": "V2", "duration_seconds": 3600, "target_multiplier": 2.0},
    ])

    tracker = SessionChannelTracker(config_mgr=cfg, channels=[ch1, ch2])
    tracker.set_active_channel("Summia Jamshed")
    tracker.add_watch_delta("Summia Jamshed", 1800.0)
    tracker.record_video_watched(is_short=False, channel_name="Summia Jamshed")

    tracker.set_active_channel("Iamcarhacker")
    tracker.add_watch_delta("Iamcarhacker", 3600.0)
    tracker.record_video_watched(is_short=True, channel_name="Iamcarhacker")

    report_data = tracker.get_session_report_data()
    report = SessionReport(
        account_email="test@example.com",
        session_mode="Automated",
        status="Completed",
        channel_reports=report_data["channels"],
        start_time=report_data["start_time"],
        end_time=report_data["end_time"],
        duration_seconds=report_data["duration_seconds"],
        total_session_watch_seconds=report_data["total_session_watch_seconds"],
    )

    plain = report.generate_plain_text()
    assert "Target Progress" in plain
    assert "Summia Jamshed" in plain
    assert "Iamcarhacker" in plain
    assert "No target set" not in plain

    html = report.generate_html()
    assert "Summia Jamshed" in html
    assert "Iamcarhacker" in html
    assert "No target set" not in html
