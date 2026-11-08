"""Unit tests verifying Add Channel and Sync All Content buttons have identical length and proper spacing."""
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from src.ui.views.channels_view import ChannelsView


@pytest.fixture
def tk_root():
    """Create a hidden Tk root for widget testing."""
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        try:
            root.destroy()
        except Exception:
            pass
    except Exception:
        pytest.skip("Tkinter display not available in environment")


def test_add_channel_and_sync_data_all_same_size_and_spacing(tk_root):
    """Verify Add Channel and Sync All Content buttons have the same width and proper vertical spacing."""
    app = MagicMock()
    app.current_view_name = "channels"
    app.appearance_mode = "Dark"
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store.channel_totals.return_value = MagicMock(watch_seconds=0, runs=0)

    view = ChannelsView(tk_root, app)
    tk_root.update()

    try:
        assert hasattr(view, "add_btn")
        assert hasattr(view, "sync_data_btn")

        add_width = view.add_btn.winfo_reqwidth()
        sync_width = view.sync_data_btn.winfo_reqwidth()
        add_height = view.add_btn.winfo_reqheight()
        sync_height = view.sync_data_btn.winfo_reqheight()

        # 1. Both buttons must be the exact same width and height
        assert add_width == sync_width, f"Expected same width, got add_btn={add_width} and sync_data_btn={sync_width}"
        assert add_height == sync_height, f"Expected same height, got add_btn={add_height} and sync_data_btn={sync_height}"

        # 2. Check grid spacing
        add_grid = view.add_btn.grid_info()
        sync_grid = view.sync_data_btn.grid_info()

        # Add Channel should have extra spacing at top and bottom
        add_pady = add_grid.get("pady", (0, 0))
        if isinstance(add_pady, tuple):
            assert add_pady[0] >= 4, f"Expected top padding >= 4 on add_btn, got {add_pady[0]}"
            assert add_pady[1] >= 10, f"Expected bottom padding >= 10 on add_btn, got {add_pady[1]}"

        # Sync All Content should have spacing from Add Channel above
        sync_pady = sync_grid.get("pady", (0, 0))
        if isinstance(sync_pady, tuple):
            assert sync_pady[0] >= 2, f"Expected top padding >= 2 on sync_data_btn, got {sync_pady[0]}"

        # Total vertical spacing between Add Channel and Sync All Content must be at least 12px
        total_v_gap = (add_pady[1] if isinstance(add_pady, tuple) else int(add_pady)) + \
                      (sync_pady[0] if isinstance(sync_pady, tuple) else int(sync_pady))
        assert total_v_gap >= 12, f"Expected vertical gap >= 12px between buttons, got {total_v_gap}px"
    finally:
        try:
            view.destroy()
        except Exception:
            pass
