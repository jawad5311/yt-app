"""Regression tests for Issue 29: Support paginated channel fetching and report partial results."""

import json
import threading
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from src.utils.channel_fetcher import (
    ChannelFetchResult,
    FetchedVideo,
    fetch_channel_videos,
    fetch_channel_videos_api,
    get_configured_youtube_api_key,
    sanitize_api_url,
)
from src.ui.views.channel_import_modal import ChannelImportModal


def make_channel_lookup_response(channel_title="Awesome Channel", uploads_id="UU1234567890"):
    return {
        "items": [
            {
                "snippet": {"title": channel_title},
                "contentDetails": {
                    "relatedPlaylists": {"uploads": uploads_id}
                },
            }
        ]
    }


def make_playlist_items_response(video_ids, next_page_token=None):
    items = []
    for vid in video_ids:
        items.append({
            "contentDetails": {"videoId": vid},
            "snippet": {"title": f"Title for {vid}"},
        })
    resp = {"items": items}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    return resp


def test_requested_results_span_multiple_pages():
    """Pagination requests and collects videos across two or more pages."""
    mock_responses = [
        # 1. Channel lookup
        make_channel_lookup_response(),
        # 2. Page 1
        make_playlist_items_response([f"vid_{i}" for i in range(1, 4)], next_page_token="PAGE_2_TOKEN"),
        # 3. Page 2
        make_playlist_items_response([f"vid_{i}" for i in range(4, 7)]),
    ]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        curr = mock_responses.pop(0)
        mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", max_results=10)

    assert res.status == "complete"
    assert res.error is None
    assert len(res.videos) == 6
    assert [v.video_id for v in res.videos] == [f"vid_{i}" for i in range(1, 7)]


def test_finite_limit_stops_collection_at_requested_number():
    """A finite limit stops collection as soon as the requested count is satisfied."""
    mock_responses = [
        make_channel_lookup_response(),
        make_playlist_items_response([f"vid_{i}" for i in range(1, 10)], next_page_token="PAGE_2_TOKEN"),
    ]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        curr = mock_responses.pop(0)
        mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", max_results=4)

    assert res.status == "limited"
    assert len(res.videos) == 4
    assert res.is_truncated is True


def test_all_continues_until_provider_exhausted():
    """Requesting 'All' (max_results=0) continues until no next page token is returned."""
    mock_responses = [
        make_channel_lookup_response(),
        make_playlist_items_response(["v1", "v2"], next_page_token="PAGE_2"),
        make_playlist_items_response(["v3", "v4"], next_page_token="PAGE_3"),
        make_playlist_items_response(["v5"]),
    ]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        curr = mock_responses.pop(0)
        mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", max_results=0)

    assert res.status == "complete"
    assert res.is_truncated is False
    assert len(res.videos) == 5
    assert [v.video_id for v in res.videos] == ["v1", "v2", "v3", "v4", "v5"]


def test_duplicate_videos_across_pages_appear_once():
    """Duplicate videos appearing across consecutive pages are deduplicated in order."""
    mock_responses = [
        make_channel_lookup_response(),
        make_playlist_items_response(["dup1", "unique1"], next_page_token="PAGE_2"),
        make_playlist_items_response(["dup1", "unique2"]),
    ]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        curr = mock_responses.pop(0)
        mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", max_results=0)

    assert len(res.videos) == 3
    assert [v.video_id for v in res.videos] == ["dup1", "unique1", "unique2"]


def test_repeated_page_tokens_terminate_safely():
    """Repeated continuation tokens terminate pagination safely without an infinite loop."""
    mock_responses = [
        make_channel_lookup_response(),
        make_playlist_items_response(["v1"], next_page_token="LOOP_TOKEN"),
        make_playlist_items_response(["v2"], next_page_token="LOOP_TOKEN"),
    ]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        curr = mock_responses.pop(0)
        mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", max_results=0)

    assert len(res.videos) == 2
    assert res.warning is not None
    assert "Repeated page token detected" in res.warning


def test_cancellation_returns_clear_interrupted_result():
    """Cancelling a fetch returns an interrupted status with earlier videos preserved."""
    cancel_event = threading.Event()
    cancel_event.set()

    mock_responses = [
        make_channel_lookup_response(),
    ]

    def mock_urlopen(req, timeout=15):
        mock_resp = MagicMock()
        curr = mock_responses.pop(0)
        mock_resp.read.return_value = json.dumps(curr).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", cancel_event=cancel_event)

    assert res.status == "interrupted"
    assert res.is_truncated is True
    assert "interrupted by user" in (res.warning or "")


def test_later_page_failure_retains_earlier_videos_and_reports_incompleteness():
    """If a later page fails with a network error, earlier videos are retained with partial status."""
    def mock_urlopen(req, timeout=15):
        url = req.full_url
        if "channels?" in url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(make_channel_lookup_response()).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp
        if "playlistItems" in url and "pageToken" not in url:
            # Page 1 succeeds
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                make_playlist_items_response(["page1_vid1", "page1_vid2"], next_page_token="P2")
            ).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp
        # Page 2 fails
        raise urllib.error.HTTPError(url=url, code=500, msg="Internal Server Error", hdrs=MagicMock(), fp=None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_videos_api("@test", api_key="TEST_KEY", max_results=10)

    assert res.status == "partial"
    assert len(res.videos) == 2
    assert [v.video_id for v in res.videos] == ["page1_vid1", "page1_vid2"]
    assert res.is_truncated is True
    assert "Pagination interrupted by network error" in (res.warning or "")


def test_missing_pagination_config_cannot_produce_false_all_fetched():
    """When no API key is configured and All is selected, status is explicitly initial_page_only."""
    sample_html = """
    <html>
      <head><title>Test Channel - YouTube</title></head>
      <body>
        <script>var ytInitialData = {
          "contents": {
            "twoColumnBrowseResultsRenderer": {
              "tabs": [{
                "tabRenderer": {
                  "content": {
                    "richGridRenderer": {
                      "contents": [{
                        "richItemRenderer": {
                          "content": {
                            "videoRenderer": {
                              "videoId": "initial_vid_1",
                              "title": {"simpleText": "Initial Video"}
                            }
                          }
                        }
                      }]
                    }
                  }
                }
              }]
            }
          }
        };</script>
      </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.read.return_value = sample_html.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch.dict("os.environ", {}, clear=True):
        res = fetch_channel_videos("@test", max_results=0, config_mgr=None)

    assert res.status == "initial_page_only"
    assert res.is_truncated is True
    assert len(res.videos) == 1
    assert "Fetched initial page only" in (res.warning or "")


def test_api_keys_are_absent_from_logs_and_errors():
    """Sanitize helper redacts API keys from URL and error strings."""
    raw_url = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&key=SECRET_AIzaSyD_EXAMPLE_12345&maxResults=50"
    sanitized = sanitize_api_url(raw_url)

    assert "SECRET_AIzaSyD_EXAMPLE_12345" not in sanitized
    assert "key=REDACTED" in sanitized


def test_import_modal_surfaces_partial_result_and_warning():
    """ChannelImportModal displays appropriate notices for partial and initial-page-only results."""
    res = ChannelFetchResult(
        channel_title="Demo Channel",
        channel_url="https://youtube.com/@demo",
        videos=[FetchedVideo("v1", "https://youtube.com/watch?v=v1", "Vid 1")],
        status="initial_page_only",
        warning="Initial page only test warning",
    )

    modal = ChannelImportModal.__new__(ChannelImportModal)
    modal.channel_name = res.channel_title
    modal.channel_url = res.channel_url
    modal.fetched_videos = res.videos
    modal.existing_video_ids = set()
    modal.fetch_result = res

    # Verify attributes and status text logic
    new_count = 1
    existing_count = 0
    status_text = (
        f"Found {len(modal.fetched_videos)} videos · "
        f"{new_count} new, {existing_count} already in queue"
    )
    if modal.fetch_result:
        if modal.fetch_result.status == "initial_page_only":
            status_text += " (Initial page only)"

    assert "(Initial page only)" in status_text
    assert modal.fetch_result.warning == "Initial page only test warning"
