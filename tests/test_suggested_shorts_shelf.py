import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import WatchingWorker
from src.models.video_task import VideoTask


@pytest.fixture
def worker():
    w = WatchingWorker.__new__(WatchingWorker)
    w.log_queue = queue.Queue()
    w.stop_event = threading.Event()
    w.driver = MagicMock()
    w.config_mgr = MagicMock()
    w.stats_store = None
    w.target_attempts = 0
    w.target_limit = 0
    w.is_unlimited = True
    w._emit_event = MagicMock()
    w.check_pause = MagicMock()
    w._get_cooldown_hours = MagicMock(return_value=0.0)
    return w


def test_has_suggested_shorts_shelf(worker):
    """Test checking if suggested Shorts shelf exists on current page."""
    worker.driver.execute_script.return_value = True
    assert worker._has_suggested_shorts_shelf() is True

    worker.driver.execute_script.return_value = False
    assert worker._has_suggested_shorts_shelf() is False

    worker.driver.execute_script.side_effect = Exception("DOM disconnected")
    assert worker._has_suggested_shorts_shelf() is False


def test_fetch_suggested_shelf_shorts(worker):
    """Test fetching candidates from suggested shelf."""
    mock_items = [
        {
            "video_id": "Ce4GC9dffxE",
            "href": "/shorts/Ce4GC9dffxE",
            "title": "Taptap Send Short",
            "is_visible": True,
            "source": "Suggested section",
            "source_type": "suggested",
        },
        {
            "video_id": "xrwoe6AW_48",
            "href": "/shorts/xrwoe6AW_48",
            "title": "Podcasting is Hard",
            "is_visible": True,
            "source": "Suggested section",
            "source_type": "suggested",
        },
    ]
    worker.driver.execute_script.return_value = mock_items
    res = worker.fetch_suggested_shelf_shorts()
    assert len(res) == 2
    assert res[0]["video_id"] == "Ce4GC9dffxE"
    assert res[0]["is_visible"] is True


def test_click_suggested_shelf_next_arrow(worker):
    """Test clicking next arrow button on shelf."""
    worker.driver.execute_script.return_value = True
    assert worker.click_suggested_shelf_next_arrow() is True

    worker.driver.execute_script.return_value = False
    assert worker.click_suggested_shelf_next_arrow() is False

    worker.driver.execute_script.side_effect = Exception("Element not interactable")
    assert worker.click_suggested_shelf_next_arrow() is False


def test_find_target_short_on_suggested_shelf_match_first_slide(worker):
    """Test match found in the first slide of visible 3 shorts on suggested shelf."""
    worker._has_suggested_shorts_shelf = MagicMock(return_value=True)

    task = VideoTask(
        url="https://www.youtube.com/shorts/rxSoFHeNCWI",
        title="How to Cap Shopify Discounts",
        enabled=True,
    )
    worker.get_target_shorts = MagicMock(return_value=[task])

    slide_1_items = [
        {"video_id": "Ce4GC9dffxE", "href": "/shorts/Ce4GC9dffxE", "title": "Other Short 1", "is_visible": True},
        {"video_id": "xrwoe6AW_48", "href": "/shorts/xrwoe6AW_48", "title": "Other Short 2", "is_visible": True},
        {"video_id": "rxSoFHeNCWI", "href": "/shorts/rxSoFHeNCWI", "title": "How to Cap Shopify Discounts", "is_visible": True},
    ]
    worker.fetch_suggested_shelf_shorts = MagicMock(return_value=slide_1_items)
    worker.click_suggested_shelf_next_arrow = MagicMock(return_value=True)

    match = worker.find_target_short_on_suggested_shelf()
    assert match is not None
    cand, matched_task = match
    assert cand["video_id"] == "rxSoFHeNCWI"
    assert cand["source_type"] == "suggested"
    assert matched_task == task
    worker.click_suggested_shelf_next_arrow.assert_not_called()


def test_find_target_short_on_suggested_shelf_match_second_slide(worker):
    """Test match found on slide 2 after clicking next arrow."""
    worker._has_suggested_shorts_shelf = MagicMock(return_value=True)

    task = VideoTask(
        url="https://www.youtube.com/shorts/LTnmHVJjrc4",
        title="Akshay Plan Went Wrong",
        enabled=True,
    )
    worker.get_target_shorts = MagicMock(return_value=[task])

    slide_1_items = [
        {"video_id": "Ce4GC9dffxE", "href": "/shorts/Ce4GC9dffxE", "title": "Slide1 Short 1", "is_visible": True},
        {"video_id": "xrwoe6AW_48", "href": "/shorts/xrwoe6AW_48", "title": "Slide1 Short 2", "is_visible": True},
        {"video_id": "rxSoFHeNCWI", "href": "/shorts/rxSoFHeNCWI", "title": "Slide1 Short 3", "is_visible": True},
    ]
    slide_2_items = [
        {"video_id": "ZcRJ-OevRYY", "href": "/shorts/ZcRJ-OevRYY", "title": "Slide2 Short 1", "is_visible": True},
        {"video_id": "LTnmHVJjrc4", "href": "/shorts/LTnmHVJjrc4", "title": "Akshay Plan Went Wrong", "is_visible": True},
        {"video_id": "zz7y7mKA5Io", "href": "/shorts/zz7y7mKA5Io", "title": "Slide2 Short 3", "is_visible": True},
    ]

    worker.fetch_suggested_shelf_shorts = MagicMock(side_effect=[slide_1_items, slide_2_items])
    worker.click_suggested_shelf_next_arrow = MagicMock(return_value=True)

    with patch("src.automation.watching_worker.human_like_delay", return_value=None):
        match = worker.find_target_short_on_suggested_shelf(max_arrow_clicks=3)

    assert match is not None
    cand, matched_task = match
    assert cand["video_id"] == "LTnmHVJjrc4"
    assert cand["source_type"] == "suggested"
    assert matched_task == task
    assert worker.click_suggested_shelf_next_arrow.call_count == 1


def test_find_target_short_on_suggested_shelf_no_match(worker):
    """Test when no matching short is found after clicking through carousel."""
    worker._has_suggested_shorts_shelf = MagicMock(return_value=True)

    task = VideoTask(
        url="https://www.youtube.com/shorts/TargetNotFound",
        title="Missing Short",
        enabled=True,
    )
    worker.get_target_shorts = MagicMock(return_value=[task])

    non_matching_items = [
        {"video_id": "vid1", "href": "/shorts/vid1", "title": "Untargeted 1", "is_visible": True},
        {"video_id": "vid2", "href": "/shorts/vid2", "title": "Untargeted 2", "is_visible": True},
        {"video_id": "vid3", "href": "/shorts/vid3", "title": "Untargeted 3", "is_visible": True},
    ]
    worker.fetch_suggested_shelf_shorts = MagicMock(return_value=non_matching_items)
    worker.click_suggested_shelf_next_arrow = MagicMock(side_effect=[True, True, False])

    with patch("src.automation.watching_worker.human_like_delay", return_value=None):
        match = worker.find_target_short_on_suggested_shelf(max_arrow_clicks=4)

    assert match is None
    assert worker.click_suggested_shelf_next_arrow.call_count == 3


def test_find_target_short_on_suggested_shelf_no_shelf(worker):
    """Test when no suggested shorts shelf exists on the page."""
    worker._has_suggested_shorts_shelf = MagicMock(return_value=False)
    task = VideoTask(url="https://www.youtube.com/shorts/someid", title="Some Short", enabled=True)
    worker.get_target_shorts = MagicMock(return_value=[task])

    with patch("src.automation.watching_worker.human_like_delay", return_value=None):
        match = worker.find_target_short_on_suggested_shelf()

    assert match is None


def test_run_shorts_cycle_checks_suggested_first_when_on_watch_page(worker):
    """Verify that _run_shorts_cycle checks suggested shelf first when on a watch page."""
    worker.driver.current_url = "https://www.youtube.com/watch?v=SomeLongVideo123"

    suggested_task = VideoTask(
        url="https://www.youtube.com/shorts/Ce4GC9dffxE",
        title="Target Short on Suggested Shelf",
        enabled=True,
    )
    suggested_match = (
        {
            "video_id": "Ce4GC9dffxE",
            "href": "/shorts/Ce4GC9dffxE",
            "title": "Target Short on Suggested Shelf",
            "source": "Suggested section",
            "source_type": "suggested",
        },
        suggested_task,
    )

    worker.find_target_short_on_suggested_shelf = MagicMock(return_value=suggested_match)
    worker.find_target_short_on_home = MagicMock()
    worker._play_target_short = MagicMock(return_value=True)
    worker.scroll_to_next_short = MagicMock()
    worker._run_shorts_reel_loop = MagicMock(return_value=(1, 0, "target_achieved"))

    with patch("src.automation.watching_worker.human_like_delay", return_value=None), \
         patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        worker.config_mgr.get_int.side_effect = lambda k, d: 1 if k == "TargetShortsPerCycle" else d

        worker._run_shorts_cycle(session_watched_keys=set())

    worker.find_target_short_on_suggested_shelf.assert_called_once()
    worker.find_target_short_on_home.assert_not_called()
    worker._play_target_short.assert_called_once()
    assert worker._play_target_short.call_args[1].get("source") == "suggested"


def test_run_shorts_cycle_falls_back_to_home_when_no_suggested_match(worker):
    """Verify fallback to Home feed navigation when no target Short matches in suggested section."""
    worker.driver.current_url = "https://www.youtube.com/watch?v=SomeLongVideo123"

    worker.find_target_short_on_suggested_shelf = MagicMock(return_value=None)

    home_task = VideoTask(
        url="https://www.youtube.com/shorts/HomeShort123",
        title="Target Short on Home",
        enabled=True,
    )
    home_match = (
        {
            "video_id": "HomeShort123",
            "href": "/shorts/HomeShort123",
            "title": "Target Short on Home",
            "source": "Home feed",
            "source_type": "home",
        },
        home_task,
    )
    worker.find_target_short_on_home = MagicMock(return_value=home_match)
    worker._play_target_short = MagicMock(return_value=True)
    worker.scroll_to_next_short = MagicMock()
    worker._run_shorts_reel_loop = MagicMock(return_value=(1, 0, "target_achieved"))

    with patch("src.automation.watching_worker.human_like_delay", return_value=None), \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs", return_value=None), \
         patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        worker.config_mgr.get_int.side_effect = lambda k, d: 1 if k == "TargetShortsPerCycle" else d

        worker._run_shorts_cycle(session_watched_keys=set())

    worker.find_target_short_on_suggested_shelf.assert_called_once()
    mock_safe_get.assert_any_call(worker.driver, "https://www.youtube.com/", timeout=30, log_queue=worker.log_queue)
    worker.find_target_short_on_home.assert_called_once()
    worker._play_target_short.assert_called_once()
    assert worker._play_target_short.call_args[1].get("source") == "home"
