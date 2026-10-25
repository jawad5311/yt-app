from unittest.mock import MagicMock
from src.models.channel import Channel
from src.ui.views.channels_view import ChannelsView


def test_get_channel_handle_extraction():
    view = ChannelsView.__new__(ChannelsView)

    # 1. Handle from URL
    ch1 = Channel(name="Tube Begins", url="https://www.youtube.com/@TUBEbegins")
    assert view.get_channel_handle(ch1) == "@TUBEbegins"

    # 2. Handle from URL with trailing subpaths
    ch2 = Channel(name="Car Hacker", url="https://www.youtube.com/@iamcarhacker/videos")
    assert view.get_channel_handle(ch2) == "@iamcarhacker"

    # 3. Handle from name if URL lacks it
    ch3 = Channel(name="@SummiaJamshed", url="")
    assert view.get_channel_handle(ch3) == "@SummiaJamshed"

    # 4. Fallback from name without @
    ch4 = Channel(name="Simple code", url="")
    assert view.get_channel_handle(ch4) == "@Simplecode"


def test_get_channel_watch_time():
    view = ChannelsView.__new__(ChannelsView)
    mock_app = MagicMock()
    mock_store = MagicMock()
    mock_store._lock = MagicMock()
    mock_store._lock.__enter__ = MagicMock(return_value=None)
    mock_store._lock.__exit__ = MagicMock(return_value=None)

    rec1 = MagicMock()
    rec1.channel = "@TUBEbegins"
    rec1.watch_seconds = 125.0

    rec2 = MagicMock()
    rec2.channel = "TUBEbegins"
    rec2.watch_seconds = 75.0

    rec3 = MagicMock()
    rec3.channel = "@OtherChannel"
    rec3.watch_seconds = 500.0

    mock_store.records = {"v1": rec1, "v2": rec2, "v3": rec3}
    mock_app.stats_store = mock_store
    view.app_controller = mock_app

    ch = Channel(name="@TUBEbegins", url="https://www.youtube.com/@TUBEbegins")
    # 125 + 75 = 200s -> 3m 20s
    assert view.get_channel_watch_time(ch) == "3m 20s"
