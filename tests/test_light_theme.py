"""Unit tests for Light Theme and dark/light switching across all views and containers."""
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.app import YTBotApp
from src.ui.components import AppleToggleSwitch, ScrollableFrame
from src.ui.theme import DARK_THEME, LIGHT_THEME, get_appearance_mode, get_theme_colors, set_appearance_mode, setup_ttk_styles
from src.ui.views.accounts_view import AccountsView
from src.ui.views.channels_view import ChannelsView
from src.ui.views.logs_view import LogsView
from src.ui.views.queue_view import QueueView
from src.ui.views.settings_view import SettingsView


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def test_theme_mode_switch_tokens():
    """Verify get_theme_colors respects active mode and returns correct palette."""
    set_appearance_mode("Dark")
    assert get_appearance_mode() == "Dark"
    assert get_theme_colors()["bg"] == "#161619"
    assert get_theme_colors()["card_bg"] == "#202024"

    set_appearance_mode("Light")
    assert get_appearance_mode() == "Light"
    assert get_theme_colors()["bg"] == "#f5f5f7"
    assert get_theme_colors()["card_bg"] == "#ffffff"
    assert get_theme_colors()["fg"] == "#1d1d1f"

    # Reset back to Dark
    set_appearance_mode("Dark")


def test_scrollable_frame_light_background_and_expansion(tk_root):
    """Verify ScrollableFrame canvas background matches theme and expands to fill height."""
    setup_ttk_styles(tk_root, mode="Light")
    sf = ScrollableFrame(tk_root, label_text="Test Frame", width=400, height=300)
    try:
        # Verify canvas background is light (#f5f5f7), not dark (#161619)
        assert sf._parent_canvas.cget("background") == "#f5f5f7"

        # Verify expansion on configure
        event = MagicMock(width=400, height=500)
        sf._on_canvas_configure(event)
        canvas_item_h = sf._parent_canvas.itemcget(sf._window_id, "height")
        assert float(canvas_item_h) >= 500

        # Switch to Dark and verify apply_theme updates canvas background
        sf.apply_theme("Dark")
        assert sf._parent_canvas.cget("background") == "#161619"

        # Switch to Light and verify apply_theme updates canvas background
        sf.apply_theme("Light")
        assert sf._parent_canvas.cget("background") == "#f5f5f7"
    finally:
        try:
            sf.destroy()
        except Exception:
            pass


def test_apple_toggle_switch_light_theme(tk_root):
    """Verify toggle switch canvas background resolves to light in light mode."""
    setup_ttk_styles(tk_root, mode="Light")
    sw = AppleToggleSwitch(tk_root, text="Test Toggle")
    try:
        assert sw._resolve_bg() in ("#f5f5f7", "#ffffff")
        assert sw._canvas.cget("background") in ("#f5f5f7", "#ffffff")
    finally:
        try:
            sw.destroy()
        except Exception:
            pass


def test_logs_view_light_theme(tk_root):
    """Verify LogsView text widget has white background and dark text in light theme."""
    controller = MagicMock()
    controller.appearance_mode = "Light"
    view = LogsView(tk_root, controller)
    try:
        view.apply_theme("Light")
        assert view.log_textbox.cget("background") == "#ffffff"
        assert view.log_textbox.cget("foreground") == "#1d1d1f"
        # Tag colors for readability on white
        assert view.log_textbox.tag_cget("default", "foreground") == "#1D1D1F"

        # Switch to Dark
        view.apply_theme("Dark")
        assert view.log_textbox.cget("background") == "#18181c"
        assert view.log_textbox.cget("foreground") == "#e2e8f0"
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_accounts_view_light_theme_empty_state(tk_root):
    """Verify AccountsView has light canvas background when no accounts are added."""
    setup_ttk_styles(tk_root, mode="Light")
    controller = MagicMock()
    controller.appearance_mode = "Light"
    controller.config_mgr.get_accounts.return_value = []
    view = AccountsView(tk_root, controller)
    try:
        assert view.scroll_frame._parent_canvas.cget("background") == "#f5f5f7"
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_settings_view_ads_tab_light_theme(tk_root):
    """Verify SettingsView Ads tab scroll frame has light canvas background."""
    setup_ttk_styles(tk_root, mode="Light")
    controller = MagicMock()
    controller.appearance_mode = "Light"
    controller.config_mgr.get_setting.return_value = "Light"
    controller.config_mgr.get_int.return_value = 0
    controller.config_mgr.get_bool.return_value = True

    view = SettingsView(tk_root, controller)
    try:
        # Locate scroll_ads in Ads tab
        ads_tab = view.tabview._tab_dict.get("Ads")
        assert ads_tab is not None
        # Find scrollable frame in ads_tab
        for child in ads_tab.winfo_children():
            if hasattr(child, "_parent_canvas"):
                assert child._parent_canvas.cget("background") == "#f5f5f7"
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_app_apply_appearance_mode_propagates_to_all_views(tk_root):
    """Verify YTBotApp.apply_appearance_mode switches all views and containers seamlessly."""
    app = YTBotApp.__new__(YTBotApp)
    app.config_mgr = MagicMock()
    app.stats_store = MagicMock()
    app.loaded_accounts = []
    app.queued_video_tasks = []
    app.current_view_name = "dashboard"
    app.nav_buttons = {}

    # Initialize views dictionary with mocked or real views
    logs = LogsView(tk_root, app)
    app.views = {"logs": logs}

    try:
        # Apply Light mode
        app.apply_appearance_mode("Light", persist=False)
        assert app.appearance_mode == "Light"
        assert logs.log_textbox.cget("background") == "#ffffff"

        # Apply Dark mode
        app.apply_appearance_mode("Dark", persist=False)
        assert app.appearance_mode == "Dark"
        assert logs.log_textbox.cget("background") == "#18181c"
    finally:
        try:
            logs.destroy()
        except Exception:
            pass
        set_appearance_mode("Dark")
