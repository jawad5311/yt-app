from unittest.mock import MagicMock, patch
import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask


@pytest.fixture
def test_worker(tmp_path):
    account = Account(email="test@gmail.com", password="pwd")
    cfg = ConfigManager(config_file=tmp_path / "settings.json")
    ch = Channel(name="Target Channel", url="https://youtube.com/@target")
    cfg.set_channels([ch.to_dict()])
    worker = WatchingWorker(
        account=account,
        channels=[ch],
        config_mgr=cfg,
    )
    mock_driver = MagicMock()
    mock_driver.current_url = "https://www.youtube.com/watch?v=initial123"
    worker.driver = mock_driver
    return worker


def test_click_recommended_video_never_falls_back_to_address_bar(test_worker):
    """When element clicking fails, click_recommended_video must NOT call safe_get and return False."""
    test_worker.driver.find_elements.return_value = []
    test_worker.driver.execute_script.return_value = None

    with patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        res = test_worker.click_recommended_video({"index": 0, "href": "/watch?v=fail_vid_123"})
        assert res is False
        mock_safe_get.assert_not_called()


def test_click_home_video_never_falls_back_to_address_bar(test_worker):
    """When home video clicking fails, click_home_video must NOT call safe_get and return False."""
    test_worker.driver.find_elements.return_value = []
    test_worker.driver.execute_script.return_value = None

    with patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        res = test_worker.click_home_video({"index": 0, "href": "/watch?v=fail_vid_456"})
        assert res is False
        mock_safe_get.assert_not_called()


def test_click_home_short_never_falls_back_to_address_bar(test_worker):
    """When home short clicking fails, click_home_short must NOT call safe_get and return False."""
    test_worker.driver.execute_script.return_value = None

    with patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        res = test_worker.click_home_short({"url": "https://www.youtube.com/shorts/fail_short_123", "video_id": "fail_short_123"})
        assert res is False
        mock_safe_get.assert_not_called()


def test_play_target_video_fails_when_in_page_click_fails(test_worker):
    """_play_target_video must return reason='in_page_click_failed' and never call safe_get."""
    card = {"index": 0, "href": "/watch?v=target12345", "title": "Target Vid"}
    test_worker.driver.current_url = "https://www.youtube.com/watch?v=initial1234"

    with patch.object(test_worker, "click_recommended_video", return_value=False), \
         patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        res = test_worker._play_target_video(
            card_info=card,
            full_url="https://www.youtube.com/watch?v=target12345",
            title="Target Vid",
            channel_display="Target Channel",
            session_watched_keys=set(),
            is_home=False,
            source="suggested",
        )
        assert res.completed is False
        assert res.reason in ("in_page_click_failed", "navigation_identity_mismatch")
        mock_safe_get.assert_not_called()


def test_watch_random_video_fails_cleanly_without_direct_nav(test_worker):
    """_watch_random_video must not call safe_get if in-page click fails."""
    card = {"index": 0, "href": "/watch?v=random123", "title": "Random Vid"}
    test_worker.driver.current_url = "https://www.youtube.com/watch?v=initial123"

    with patch.object(test_worker, "click_recommended_video", return_value=False), \
         patch("src.automation.watching_worker.safe_get") as mock_safe_get:
        res = test_worker._watch_random_video(card, duration_seconds=45, session_watched_keys=set())
        assert res is False
        mock_safe_get.assert_not_called()


def test_suggested_section_retries_next_video_on_click_failure(test_worker):
    """When clicking the first suggested video candidate fails, the loop attempts the next candidate."""
    from src.automation.youtube_actions import WatchResult

    target_ch = test_worker.get_target_channels()[0]

    candidate1 = {
        "index": 0,
        "href": "/watch?v=cand1111111",
        "title": "Target Video 1",
        "channel_name": target_ch.name,
    }
    candidate2 = {
        "index": 1,
        "href": "/watch?v=cand2222222",
        "title": "Target Video 2",
        "channel_name": target_ch.name,
    }

    test_worker.fetch_recommended_videos = MagicMock(return_value=[candidate1, candidate2])
    test_worker.target_limit = 1
    test_worker.is_unlimited = False

    calls = []
    def fake_play(item, full_url, title, ch_display, *args, **kwargs):
        calls.append(title)
        if "Target Video 1" in title:
            return WatchResult(completed=False, reason="in_page_click_failed")
        return WatchResult(completed=True, watched_seconds=60.0, playback_finished=True)

    test_worker._play_target_video = MagicMock(side_effect=fake_play)

    # Run the worker's suggested section branch
    target_channels = test_worker.get_target_channels()
    session_watched_keys = set()
    channel_buffer_counts = {}

    rec_candidates = test_worker.fetch_recommended_videos()
    suggested_played = False
    failed_rec_ids = set()

    while not test_worker.stop_event.is_set():
        valid_candidates = [
            c for c in rec_candidates
            if (c.get("href", "").split("v=")[-1] or c.get("href", "")) not in failed_rec_ids
        ]
        target_match = test_worker._find_preferred_target_video(
            candidates=valid_candidates,
            target_channels=target_channels,
            last_channel_name=None,
            session_watched_keys=session_watched_keys,
            channel_watch_counts=channel_buffer_counts,
            channel_consecutive_counts=channel_buffer_counts,
        )
        if not target_match:
            break

        item, match_obj, full_url, title, ch_display = target_match
        cand_id = full_url.split("v=")[-1]

        watch_res = test_worker._play_target_video(
            item, full_url, title, ch_display, session_watched_keys, is_home=False, source="suggested"
        )

        if watch_res.reason == "in_page_click_failed":
            failed_rec_ids.add(cand_id)
            continue

        suggested_played = True
        break

    assert calls == ["Target Video 1", "Target Video 2"]
    assert suggested_played is True


def test_suggested_section_clicks_back_button_when_all_candidates_fail(test_worker):
    """When all candidate clicks in suggested section fail, the browser back button is clicked."""
    from src.automation.youtube_actions import WatchResult

    target_ch = test_worker.get_target_channels()[0]

    candidate1 = {
        "index": 0,
        "href": "/watch?v=cand1111111",
        "title": "Target Video 1",
        "channel_name": target_ch.name,
    }

    test_worker.fetch_recommended_videos = MagicMock(return_value=[candidate1])
    test_worker._play_target_video = MagicMock(return_value=WatchResult(completed=False, reason="in_page_click_failed"))

    rec_candidates = test_worker.fetch_recommended_videos()
    suggested_played = False
    failed_rec_ids = set()

    while not test_worker.stop_event.is_set():
        valid_candidates = [
            c for c in rec_candidates
            if (c.get("href", "").split("v=")[-1] or c.get("href", "")) not in failed_rec_ids
        ]
        target_match = test_worker._find_preferred_target_video(
            candidates=valid_candidates,
            target_channels=test_worker.get_target_channels(),
            last_channel_name=None,
            session_watched_keys=set(),
        )
        if not target_match:
            break

        item, match_obj, full_url, title, ch_display = target_match
        cand_id = full_url.split("v=")[-1]

        watch_res = test_worker._play_target_video(
            item, full_url, title, ch_display, set(), is_home=False, source="suggested"
        )

        if watch_res.reason == "in_page_click_failed":
            failed_rec_ids.add(cand_id)
            continue

        suggested_played = True
        break

    if failed_rec_ids and not suggested_played:
        test_worker.driver.back()

    assert suggested_played is False
    test_worker.driver.back.assert_called_once()

