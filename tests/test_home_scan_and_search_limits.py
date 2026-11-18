"""Tests for 2-attempt Home feed scan and 5-videos-per-channel YouTube search prioritization & completion gating."""

from __future__ import annotations

import queue
from unittest.mock import MagicMock, patch

from src.automation.navigation import find_and_click_search_result
from src.automation.watching_worker import WatchingWorker
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import StatRecord


def _make_worker(channels=None, stats_store=None):
    mock_config = MagicMock()
    mock_config.get_int.return_value = 5
    mock_config.get_float.side_effect = lambda key, default=0.0: (
        3.0 if key == "DefaultVideoTargetViews" else (0.0 if key == "VideoCooldownHours" else default)
    )
    mock_config.get_bool.return_value = True
    mock_config.get_video_queue.return_value = []
    ch_list = channels if channels is not None else [
        Channel(name="Channel A", enabled=True),
        Channel(name="Channel B", enabled=True),
    ]
    mock_config.get_channels.return_value = [c.to_dict() for c in ch_list]

    worker = WatchingWorker(
        account=Account(email="test@example.com", password="pw"),
        channels=ch_list,
        config_mgr=mock_config,
        log_queue=queue.Queue(),
        stats_store=stats_store,
    )
    worker.driver = MagicMock()
    worker.driver.current_url = "https://www.youtube.com/"
    return worker, mock_config


def test_home_feed_scan_defaults_to_two_attempts():
    """_scan_home_feed_for_target must only scan the YouTube Home feed 2 times by default."""
    worker, _ = _make_worker()
    worker.driver.execute_script.return_value = []

    with patch("src.automation.watching_worker.human_like_delay"), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("src.automation.watching_worker.safe_get"):
        result = worker._scan_home_feed_for_target(session_watched_keys=set())

    assert result is None
    logs = []
    while not worker.log_queue.empty():
        logs.append(worker.log_queue.get_nowait())

    scan_lines = [line for line in logs if "Scanning YouTube Home feed (attempt " in line]
    assert len(scan_lines) == 2
    assert "attempt 1/2" in scan_lines[0]
    assert "attempt 2/2" in scan_lines[1]


def test_search_goes_through_at_most_five_videos_per_added_channel_prioritizing_zero_then_least_watch():
    """YouTube search selects up to 5 eligible videos per added channel, zero watch time first then least watch time."""
    records = {}
    queue_entries = []

    # Channel A: 2 zero-watch + 6 watched (watch_seconds = 10, 20, 30, 40, 50, 60) -> should pick 2 zero + 10, 20, 30 (5 total)
    for i in range(2):
        vid = f"CHA_ZERO_{i:02d}"
        queue_entries.append({
            "title": f"ChA Zero {i}",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": "Channel A",
            "enabled": True,
        })
        records[vid] = StatRecord(watch_seconds=0.0, runs=0)

    for idx, ws in enumerate([60.0, 20.0, 50.0, 10.0, 40.0, 30.0]):
        vid = f"CHA_W_{int(ws):03d}"
        queue_entries.append({
            "title": f"ChA Watch {int(ws)}",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": "Channel A",
            "enabled": True,
        })
        records[vid] = StatRecord(watch_seconds=ws, runs=1, video_duration=300.0)

    # Channel B: 7 watched videos (watch_seconds = 15, 25, 35, 45, 55, 65, 75) -> should pick 15, 25, 35, 45, 55 (5 total)
    for ws in [75.0, 15.0, 65.0, 35.0, 55.0, 25.0, 45.0]:
        vid = f"CHB_W_{int(ws):03d}"
        queue_entries.append({
            "title": f"ChB Watch {int(ws)}",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": "Channel B",
            "enabled": True,
        })
        records[vid] = StatRecord(watch_seconds=ws, runs=1, video_duration=300.0)

    # Completed / target-achieved video on Channel A (must NEVER be included)
    completed_vid = "CHA_DONE_99"
    queue_entries.append({
        "title": "ChA Completed Video",
        "url": f"https://www.youtube.com/watch?v={completed_vid}",
        "channel": "Channel A",
        "enabled": True,
        "max_runs": 3,
        "target_multiplier": 3.0,
        "duration_seconds": 100,
    })
    records[completed_vid] = StatRecord(watch_seconds=300.0, runs=3, video_duration=100.0)

    mock_stats = MagicMock()

    def _find_for_task(task: VideoTask):
        for vid_key, rec in records.items():
            if vid_key in (task.url or ""):
                return rec
        return None

    mock_stats.find_for_task.side_effect = _find_for_task
    mock_stats.get.side_effect = lambda key: records.get(key)

    worker, mock_config = _make_worker(stats_store=mock_stats)
    mock_config.get_video_queue.return_value = queue_entries
    worker.search_youtube_query = MagicMock(return_value=True)

    searched_task_titles = []

    def mock_find(driver, task=None, **kwargs):
        if task is not None:
            searched_task_titles.append(task.title)
        return None

    with patch("src.automation.watching_worker.find_and_click_search_result", side_effect=mock_find):
        worker.search_and_play_target_video(session_watched_keys=set())

    unique_searched = list(dict.fromkeys(searched_task_titles))

    # Completed video must NEVER be searched
    assert "ChA Completed Video" not in unique_searched

    # Exactly 5 from Channel A and 5 from Channel B = 10 total
    cha_searched = [t for t in unique_searched if t.startswith("ChA ")]
    chb_searched = [t for t in unique_searched if t.startswith("ChB ")]
    assert len(cha_searched) == 5
    assert len(chb_searched) == 5
    assert len(unique_searched) == 10

    # Highest-watch videos beyond top-5 per channel must be excluded
    assert "ChA Watch 40" not in cha_searched
    assert "ChA Watch 50" not in cha_searched
    assert "ChA Watch 60" not in cha_searched
    assert "ChB Watch 65" not in chb_searched
    assert "ChB Watch 75" not in chb_searched

    # First 2 searched must be the zero-watch videos
    assert set(unique_searched[:2]) == {"ChA Zero 0", "ChA Zero 1"}

    # Remaining 8 watched videos must be strictly in ascending watch-time order
    assert unique_searched[2:] == [
        "ChA Watch 10",
        "ChB Watch 15",
        "ChA Watch 20",
        "ChB Watch 25",
        "ChA Watch 30",
        "ChB Watch 35",
        "ChB Watch 45",
        "ChB Watch 55",
    ]


def test_find_and_click_search_result_skips_completed_and_prioritizes_zero_then_least_watch():
    """Channel search results must never click completed/ineligible videos, and pick zero-watch then least-watch."""
    driver = MagicMock()
    results_payload = [
        {
            "title": "Completed Video",
            "url": "https://www.youtube.com/watch?v=DONE_111111",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Completed Video\nChannel A",
        },
        {
            "title": "Short Video",
            "url": "https://www.youtube.com/shorts/SHORT_11111",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Short Video\nChannel A",
        },
        {
            "title": "High Watch Video",
            "url": "https://www.youtube.com/watch?v=WATCH_30000",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "High Watch Video\nChannel A",
        },
        {
            "title": "Low Watch Video",
            "url": "https://www.youtube.com/watch?v=WATCH_05000",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Low Watch Video\nChannel A",
        },
        {
            "title": "Zero Watch Video",
            "url": "https://www.youtube.com/watch?v=ZERO_000000",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Zero Watch Video\nChannel A",
        },
    ]

    watch_times = {
        "WATCH_30000": (1, 300.0),
        "WATCH_05000": (1, 50.0),
        "ZERO_000000": (0, 0.0),
    }

    def eligibility_check(url, title, channel):
        return "DONE_111111" not in url

    def priority_key(url, title, channel):
        for key, prio in watch_times.items():
            if key in url:
                return prio
        return (0, 0.0)

    driver.execute_script.side_effect = [results_payload, True, True]
    with patch("src.automation.navigation.dismiss_youtube_dialogs"), \
         patch("src.automation.navigation.human_like_delay"):
        matched = find_and_click_search_result(
            driver,
            target_channels=["Channel A"],
            max_scrolls=1,
            eligibility_check=eligibility_check,
            priority_key=priority_key,
            exclude_shorts=True,
            max_channel_candidates=5,
        )

    assert matched is not None
    assert matched["title"] == "Zero Watch Video"
    # Clicked index must be 4 (Zero Watch Video), NOT 0 (Completed Video)
    assert driver.execute_script.call_args_list[1].args[1] == 4
    assert driver.execute_script.call_args_list[2].args[1] == 4


def test_find_and_click_search_result_picks_least_watch_when_all_have_watch_time():
    """When all eligible channel search results have watch time, picks the one with least watch time."""
    driver = MagicMock()
    results_payload = [
        {
            "title": "Completed Video",
            "url": "https://www.youtube.com/watch?v=DONE_111111",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Completed Video\nChannel A",
        },
        {
            "title": "Watch 200s",
            "url": "https://www.youtube.com/watch?v=W_200",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Watch 200s\nChannel A",
        },
        {
            "title": "Watch 45s",
            "url": "https://www.youtube.com/watch?v=W_045",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Watch 45s\nChannel A",
        },
        {
            "title": "Watch 120s",
            "url": "https://www.youtube.com/watch?v=W_120",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Watch 120s\nChannel A",
        },
    ]

    watch_times = {
        "W_200": (1, 200.0),
        "W_045": (1, 45.0),
        "W_120": (1, 120.0),
    }

    driver.execute_script.side_effect = [results_payload, True, True]
    with patch("src.automation.navigation.dismiss_youtube_dialogs"), \
         patch("src.automation.navigation.human_like_delay"):
        matched = find_and_click_search_result(
            driver,
            target_channels=["Channel A"],
            max_scrolls=0,
            eligibility_check=lambda u, t, c: "DONE_111111" not in u,
            priority_key=lambda u, t, c: next((p for k, p in watch_times.items() if k in u), (1, 999.0)),
            exclude_shorts=True,
            max_channel_candidates=5,
        )

    assert matched is not None
    assert matched["title"] == "Watch 45s"
    assert driver.execute_script.call_args_list[1].args[1] == 2


def test_find_and_click_search_result_returns_none_when_all_matching_videos_completed():
    """Must NEVER click or open a video if every matching search result is already completed / target achieved."""
    driver = MagicMock()
    results_payload = [
        {
            "title": "Completed 1",
            "url": "https://www.youtube.com/watch?v=DONE_1",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Completed 1\nChannel A",
        },
        {
            "title": "Completed 2",
            "url": "https://www.youtube.com/watch?v=DONE_2",
            "channel": "Channel A",
            "channelHref": "/@channela",
            "rawText": "Completed 2\nChannel A",
        },
    ]

    driver.execute_script.return_value = results_payload
    with patch("src.automation.navigation.dismiss_youtube_dialogs"), \
         patch("src.automation.navigation.human_like_delay"):
        matched = find_and_click_search_result(
            driver,
            target_channels=["Channel A"],
            max_scrolls=0,
            eligibility_check=lambda u, t, c: False,
            priority_key=lambda u, t, c: (0, 0.0),
            exclude_shorts=True,
            max_channel_candidates=5,
        )

    assert matched is None
    # Only the scan script ran — NO click script was ever executed!
    assert driver.execute_script.call_count == 1


def test_is_candidate_eligible_rejects_non_queue_video_with_target_multiplier_achieved():
    """Even if a video is not in video_queue, achieving target multiplier in stats_store marks it ineligible."""
    mock_stats = MagicMock()
    rec = StatRecord(watch_seconds=310.0, runs=2, video_duration=100.0)
    mock_stats.find_for_task.return_value = rec
    mock_stats.get.return_value = rec

    worker, _ = _make_worker(stats_store=mock_stats)
    eligible, task, reason = worker.is_candidate_eligible(
        "https://www.youtube.com/watch?v=TARGET_MET1",
        "Target Achieved Video",
        "Channel A",
    )

    assert eligible is False
    assert "target multiplier" in reason
