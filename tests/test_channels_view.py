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
    assert "Watch Time:" in card_text

    # Test Option C calculation with tasks having duration and multipliers
    task1 = MagicMock(channel="Tube Begins", duration_seconds=1800, target_multiplier=2.0) # 3600s
    task2 = MagicMock(channel="@tubebegins", duration_seconds=3600, target_multiplier=1.5) # 5400s
    # Total target = 9000s = 2.5 hours
    app.queued_video_tasks = [task1, task2]

    # With 3600s watched (1.0 hour)
    app.stats_store.channel_totals.return_value = MagicMock(runs=1, watch_seconds=3600.0)

    display = view.get_channel_watch_time_display(channel)
    assert "Watch Time: 1.0 / 2.5 hours (40%)" in display

    view.destroy()


def test_open_channel_button_in_modal(ctk_app):
    from unittest.mock import patch
    import customtkinter as ctk

    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(app, app)
    channel = Channel(
        name="Fabio Rodrigues",
        url="https://www.youtube.com/@fabiorodrigues3083",
    )

    with patch("webbrowser.open_new") as mock_open:
        view.open_channel_videos_modal(channel)
        app.update()

        def find_btn(w):
            if isinstance(w, ctk.CTkButton) and w.cget("text") == "Open Channel":
                return w
            for c in w.winfo_children():
                res = find_btn(c)
                if res:
                    return res
            return None

        btn = find_btn(view)
        assert btn is not None, "Open Channel button should exist in channel videos modal"
        btn._command()
        mock_open.assert_called_once_with("https://www.youtube.com/@fabiorodrigues3083")

    view.destroy()



def test_channels_view_reordered_buttons_and_archive_all(ctk_app, tmp_path):
    from src.ui.background_jobs import BackgroundJobs
    from src.config.manager import ConfigManager
    config_path = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=config_path)

    app = ctk_app
    app.config_mgr = mgr
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)

    # 1. Verify clear_btn and archive_all_btn exist and are in the header frame
    assert hasattr(view, "clear_btn")
    assert hasattr(view, "archive_all_btn")
    assert "Clear All" in view.clear_btn.cget("text")
    assert "Archive All" in view.archive_all_btn.cget("text")
    # Verify their master is the header frame (row 0)
    assert view.clear_btn.master.grid_info()["row"] == 0
    assert view.archive_all_btn.master.grid_info()["row"] == 0

    # 2. Verify tab_selector is present
    assert hasattr(view, "tab_selector")
    assert view.tab_selector.master.grid_info()["row"] == 1

    # 3. Verify sync_all_channels_shorts_btn is in row 1 action container
    assert hasattr(view, "sync_all_channels_shorts_btn")
    assert view.sync_all_channels_shorts_btn.master.grid_info()["row"] == 1

    # 4. Verify archive_all_channels functionality
    ch1 = Channel(name="Channel A", url="https://youtube.com/@chA", archived=False)
    ch2 = Channel(name="Channel B", url="https://youtube.com/@chB", archived=False)
    ch3 = Channel(name="Channel C", url="https://youtube.com/@chC", archived=True)
    view.channels = [ch1, ch2, ch3]
    view.refresh_list()

    assert view.current_tab == "Active"
    view.archive_all_channels()

    # All channels should now be marked archived
    assert all(c.archived for c in view.channels)
    # View should switch to Archived tab
    assert view.current_tab == "Archived"

    # Persisted to config manager as archived
    saved_channels = mgr.get_channels()
    assert len(saved_channels) == 3
    assert all(c.get("archived") is True for c in saved_channels)

    view.destroy()
