import pytest
from unittest.mock import patch, MagicMock
import json

from src.models.video_task import VideoTask
from src.models.watch_stats import format_video_watch_ratio, StatRecord
from src.ui.views.queue_view import format_published_date, _get_task_sort_val
from src.utils.channel_fetcher import (
    parse_duration_str,
    enrich_video_tasks,
    FetchedVideo,
    extract_videos_from_data,
)


def test_format_published_date_iso():
    """ISO 8601 timestamps should format as DD Mon YYYY."""
    assert format_published_date("2025-02-26T13:49:27Z") == "26 Feb 2025"
    assert format_published_date("2026-08-16T07:09:49Z") == "16 Aug 2026"
    assert format_published_date("2024-11-23T11:22:44+00:00") == "23 Nov 2024"


def test_format_published_date_calendar_and_formatted():
    """Already formatted and calendar strings should be cleanly parsed and preserved."""
    assert format_published_date("01 Jan 2026") == "01 Jan 2026"
    assert format_published_date("16 Aug 2026") == "16 Aug 2026"
    assert format_published_date("2025-02-26") == "26 Feb 2025"
    assert format_published_date("2025/03/11") == "11 Mar 2025"


def test_format_published_date_fallback():
    """Empty or None values should return the fallback value."""
    assert format_published_date("", fallback="Date: N/A") == "Date: N/A"
    assert format_published_date(None, fallback="Date: N/A") == "Date: N/A"
    assert format_published_date("   ", fallback="Date: N/A") == "Date: N/A"
    assert format_published_date(None) == ""


def test_format_published_date_relative():
    """Relative date strings from YouTube web scraping should be preserved."""
    assert format_published_date("3 days ago", fallback="Date: N/A") == "3 days ago"
    assert format_published_date("Streamed 2 weeks ago", fallback="Date: N/A") == "Streamed 2 weeks ago"


def test_parse_duration_str_formats():
    """Duration strings of various YouTube forms should correctly parse to seconds."""
    assert parse_duration_str("8:20") == 500
    assert parse_duration_str("1:14:05") == 4445
    assert parse_duration_str("16 min") == 960
    assert parse_duration_str("28s") == 28
    assert parse_duration_str("Short") == 30
    assert parse_duration_str("1 hr 15 mins 20s") == 4520
    assert parse_duration_str("62") == 62
    assert parse_duration_str("") == 0


def test_queue_card_metrics_complete_and_present():
    """Second line of queue card must always have Length, Watch Time, and Published Date."""
    task = VideoTask(
        url="https://www.youtube.com/watch?v=-Z0sDpgzf2A",
        title="Test Long Video",
        duration_seconds=1012,
        target_multiplier=3.0,
        published_at="2025-02-26T13:49:27Z",
    )

    effective_duration = float(task.duration_seconds)
    is_short_item = task.is_short
    length_part = f"Length: {max(1, int(round(effective_duration / 60.0)))} min"
    target_sec = effective_duration * task.target_multiplier
    ratio_part = format_video_watch_ratio(0.0, target_sec) or "0s"
    pub_date_text = format_published_date(task.published_at, fallback="Date: N/A")

    second_line_parts = [length_part, ratio_part, pub_date_text]
    stats_second_line = " | ".join(second_line_parts)

    assert stats_second_line == "Length: 17 min | 0 / 51 min (0%) | 26 Feb 2025"
    assert len(second_line_parts) == 3


def test_queue_card_metrics_fallback_for_unknowns():
    """When duration and published date are unknown, clear placeholders are displayed."""
    # Regular video with 0 duration and no date
    task = VideoTask(
        url="https://www.youtube.com/watch?v=unknown1234",
        title="Unknown Video",
        duration_seconds=0,
        target_multiplier=0.0,
        published_at="",
    )

    effective_duration = float(task.duration_seconds)
    is_short_item = task.is_short
    length_part = "Length: Unknown"
    ratio_part = format_video_watch_ratio(0.0, 0.0) or "0s"
    pub_date_text = format_published_date(task.published_at, fallback="Date: N/A")

    second_line_parts = [length_part, ratio_part, pub_date_text]
    stats_second_line = " | ".join(second_line_parts)

    assert stats_second_line == "Length: Unknown | 0s | Date: N/A"
    assert len(second_line_parts) == 3


def test_queue_card_short_item_metrics():
    """Short items display proper short length and published date."""
    task = VideoTask(
        url="https://www.youtube.com/shorts/yYZH7EI9sLM",
        title="Test Short",
        duration_seconds=28,
        published_at="2026-08-13T22:00:26Z",
    )

    effective_duration = float(task.duration_seconds)
    is_short_item = task.is_short
    length_part = f"Length: {int(effective_duration)}s"
    ratio_part = format_video_watch_ratio(0.0, 0.0) or "0s"
    pub_date_text = format_published_date(task.published_at, fallback="Date: N/A")

    second_line_parts = [length_part, ratio_part, pub_date_text]
    stats_second_line = " | ".join(second_line_parts)

    assert stats_second_line == "Length: 28s | 0s | 13 Aug 2026"
    assert len(second_line_parts) == 3


def test_task_sort_val_published_date():
    """Published date sort values should correctly parse dates and put unparsed at bottom."""
    t1 = VideoTask(url="https://www.youtube.com/watch?v=11111111111", published_at="2025-02-26T13:49:27Z")
    t2 = VideoTask(url="https://www.youtube.com/watch?v=22222222222", published_at="2026-08-16T07:09:49Z")
    t3 = VideoTask(url="https://www.youtube.com/watch?v=33333333333", published_at="")

    val1 = _get_task_sort_val(t1, "published_date", None, reverse=True)
    val2 = _get_task_sort_val(t2, "published_date", None, reverse=True)
    val3 = _get_task_sort_val(t3, "published_date", None, reverse=True)

    assert val2 > val1 > val3
    assert val3 == float("-inf")


def test_enrich_video_tasks_batches_and_populates_metadata():
    """enrich_video_tasks should batch items and update missing duration and published_at."""
    tasks = [
        VideoTask(url="https://www.youtube.com/watch?v=vid11111111", duration_seconds=0, published_at=""),
        VideoTask(url="https://www.youtube.com/watch?v=vid22222222", duration_seconds=120, published_at="2025-01-01T00:00:00Z"),
    ]

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = json.dumps({
        "items": [
            {
                "id": "vid11111111",
                "contentDetails": {"duration": "PT5M30S"},
                "snippet": {
                    "publishedAt": "2026-05-10T12:00:00Z",
                    "title": "Enriched Title",
                },
            }
        ]
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_response):
        updated = enrich_video_tasks(tasks, api_key="test_api_key")

    assert updated == 1
    assert tasks[0].duration_seconds == 330
    assert tasks[0].duration_text == "5:30"
    assert tasks[0].published_at == "2026-05-10T12:00:00Z"

    # Already populated task remains untouched
    assert tasks[1].duration_seconds == 120
    assert tasks[1].published_at == "2025-01-01T00:00:00Z"
