from unittest.mock import MagicMock

import customtkinter as ctk

from src.models.channel import Channel
from src.ui.views.channels_view import ChannelsView


def test_channel_row_no_edit_button(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(app, app)
    channel = Channel(name="Test Channel", url="https://youtube.com/@test")
    view.channels = [channel]
    view.refresh_list()

    # Verify buttons inside action_frame
    buttons = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(buttons) >= 1
    action_buttons = []
    for frame in buttons:
        for child in frame.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                for btn in child.winfo_children():
                    if isinstance(btn, ctk.CTkButton):
                        action_buttons.append(btn.cget("text"))

    # Edit button is removed from channel card
    assert "✏ Edit" not in action_buttons
    # Action buttons present: reorder, Sync All, Sync Recent, Archive, Delete
    assert "🎬 Videos" not in action_buttons
    assert "⚡ Sync All" in action_buttons
    assert "⚡ Sync Recent" in action_buttons
    assert "📁 Archive" in action_buttons
    assert "🗑 Delete" in action_buttons

    view.destroy()
