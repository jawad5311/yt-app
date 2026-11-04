import queue
from unittest.mock import MagicMock, call, patch

from src.automation.youtube_actions import check_ad_playback_progress, ensure_video_playing


def test_check_ad_playback_progress_when_no_ad():
    driver = MagicMock()
    driver.execute_script.return_value = {"isAd": False}

    res = check_ad_playback_progress(driver, sample_delay=0.01)
    assert res["is_ad"] is False
    assert res["is_changing"] is False


def test_check_ad_playback_progress_when_ad_is_actively_advancing():
    driver = MagicMock()
    # First sample: 2.0s, second sample: 2.8s
    driver.execute_script.side_effect = [
        {"isAd": True, "currentTime": 2.0, "paused": False, "timeCurrentText": "0:02", "adText": "Ad 1 of 2"},
        {"isAd": True, "currentTime": 2.8, "paused": False, "timeCurrentText": "0:03", "adText": "Ad 1 of 2"},
    ]

    res = check_ad_playback_progress(driver, sample_delay=0.01)
    assert res["is_ad"] is True
    assert res["is_changing"] is True
    assert res["time1"] == 2.0
    assert res["time2"] == 2.8


def test_check_ad_playback_progress_when_ad_text_changes():
    driver = MagicMock()
    # Current time float static, but countdown text changed
    driver.execute_script.side_effect = [
        {"isAd": True, "currentTime": 0.0, "paused": False, "timeCurrentText": "0:15", "adText": "Ad · 0:15"},
        {"isAd": True, "currentTime": 0.0, "paused": False, "timeCurrentText": "0:14", "adText": "Ad · 0:14"},
    ]

    res = check_ad_playback_progress(driver, sample_delay=0.01)
    assert res["is_ad"] is True
    assert res["is_changing"] is True


def test_check_ad_playback_progress_when_ad_is_stalled():
    driver = MagicMock()
    # Ad is stalled: time not changing, paused is True
    driver.execute_script.side_effect = [
        {"isAd": True, "currentTime": 5.0, "paused": True, "timeCurrentText": "0:05", "adText": "Ad 1 of 2"},
        {"isAd": True, "currentTime": 5.0, "paused": True, "timeCurrentText": "0:05", "adText": "Ad 1 of 2"},
    ]

    res = check_ad_playback_progress(driver, sample_delay=0.01)
    assert res["is_ad"] is True
    assert res["is_changing"] is False
    assert res["time1"] == 5.0
    assert res["time2"] == 5.0


def test_ensure_video_playing_does_not_click_when_ad_already_playing():
    driver = MagicMock()
    log_q = queue.Queue()

    # Ad is actively playing (currentTime changing 1.0 -> 1.5)
    driver.execute_script.side_effect = [
        {"isAd": True, "currentTime": 1.0, "paused": False, "timeCurrentText": "0:01"},
        {"isAd": True, "currentTime": 1.5, "paused": False, "timeCurrentText": "0:02"},
    ]

    res = ensure_video_playing(driver, log_queue=log_q)
    assert res is True

    # Play buttons should NOT have been searched or clicked
    driver.find_elements.assert_not_called()

    # Verify log output confirms ad is already playing and play button was skipped
    messages = []
    while not log_q.empty():
        messages.append(log_q.get())
    assert any("Ad is already playing" in m for m in messages)
    assert any("Not clicking play button" in m for m in messages)


def test_ensure_video_playing_clicks_when_ad_is_stalled():
    driver = MagicMock()
    log_q = queue.Queue()

    # Ad is stalled (time 4.0 -> 4.0, paused=True), then ENSURE_PLAYING_JS returns playing=False
    driver.execute_script.side_effect = [
        {"isAd": True, "currentTime": 4.0, "paused": True, "timeCurrentText": "0:04"},
        {"isAd": True, "currentTime": 4.0, "paused": True, "timeCurrentText": "0:04"},
        {"found": True, "playing": False},  # ENSURE_PLAYING_JS probe
    ]

    mock_btn = MagicMock()
    mock_btn.is_displayed.return_value = True
    mock_btn.get_attribute.return_value = "Play"
    driver.find_elements.return_value = [mock_btn]

    with patch("src.automation.youtube_actions.click_element") as mock_click:
        res = ensure_video_playing(driver, log_queue=log_q)
        assert res is True
        mock_click.assert_called_once_with(driver, mock_btn)

    messages = []
    while not log_q.empty():
        messages.append(log_q.get())
    assert any("Ad playback is stalled" in m for m in messages)
    assert any("Clicking play button" in m for m in messages)
