"""Unit tests for YouTube Data API v3 channel pagination and error notifications (no scraper fallback)."""

import json
import threading
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from src.utils.channel_fetcher import (
    ChannelFetchResult,
    FetchedVideo,
    fetch_channel_data,
    fetch_channel_data_api,
    fetch_channel_videos,
)


def _make_channel_lookup_response(channel_id="UC1234567890abcdefghij", title="Test Creator"):
    return {
        "items": [
            {
                "id": channel_id,
                "snippet": {"title": title},
                "contentDetails": {
                    "relatedPlaylists": {
                        "uploads": "UU" + channel_id[2:],
                    }
                },
                "statistics": {"videoCount": "150"},
            }
        ]
    }


def _make_playlist_items_response(video_ids, next_page_token=None, total_results=150):
    items = []
    for vid in video_ids:
        items.append({
            "contentDetails": {"videoId": vid},
            "snippet": {
                "title": f"Video Title {vid}",
                "resourceId": {"videoId": vid},
            },
        })
    resp = {
        "items": items,
        "pageInfo": {"totalResults": total_results, "resultsPerPage": len(video_ids)},
    }
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    return resp


def test_fetch_channel_data_notifies_when_api_key_missing():
    """When no API key is configured, fetch_channel_data returns an error notification and does not fall back."""
    with patch.dict("os.environ", {}, clear=True):
        res = fetch_channel_data("https://www.youtube.com/@somechannel", config_mgr=None)

    assert res.status == "error"
    assert res.error is not None
    assert "YouTube Data API key is not configured" in res.error
    assert res.provider == "youtube_api_v3"


def test_fetch_channel_videos_notifies_when_api_key_missing():
    """When no API key is configured, fetch_channel_videos returns an error notification."""
    with patch.dict("os.environ", {}, clear=True):
        res = fetch_channel_videos("https://www.youtube.com/@somechannel", config_mgr=None)

    assert res.status == "error"
    assert res.error is not None
    assert "YouTube Data API key is not configured" in res.error


def test_fetch_channel_data_paginates_all_videos_via_api():
    """When video_limit=0 (unlimited), fetch_channel_data paginates all pages via YouTube Data API."""
    ch_json = _make_channel_lookup_response()
    page1 = _make_playlist_items_response([f"vid_p1_{i}" for i in range(50)], next_page_token="TOKEN_PAGE2")
    page2 = _make_playlist_items_response([f"vid_p2_{i}" for i in range(50)], next_page_token="TOKEN_PAGE3")
    page3 = _make_playlist_items_response([f"vid_p3_{i}" for i in range(30)], next_page_token=None)

    def mock_urlopen(req, timeout=15):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        mock_resp = MagicMock()
        if "channels?" in url:
            mock_resp.read.return_value = json.dumps(ch_json).encode("utf-8")
        elif "playlistItems" in url:
            if "pageToken=TOKEN_PAGE2" in url:
                mock_resp.read.return_value = json.dumps(page2).encode("utf-8")
            elif "pageToken=TOKEN_PAGE3" in url:
                mock_resp.read.return_value = json.dumps(page3).encode("utf-8")
            else:
                mock_resp.read.return_value = json.dumps(page1).encode("utf-8")
        elif "videos?" in url:
            mock_resp.read.return_value = json.dumps({"items": []}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    progress_reports = []

    def _prog(count, kind):
        progress_reports.append((count, kind))

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
         patch.dict("os.environ", {"YOUTUBE_API_KEY": "AIzaSyTEST_KEY"}):
        res = fetch_channel_data(
            "https://www.youtube.com/@testcreator",
            video_limit=0,
            shorts_limit=None,
            config_mgr=None,
            progress_callback=_prog,
        )

    assert res.error is None
    assert res.status == "complete"
    assert len(res.videos) == 130  # 50 + 50 + 30
    assert res.fetched_count == 130
    assert len(progress_reports) >= 3


def test_fetch_channel_data_api_notifies_on_invalid_api_key():
    """When API key is rejected (HTTP 400), notify the user with the detailed message from YouTube."""
    error_body = json.dumps({
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
        }
    }).encode("utf-8")

    def mock_urlopen(req, timeout=15):
        raise urllib.error.HTTPError(
            url="https://googleapis.com",
            code=400,
            msg="Bad Request",
            hdrs=MagicMock(),
            fp=MagicMock(read=lambda: error_body),
        )

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_data_api("@test", api_key="INVALID_KEY")

    assert res.status == "error"
    assert res.error is not None
    assert "API key not valid" in res.error


def test_fetch_channel_data_api_notifies_on_quota_exceeded():
    """When quota is exceeded (HTTP 403), notify the caller with the quota message."""
    error_body = json.dumps({
        "error": {
            "code": 403,
            "message": "The request cannot be completed because you have exceeded your quota.",
            "status": "RESOURCE_EXHAUSTED",
        }
    }).encode("utf-8")

    def mock_urlopen(req, timeout=15):
        raise urllib.error.HTTPError(
            url="https://googleapis.com",
            code=403,
            msg="Forbidden",
            hdrs=MagicMock(),
            fp=MagicMock(read=lambda: error_body),
        )

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_data_api("@test", api_key="QUOTA_KEY")

    assert res.status == "error"
    assert res.error is not None
    assert "exceeded your quota" in res.error


def test_fetch_channel_data_api_notifies_channel_not_found():
    """When channel lookup returns 0 items, notify that the channel was not found."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"items": []}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = fetch_channel_data_api("@unknown_channel", api_key="VALID_KEY")

    assert res.status == "error"
    assert res.error is not None
    assert "Channel not found" in res.error
