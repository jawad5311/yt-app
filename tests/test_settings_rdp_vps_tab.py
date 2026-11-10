"""Unit tests for SettingsView RDP/VPS tab, credential fields, and persistence."""
import tkinter as tk
from unittest.mock import MagicMock
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


@pytest.fixture
def settings_view(ctk_app, tmp_path):
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("SystemName", "TestWorker-PC")
    mgr.set_setting("RdpUsername", "Administrator")
    mgr.set_setting("RdpPassword", "SecretPass123!")
    app = ctk_app
    app.config_mgr = mgr
    app.appearance_mode = "Dark"
    app.apply_appearance_mode = MagicMock()

    view = SettingsView(app, app)
    yield view
    try:
        view.destroy()
    except Exception:
        pass


def test_rdp_vps_tab_presence_and_order(settings_view):
    """Verify that RDP/VPS tab exists in tabview and appears in tab list."""
    assert "RDP/VPS" in settings_view.tabview._tab_dict
    tab_names = list(settings_view.tabview._segmented_button._buttons_dict.keys())
    assert "RDP/VPS" in tab_names
    assert tab_names.index("RDP/VPS") == tab_names.index("Warm Up") - 1


def test_rdp_vps_entries_and_explanatory_label(settings_view):
    """Verify Username and Password entries and explanatory text in RDP/VPS tab."""
    assert hasattr(settings_view, "rdp_username_entry")
    assert hasattr(settings_view, "rdp_password_entry")

    assert settings_view.rdp_username_entry.get() == "Administrator"
    assert settings_view.rdp_password_entry.get() == "SecretPass123!"

    # Verify explanatory text widget
    assert hasattr(settings_view, "rdp_info_label")
    expected_text = "This username and password is sent to the email address for the user to log in to the RDP or VPS if needed."
    assert expected_text in settings_view.rdp_info_label.cget("text")


def test_rdp_vps_save_and_persist(settings_view):
    """Verify that updating RDP username and password saves to configuration file."""
    settings_view.rdp_username_entry.delete(0, tk.END)
    settings_view.rdp_username_entry.insert(0, "NewVPSUser")

    settings_view.rdp_password_entry.delete(0, tk.END)
    settings_view.rdp_password_entry.insert(0, "SuperSecretVPS!99")

    # Form dirty state should be detected
    settings_view._check_form_dirty()
    assert str(settings_view.save_btn.cget("state")) == "normal"

    # Save
    success = settings_view.save_settings()
    assert success is True

    # Check config manager
    cfg = settings_view.config_mgr
    assert cfg.get_setting("RdpUsername") == "NewVPSUser"
    assert cfg.get_setting("RdpPassword") == "SuperSecretVPS!99"

    # Save button should return to disabled after saving
    assert str(settings_view.save_btn.cget("state")) == "disabled"
