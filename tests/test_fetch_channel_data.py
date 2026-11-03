"""Unit tests for fetch_channel_data_api and separate Videos/Shorts fetching."""

import json
from unittest.mock import MagicMock, patch

from src.utils.channel_fetcher import (
    fetch_channel_data_api,
    format_seconds_to_duration,
    parse_iso8601_duration,
)


def test_parse_iso8601_duration():
    assert parse_iso8601_duration("PT35S") == 35
    assert parse_iso8601_duration("PT7M58S") == 478
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT1H") == 3600
    assert parse_iso8601_duration("PT5M") == 300
    assert parse_iso8601_duration("invalid") == 0
    assert parse_iso8601_duration("") == 0


def test_format_seconds_to_duration():
    assert format_seconds_to_duration(0) == "0:00"
    assert format_seconds_to_duration(35) == "0:35"
    assert format_seconds_to_duration(478) == "7:58"
    assert format_seconds_to_duration(3723) == "1:02:03"


def test_fetch_channel_data_api_videos_and_shorts_separate_limits():
    """Verify that fetch_channel_data_api respects separate video and shorts limits."""
    channel_lookup_resp = {
        "items": [
            {
                "id": "UC1234567890123456789012",
                "snippet": {"title": "Test Channel"},
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "UU1234567890123456789012"}
                },
            }
        ]
    }

    # UULF playlist items (long form)
    uulf_resp = {
        "items": [
            {
                "contentDetails": {"videoId": "vid_long_1"},
                "snippet": {"title": "Long Video 1"},
            },
            {
                "contentDetails": {"videoId": "vid_long_2"},
                "snippet": {"title": "Long Video 2"},
            },
        ]
    }

    # UUSH playlist items (shorts)
    uush_resp = {
        "items": [
            {
                "contentDetails": {"videoId": "short_1"},
                "snippet": {"title": "Short 1"},
            },
            {
                "contentDetails": {"videoId": "short_2"},
                "snippet": {"title": "Short 2"},
            },
        ]
    }

    # videos.list duration response
    videos_list_resp = {
        "items": [
            {"id": "vid_long_1", "contentDetails": {"duration": "PT10M15S"}},
            {"id": "vid_long_2", "contentDetails": {"duration": "PT5M30S"}},
            {"id": "short_1", "contentDetails": {"duration": "PT45S"}},
            {"id": "short_2", "contentDetails": {"duration": "PT15S"}},
        ]
    }

    def mock_urlopen(req, timeout=15):
        url = req.full_url
        mock_resp = MagicMock()
        if "channels?" in url:
            mock_resp.read.return_value = json.dumps(channel_lookup_resp).encode("utf-8")
        elif "playlistId=UULF" in url or "playlistId=UU1234567890" in url:
            mock_resp.read.return_value = json.dumps(uulf_resp).encode("utf-8")
        elif "playlistId=UUSH" in url:
            mock_resp.read.return_value = json.dumps(uush_resp).encode("utf-8")
        elif "videos?" in url:
            mock_resp.read.return_value = json.dumps(videos_list_resp).encode("utf-8")
        else:
            mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_data_api(
            channel_input="@testchannel",
            api_key="TEST_API_KEY",
            video_limit=2,
            shorts_limit=2,
        )

    assert res.error is None
    assert res.status == "limited"
    assert len(res.videos) == 4
    assert res.videos_fetched == 2
    assert res.shorts_fetched == 2

    # Check long form video
    v1 = next(v for v in res.videos if v.video_id == "vid_long_1")
    assert v1.is_short is False
    assert v1.duration_seconds == 615
    assert v1.duration_text == "10:15"
    assert "watch?v=vid_long_1" in v1.url

    # Check short
    s1 = next(v for v in res.videos if v.video_id == "short_1")
    assert s1.is_short is True
    assert s1.duration_seconds == 45
    assert s1.duration_text == "0:45"
    assert "/shorts/short_1" in s1.url


def test_fetch_channel_data_api_all_and_skip():
    """Verify that video_limit=0 fetches all items, and shorts_limit=None skips shorts."""
    channel_lookup_resp = {
        "items": [
            {
                "id": "UC1234567890123456789012",
                "snippet": {"title": "Test Channel"},
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "UU1234567890123456789012"}
                },
            }
        ]
    }

    # Page 1
    page1_resp = {
        "items": [{"contentDetails": {"videoId": "v1"}, "snippet": {"title": "Video 1"}}],
        "nextPageToken": "PAGE_2",
    }
    # Page 2 (end)
    page2_resp = {
        "items": [{"contentDetails": {"videoId": "v2"}, "snippet": {"title": "Video 2"}}],
    }

    mock_responses = [channel_lookup_resp, page1_resp, page2_resp]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        if mock_responses:
            curr = mock_responses.pop(0)
            mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        else:
            mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_data_api(
            channel_input="@testchannel",
            api_key="TEST_API_KEY",
            video_limit=0,  # 0 indicates All
            shorts_limit=None,  # None indicates Skip
        )

    assert res.error is None
    assert res.status == "complete"
    assert res.is_truncated is False
    assert len(res.videos) == 2
    assert res.videos_fetched == 2
    assert res.shorts_fetched == 0


def test_fetch_channel_data_api_shorts_only_all():
    """Verify that video_limit=None (skip videos) and shorts_limit=0 (fetch all shorts) works without error."""
    channel_lookup_resp = {
        "items": [
            {
                "id": "UC1234567890123456789012",
                "snippet": {"title": "Test Channel"},
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "UU1234567890123456789012"}
                },
            }
        ]
    }

    # UUSH playlist items (shorts)
    uush_resp = {
        "items": [
            {"contentDetails": {"videoId": "short_1"}, "snippet": {"title": "Short 1"}},
            {"contentDetails": {"videoId": "short_2"}, "snippet": {"title": "Short 2"}},
        ]
    }

    videos_list_resp = {
        "items": [
            {"id": "short_1", "contentDetails": {"duration": "PT45S"}},
            {"id": "short_2", "contentDetails": {"duration": "PT15S"}},
        ]
    }

    def mock_urlopen(req, timeout=15):
        url = req.full_url
        mock_resp = MagicMock()
        if "channels?" in url:
            mock_resp.read.return_value = json.dumps(channel_lookup_resp).encode("utf-8")
        elif "playlistId=UUSH" in url:
            mock_resp.read.return_value = json.dumps(uush_resp).encode("utf-8")
        elif "videos?" in url:
            mock_resp.read.return_value = json.dumps(videos_list_resp).encode("utf-8")
        else:
            mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_data_api(
            channel_input="@testchannel",
            api_key="TEST_API_KEY",
            video_limit=None,  # Skip regular videos
            shorts_limit=0,     # Fetch all shorts
        )

    assert res.error is None
    assert len(res.videos) == 2
    assert res.shorts_fetched == 2
    assert res.videos_fetched == 0
    assert all(v.is_short for v in res.videos)


@patch("src.utils.channel_fetcher.get_configured_youtube_api_key", return_value=None)
def test_fetch_channel_data_missing_api_key_returns_error(mock_get_key):
    """Verify that fetch_channel_data notifies with an error when no API key is configured."""
    from src.utils.channel_fetcher import fetch_channel_data

    res = fetch_channel_data(
        channel_input="@love_guru_161",
        video_limit=None,
        shorts_limit=50,
    )

    assert res.error is not None
    assert "YouTube Data API key is not configured" in res.error
    assert res.status == "error"
    assert len(res.videos or []) == 0

