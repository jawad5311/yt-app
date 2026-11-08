"""Tests for in-app self-dismissing bubble/balloon notification on Save Settings."""
import tkinter as tk
from unittest.mock import MagicMock, patch
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


def test_settings_bubble_notification_on_success(ctk_app, tmp_path):
    """Test that valid settings show in-app bubble 'Settings updated' for 3 seconds without Windows dialog."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = ctk_app
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

    # Verify bubble widget styling matches theme colors
    assert view._bubble_widget.cget("background") in ("#064e3b", "#f0fdf4", "#1e293b")

    # Verify action bar status indicator
    assert view._save_status_lbl is not None
    assert "Settings updated" in view._save_status_lbl.cget("text")

    # Test dismissal
    view.dismiss_bubble()
    assert view._bubble_widget is None
    assert view._save_status_lbl is None
    assert view._bubble_timer is None
    view.destroy()


def test_settings_bubble_notification_on_failure(ctk_app, tmp_path):
    """Test that invalid settings show in-app bubble with failure reason without Windows dialog."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = ctk_app
    app = MagicMock()
    app.config_mgr = mgr
    view = SettingsView(root, app)

    # Set invalid value: negative threads
    view.threads_entry.delete(0, tk.END)
    view.threads_entry.insert(0, "-9")

    view.save_settings()

    assert view.last_settings_updated is False
    assert "between 1 and 10" in view.last_bubble_message or "not updated" in view.last_bubble_message
    assert view._bubble_widget is not None
    assert view._bubble_timer is not None

    # Verify bubble widget styling matches error red
    assert view._bubble_widget.cget("background") in ("#7f1d1d", "#fef2f2", "#1e293b")

    # Verify action bar status indicator
    assert view._save_status_lbl is not None
    assert ("between 1 and 10" in view._save_status_lbl.cget("text") or "not updated" in view._save_status_lbl.cget("text"))
    view.destroy()


def test_settings_bubble_auto_dismiss(ctk_app, tmp_path):
    """Test that the bubble timer cancels old bubble when save_settings is called repeatedly."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = ctk_app
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
    assert ("between 1 and 10" in view.last_bubble_message or "not updated" in view.last_bubble_message)
    assert view._bubble_widget is not first_bubble
    view.destroy()
