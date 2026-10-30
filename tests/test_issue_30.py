import json
import math
from unittest.mock import MagicMock, patch

import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import APPEARANCE_OPTIONS, SettingsView


def make_dummy_entry(val=""):
    m = MagicMock()
    m.get.return_value = str(val)
    return m


def make_dummy_switch(val=True):
    m = MagicMock()
    m.get.return_value = 1 if val else 0
    return m


def make_dummy_menu(val="Dark"):
    m = MagicMock()
    m.get.return_value = str(val)
    return m


def create_mock_settings_view(config_mgr, app_controller=None):
    view = SettingsView.__new__(SettingsView)
    view.config_mgr = config_mgr
    view.app_controller = app_controller or MagicMock()

    view.fallback_switch = make_dummy_switch(False)
    view.skip_ads_switch = make_dummy_switch(True)
    view.threads_entry = make_dummy_entry("2")
    view.duration_menu = make_dummy_menu("Full Video")
    view.max_ad_entry = make_dummy_entry("45")
    view.full_timeout_entry = make_dummy_entry("10")
    view.stop_buffer_entry = make_dummy_entry("15")
    view.cooldown_entry = make_dummy_entry("2.5")
    view.loop_entry = make_dummy_entry("3")

    view.interactivity_switch = make_dummy_switch(True)
    view.scroll_switch = make_dummy_switch(True)
    view.caption_switch = make_dummy_switch(True)
    view.quality_switch = make_dummy_switch(True)
    view.quality_menu = make_dummy_menu("360p")
    view.micro_pause_switch = make_dummy_switch(True)
    view.like_video_switch = make_dummy_switch(True)
    view.like_comments_switch = make_dummy_switch(True)
    view.appearance_menu = make_dummy_menu("Dark")

    return view


def test_invalid_early_field_produces_no_partial_writes(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    original_settings = dict(mgr.data["settings"])
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    # Change fallback switch (early) and set invalid threads_entry (early)
    view.fallback_switch.get.return_value = 1
    view.threads_entry.get.return_value = "0"  # Invalid: ParallelBrowsers must be 1-10

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called
        assert not mock_mb.showinfo.called

    assert mgr.data["settings"] == original_settings
    disk_data = json.loads(mgr.config_file.read_text(encoding="utf-8"))
    assert disk_data["settings"] == original_settings


def test_invalid_middle_field_produces_no_partial_writes(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    original_settings = dict(mgr.data["settings"])
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    # Change fallback and threads (valid), but set invalid stop buffer (middle)
    view.fallback_switch.get.return_value = 1
    view.threads_entry.get.return_value = "4"
    view.stop_buffer_entry.get.return_value = "999"  # Invalid: > 120

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called
        assert not mock_mb.showinfo.called

    assert mgr.data["settings"] == original_settings
    disk_data = json.loads(mgr.config_file.read_text(encoding="utf-8"))
    assert disk_data["settings"] == original_settings


def test_invalid_final_field_produces_no_partial_writes(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    original_settings = dict(mgr.data["settings"])
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    # Valid earlier entries, but invalid appearance menu (final)
    view.fallback_switch.get.return_value = 1
    view.threads_entry.get.return_value = "4"
    view.appearance_menu.get.return_value = "NeonGlow"  # Invalid

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called
        assert not mock_mb.showinfo.called

    assert mgr.data["settings"] == original_settings
    disk_data = json.loads(mgr.config_file.read_text(encoding="utf-8"))
    assert disk_data["settings"] == original_settings


def test_valid_form_results_in_one_settings_persistence_operation(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    save_count = 0
    original_save_unlocked = mgr._save_unlocked

    def counting_save(force=False):
        nonlocal save_count
        save_count += 1
        return original_save_unlocked(force=force)

    mgr._save_unlocked = counting_save

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showinfo.called

    assert save_count == 1
    assert mgr.get_int("ParallelBrowsers") == 2
    assert mgr.get_int("AutomationLoops") == 3
    assert mgr.get_float("VideoCooldownHours") == 2.5


def test_fractional_cooldown_survives_reopening_and_saving_unchanged(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_setting("VideoCooldownHours", 1.75)

    import customtkinter as ctk
    root = ctk.CTk()
    try:
        app = MagicMock()
        app.config_mgr = mgr
        view = SettingsView(root, app)
        # Verify displayed text is preserved as fractional
        assert view.cooldown_entry.get() == "1.75"

        with patch("src.ui.views.settings_view.messagebox") as mock_mb:
            view.save_settings()
            assert mock_mb.showinfo.called

        # Cooldown remains 1.75
        assert mgr.get_float("VideoCooldownHours") == 1.75

        # Reopen in new config manager
        reloaded = ConfigManager(tmp_path / "settings.json")
        assert reloaded.get_float("VideoCooldownHours") == 1.75
    finally:
        root.destroy()


def test_zero_retains_documented_meaning(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.update_settings({
        "FullVideoTimeoutMinutes": 0,
        "MaxAdWatchSeconds": 0,
        "AutoplayEndBufferSeconds": 0,
        "VideoCooldownHours": 0.0,
    })

    assert mgr.get_int("FullVideoTimeoutMinutes", 5) == 0
    assert mgr.get_int("MaxAdWatchSeconds", 32) == 0
    assert mgr.get_int("AutoplayEndBufferSeconds", 10) == 0
    assert mgr.get_float("VideoCooldownHours", 12.0) == 0.0

    # Ensure get_float also respects 0.0
    assert mgr.get_float("FullVideoTimeoutMinutes", 5.0) == 0.0


def test_nan_infinity_malformed_numbers_and_invalid_option_values_rejected(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    invalid_cooldowns = ["nan", "NaN", "inf", "-inf", "infinity", "abc", "-1.0", "99999"]
    for inv in invalid_cooldowns:
        view.cooldown_entry.get.return_value = inv
        with patch("src.ui.views.settings_view.messagebox") as mock_mb:
            view.save_settings()
            assert mock_mb.showerror.called

    # Reset cooldown to valid
    view.cooldown_entry.get.return_value = "1.5"

    # Malformed ints
    invalid_ints = ["nan", "inf", "1.5", "-5", "abc", ""]
    for inv in invalid_ints:
        view.threads_entry.get.return_value = inv
        with patch("src.ui.views.settings_view.messagebox") as mock_mb:
            view.save_settings()
            assert mock_mb.showerror.called

    # Reset threads
    view.threads_entry.get.return_value = "2"

    # Invalid options
    view.quality_menu.get.return_value = "1080p"
    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called

    view.quality_menu.get.return_value = "360p"
    view.duration_menu.get.return_value = "Invalid Duration"
    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called


def test_persistence_failure_leaves_previous_settings_usable(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    original_settings = dict(mgr.data["settings"])
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    def failing_save(force=False):
        raise IOError("Disk full or permission denied")

    mgr._save_unlocked = failing_save

    view.threads_entry.get.return_value = "5"
    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called

    # In-memory settings rolled back
    assert mgr.data["settings"] == original_settings


def test_failed_validation_does_not_change_appearance(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    view.appearance_menu.get.return_value = "Light"
    view.threads_entry.get.return_value = "invalid"

    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert not app.apply_appearance_mode.called
    assert mgr.get_setting("AppearanceMode") == "Dark"


def test_successful_saving_applies_appearance_without_extra_settings_write(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    view.appearance_menu.get.return_value = "Light"

    with patch.object(mgr, "set_setting") as mock_set:
        with patch("src.ui.views.settings_view.messagebox"):
            view.save_settings()
        # set_setting was not called because update_settings was used
        assert not mock_set.called

    app.apply_appearance_mode.assert_called_once_with("Light", persist=False)
    assert mgr.get_setting("AppearanceMode") == "Light"


def test_concurrent_channel_counter_updates_are_preserved(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    mgr.set_channels([{"name": "Cooking", "url": "https://youtube.com/@cooking", "watch_seconds": 100.0}])

    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    # Simulate worker updating channel watch time while settings view is active
    mgr.increment_channel_watch_seconds("Cooking", 50.0)
    assert mgr.get_channels()[0]["watch_seconds"] == 150.0

    # Save settings from SettingsView
    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    # Channel counter is preserved both in memory and on disk
    assert mgr.get_channels()[0]["watch_seconds"] == 150.0
    disk_data = json.loads(mgr.config_file.read_text(encoding="utf-8"))
    assert disk_data["channels"][0]["watch_seconds"] == 150.0


def test_compatibility_aliases_remain_consistent(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    app = MagicMock()
    view = create_mock_settings_view(mgr, app)

    # Turn off ads
    view.skip_ads_switch.get.return_value = 0
    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert mgr.get_bool("SkipAds") is False
    assert mgr.get_bool("BlockAds") is False

    # Turn on ads
    view.skip_ads_switch.get.return_value = 1
    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert mgr.get_bool("SkipAds") is True
    assert mgr.get_bool("BlockAds") is True
