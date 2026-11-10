"""Unit tests for SettingsView Enter key saving, balloon notifications, and dynamic Save button state."""
import tkinter as tk
from unittest.mock import MagicMock
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


def test_initial_save_button_is_unclickable(ctk_app, tmp_path):
    """When SettingsView is loaded with saved settings, the Save Settings button is disabled."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "Office-PC-1")
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"

    view = SettingsView(ctk_app, app)
    assert str(view.save_btn.cget("state")) == "disabled"
    view.destroy()


def test_entry_modification_enables_save_button_and_revert_disables_it(ctk_app, tmp_path):
    """Modifying an entry makes Save button clickable; reverting to saved baseline disables it."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "Office-PC-1")
    mgr.set_setting("ParallelBrowsers", 2)
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"

    view = SettingsView(ctk_app, app)
    assert str(view.save_btn.cget("state")) == "disabled"

    # Modify threads entry
    view.threads_entry.delete(0, tk.END)
    view.threads_entry.insert(0, "5")
    view._check_form_dirty()
    assert str(view.save_btn.cget("state")) == "normal"

    # Revert back to original saved value "2"
    view.threads_entry.delete(0, tk.END)
    view.threads_entry.insert(0, "2")
    view._check_form_dirty()
    assert str(view.save_btn.cget("state")) == "disabled"
    view.destroy()


def test_enter_key_saves_settings_and_shows_balloon_notification(ctk_app, tmp_path):
    """Pressing Enter in an entry saves settings immediately, triggers balloon notification, and disables button."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "Initial-PC")
    mgr.set_setting("ParallelBrowsers", 1)
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"

    view = SettingsView(ctk_app, app)
    assert str(view.save_btn.cget("state")) == "disabled"

    # Update system name
    view.system_name_entry.delete(0, tk.END)
    view.system_name_entry.insert(0, "Updated-Worker-PC")
    view._check_form_dirty()
    assert str(view.save_btn.cget("state")) == "normal"

    # Press Enter
    res = view._on_entry_return(None)
    assert res == "break"

    # Verify settings persisted
    assert mgr.get_setting("SystemName") == "Updated-Worker-PC"
    assert view.last_settings_updated is True

    # Verify balloon notification is visible
    assert view._bubble_widget is not None
    assert "Settings updated" in view._save_status_lbl.cget("text")

    # Verify button is now disabled (unclickable) because changes are saved
    assert str(view.save_btn.cget("state")) == "disabled"
    view.destroy()


def test_switch_toggle_enables_save_button_and_click_saves_for_double_confirmation(ctk_app, tmp_path):
    """Toggling a switch enables Save button; clicking the Save button saves and disables it."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "TestPC")
    mgr.set_setting("DirectLinkFallback", False)
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"

    view = SettingsView(ctk_app, app)
    assert str(view.save_btn.cget("state")) == "disabled"

    # Toggle switch
    view.fallback_switch.toggle()
    assert str(view.save_btn.cget("state")) == "normal"

    # Click Save button for double confirmation
    view.save_settings()
    assert view.last_settings_updated is True
    assert mgr.get_bool("DirectLinkFallback") is True

    # Verify balloon notification and disabled button
    assert view._bubble_widget is not None
    assert str(view.save_btn.cget("state")) == "disabled"
    view.destroy()


def test_option_menu_change_enables_save_button(ctk_app, tmp_path):
    """Changing an option menu dropdown selection enables the Save button."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "TestPC")
    mgr.set_setting("WatchtimeDuration", "Full Video")
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"

    view = SettingsView(ctk_app, app)
    assert str(view.save_btn.cget("state")) == "disabled"

    # Change duration menu
    view.duration_menu.set("5 minutes")
    view._check_form_dirty()
    assert str(view.save_btn.cget("state")) == "normal"

    # Save
    view.save_settings()
    assert mgr.get_setting("WatchtimeDuration") == "5 minutes"
    assert str(view.save_btn.cget("state")) == "disabled"
    view.destroy()


def test_enter_key_validation_failure_keeps_save_button_clickable(ctk_app, tmp_path):
    """If an invalid value is entered and Enter is pressed, error balloon is shown and button remains clickable."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "ValidName")
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"

    view = SettingsView(ctk_app, app)
    assert str(view.save_btn.cget("state")) == "disabled"

    # Clear compulsory system name (invalid)
    view.system_name_entry.delete(0, tk.END)
    view._check_form_dirty()
    assert str(view.save_btn.cget("state")) == "normal"

    # Press Enter
    res = view._on_entry_return(None)
    assert res == "break"

    # Save failed
    assert view.last_settings_updated is False
    assert "System Name is required" in view.last_bubble_message

    # Button remains clickable so user can fix and save
    assert str(view.save_btn.cget("state")) == "normal"
    view.destroy()
