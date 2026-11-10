"""Unit tests for runtime recovery, preview click interception fallback,
shorts reel advancement, stalled video refresh/abandon, multi-stage search, and email browser mode.
"""

import queue
import threading
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from src.automation.email_reporter import EmailReporter
from src.automation.navigation import find_and_click_search_result
from src.automation.youtube_actions import WatchResult, watch_video
from src.models import Account, Channel, VideoTask


# ============================================================================
# 1. Stalled Video Recovery (Refresh then Abandon)
# ============================================================================

def test_stalled_video_triggers_refresh_then_aborts_to_home(monkeypatch):
    """Verify that if video is stalled:
    1. First 2 checks log stalled and try ensure_video_playing.
    2. 3rd check triggers driver.refresh() and resets stall counter.
    3. If still stalled after refresh for 3 more checks, aborts with 'stalled_unresponsive'
       and completed=False without infinite looping.
    """
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/watch?v=stuckVideo123"

    log_q = queue.Queue()

    # Create states that stay at currentTime 0.0 for 7 checks
    stuck_state = {
        "found": True,
        "paused": False,
        "ended": False,
        "currentTime": 0.0,
        "duration": 500.0,
        "isAd": False,
        "readyState": 4,
        "seeking": False,
        "seekCount": 0,
        "playbackRate": 1.0,
    }

    # Mock time.monotonic to advance by 10s on each tick
    clock = [100.0]

    def fake_monotonic():
        clock[0] += 10.0
        return clock[0]

    monkeypatch.setattr("time.monotonic", fake_monotonic)
    monkeypatch.setattr("time.sleep", lambda s: None)

    # driver.execute_script returns stuck_state for player state probes
    driver.execute_script.return_value = stuck_state

    result = watch_video(
        driver,
        duration_seconds=None,
        log_queue=log_q,
        full_timeout_seconds=3600,
        enable_interactivity=False,
    )

    # Must abort with stalled_unresponsive, not completed
    assert result.completed is False
    assert result.reason == "stalled_unresponsive"
    assert result.interrupted is True

    # Check driver.refresh was called once
    assert driver.refresh.call_count == 1

    logs = []
    while not log_q.empty():
        logs.append(log_q.get_nowait())
    log_text = " ".join(logs)

    assert "Refreshing page to recover playback" in log_text
    assert "Aborting stuck video and returning to YouTube Home" in log_text


# ============================================================================
# 2. Multi-Stage YouTube Search Strategy
# ============================================================================

def test_find_and_click_search_result_channel_fallback():
    """Verify find_and_click_search_result supports allow_channel_fallback."""
    driver = MagicMock()
    log_q = queue.Queue()

    # Search results contain videos from different channels, including the target channel
    search_dom = [
        {"title": "Viral Unrelated Video", "url": "https://www.youtube.com/watch?v=abc11234567", "channel": "Random Creator", "channelHref": "/@random"},
        {"title": "Alternative Channel Video", "url": "https://www.youtube.com/watch?v=abc21234567&t=100s", "channel": "Lifestyle Mapping", "channelHref": "/@LifestyleMapping"},
    ]
    driver.execute_script.return_value = search_dom

    task = VideoTask(
        url="https://www.youtube.com/watch?v=missingVideo",
        title="Exact Missing Video Title",
        channel="Lifestyle Mapping",
    )

    # Without allow_channel_fallback, exact title isn't in search_dom -> returns None
    res_no_fallback = find_and_click_search_result(
        driver,
        task=task,
        target_channels=["Lifestyle Mapping"],
        max_scrolls=0,
        log_queue=log_q,
        allow_channel_fallback=False,
    )
    assert res_no_fallback is None

    # With allow_channel_fallback=True, matches alternative video from target channel
    res_fallback = find_and_click_search_result(
        driver,
        task=task,
        target_channels=["Lifestyle Mapping"],
        max_scrolls=0,
        log_queue=log_q,
        allow_channel_fallback=True,
    )
    assert res_fallback is not None
    assert res_fallback["title"] == "Alternative Channel Video"
    # Verify &t= parameter was cleaned from url
    assert "&t=" not in res_fallback["url"]
    assert "watch?v=abc21234567" in res_fallback["url"]


# ============================================================================
# 3. Email Reporter Browser Mode (Headed vs Headless)
# ============================================================================

def test_send_via_headless_browser_gmail_mode_selection(monkeypatch):
    """Verify send_via_headless_browser_gmail uses:
    - headless=True for start notifications
    - headless=False for session reports, sleep notifications, and alerts
    """
    account = Account(email="test@gmail.com", password="dummy_password")
    setup_calls = []

    def mock_setup_driver(account, log_queue, headless=False, is_reporting=False):
        setup_calls.append({"headless": headless, "is_reporting": is_reporting})
        mock_d = MagicMock()
        mock_d.current_url = "https://mail.google.com"
        return mock_d

    monkeypatch.setattr("src.automation.driver.setup_driver", mock_setup_driver)
    monkeypatch.setattr("src.automation.driver.close_driver", lambda d, log_queue=None: None)
    monkeypatch.setattr(EmailReporter, "_deliver_via_gmail_thread_reply", lambda **kwargs: True)
    monkeypatch.setattr(EmailReporter, "_deliver_via_gmail_compose", lambda **kwargs: True)

    # 1. Start notification -> headless must be True
    EmailReporter.send_via_headless_browser_gmail(
        account=account,
        subject="Start",
        body_text="Session started",
        recipient_email="user@example.com",
        email_description="start notification email",
    )
    assert len(setup_calls) == 1
    assert setup_calls[0]["headless"] is True

    # 2. Session watch time report -> headless must be False (headed browser)
    EmailReporter.send_via_headless_browser_gmail(
        account=account,
        subject="Report",
        body_text="Session summary",
        recipient_email="user@example.com",
        email_description="session watch time report",
    )
    assert len(setup_calls) == 2
    assert setup_calls[1]["headless"] is False

    # 3. Long sleep notification -> headless must be False (headed browser)
    EmailReporter.send_via_headless_browser_gmail(
        account=account,
        subject="Sleep",
        body_text="Entering Long Sleep",
        recipient_email="user@example.com",
        email_description="long sleep notification",
    )
    assert len(setup_calls) == 3
    assert setup_calls[2]["headless"] is False
