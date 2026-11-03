"""Unit tests for the Channel Video Fetcher utility."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from src.utils.channel_fetcher import (
    ChannelFetchResult,
    FetchedVideo,
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


def test_fetch_channel_videos_requires_api_key():
    result = fetch_channel_videos("@coolchannel")
    assert isinstance(result, ChannelFetchResult)
    assert result.error is not None
    assert "YouTube Data API key is not configured" in result.error


@patch("src.utils.channel_fetcher.fetch_channel_videos_api")
def test_fetch_channel_videos_delegates_to_api(mock_api):
    mock_api.return_value = ChannelFetchResult(
        channel_title="Cool Channel",
        channel_url="https://www.youtube.com/@coolchannel/videos",
        videos=[FetchedVideo("xyz987", "https://youtube.com/watch?v=xyz987", "Test Video")],
    )
    with patch.dict("os.environ", {"YOUTUBE_API_KEY": "AIzaSyTEST_KEY"}):
        result = fetch_channel_videos("@coolchannel")
    assert result.error is None
    assert result.channel_title == "Cool Channel"
    assert len(result.videos) == 1
    assert result.videos[0].video_id == "xyz987"


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


def test_fetch_channel_title_online_without_api_key():
    from src.utils.channel_fetcher import fetch_channel_title_online
    with patch.dict("os.environ", {}, clear=True):
        name = fetch_channel_title_online("@iamcarhacker")
    assert name is None


@patch("src.utils.channel_fetcher.fetch_channel_data_api")
def test_fetch_channel_title_online_with_api_key(mock_api):
    from src.utils.channel_fetcher import fetch_channel_title_online
    mock_api.return_value = ChannelFetchResult(channel_title="Official Car Hacker Channel")
    with patch.dict("os.environ", {"YOUTUBE_API_KEY": "AIzaSyTEST_KEY"}):
        name = fetch_channel_title_online("@iamcarhacker")
    assert name == "Official Car Hacker Channel"


def test_extract_videos_from_data_with_none_max_results():
    """Verify extract_videos_from_data does not raise TypeError when max_results is None."""
    data = {
        "lockupViewModel": {
            "contentId": "dQw4w9WgXcQ",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": "Never Gonna Give You Up"}
                }
            },
        }
    }
    # Should not raise TypeError: '>' not supported between instances of 'NoneType' and 'int'
    videos = extract_videos_from_data(data, max_results=None)
    assert len(videos) == 1
    assert videos[0].video_id == "dQw4w9WgXcQ"


@patch("src.utils.channel_fetcher.fetch_channel_videos_api")
def test_fetch_channel_videos_with_none_max_results(mock_api):
    """Verify fetch_channel_videos passes max_results=50 when max_results is None."""
    mock_api.return_value = ChannelFetchResult(
        channel_title="Test Channel",
        videos=[FetchedVideo("dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ", "Test")],
    )
    with patch.dict("os.environ", {"YOUTUBE_API_KEY": "AIzaSyTEST_KEY"}):
        result = fetch_channel_videos("@testchannel", max_results=None)
    assert result.error is None
    assert len(result.videos) == 1
    assert result.videos[0].video_id == "dQw4w9WgXcQ"
    mock_api.assert_called_once()


@patch("urllib.request.urlopen")
def test_extract_shorts_from_reel_item_and_shorts_lockup(mock_urlopen):
    """Verify Shorts format items (reelItemRenderer & shortsLockupViewModel) are parsed with is_short=True."""
    data = {
        "reelItemRenderer": {
            "videoId": "short_vid_1",
            "headline": {"simpleText": "Short Reel Video"},
        },
        "shortsLockupViewModel": {
            "entityId": "shorts-shelf-item-short_vid_2",
            "overlayMetadata": {
                "primaryText": {"content": "Short Lockup Video"}
            },
        },
    }
    videos = extract_videos_from_data(data, max_results=10)
    assert len(videos) == 2
    s1 = next(v for v in videos if v.video_id == "short_vid_1")
    assert s1.is_short is True
    assert "shorts/short_vid_1" in s1.url
    assert s1.title == "Short Reel Video"

    s2 = next(v for v in videos if v.video_id == "short_vid_2")
    assert s2.is_short is True
    assert "shorts/short_vid_2" in s2.url
    assert s2.title == "Short Lockup Video"

