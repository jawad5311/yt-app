from unittest.mock import MagicMock

from src.config.stats_store import StatsStore
from src.models.channel import Channel
from src.models.watch_stats import StatRecord
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


def test_get_channel_watch_time(tmp_path):
    view = ChannelsView.__new__(ChannelsView)
    mock_app = MagicMock()
    store = StatsStore(tmp_path / "stats.json")
    rec1 = StatRecord(channel="@TUBEbegins", watch_seconds=125.0)
    rec2 = StatRecord(channel="TUBEbegins", watch_seconds=75.0)
    rec3 = StatRecord(channel="@OtherChannel", watch_seconds=500.0)
    store.records = {
        "v1": rec1, "v2": rec2, "v3": rec3,
        "channel::tubebegins": StatRecord(channel="@TUBEbegins", watch_seconds=200.0),
    }
    store.save()
    mock_app.stats_store = StatsStore(tmp_path / "stats.json")
    view.app_controller = mock_app

    ch = Channel(name="@TUBEbegins", url="https://www.youtube.com/@TUBEbegins")
    # 125 + 75 = 200s -> 200 / 3600 = 0.055 -> 0.1 hours
    assert view.get_channel_watch_time(ch) == "0.1 hours"

    # Test larger duration (e.g. 5400s -> 1.5 hours)
    mock_app.stats_store.get("v1").watch_seconds = 5400.0
    mock_app.stats_store.get("v2").watch_seconds = 0.0
    assert view.get_channel_watch_time(ch) == "1.5 hours"


def test_format_watch_line_with_hours():
    from src.models.watch_stats import StatRecord, format_watch_line

    rec = StatRecord(runs=2, watch_seconds=3600.0, last_result="success")
    line = format_watch_line(rec, max_runs=5, use_hours=True)
    assert "1.0 hours watched" in line
    assert "2/5 runs" in line

    rec2 = StatRecord(runs=1, watch_seconds=500.0, last_result="success")
    line2 = format_watch_line(rec2, use_hours=True)
    assert "8 min watched" in line2


def test_channel_card_name_and_handle_format(ctk_app):
    import customtkinter as ctk
    from src.ui.background_jobs import BackgroundJobs

    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    channel = Channel(
        name="Tube Begins",
        url="https://www.youtube.com/@TUBEbegins",
    )
    view.channels = [channel]
    view.refresh_list()

    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 1

    labels = []
    def _collect(w):
        for c in w.winfo_children():
            if isinstance(c, ctk.CTkLabel):
                labels.append(c.cget("text"))
            _collect(c)
    _collect(cards[0])

    card_text = "\n".join(labels)
    assert "1 - Tube Begins -- @TUBEbegins" in card_text
    assert "Total Watch Time:" in card_text

    view.destroy()
