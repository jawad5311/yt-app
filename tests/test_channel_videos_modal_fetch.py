"""Unit tests for the Videos to Fetch / Shorts to Fetch dropdowns and Fetch Data button in open_channel_videos_modal."""

from unittest.mock import MagicMock, patch

import customtkinter as ctk

from src.models.channel import Channel
from src.ui.views.channels_view import FETCH_DATA_OPTIONS, ChannelsView


def test_channel_videos_modal_has_fetch_dropdowns_and_fetches_directly(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(app, app)
    channel = Channel(name="Test Channel", url="https://youtube.com/@testchannel")
    view.channels = [channel]

    with patch.object(view, "fetch_data_for_channel") as mock_fetch:
        view.open_channel_videos_modal(channel)

        # Find the opened CTkToplevel in view
        modal = None
        for child in view.winfo_children():
            if isinstance(child, ctk.CTkToplevel):
                modal = child
                break
        assert modal is not None, "Modal toplevel should be opened"

        # Search for option menus inside modal
        option_menus = []
        buttons = []
        def _scan(widget):
            for w in widget.winfo_children():
                if isinstance(w, ctk.CTkOptionMenu):
                    option_menus.append(w)
                elif isinstance(w, ctk.CTkButton):
                    buttons.append(w)
                _scan(w)

        _scan(modal)

        # There should be 2 option menus (Videos to Fetch and Shorts to Fetch)
        assert len(option_menus) == 2, f"Expected 2 option menus, found {len(option_menus)}"
        v_menu, s_menu = option_menus[0], option_menus[1]
        assert v_menu.cget("values") == FETCH_DATA_OPTIONS
        assert s_menu.cget("values") == FETCH_DATA_OPTIONS
        assert v_menu.get() == "100"
        assert s_menu.get() == "100"

        # Locate Fetch Data button
        fetch_btn = None
        for b in buttons:
            if "Fetch Data" in b.cget("text"):
                fetch_btn = b
                break
        assert fetch_btn is not None, "Fetch Data button should be present"

        # Click Fetch Data with default "100" and "100"
        fetch_btn._command()
        mock_fetch.assert_called_once()
        args, kwargs = mock_fetch.call_args
        assert kwargs["channel"] == channel
        assert kwargs["video_limit"] == 100
        assert kwargs["shorts_limit"] == 100

        mock_fetch.reset_mock()

        # Change values to "200" videos and "Fetch No Data" shorts
        v_menu.set("200")
        s_menu.set("Fetch No Data")
        fetch_btn._command()
        mock_fetch.assert_called_once()
        args, kwargs = mock_fetch.call_args
        assert kwargs["video_limit"] == 200
        assert kwargs["shorts_limit"] is None

        mock_fetch.reset_mock()

        # When both are "Fetch No Data", warning is shown and fetch is not called
        v_menu.set("Fetch No Data")
        s_menu.set("Fetch No Data")
        with patch("src.ui.views.channels_view.messagebox.showwarning") as mock_warn:
            fetch_btn._command()
            mock_warn.assert_called_once()
            mock_fetch.assert_not_called()

        modal.destroy()
    view.destroy()
