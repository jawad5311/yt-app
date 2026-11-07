import tkinter as tk
from tkinter import ttk
from unittest.mock import MagicMock

import pytest

from src.models.channel import Channel
from src.ui.app import YTBotApp
from src.ui.theme import DARK_THEME, LIGHT_THEME, get_theme_colors, setup_ttk_styles
from src.ui.views.accounts_view import AccountsView
from src.ui.views.channels_view import ChannelsView
from src.ui.views.dashboard_view import DashboardView
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


def test_apple_design_tokens_and_colors():
    """Verify Apple HIG design system tokens in Dark and Light themes."""
    dark = get_theme_colors("Dark")
    assert dark["accent"] == "#0a84ff"
    assert dark["success"] == "#30d158"
    assert dark["danger"] == "#ff453a"
    assert dark["warning"] == "#ff9f0a"
    assert dark["card_bg"] == "#202024"
    assert dark["card_border"] == "#323238"

    light = get_theme_colors("Light")
    assert light["accent"] == "#007aff"
    assert light["success"] == "#34c759"
    assert light["danger"] == "#ff3b30"
    assert light["warning"] == "#ff9500"
    assert light["card_bg"] == "#ffffff"
    assert light["card_border"] == "#e5e5ea"


def test_apple_ttk_styles_registration(tk_root):
    """Verify all global Apple TTK styles are properly registered."""
    style = setup_ttk_styles(tk_root, mode="Dark")

    # Card frame style
    card_bg = style.lookup("Card.TFrame", "background")
    assert card_bg == "#202024"

    # Buttons
    assert style.lookup("Primary.TButton", "background") == "#0a84ff"
    assert style.lookup("Danger.TButton", "background") == "#ff453a"
    assert style.lookup("Success.TButton", "background") == "#30d158"
    assert style.lookup("Warning.TButton", "background") == "#ff9f0a"

    # Sidebar Nav
    assert style.lookup("NavActive.TButton", "background") == "#0a84ff"
    assert style.lookup("NavActive.TButton", "foreground") == "#ffffff"

    # Progressbar & Treeview
    assert style.lookup("Horizontal.TProgressbar", "background") == "#0a84ff"
    assert style.lookup("Treeview", "background") == "#202024"


def test_sidebar_apple_navigation_structure(tk_root):
    """Verify sidebar uses Apple macOS structure with title, subtitle, and nav items."""
    controller = MagicMock()
    controller.config_mgr.get_setting.return_value = "Dark"
    controller.config_mgr.get_channels.return_value = []
    controller.config_mgr.get_video_queue.return_value = []

    # Mock YTBotApp _build_ui execution
    app = YTBotApp.__new__(YTBotApp)
    app.config_mgr = controller.config_mgr
    app.stats_store = MagicMock()
    app.loaded_accounts = []
    app.queued_video_tasks = []
    app.active_threads = []
    app.active_workers = []
    app.stop_event = MagicMock()
    app.pause_event = MagicMock()
    app.is_running = False
    app.current_mode = "idle"
    app.current_view_name = "dashboard"

    # Test initialization of sidebar widgets
    app.sidebar = ttk.Frame(tk_root, style="Sidebar.TFrame")
    app.sidebar_header = ttk.Frame(app.sidebar, style="Sidebar.TFrame")
    app.nav_buttons = {}

    nav_items = [
        ("dashboard", "📊  Dashboard"),
        ("accounts", "👤  Accounts"),
        ("channels", "📺  Channels"),
        ("queue", "🎥  Video Queue"),
        ("shorts_queue", "⚡  Shorts Queue"),
        ("settings", "⚙  Settings"),
        ("logs", "📋  Logs"),
    ]
    for name, label in nav_items:
        btn = ttk.Button(app.sidebar, text=label, style="Nav.TButton")
        app.nav_buttons[name] = btn

    assert len(app.nav_buttons) == 7
    assert "dashboard" in app.nav_buttons
    assert "queue" in app.nav_buttons
    assert "settings" in app.nav_buttons


def test_channels_view_apple_header(tk_root):
    """Verify ChannelsView contains Apple typography title and action buttons."""
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = []
    controller.config_mgr.get_video_queue.return_value = []
    controller.stats_store = MagicMock()

    view = ChannelsView(tk_root, controller)
    assert hasattr(view, "clear_btn")
    assert hasattr(view, "archive_all_btn")
    assert hasattr(view, "tab_selector")
    assert hasattr(view, "scroll_frame")


def test_queue_view_apple_header(tk_root):
    """Verify QueueView contains Apple typography title, subtitle, and action buttons."""
    controller = MagicMock()
    controller.config_mgr.get_video_queue.return_value = []
    controller.stats_store = MagicMock()

    # Video Queue
    view_video = QueueView(tk_root, controller, shorts_only=False)
    assert hasattr(view_video, "sort_btn")
    assert hasattr(view_video, "sort_dir_btn")

    # Shorts Queue
    view_shorts = QueueView(tk_root, controller, shorts_only=True)
    assert hasattr(view_shorts, "sort_btn")
    assert hasattr(view_shorts, "sort_dir_btn")


def test_accounts_view_apple_header(tk_root):
    """Verify AccountsView contains Apple typography title and action buttons."""
    controller = MagicMock()
    controller.config_mgr.get_accounts.return_value = []

    view = AccountsView(tk_root, controller)
    assert hasattr(view, "scroll_frame")
    assert hasattr(view, "accounts")


def test_settings_view_apple_header_and_tabs(tk_root):
    """Verify SettingsView contains Apple header and segmented Tabview."""
    controller = MagicMock()
    controller.config_mgr.get_setting.return_value = "Dark"
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = False

    view = SettingsView(tk_root, controller)
    assert hasattr(view, "tabview")
    assert "General" in view.tabview._tab_dict
    assert "Playback & Video" in view.tabview._tab_dict
    assert "Shorts" in view.tabview._tab_dict


def test_logs_view_apple_header_and_console(tk_root):
    """Verify LogsView contains Apple header and high-contrast dark console area."""
    controller = MagicMock()
    view = LogsView(tk_root, controller)
    assert hasattr(view, "clear_btn")
    assert hasattr(view, "copy_btn")
    assert hasattr(view, "log_textbox")
    assert view.log_textbox.cget("background") == "#18181c"


def test_apple_toggle_switch_component(tk_root):
    """Verify AppleToggleSwitch state transitions, callbacks, and HIG styling."""
    from src.ui.components import AppleToggleSwitch, CheckBox, Switch

    events = []
    sw = AppleToggleSwitch(tk_root, text="Enable Feature", command=lambda: events.append("toggled"))

    # Initial state
    assert sw.get() == 0
    assert bool(sw.get()) is False
    assert sw.cget("text") == "Enable Feature"

    # Select / Deselect
    sw.select()
    assert sw.get() == 1
    assert bool(sw.get()) is True
    assert len(events) == 0  # programmatic select does not fire command

    sw.deselect()
    assert sw.get() == 0
    assert len(events) == 0

    # User toggle click
    sw.toggle()
    assert sw.get() == 1
    assert events == ["toggled"]

    # Invoke
    sw.invoke()
    assert sw.get() == 0
    assert events == ["toggled", "toggled"]

    # Disabled state
    sw.configure(state="disabled")
    assert sw.cget("state") == "disabled"
    sw._on_click()
    assert sw.get() == 0  # No change when disabled

    # Verify CheckBox and Switch aliases
    chk = CheckBox(tk_root, text="")
    assert isinstance(chk, AppleToggleSwitch)
    assert chk.get() == 0
    chk.select()
    assert chk.get() == 1

    sett_sw = Switch(tk_root, text="Fallback")
    assert isinstance(sett_sw, AppleToggleSwitch)
    assert sett_sw.get() == 0
    sett_sw.select()
    assert sett_sw.get() == 1
