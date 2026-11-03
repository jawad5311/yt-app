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


def test_match_home_short_matches_first_short_only():
    """Test matching only one Short at a time by inspecting the first Short in candidate list."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = None

    # First candidate is Mercedes CLR, second is Fiat Doblo
    candidates = [
        {"video_id": "EmWlMjI_uXQ", "href": "/shorts/EmWlMjI_uXQ", "title": "Mercedes CLR"},
        {"video_id": "fAwV9Hp_bNc", "href": "/shorts/fAwV9Hp_bNc", "title": "Fiat Doblo"},
    ]

    # Target queue has Mercedes CLR: match succeeds because index 0 matches
    target_shorts_1 = [
        VideoTask(url="https://www.youtube.com/shorts/EmWlMjI_uXQ", title="Target Mercedes Short", enabled=True),
    ]
    match1 = worker.match_home_short(candidates, target_shorts_1)
    assert match1 is not None
    cand_info, matched_task = match1
    assert cand_info["video_id"] == "EmWlMjI_uXQ"
    assert matched_task.title == "Target Mercedes Short"

    # Target queue ONLY has Fiat Doblo (which is at index 1, NOT index 0):
    # Must return None because only the first Short is evaluated
    target_shorts_2 = [
        VideoTask(url="https://www.youtube.com/shorts/fAwV9Hp_bNc", title="Fiat Short", enabled=True),
    ]
    match2 = worker.match_home_short(candidates, target_shorts_2)
    assert match2 is None


def test_play_target_short_integration():
    """Test _play_target_short records watch stats and triggers reel loop."""
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
    worker._run_shorts_reel_loop = MagicMock(return_value=1)

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
        # Check task duration was updated
        worker._update_task_duration.assert_called_once_with(task, 15.0)
        # Check stats store recorded outcome
        worker.stats_store.record.assert_called_once()
        # Check that shorts reel loop was entered
        worker._run_shorts_reel_loop.assert_called_once_with(session_watched_keys=watched_keys)


def test_shorts_reel_loop_skips_non_target_until_limit():
    """Verify shorts reel loop skips non-target shorts and exits when NonTargetShortsLimit is reached."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.stats_store = MagicMock()
    worker.target_attempts = 0
    worker.target_limit = 5
    worker.is_unlimited = False
    worker._emit_event = MagicMock()
    worker.check_pause = MagicMock()
    worker.channel_tracker = SessionChannelTracker(channels=[Channel(name="Target Channel", enabled=True)])
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", enabled=True)])
    worker.get_target_shorts = MagicMock(return_value=[])

    config_mgr = MagicMock()
    config_mgr.get_int.return_value = 3  # Limit of 3 non-target shorts
    worker.config_mgr = config_mgr

    # Sequence of 5 shorts demonstrating counter reset:
    # 1. Non-target Short (non_target_count becomes 1) -> scroll
    # 2. Target Short (target found -> non_target_count RESETS to 0!) -> scroll
    # 3. Non-target Short (non_target_count becomes 1) -> scroll
    # 4. Non-target Short (non_target_count becomes 2) -> scroll
    # 5. Non-target Short (non_target_count reaches limit of 3 -> exits loop without scroll!)
    reel_shorts = [
        {"found": True, "channel_name": "Random Creator 1", "channel_url": "/@random1", "title": "Random 1", "video_id": "r1"},
        {"found": True, "channel_name": "Target Channel", "channel_url": "/@target", "title": "Target 1", "video_id": "t1"},
        {"found": True, "channel_name": "Random Creator 2", "channel_url": "/@random2", "title": "Random 2", "video_id": "r2"},
        {"found": True, "channel_name": "Random Creator 3", "channel_url": "/@random3", "title": "Random 3", "video_id": "r3"},
        {"found": True, "channel_name": "Random Creator 4", "channel_url": "/@random4", "title": "Random 4", "video_id": "r4"},
    ]

    worker.get_active_short_info = MagicMock(side_effect=lambda: reel_shorts.pop(0) if reel_shorts else {"found": False})
    worker.scroll_to_next_short = MagicMock(return_value=True)
    worker._ensure_video_in_queue = MagicMock(side_effect=lambda t: t)
    worker._update_task_duration = MagicMock()
    worker._record_watch = MagicMock()

    with patch("src.automation.watching_worker.watch_short") as mock_ws, \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("time.sleep", return_value=None), \
         patch("random.randint", side_effect=[10, 3, 1]):
        from src.models.watch_stats import WatchResult
        mock_ws.return_value = WatchResult(
            completed=True,
            watched_seconds=25.0,
            video_duration=12.0,
            playback_finished=True,
            reason="shorts_loops_completed",
        )
        watched_keys = set()
        count = worker._run_shorts_reel_loop(session_watched_keys=watched_keys)

        assert count == 1  # 1 target short watched
        assert mock_ws.call_count == 1  # watch_short only called for target short
        assert worker.scroll_to_next_short.call_count == 4  # scrolled 4 times; breaks on 3rd non-target after reset (short #5)
        assert "t1" in watched_keys
        assert count == 1


def test_non_target_short_watches_random_seconds_before_skipping():
    """Verify non-target short is watched for a few seconds randomly before skipping."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.scroll_to_next_short = MagicMock(return_value=True)
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", enabled=True)])
    worker.get_target_shorts = MagicMock(return_value=[])

    config_mgr = MagicMock()
    config_mgr.get_int.return_value = 1  # Limit of 1 non-target short -> immediate exit after 1
    worker.config_mgr = config_mgr

    non_target_short = {
        "found": True,
        "channel_name": "Non Target Channel",
        "channel_url": "/@nontarget",
        "title": "Random Short",
        "video_id": "nt1",
    }
    worker.get_active_short_info = MagicMock(return_value=non_target_short)

    with patch("src.automation.watching_worker.random.uniform", return_value=3.5) as mock_uniform, \
         patch("src.automation.watching_worker.ensure_video_playing") as mock_evp, \
         patch("src.automation.watching_worker.time.sleep") as mock_sleep, \
         patch("random.randint", return_value=1):

        worker._run_shorts_reel_loop(session_watched_keys=set())

        mock_uniform.assert_called_with(2.0, 5.0)
        mock_evp.assert_called_once()
        assert mock_sleep.call_count >= 1


def test_non_target_short_never_added_to_target_channel_queue():
    """Verify non-target short (e.g. Sami Yusuf) is not misattributed to target channel (e.g. Summia Jamshed) or queued."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.scroll_to_next_short = MagicMock(return_value=True)
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Summia Jamshed", enabled=True)])
    worker.get_target_shorts = MagicMock(return_value=[])
    worker._ensure_video_in_queue = MagicMock()

    config_mgr = MagicMock()
    config_mgr.get_int.return_value = 1
    config_mgr.get_video_queue.return_value = []
    worker.config_mgr = config_mgr

    other_short = {
        "found": True,
        "channel_name": "Sami Yusuf",
        "channel_url": "https://www.youtube.com/@samiyusuf",
        "title": "Sami Yusuf - Lifting the Veil",
        "video_id": "sKapAW2nM1o",
        "url": "https://www.youtube.com/shorts/sKapAW2nM1o",
    }
    worker.get_active_short_info = MagicMock(return_value=other_short)

    with patch("src.automation.watching_worker.ensure_video_playing"), \
         patch("src.automation.watching_worker.time.sleep"), \
         patch("random.randint", return_value=1):

        worker._run_shorts_reel_loop(session_watched_keys=set())

        # Crucial check: _ensure_video_in_queue must NEVER be called for other channel
        worker._ensure_video_in_queue.assert_not_called()


def test_like_active_short_when_unliked():
    """Verify like_active_short clicks like button when aria-pressed is false."""
    from src.automation.interactivity import like_active_short

    driver = MagicMock()
    mock_btn = MagicMock()

    with patch("src.automation.interactivity.is_short_liked") as mock_is_liked, \
         patch("src.automation.interactivity.time.sleep"):
        liked_state = [False]
        mock_is_liked.side_effect = lambda *a, **k: liked_state[0]

        def on_click():
            liked_state[0] = True
        mock_btn.click.side_effect = on_click

        driver.execute_script.return_value = {
            "success": True,
            "already_liked": False,
            "button": mock_btn,
            "renderer": mock_btn,
        }

        log_q = queue.Queue()
        res = like_active_short(driver, log_queue=log_q)

        assert res is True
        mock_btn.click.assert_called()


def test_like_active_short_skips_when_already_liked():
    """Verify like_active_short skips clicking if the Short is already liked."""
    from src.automation.interactivity import like_active_short

    driver = MagicMock()
    mock_btn = MagicMock()

    with patch("src.automation.interactivity.is_short_liked") as mock_is_liked:
        mock_is_liked.return_value = True

        log_q = queue.Queue()
        res = like_active_short(driver, log_queue=log_q)

        assert res is True
        mock_btn.click.assert_not_called()


def test_watch_short_invokes_like_for_target():
    """Verify watch_short calls like_active_short when like_short=True."""
    driver = MagicMock()
    driver.execute_script.return_value = {
        "found": True,
        "currentTime": 5.0,
        "duration": 15.0,
        "ended": False,
        "paused": False,
    }

    with patch("src.automation.youtube_actions.ensure_video_playing"), \
         patch("src.automation.youtube_actions.like_active_short") as mock_like:
        mock_like.return_value = True

        res = watch_short(
            driver,
            target_loops=1,
            max_duration_seconds=10,
            like_short=True,
        )

        assert res.completed is True
        assert mock_like.call_count >= 1


def test_channel_bar_handle_matching():
    """Verify that channel handles from Shorts channel bar layout match configured channels."""
    from src.automation.watching_worker import match_channel

    channels = [
        Channel(name="Love Guru", url="https://www.youtube.com/@love_guru_161", enabled=True),
    ]

    # Extracted from ytReelChannelBarViewModel: name '@love_guru_161', url '/@love_guru_161/shorts'
    matched = match_channel("@love_guru_161", "/@love_guru_161/shorts", channels)
    assert matched is not None
    assert matched.name == "Love Guru"


def test_reel_short_added_to_shorts_queue_when_not_present():
    """Verify that a Short from a targeted channel not yet in the Shorts queue is added to the queue and stats updated."""
    from src.models.watch_stats import WatchResult

    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.target_attempts = 0
    worker.watched_count = 0
    worker.target_limit = 10
    worker.is_unlimited = False
    worker._emit_event = MagicMock()

    target_ch = Channel(name="Love Guru", url="https://www.youtube.com/@love_guru_161", enabled=True)
    worker.get_target_channels = MagicMock(return_value=[target_ch])
    worker.get_target_shorts = MagicMock(return_value=[])

    config_mgr = MagicMock()
    config_mgr.get_int.return_value = 1
    config_mgr.get_video_queue.return_value = []
    worker.config_mgr = config_mgr

    channel_tracker = MagicMock()
    worker.channel_tracker = channel_tracker

    active_short = {
        "found": True,
        "channel_name": "@love_guru_161",
        "channel_url": "/@love_guru_161/shorts",
        "title": "New Love Guru Short",
        "video_id": "lg_new_01",
        "url": "https://www.youtube.com/shorts/lg_new_01",
    }
    # First active short is target, next iteration stop_event triggers
    call_count = [0]
    def mock_get_info():
        call_count[0] += 1
        if call_count[0] == 1:
            return active_short
        worker.stop_event.set()
        return {"found": False}

    worker.get_active_short_info = MagicMock(side_effect=mock_get_info)
    worker.scroll_to_next_short = MagicMock(return_value=True)

    added_task = VideoTask(
        url="https://www.youtube.com/shorts/lg_new_01",
        title="New Love Guru Short",
        channel="Love Guru",
        enabled=True,
    )
    worker._ensure_video_in_queue = MagicMock(return_value=added_task)
    worker._record_watch = MagicMock()
    worker._update_task_duration = MagicMock()

    with patch("src.automation.watching_worker.watch_short") as mock_ws, \
         patch("src.automation.watching_worker.human_like_delay"):
        mock_ws.return_value = WatchResult(
            completed=True,
            watched_seconds=18.5,
            video_duration=18.5,
            playback_finished=True,
            reason="shorts_loops_completed",
        )
        watched_keys = set()
        count = worker._run_shorts_reel_loop(session_watched_keys=watched_keys)

        assert count == 1
        # Added to queue because it was not in queue
        worker._ensure_video_in_queue.assert_called_once()
        queued_task = worker._ensure_video_in_queue.call_args[0][0]
        assert queued_task.channel == "Love Guru"
        assert queued_task.url == "https://www.youtube.com/shorts/lg_new_01"

        # Watched and stats updated
        worker._record_watch.assert_called_once()
        assert worker._record_watch.call_args[1]["task"] == added_task
        assert worker._record_watch.call_args[1]["channel_name"] == "Love Guru"
        worker._update_task_duration.assert_called_once_with(added_task, 18.5)
        channel_tracker.record_video_watched.assert_called_once_with(
            is_short=True,
            channel_name="Love Guru",
            source="shorts_reel",
            is_random=False,
        )


def test_reel_short_already_in_shorts_queue_updates_stats():
    """Verify that a Short already in the Shorts queue is not re-added, but watched and stats updated."""
    from src.models.watch_stats import WatchResult

    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.check_pause = MagicMock()
    worker.target_attempts = 0
    worker.watched_count = 0
    worker.target_limit = 10
    worker.is_unlimited = False
    worker._emit_event = MagicMock()

    target_ch = Channel(name="Love Guru", url="https://www.youtube.com/@love_guru_161", enabled=True)
    existing_task = VideoTask(
        url="https://www.youtube.com/shorts/lg_exist0001",
        title="Existing Guru Short",
        channel="Love Guru",
        enabled=True,
    )

    worker.get_target_channels = MagicMock(return_value=[target_ch])
    worker.get_target_shorts = MagicMock(return_value=[existing_task])

    config_mgr = MagicMock()
    config_mgr.get_int.return_value = 1
    config_mgr.get_video_queue.return_value = [existing_task.to_dict()]
    worker.config_mgr = config_mgr

    channel_tracker = MagicMock()
    worker.channel_tracker = channel_tracker

    active_short = {
        "found": True,
        "channel_name": "@love_guru_161",
        "channel_url": "/@love_guru_161/shorts",
        "title": "Existing Guru Short",
        "video_id": "lg_exist0001",
        "url": "https://www.youtube.com/shorts/lg_exist0001",
    }
    call_count = [0]
    def mock_get_info():
        call_count[0] += 1
        if call_count[0] == 1:
            return active_short
        worker.stop_event.set()
        return {"found": False}

    worker.get_active_short_info = MagicMock(side_effect=mock_get_info)
    worker.scroll_to_next_short = MagicMock(return_value=True)

    worker._ensure_video_in_queue = MagicMock()
    worker._record_watch = MagicMock()
    worker._update_task_duration = MagicMock()

    with patch("src.automation.watching_worker.watch_short") as mock_ws, \
         patch("src.automation.watching_worker.human_like_delay"):
        mock_ws.return_value = WatchResult(
            completed=True,
            watched_seconds=30.0,
            video_duration=15.0,
            playback_finished=True,
            reason="shorts_loops_completed",
        )
        watched_keys = set()
        count = worker._run_shorts_reel_loop(session_watched_keys=watched_keys)

        assert count == 1
        # Because it is already in the queue, _ensure_video_in_queue MUST NOT be called!
        worker._ensure_video_in_queue.assert_not_called()

        # Stats MUST be updated with the existing task
        worker._record_watch.assert_called_once()
        assert worker._record_watch.call_args[1]["task"].url == existing_task.url
        assert worker._record_watch.call_args[1]["channel_name"] == "Love Guru"
        worker._update_task_duration.assert_called_once()
        assert worker._update_task_duration.call_args[0][0].url == existing_task.url
        assert worker._update_task_duration.call_args[0][1] == 15.0
        channel_tracker.record_video_watched.assert_called_once_with(
            is_short=True,
            channel_name="Love Guru",
            source="shorts_reel",
            is_random=False,
        )




def test_target_short_resets_non_target_counter_example():
    """Verify that watching a target short resets non_target_count back to 0 as requested in user example."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker.stats_store = MagicMock()
    worker.target_attempts = 0
    worker.target_limit = 10
    worker.is_unlimited = False
    worker._emit_event = MagicMock()
    worker.check_pause = MagicMock()
    worker.channel_tracker = SessionChannelTracker(channels=[Channel(name="Target Channel", enabled=True)])
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", enabled=True)])
    worker.get_target_shorts = MagicMock(return_value=[])

    config_mgr = MagicMock()
    config_mgr.get_int.return_value = 10  # Max non-target limit of 10
    worker.config_mgr = config_mgr

    # User scenario:
    # 8 non-target shorts (counts 1 to 8)
    # 9th short is from target channel -> resets count to 0!
    # 10 non-target shorts follow -> reaches 10, then exits loop
    reel = []
    for i in range(1, 9):
        reel.append({"found": True, "channel_name": f"Other {i}", "title": f"Other {i}", "video_id": f"other_{i}"})
    # Target short at #9
    reel.append({"found": True, "channel_name": "Target Channel", "title": "Target Short", "video_id": "target_9"})
    for i in range(10, 21):
        reel.append({"found": True, "channel_name": f"Other {i}", "title": f"Other {i}", "video_id": f"other_{i}"})

    worker.get_active_short_info = MagicMock(side_effect=lambda: reel.pop(0) if reel else {"found": False})
    worker.scroll_to_next_short = MagicMock(return_value=True)
    worker._ensure_video_in_queue = MagicMock(side_effect=lambda t: t)
    worker._update_task_duration = MagicMock()
    worker._record_watch = MagicMock()

    with patch("src.automation.watching_worker.watch_short") as mock_ws, \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("time.sleep", return_value=None), \
         patch("random.randint", side_effect=[10, 10, 1]):
        from src.models.watch_stats import WatchResult
        mock_ws.return_value = WatchResult(completed=True, watched_seconds=15.0, video_duration=10.0, playback_finished=True)
        watched_keys = set()
        count = worker._run_shorts_reel_loop(session_watched_keys=watched_keys)

        assert count == 1
        assert "target_9" in watched_keys
        # 8 skips + 1 target watch + 10 skips = 19 shorts processed.
        # It scrolls 18 times and breaks on the 10th non-target after target short.
        assert worker.scroll_to_next_short.call_count == 18


def test_home_shorts_matching_by_id_and_title_and_channel():
    """Verify match_home_short matches tasks in Shorts Queue by ID, Title, and Channel."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.log_queue = queue.Queue()

    task_by_id = VideoTask(url="https://www.youtube.com/shorts/vid12345678", title="Custom Title", enabled=True)
    task_by_title = VideoTask(url="https://www.youtube.com/shorts/different11", title="Viral Cat Video", enabled=True)
    task_by_channel = VideoTask(url="https://www.youtube.com/shorts/other123456", title="", channel="Target Creator", enabled=True)

    # 1. Candidate matching by video ID
    cand1 = [{"video_id": "vid12345678", "href": "/shorts/vid12345678", "title": "Random Title"}]
    m1 = worker.match_home_short(cand1, [task_by_id])
    assert m1 is not None
    assert m1[1] == task_by_id

    # 2. Candidate matching by Title
    cand2 = [{"video_id": "random99999", "href": "/shorts/random99999", "title": "Viral Cat Video Highlights"}]
    m2 = worker.match_home_short(cand2, [task_by_title])
    assert m2 is not None
    assert m2[1] == task_by_title

    # 3. Candidate matching by Channel
    cand3 = [{"video_id": "random88888", "href": "/shorts/random88888", "title": "Cool Video", "channel": "Target Creator"}]
    m3 = worker.match_home_short(cand3, [task_by_channel])
    assert m3 is not None
    assert m3[1] == task_by_channel

    # 4. Non-matching candidate returns None
    cand4 = [{"video_id": "unmatched11", "href": "/shorts/unmatched11", "title": "Unrelated Cooking Video", "channel": "Chef Joe"}]
    m4 = worker.match_home_short(cand4, [task_by_id, task_by_title, task_by_channel])
    assert m4 is None


def test_find_target_short_on_home_diagnostic_logging():
    """Verify find_target_short_on_home logs candidate evaluation correctly and distinguishes missing shelf vs unmatched candidates."""
    worker = WatchingWorker.__new__(WatchingWorker)
    worker.driver = MagicMock()
    worker.log_queue = queue.Queue()
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()

    task = VideoTask(url="https://www.youtube.com/shorts/targetVid11", title="Target Short", enabled=True)
    worker.get_target_shorts = MagicMock(return_value=[task])

    # Scenario A: Shelf is present with 3 shorts, but NONE match our queue
    unmatched_candidates = [
        {"video_id": "otherVid111", "href": "/shorts/otherVid111", "title": "Cooking 101"},
        {"video_id": "otherVid222", "href": "/shorts/otherVid222", "title": "Gaming Clip"},
        {"video_id": "otherVid333", "href": "/shorts/otherVid333", "title": "Funny Dog"},
    ]
    worker.fetch_home_shorts = MagicMock(return_value=unmatched_candidates)

    with patch("src.automation.watching_worker.human_like_delay"):
        result = worker.find_target_short_on_home(max_scrolls=1)

    assert result is None
    logs = []
    while not worker.log_queue.empty():
        logs.append(worker.log_queue.get())

    # Ensure it logged that 3 Shorts were found on Home feed, but none matched
    found_unmatched_msg = any("Found 3 Short(s) on Home feed, but none matched any enabled task in your Shorts Queue" in log for log in logs)
    assert found_unmatched_msg is True
    # Ensure it did NOT falsely claim the shelf was missing
    falsely_missing = any("Shorts shelf/items not yet visible in Home feed" in log for log in logs)
    assert falsely_missing is False

    # Scenario B: Shelf is genuinely missing (0 items found)
    worker.fetch_home_shorts = MagicMock(return_value=[])
    while not worker.log_queue.empty():
        worker.log_queue.get()

    with patch("src.automation.watching_worker.human_like_delay"):
        result_empty = worker.find_target_short_on_home(max_scrolls=1)

    assert result_empty is None
    empty_logs = []
    while not worker.log_queue.empty():
        empty_logs.append(worker.log_queue.get())

    # When 0 candidates, it correctly reports shelf not yet visible
    correctly_missing = any("Shorts shelf/items not yet visible in Home feed" in log for log in empty_logs)
    assert correctly_missing is True

