import tkinter as tk
from unittest.mock import MagicMock

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


def test_settings_bubble_notification_on_success(tmp_path):
    """Test that valid settings show in-app bubble 'Settings updated' for 3 seconds without Windows dialog."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = tk.Tk()
    root.withdraw()
    try:
        app = MagicMock()
        app.config_mgr = mgr
        view = SettingsView(root, app)

        # Set valid settings
        view.threads_entry.delete(0, tk.END)
        view.threads_entry.insert(0, "3")

        view.save_settings()

        assert view.last_settings_updated is True
        assert view.last_bubble_message == "Settings updated"
        assert view._bubble_widget is not None
        assert view._bubble_timer is not None

        # Verify bubble widget styling matches dark theme and success green
        assert view._bubble_widget.cget("background") == "#1e293b"
        assert view._bubble_widget.cget("highlightbackground") == "#10b981"

        # Verify action bar status indicator
        assert view._save_status_lbl is not None
        assert "Settings updated" in view._save_status_lbl.cget("text")

        # Test dismissal
        view.dismiss_bubble()
        assert view._bubble_widget is None
        assert view._save_status_lbl is None
        assert view._bubble_timer is None
    finally:
        root.destroy()


def test_settings_bubble_notification_on_failure(tmp_path):
    """Test that invalid settings show in-app bubble 'Settings not updated' without Windows dialog."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = tk.Tk()
    root.withdraw()
    try:
        app = MagicMock()
        app.config_mgr = mgr
        view = SettingsView(root, app)

        # Set invalid value: negative threads
        view.threads_entry.delete(0, tk.END)
        view.threads_entry.insert(0, "-9")

        view.save_settings()

        assert view.last_settings_updated is False
        assert view.last_bubble_message == "Settings not updated"
        assert view._bubble_widget is not None
        assert view._bubble_timer is not None

        # Verify bubble widget styling matches error red
        assert view._bubble_widget.cget("background") == "#1e293b"
        assert view._bubble_widget.cget("highlightbackground") == "#ef4444"

        # Verify action bar status indicator
        assert view._save_status_lbl is not None
        assert "Settings not updated" in view._save_status_lbl.cget("text")
    finally:
        root.destroy()


def test_settings_bubble_auto_dismiss(tmp_path):
    """Test that the bubble timer cancels old bubble when save_settings is called repeatedly."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = tk.Tk()
    root.withdraw()
    try:
        app = MagicMock()
        app.config_mgr = mgr
        view = SettingsView(root, app)

        # First save: success
        view.threads_entry.delete(0, tk.END)
        view.threads_entry.insert(0, "2")
        view.save_settings()
        first_bubble = view._bubble_widget
        assert view.last_bubble_message == "Settings updated"

        # Second save immediately: failure
        view.threads_entry.delete(0, tk.END)
        view.threads_entry.insert(0, "invalid")
        view.save_settings()

        # The first bubble was destroyed and replaced with the new one
        assert view.last_bubble_message == "Settings not updated"
        assert view._bubble_widget is not first_bubble
    finally:
        root.destroy()
