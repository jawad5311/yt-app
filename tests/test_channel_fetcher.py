"""Unit tests for the Channel Video Fetcher utility."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from src.utils.channel_fetcher import (
    ChannelFetchResult,
    extract_channel_title,
    extract_fallback_channel_name,
    extract_videos_from_data,
    extract_yt_initial_data,
    fetch_channel_videos,
    normalize_channel_url,
    parse_duration_str,
)


def test_extract_fallback_channel_name():
    assert extract_fallback_channel_name("@myhandle") == "@myhandle"
    assert extract_fallback_channel_name("https://www.youtube.com/@myhandle") == "@myhandle"
    assert extract_fallback_channel_name("https://www.youtube.com/@myhandle/videos") == "@myhandle"
    assert extract_fallback_channel_name("https://www.youtube.com/channel/UC12345/videos") == "UC12345"
    assert extract_fallback_channel_name("https://www.youtube.com/c/TechTips/videos") == "TechTips"
    assert extract_fallback_channel_name("") == "YouTube Channel"


def test_normalize_channel_url():
    assert normalize_channel_url("@myhandle") == "https://www.youtube.com/@myhandle/videos"
    assert normalize_channel_url("myhandle") == "https://www.youtube.com/@myhandle/videos"
    assert (
        normalize_channel_url("https://www.youtube.com/@myhandle")
        == "https://www.youtube.com/@myhandle/videos"
    )
    assert (
        normalize_channel_url("https://www.youtube.com/@myhandle/videos")
        == "https://www.youtube.com/@myhandle/videos"
    )
    assert (
        normalize_channel_url("youtube.com/channel/UC1234567890")
        == "https://youtube.com/channel/UC1234567890/videos"
    )
    assert normalize_channel_url("") == ""


def test_parse_duration_str():
    assert parse_duration_str("45") == 45
    assert parse_duration_str("8:20") == 500
    assert parse_duration_str("1:02:15") == 3735
    assert parse_duration_str("0:30") == 30
    assert parse_duration_str("") == 0
    assert parse_duration_str("invalid") == 0


def test_extract_yt_initial_data():
    sample_html = """
    <html>
      <head></head>
      <body>
        <script>var ytInitialData = {"foo": "bar", "number": 123};</script>
      </body>
    </html>
    """
    data = extract_yt_initial_data(sample_html)
    assert data is not None
    assert data.get("foo") == "bar"
    assert data.get("number") == 123
    assert extract_yt_initial_data("<html></html>") is None


def test_extract_channel_title():
    data1 = {"metadata": {"channelMetadataRenderer": {"title": "Tech Channel"}}}
    assert extract_channel_title(data1) == "Tech Channel"

    data2 = {"header": {"pageHeaderRenderer": {"pageTitle": "Gaming Hub"}}}
    assert extract_channel_title(data2) == "Gaming Hub"

    data3 = {
        "header": {
            "c4TabbedHeaderRenderer": {
                "title": {"runs": [{"text": "Vlog World"}]}
            }
        }
    }
    assert extract_channel_title(data3) == "Vlog World"
    assert extract_channel_title({}) == ""


def test_extract_videos_from_lockup_view_model():
    mock_data = {
        "contents": {
            "richGridRenderer": {
                "contents": [
                    {
                        "richItemRenderer": {
                            "content": {
                                "lockupViewModel": {
                                    "contentId": "abc123xyz00",
                                    "metadata": {
                                        "lockupMetadataViewModel": {
                                            "title": {
                                                "content": "Awesome Video 1"
                                            }
                                        }
                                    },
                                    "contentImage": {
                                        "thumbnailViewModel": {
                                            "overlays": [
                                                {
                                                    "thumbnailBottomOverlayViewModel": {
                                                        "badges": [
                                                            {
                                                                "thumbnailBadgeViewModel": {
                                                                    "text": "10:15"
                                                                }
                                                            }
                                                        ]
                                                    }
                                                }
                                            ]
                                        }
                                    },
                                }
                            }
                        }
                    }
                ]
            }
        }
    }
    videos = extract_videos_from_data(mock_data)
    assert len(videos) == 1
    assert videos[0].video_id == "abc123xyz00"
    assert videos[0].title == "Awesome Video 1"
    assert videos[0].duration_text == "10:15"
    assert videos[0].duration_seconds == 615
    assert videos[0].url == "https://www.youtube.com/watch?v=abc123xyz00"


def test_extract_videos_from_video_renderer():
    mock_data = {
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "content": {
                                "sectionListRenderer": {
                                    "contents": [
                                        {
                                            "itemSectionRenderer": {
                                                "contents": [
                                                    {
                                                        "videoRenderer": {
                                                            "videoId": "legacy_vid_1",
                                                            "title": {"runs": [{"text": "Legacy Video Title"}]},
                                                            "lengthText": {"simpleText": "5:30"},
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ]
            }
        }
    }
    videos = extract_videos_from_data(mock_data)
    assert len(videos) == 1
    assert videos[0].video_id == "legacy_vid_1"
    assert videos[0].title == "Legacy Video Title"
    assert videos[0].duration_text == "5:30"
    assert videos[0].duration_seconds == 330


def test_extract_videos_deduplication_and_limit():
    # Construct data with duplicate video IDs
    dup_item = {
        "lockupViewModel": {
            "contentId": "dup123",
            "metadata": {"lockupMetadataViewModel": {"title": {"content": "Duplicate Video"}}},
            "contentImage": {},
        }
    }
    item2 = {
        "lockupViewModel": {
            "contentId": "vid2",
            "metadata": {"lockupMetadataViewModel": {"title": {"content": "Second Video"}}},
            "contentImage": {},
        }
    }
    data = {"contents": [dup_item, dup_item, item2]}

    videos = extract_videos_from_data(data, max_results=50)
    assert len(videos) == 2
    assert [v.video_id for v in videos] == ["dup123", "vid2"]

    # Test max_results = 1
    limited = extract_videos_from_data(data, max_results=1)
    assert len(limited) == 1
    assert limited[0].video_id == "dup123"


def test_fetch_channel_videos_empty_input():
    result = fetch_channel_videos("")
    assert result.error == "Channel URL or handle is required."


@patch("urllib.request.urlopen")
def test_fetch_channel_videos_success(mock_urlopen):
    payload = {
        "metadata": {"channelMetadataRenderer": {"title": "Cool Channel"}},
        "contents": {
            "lockupViewModel": {
                "contentId": "xyz987",
                "metadata": {"lockupMetadataViewModel": {"title": {"content": "Test Video"}}},
                "contentImage": {},
            }
        },
    }
    html = f"<script>var ytInitialData = {json.dumps(payload)};</script>"
    mock_resp = MagicMock()
    mock_resp.read.return_value = html.encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    result = fetch_channel_videos("@coolchannel")
    assert isinstance(result, ChannelFetchResult)
    assert result.error is None
    assert result.channel_title == "Cool Channel"
    assert len(result.videos) == 1
    assert result.videos[0].video_id == "xyz987"


@patch("urllib.request.urlopen")
def test_fetch_channel_videos_http_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://youtube.com", code=404, msg="Not Found", hdrs=MagicMock(), fp=None
    )
    result = fetch_channel_videos("@nonexistent")
    assert result.error is not None
    assert "HTTP Error 404" in result.error


def test_extract_channel_title_from_html():
    from src.utils.channel_fetcher import extract_channel_title_from_html

    # og:title meta tag
    html_og = '<html><head><meta property="og:title" content="Iamcarhacker"></head><body></body></html>'
    assert extract_channel_title_from_html(html_og) == "Iamcarhacker"

    # <title> tag with ' - YouTube' suffix
    html_title = '<html><head><title>TUBE begins - YouTube</title></head><body></body></html>'
    assert extract_channel_title_from_html(html_title) == "TUBE begins"

    # meta name="title"
    html_meta = '<html><head><meta name="title" content="Tech Insider"></head><body></body></html>'
    assert extract_channel_title_from_html(html_meta) == "Tech Insider"


@patch("urllib.request.urlopen")
def test_fetch_channel_title_online(mock_urlopen):
    from src.utils.channel_fetcher import fetch_channel_title_online

    html = '<html><head><meta property="og:title" content="Official Car Hacker Channel"></head></html>'
    mock_resp = MagicMock()
    mock_resp.read.return_value = html.encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    name = fetch_channel_title_online("@iamcarhacker")
    assert name == "Official Car Hacker Channel"


@patch("urllib.request.urlopen")
def test_fetch_channel_videos_fallback_to_html_title(mock_urlopen):
    html = '<html><head><meta property="og:title" content="Fallback Car Hacker"></head><body><script>var ytInitialData = {"contents": {}};</script></body></html>'
    mock_resp = MagicMock()
    mock_resp.read.return_value = html.encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    result = fetch_channel_videos("@iamcarhacker")
    assert result.channel_title == "Fallback Car Hacker"
