"""Unit test verifying that 'Fetch Name' button is removed, 'Channel Name (Optional):' is shown,
and submitting with blank name resolves channel details via backend."""

from unittest.mock import MagicMock, patch

import customtkinter as ctk

from src.ui.views.channels_view import ChannelsView


def test_add_channel_modal_no_fetch_name_btn_and_optional_label(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(app, app)
    view.open_channel_modal()

    # Find the opened CTkToplevel in view
    modal = None
    for child in view.winfo_children():
        if isinstance(child, ctk.CTkToplevel):
            modal = child
            break
    assert modal is not None, "Add Channel modal should be opened"

    labels = []
    buttons = []
    def _scan(widget):
        for w in widget.winfo_children():
            if isinstance(w, ctk.CTkLabel):
                labels.append(w.cget("text"))
            elif isinstance(w, ctk.CTkButton):
                buttons.append(w.cget("text"))
            _scan(w)

    _scan(modal)

    # Fetch Name button must NOT be present
    assert not any("Fetch Name" in b for b in buttons), "Fetch Name button should be removed"

    # Channel Name label should clearly say '(Optional)'
    name_labels = [l for l in labels if "Channel Name" in l]
    assert len(name_labels) >= 1
    assert "Optional" in name_labels[0]

    modal.destroy()
    view.destroy()


def test_add_channel_submits_blank_name_and_fetches_via_backend(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(app, app)

    with patch.object(view, "_fetch_and_create_channel") as mock_create:
        view.open_channel_modal()

        modal = None
        for child in view.winfo_children():
            if isinstance(child, ctk.CTkToplevel):
                modal = child
                break
        assert modal is not None

        entries = []
        buttons = []
        def _scan(widget):
            for w in widget.winfo_children():
                if isinstance(w, ctk.CTkEntry):
                    entries.append(w)
                elif isinstance(w, ctk.CTkButton):
                    buttons.append(w)
                _scan(w)

        _scan(modal)

        # URL entry is first, Name entry is second
        url_entry = entries[0]
        name_entry = entries[1]
        url_entry.insert(0, "https://www.youtube.com/@techchannel")
        # Name entry left blank

        submit_btn = next(b for b in buttons if b.cget("text") == "Add Channel")
        submit_btn._command()

        # Modal is destroyed and _fetch_and_create_channel is called
        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        assert kwargs["raw_url"] == "https://www.youtube.com/@techchannel"
        assert kwargs["video_limit"] == 100
        assert kwargs["shorts_limit"] == 100
        assert kwargs["fetch_items"] is True

    view.destroy()
