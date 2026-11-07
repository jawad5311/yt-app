"""Unit tests for Channel action button reorganization and Sync All Shorts button functionality."""

from unittest.mock import MagicMock, patch

import customtkinter as ctk

from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.background_jobs import BackgroundJobs
from src.ui.views.channels_view import ChannelsView
from src.utils.channel_fetcher import ChannelFetchResult, FetchedVideo


def test_channel_action_buttons_order_and_grid_positions(ctk_app):
    """Verify that buttons on channel cards follow the required order and grid alignment:
    - Col 0 & 1: Up and down arrow buttons
    - Col 2: Sync Recent button (shown before Sync All)
    - Col 3: Sync All (row 0) and Sync All Shorts (row 1, below Sync All)
    - Col 4: Archive (row 0) and Delete (row 1, below Archive)
    """
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    channel = Channel(
        name="TechCreator",
        url="https://youtube.com/@techcreator",
        total_videos=50,
        total_shorts=20,
        has_new_videos=True,
    )
    view.channels = [channel]
    view.refresh_list()

    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 1
    action_frame = cards[0].grid_slaves(row=0, column=2)[0]

    btn_up = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "▲"), None)
    btn_down = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "▼"), None)
    sync_recent_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync Recent" in b.cget("text")), None)
    sync_all_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and b.cget("text") == "⚡ Sync All"), None)
    sync_shorts_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync All Shorts" in b.cget("text")), None)
    archive_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Archive" in b.cget("text")), None)
    del_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Delete" in b.cget("text")), None)

    assert btn_up is not None
    assert btn_down is not None
    assert sync_recent_btn is not None
    assert sync_all_btn is not None
    assert sync_shorts_btn is not None
    assert archive_btn is not None
    assert del_btn is not None

    # Verify grid positioning
    up_info = btn_up.grid_info()
    dn_info = btn_down.grid_info()
    recent_info = sync_recent_btn.grid_info()
    all_info = sync_all_btn.grid_info()
    shorts_info = sync_shorts_btn.grid_info()
    archive_info = archive_btn.grid_info()
    del_info = del_btn.grid_info()

    # Column 0 & 1: arrows
    assert up_info["column"] == 0
    assert dn_info["column"] == 1

    # Column 2: Sync Recent (shown separately before Sync All)
    assert recent_info["column"] == 2
    assert recent_info["rowspan"] == 2

    # Column 3: Sync All on top (row 0), Sync All Shorts below (row 1)
    assert all_info["column"] == 3
    assert all_info["row"] == 0
    assert shorts_info["column"] == 3
    assert shorts_info["row"] == 1

    # Column 4: Archive on top (row 0), Delete below (row 1)
    assert archive_info["column"] == 4
    assert archive_info["row"] == 0
    assert del_info["column"] == 4
    assert del_info["row"] == 1

    # Verify Sync All (Royal Blue) and Sync All Shorts (Ocean Cerulean Blue) have distinct blue colors and styles
    assert sync_all_btn.cget("style") == "Primary.TButton"
    assert sync_shorts_btn.cget("style") == "Cerulean.TButton"
    assert sync_all_btn.cget("fg_color").lower() == "#2563eb"
    assert sync_shorts_btn.cget("fg_color").lower() == "#0284c7"
    assert sync_all_btn.cget("fg_color").lower() != sync_shorts_btn.cget("fg_color").lower()

    view.destroy()


def test_channel_action_buttons_archived_restore_and_delete(ctk_app):
    """Verify that on archived channels, Restore is on top and Delete is below."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    view.current_tab = "Archived"
    channel = Channel(
        name="ArchivedCreator",
        url="https://youtube.com/@archivedcreator",
        archived=True,
    )
    view.channels = [channel]
    view.refresh_list()

    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 1
    action_frame = cards[0].grid_slaves(row=0, column=2)[0]

    restore_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Restore" in b.cget("text")), None)
    del_btn = next((b for b in action_frame.winfo_children() if isinstance(b, ctk.CTkButton) and "Delete" in b.cget("text")), None)

    assert restore_btn is not None
    assert del_btn is not None

    restore_info = restore_btn.grid_info()
    del_info = del_btn.grid_info()

    assert restore_info["column"] == 4
    assert restore_info["row"] == 0
    assert del_info["column"] == 4
    assert del_info["row"] == 1

    view.destroy()


def test_sync_all_shorts_state_and_command(ctk_app):
    """Verify state of Sync All Shorts button when URL is missing vs present, and verify command invocation."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    ch_nourl = Channel(name="No URL Channel", url="")
    ch_withurl = Channel(name="With URL Channel", url="https://youtube.com/@channel")
    view.channels = [ch_nourl, ch_withurl]
    view.refresh_list()

    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) == 2

    # Card 0 (No URL)
    action_frame_nourl = cards[0].grid_slaves(row=0, column=2)[0]
    shorts_btn_nourl = next((b for b in action_frame_nourl.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync All Shorts" in b.cget("text")), None)
    assert shorts_btn_nourl is not None
    assert shorts_btn_nourl.cget("state") == "disabled"

    # Card 1 (With URL)
    action_frame_withurl = cards[1].grid_slaves(row=0, column=2)[0]
    shorts_btn_withurl = next((b for b in action_frame_withurl.winfo_children() if isinstance(b, ctk.CTkButton) and "Sync All Shorts" in b.cget("text")), None)
    assert shorts_btn_withurl is not None
    assert shorts_btn_withurl.cget("state") == "normal"

    with patch.object(view, "sync_channel_all_shorts") as mock_sync_shorts:
        shorts_btn_withurl._command()
        mock_sync_shorts.assert_called_once_with(ch_withurl)

    view.destroy()


def test_sync_channel_all_shorts_calls_fetch_data_and_opens_modal(ctk_app):
    """Verify that calling sync_channel_all_shorts triggers fetch_data_for_channel with video_limit=None and shorts_limit=0,
    and opens ChannelImportModal on success."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()
    app.queued_video_tasks = []

    view = ChannelsView(app, app)
    channel = Channel(name="Shorts Creator", url="https://youtube.com/@shortscreator")
    view.channels = [channel]
    view.refresh_list()

    mock_res = ChannelFetchResult(
        channel_title="Shorts Creator",
        channel_url=channel.url,
        videos=[
            FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", is_short=True, duration_seconds=45.0),
            FetchedVideo(video_id="s2", url="https://youtube.com/shorts/s2", title="Short 2", is_short=True, duration_seconds=58.0),
        ],
        total_videos=0,
        total_shorts=2,
        status="complete",
    )

    with patch("src.ui.views.channels_view.fetch_channel_data", return_value=mock_res) as mock_fetch, \
         patch("src.ui.views.channels_view.ChannelImportModal") as mock_modal_cls:
        view.sync_channel_all_shorts(channel)
        app.background_jobs.drain()

        # Check fetch_channel_data called with video_limit=None and shorts_limit=0
        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("video_limit") is None
        assert kwargs.get("shorts_limit") == 0

        # Check ChannelImportModal was opened
        mock_modal_cls.assert_called_once()
        modal_kwargs = mock_modal_cls.call_args[1]
        assert modal_kwargs["channel_name"] == "Shorts Creator"
        assert len(modal_kwargs["fetched_videos"]) == 2

    view.destroy()


def test_sync_all_channels_shorts_button_present_in_top_toolbar(ctk_app):
    """Verify that 'Sync All Channels Shorts' button is present in the top action toolbar."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()

    view = ChannelsView(app, app)
    assert hasattr(view, "sync_all_channels_shorts_btn")
    assert "Sync All Channels Shorts" in view.sync_all_channels_shorts_btn.cget("text")
    assert "sync_all_channels_shorts" in str(view.sync_all_channels_shorts_btn.cget("command"))
    view.destroy()


def test_sync_all_channels_shorts_execution_syncs_all_channel_shorts(ctk_app):
    """Verify sync_all_channels_shorts fetches shorts for all active channels and adds to queue."""
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.config_mgr.append_video_tasks.side_effect = lambda tasks, deduplicate=True: tasks
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.background_jobs = BackgroundJobs()
    app.queued_video_tasks = []

    view = ChannelsView(app, app)
    ch1 = Channel(name="Channel One", url="https://youtube.com/@ch1")
    ch2 = Channel(name="Channel Two", url="https://youtube.com/@ch2")
    view.channels = [ch1, ch2]
    view.refresh_list()

    res1 = ChannelFetchResult(
        channel_title="Channel One",
        channel_url=ch1.url,
        videos=[
            FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", is_short=True, duration_seconds=30.0),
        ],
        total_shorts=1,
        status="complete",
    )
    res2 = ChannelFetchResult(
        channel_title="Channel Two",
        channel_url=ch2.url,
        videos=[
            FetchedVideo(video_id="s2", url="https://youtube.com/shorts/s2", title="Short 2", is_short=True, duration_seconds=45.0),
        ],
        total_shorts=1,
        status="complete",
    )

    fetch_map = {ch1.url: res1, ch2.url: res2}
    def mock_fetch(url, **kwargs):
        assert kwargs.get("video_limit") is None
        assert kwargs.get("shorts_limit") == 0
        return fetch_map[url]

    with patch("src.ui.views.channels_view.fetch_channel_data", side_effect=mock_fetch), \
         patch("tkinter.messagebox.showinfo") as mock_info:
        view.sync_all_channels_shorts()
        app.background_jobs.drain()

        # Both channels fetched with video_limit=None and shorts_limit=0
        assert app.config_mgr.append_video_tasks.called
        appended = app.config_mgr.append_video_tasks.call_args[0][0]
        assert len(appended) == 2
        assert appended[0]["url"] == "https://youtube.com/shorts/s1"
        assert appended[1]["url"] == "https://youtube.com/shorts/s2"
        assert ch1.total_shorts == 1
        assert ch2.total_shorts == 1
        mock_info.assert_called_once()

    view.destroy()
