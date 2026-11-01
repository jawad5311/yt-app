import queue
import re
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.session_tracker import SessionChannelTracker
from src.automation.watching_worker import WatchingWorker
from src.automation.youtube_actions import watch_short
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.utils.helpers import extract_video_id

USER_SHORTS_HTML = """
<ytd-rich-section-renderer class="style-scope ytd-rich-grid-renderer" page-subtype="home">
<div id="content" class="style-scope ytd-rich-section-renderer">
<ytd-rich-shelf-renderer class="style-scope ytd-rich-section-renderer" elements-per-row="5" is-shorts="" show-bottom-divider="" is-truncated="" restrict-contents-overflow="" style="--ytd-rich-grid-items-per-row: 5; --ytd-rich-grid-item-margin: 16px; --ytd-rich-shelf-items-count: 9;">
<div id="dismissible" class="style-scope ytd-rich-shelf-renderer">
  <div id="rich-shelf-header-container" class="style-scope ytd-rich-shelf-renderer">
    <div id="rich-shelf-header" class="style-scope ytd-rich-shelf-renderer">
      <h2 class="style-scope ytd-rich-shelf-renderer">
          <span id="title" class="style-scope ytd-rich-shelf-renderer">Shorts</span>
      </h2>
    </div>
  </div>
  <div id="contents-container" class="style-scope ytd-rich-shelf-renderer">
    <div id="contents" class="style-scope ytd-rich-shelf-renderer">
      <ytd-rich-item-renderer class="style-scope ytd-rich-shelf-renderer" items-per-row="3" lockup="true">
        <div id="content" class="style-scope ytd-rich-item-renderer">
          <ytm-shorts-lockup-view-model-v2 class="shortsLockupViewModelHost style-scope ytd-rich-item-renderer">
            <ytm-shorts-lockup-view-model class="shortsLockupViewModelHost style-scope ytd-rich-item-renderer">
              <a href="/shorts/EmWlMjI_uXQ" class="shortsLockupViewModelHostEndpoint reel-item-endpoint"></a>
              <div class="shortsLockupViewModelHostOutsideMetadata">
                <div>
                  <h3 class="shortsLockupViewModelHostMetadataTitle">
                    <a href="/shorts/EmWlMjI_uXQ" title="Mercedes CLR GTR Short"><span>Mercedes CLR GTR Short</span></a>
                  </h3>
                  <div class="shortsLockupViewModelHostOutsideMetadataSubhead"><span>1.1m views</span></div>
                </div>
              </div>
            </ytm-shorts-lockup-view-model>
          </ytm-shorts-lockup-view-model-v2>
        </div>
      </ytd-rich-item-renderer>
      <ytd-rich-item-renderer class="style-scope ytd-rich-shelf-renderer">
        <div id="content" class="style-scope ytd-rich-item-renderer">
          <ytm-shorts-lockup-view-model-v2>
            <ytm-shorts-lockup-view-model>
              <a href="/shorts/fAwV9Hp_bNc" class="shortsLockupViewModelHostEndpoint reel-item-endpoint"></a>
              <div class="shortsLockupViewModelHostOutsideMetadata">
                <div>
                  <h3 class="shortsLockupViewModelHostMetadataTitle">
                    <a href="/shorts/fAwV9Hp_bNc" title="Keys Lost Fiat Doblo"><span>Keys Lost Fiat Doblo</span></a>
                  </h3>
                  <div class="shortsLockupViewModelHostOutsideMetadataSubhead"><span>126 views</span></div>
                </div>
              </div>
            </ytm-shorts-lockup-view-model>
          </ytm-shorts-lockup-view-model-v2>
        </div>
      </ytd-rich-item-renderer>
    </div>
  </div>
</div>
</ytd-rich-shelf-renderer>
</div>
</ytd-rich-section-renderer>
"""


def test_parse_user_provided_shorts_html():
    """Verify parsing and extraction from user-provided shorts HTML."""
    matches = re.findall(
        r'href=["\'](/shorts/([A-Za-z0-9_\-]{11}))["\'].*?title=["\'](.*?)["\']',
        USER_SHORTS_HTML,
        re.DOTALL,
    )
    extracted = []
    seen = set()
    for href, vid_id, title in matches:
        if vid_id not in seen:
            seen.add(vid_id)
            extracted.append({"video_id": vid_id, "href": href, "title": title})

    assert len(extracted) == 2
    assert extracted[0]["video_id"] == "EmWlMjI_uXQ"
    assert "Mercedes" in extracted[0]["title"]
    assert extracted[1]["video_id"] == "fAwV9Hp_bNc"
    assert "Fiat" in extracted[1]["title"]


def test_watch_short_single_loop_completion():
    """Test watch_short when configured for 1 loop and completes at end of short."""
    driver = MagicMock()
    states = [
        {"found": True, "currentTime": 0.5, "duration": 15.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 5.0, "duration": 15.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 14.2, "duration": 15.0, "paused": False, "ended": False},
    ]

    def mock_execute_script(script, *args):
        if "currentTime" in str(script):
            return states.pop(0) if states else {"found": True, "currentTime": 14.5, "duration": 15.0, "ended": True}
        return None

    driver.execute_script.side_effect = mock_execute_script
    log_q = queue.Queue()

    with patch("time.sleep", return_value=None):
        result = watch_short(driver, target_loops=1, log_queue=log_q)

    assert result.completed is True
    assert result.playback_finished is True
    assert result.reason == "shorts_loops_completed"
    assert result.video_duration == 15.0


def test_watch_short_multi_loop_wrap_around():
    """Test watch_short loop wrap-around progression for 2 loops."""
    driver = MagicMock()
    # Loop 1: 0 -> 10 -> 19 -> wrap-around to 0.5 -> Loop 2: 10 -> 19.5 (end of loop 2)
    states = [
        # Loop 1
        {"found": True, "currentTime": 2.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 10.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 19.0, "duration": 20.0, "paused": False, "ended": False},
        # Wrap-around happens here (currentTime resets to near 0)
        {"found": True, "currentTime": 0.8, "duration": 20.0, "paused": False, "ended": False},
        # Loop 2
        {"found": True, "currentTime": 10.0, "duration": 20.0, "paused": False, "ended": False},
        {"found": True, "currentTime": 19.5, "duration": 20.0, "paused": False, "ended": False},
    ]

    def mock_execute_script(script, *args):
        if "currentTime" in str(script):
            return states.pop(0) if states else {"found": True, "currentTime": 20.0, "duration": 20.0, "ended": True}
        return None

    driver.execute_script.side_effect = mock_execute_script
    log_q = queue.Queue()

    with patch("time.sleep", return_value=None):
        result = watch_short(driver, target_loops=2, log_queue=log_q)

    assert result.completed is True
    assert result.reason == "shorts_loops_completed"
    assert result.video_duration == 20.0


def test_watch_short_user_stop_interruption():
    """Test watch_short gracefully handles stop_event."""
    driver = MagicMock()
    driver.execute_script.return_value = {"found": True, "currentTime": 2.0, "duration": 20.0, "paused": False}
    stop_event = threading.Event()
    stop_event.set()

    result = watch_short(driver, target_loops=3, stop_event=stop_event)
    assert result.completed is False
    assert result.interrupted is True
    assert result.reason == "interrupted"


def test_match_home_short():
    """Test matching candidate shorts from Home page with app target shorts."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = None

    candidates = [
        {"video_id": "EmWlMjI_uXQ", "href": "/shorts/EmWlMjI_uXQ", "title": "Mercedes CLR"},
        {"video_id": "fAwV9Hp_bNc", "href": "/shorts/fAwV9Hp_bNc", "title": "Fiat Doblo"},
        {"video_id": "random12345", "href": "/shorts/random12345", "title": "Unrelated Short"},
    ]

    target_shorts = [
        VideoTask(url="https://www.youtube.com/shorts/EmWlMjI_uXQ", title="Target Mercedes Short", enabled=True),
        VideoTask(url="https://www.youtube.com/shorts/another9999", title="Other Short", enabled=True),
    ]

    # Matching with no previous session watch
    match = worker.match_home_short(candidates, target_shorts, session_watched_keys=set())
    assert match is not None
    cand_info, matched_task = match
    assert cand_info["video_id"] == "EmWlMjI_uXQ"
    assert matched_task.title == "Target Mercedes Short"

    # If already watched, but no alternative, returns it; if alternative unwatched exists, picks unwatched
    target_shorts.append(VideoTask(url="https://www.youtube.com/shorts/fAwV9Hp_bNc", title="Fiat Short", enabled=True))
    watched = {"EmWlMjI_uXQ"}
    match2 = worker.match_home_short(candidates, target_shorts, session_watched_keys=watched)
    assert match2 is not None
    assert match2[0]["video_id"] == "fAwV9Hp_bNc"


def test_play_target_short_integration():
    """Test _play_target_short records watch stats and updates session tracker."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.stats_store = MagicMock()
    worker.channel_tracker = SessionChannelTracker(channels=[Channel(name="Mercedes Fans", enabled=True)])
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Mercedes Fans", enabled=True)])

    short_info = {
        "video_id": "EmWlMjI_uXQ",
        "href": "/shorts/EmWlMjI_uXQ",
        "url": "https://www.youtube.com/shorts/EmWlMjI_uXQ",
        "title": "Mercedes CLR GTR",
    }
    task = VideoTask(
        url="https://www.youtube.com/shorts/EmWlMjI_uXQ",
        title="Mercedes CLR GTR",
        channel="Mercedes Fans",
        enabled=True,
    )
    worker._ensure_video_in_queue = MagicMock(return_value=task)
    worker._update_task_duration = MagicMock()
    worker.click_home_short = MagicMock(return_value=True)

    with patch("src.automation.watching_worker.watch_short") as mock_ws, \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("src.automation.watching_worker.human_like_delay"):
        from src.models.watch_stats import WatchResult
        mock_ws.return_value = WatchResult(
            completed=True,
            watched_seconds=30.0,
            video_duration=15.0,
            playback_finished=True,
            reason="shorts_loops_completed",
        )
        watched_keys = set()
        ok = worker._play_target_short(short_info, task, watched_keys)
        assert ok is True
        assert "EmWlMjI_uXQ" in watched_keys
        # Check channel tracker recorded the short
        assert worker.channel_tracker.total_shorts == 1
        assert worker.channel_tracker.channel_shorts.get("mercedes fans") == 1
