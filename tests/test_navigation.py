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


def test_dismiss_youtube_dialogs_no_elements():
    """Verify dismiss_youtube_dialogs handles empty/mock drivers gracefully."""
    class MockDriver:
        def find_elements(self, by, value):
            return []

    from src.automation.navigation import dismiss_youtube_dialogs
    # Should not raise exception
    dismiss_youtube_dialogs(MockDriver())


def test_dismiss_youtube_dialogs_clicks_displayed():
    """Verify dismiss_youtube_dialogs clicks displayed dialog buttons."""
    clicked = []

    class MockElement:
        def __init__(self, name):
            self.name = name

        def is_displayed(self):
            return True

        def click(self):
            clicked.append(self.name)

    class MockDriver:
        def find_elements(self, by, value):
            if "Accept all" in value:
                return [MockElement("consent")]
            if "dismiss-button" in value:
                return [MockElement("promo")]
            return []

    from src.automation.navigation import dismiss_youtube_dialogs
    dismiss_youtube_dialogs(MockDriver())
    assert "consent" in clicked
    assert "promo" in clicked

