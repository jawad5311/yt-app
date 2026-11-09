"""Unit tests for Save Settings self-dismissing bubble/balloon notification."""
import tkinter as tk
from unittest.mock import MagicMock, patch

from src.config.manager import ConfigManager
from src.ui.confirm_dialog import ThemedAlertDialog
from src.ui.views.settings_view import SettingsView


def test_save_settings_shows_success_bubble_notification(ctk_app, tmp_path):
    """Verify that clicking Save Settings displays a self-dismissing bubble/balloon notification without modal confirmation."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "TestPC")
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

    # Verify NO modal confirmation dialog is shown (user doesn't have to confirm)
    assert getattr(view, "last_alert_dialog", None) is None

    # Verify self-dismissing floating bubble/balloon notification is displayed
    assert hasattr(view, "_bubble_widget")
    assert view._bubble_widget is not None

    # Verify status in action bar
    assert view._save_status_lbl is not None
    assert "Settings updated" in view._save_status_lbl.cget("text")

    # Dismiss bubble notification
    view.dismiss_bubble()
    assert view._bubble_widget is None
    assert view._save_status_lbl is None
    view.destroy()


def test_save_settings_shows_validation_error_bubble(ctk_app, tmp_path):
    """Verify that clicking Save Settings with invalid entries displays a self-dismissing error bubble without modal confirmation."""
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("SystemName", "TestPC")
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
    assert "Settings not updated" in view.last_bubble_message or "Parallel Browsers" in view.last_bubble_message

    # Verify NO modal confirmation dialog is shown
    assert getattr(view, "last_alert_dialog", None) is None

    # Verify error bubble widget is displayed
    assert hasattr(view, "_bubble_widget")
    assert view._bubble_widget is not None

    # Verify error status label in action bar
    assert view._save_status_lbl is not None

    view.dismiss_bubble()
    assert view._bubble_widget is None
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
        button_text="OK",
        icon="✓",
        mode="Dark",
    )
    assert dark_dialog.title() == "Dark Alert"
    assert dark_dialog._mode == "Dark"
    assert hasattr(dark_dialog, "btn_ok")
    dark_dialog._on_close()

    # Light mode alert
    light_dialog = ThemedAlertDialog(
        parent=root,
        title="Light Alert",
        heading="Notice",
        message="Check parameters.",
        button_text="Close",
        icon="⚠️",
        mode="Light",
    )
    assert light_dialog.title() == "Light Alert"
    assert light_dialog._mode == "Light"
    assert hasattr(light_dialog, "btn_ok")
    light_dialog._on_close()
