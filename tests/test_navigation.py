"""Tests for URL / video-ID helpers in the navigation module."""

import pytest

from src.automation.navigation import extract_video_id


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk"),
        ("https://www.youtube.com/embed/abcdefghijk", "abcdefghijk"),
        ("https://www.youtube.com/v/abcdefghijk", "abcdefghijk"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://example.com/watch?v=notenough", None),
        ("https://www.youtube.com/", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_video_id(url, expected):
    assert extract_video_id(url) == expected
