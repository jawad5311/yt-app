"""Unit tests for Save Settings pop-up confirmation and validation feedback."""
import tkinter as tk
from unittest.mock import MagicMock, patch

from src.config.manager import ConfigManager
from src.ui.confirm_dialog import ThemedAlertDialog, show_themed_alert_dialog
from src.ui.views.settings_view import SettingsView


def test_save_settings_shows_success_popup(ctk_app, tmp_path):
    """Verify that clicking Save Settings with valid entries presents an Apple HIG themed success pop-up."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = ctk_app
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Dark"
    view = SettingsView(root, app)

    # Valid settings
    view.threads_entry.delete(0, tk.END)
    view.threads_entry.insert(0, "4")

    view.save_settings()

    # Verify settings save status
    assert view.last_settings_updated is True
    assert view.last_bubble_message == "Settings updated"

    # Verify pop-up alert dialog was generated
    assert hasattr(view, "last_alert_dialog")
    dialog = view.last_alert_dialog
    assert dialog is not None
    assert isinstance(dialog, ThemedAlertDialog)
    assert dialog.title() == "Settings Saved"
    assert dialog._mode == "Dark"

    # Verify status in action bar
    assert view._save_status_lbl is not None
    assert "Settings updated" in view._save_status_lbl.cget("text")

    # Dismiss dialog
    dialog._on_close()
    view.destroy()


def test_save_settings_shows_validation_error_popup(ctk_app, tmp_path):
    """Verify that clicking Save Settings with invalid entries presents an Apple HIG error pop-up with details."""
    mgr = ConfigManager(tmp_path / "settings.json")
    root = ctk_app
    app = MagicMock()
    app.config_mgr = mgr
    app.appearance_mode = "Light"
    view = SettingsView(root, app)

    # Invalid threads count
    view.threads_entry.delete(0, tk.END)
    view.threads_entry.insert(0, "999")

    view.save_settings()

    # Verify save failed
    assert view.last_settings_updated is False
    assert view.last_bubble_message == "Settings not updated"

    # Verify error alert dialog was generated
    assert hasattr(view, "last_alert_dialog")
    dialog = view.last_alert_dialog
    assert dialog is not None
    assert isinstance(dialog, ThemedAlertDialog)
    assert dialog.title() == "Invalid Settings"
    assert dialog._mode == "Light"

    # Verify error status label
    assert view._save_status_lbl is not None
    assert "Settings not updated" in view._save_status_lbl.cget("text")

    dialog._on_close()
    view.destroy()


def test_themed_alert_dialog_light_and_dark_modes(ctk_app):
    """Verify ThemedAlertDialog initializes correctly and dismisses gracefully in Dark and Light themes."""
    root = ctk_app

    # Dark mode alert
    dark_dialog = ThemedAlertDialog(
        parent=root,
        title="Dark Alert",
        heading="Saved",
        message="Changes applied.",
        icon="✓",
        mode="Dark",
    )
    assert dark_dialog.title() == "Dark Alert"
    assert dark_dialog._mode == "Dark"
    assert dark_dialog.cget("bg") == "#161619"
    dark_dialog._on_close()

    # Light mode alert
    light_dialog = ThemedAlertDialog(
        parent=root,
        title="Light Alert",
        heading="Warning",
        message="Please check your input.",
        icon="⚠️",
        mode="Light",
    )
    assert light_dialog.title() == "Light Alert"
    assert light_dialog._mode == "Light"
    assert light_dialog.cget("bg") == "#f5f5f7"
    light_dialog._on_close()
