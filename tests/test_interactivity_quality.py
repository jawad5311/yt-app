"""Tests for extended Target Quality Level (720p, 1080p, 1440p, Max Resolution, 720p default)
and dedicated Sleep & Scheduling tab in SettingsView.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.config.manager import ConfigManager
from src.automation.interactivity import (
    SET_QUALITY_JS,
    HumanInteractivitySimulator,
    set_video_quality,
)
from src.ui.views.settings_view import (
    QUALITY_OPTIONS,
    SettingsView,
)


def test_default_quality_level_is_720p(tmp_path):
    """Verify fresh configuration initializes QualityLevel to '720p' by default."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    assert mgr.get_setting("QualityLevel") == "720p"
    assert mgr.data["settings"]["QualityLevel"] == "720p"


def test_quality_options_include_requested_resolutions():
    """Verify QUALITY_OPTIONS contains 720p, 1080p, 1440p, Max Resolution, and default options."""
    assert "720p" in QUALITY_OPTIONS
    assert "1080p" in QUALITY_OPTIONS
    assert "1440p" in QUALITY_OPTIONS
    assert "Max Resolution" in QUALITY_OPTIONS
    assert "360p" in QUALITY_OPTIONS
    assert "480p" in QUALITY_OPTIONS
    assert "Random (360p / 480p)" in QUALITY_OPTIONS


@pytest.mark.parametrize(
    "input_val,expected_label,expected_level",
    [
        ("720p", "720p", "hd720"),
        ("720", "720p", "hd720"),
        ("1080p", "1080p", "hd1080"),
        ("1080", "1080p", "hd1080"),
        ("1440p", "1440p", "hd1440"),
        ("1440", "1440p", "hd1440"),
        ("Max Resolution", "max", "max"),
        ("max", "max", "max"),
        ("360p", "360p", "medium"),
        ("480p", "480p", "large"),
    ],
)
def test_set_video_quality_arguments(input_val, expected_label, expected_level):
    """Verify set_video_quality passes normalized label and level into SET_QUALITY_JS."""
    driver = MagicMock()
    res = set_video_quality(driver, input_val)
    assert res is True
    driver.execute_script.assert_called_once_with(SET_QUALITY_JS, expected_label, expected_level)


@pytest.mark.parametrize(
    "quality_config,expected_chosen",
    [
        ("720p", "720p"),
        ("1080p", "1080p"),
        ("1440p", "1440p"),
        ("Max Resolution", "Max Resolution"),
        ("max", "Max Resolution"),
        ("480p", "480p"),
        ("360p", "360p"),
    ],
)
def test_interactivity_controller_chosen_quality(quality_config, expected_chosen):
    """Verify HumanInteractivitySimulator plans target quality properly for each setting."""
    controller = HumanInteractivitySimulator(
        enabled=True,
        adjust_quality=True,
        quality_level=quality_config,
        quality_chance=1.0,
    )
    controller.plan_session(video_duration=300.0)
    assert controller.chosen_quality == expected_chosen


def test_interactivity_controller_default_is_720p():
    """Verify default HumanInteractivitySimulator uses 720p."""
    controller = HumanInteractivitySimulator()
    assert controller.quality_level == "720p"
    controller.plan_session(video_duration=300.0)
    assert controller.chosen_quality == "720p"


def test_settings_view_quality_dropdown_defaults_and_normalization(ctk_app, tmp_path):
    """Verify SettingsView initializes Target Quality Level to 720p and normalizes raw inputs."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("SystemName", "TestPC")
    app = ctk_app
    app.config_mgr = mgr

    view = SettingsView(app, app)
    try:
        assert view.quality_menu.get() == "720p"

        # Test setting to 1080p and saving
        view.quality_menu.set("1080p")
        with patch("src.ui.views.settings_view.show_themed_alert_dialog"):
            ok = view.save_settings()
        assert ok is True
        assert mgr.get_setting("QualityLevel") == "1080p"

        # Test setting to Max Resolution and saving
        view.quality_menu.set("Max Resolution")
        with patch("src.ui.views.settings_view.show_themed_alert_dialog"):
            ok = view.save_settings()
        assert ok is True
        assert mgr.get_setting("QualityLevel") == "Max Resolution"

        # Test setting to 1440p and saving
        view.quality_menu.set("1440p")
        with patch("src.ui.views.settings_view.show_themed_alert_dialog"):
            ok = view.save_settings()
        assert ok is True
        assert mgr.get_setting("QualityLevel") == "1440p"
    finally:
        view.destroy()


def test_settings_view_sleep_scheduling_tab(ctk_app, tmp_path):
    """Verify that Sleep & Scheduling has its own tab in SettingsView with all relevant widgets."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("SystemName", "TestPC")
    app = ctk_app
    app.config_mgr = mgr

    view = SettingsView(app, app)
    try:
        # Check tab existence
        assert "Sleep & Scheduling" in view.tabview._tab_dict
        tab_names = list(view.tabview._segmented_button._buttons_dict.keys())
        assert "Sleep & Scheduling" in tab_names

        # Check widgets on Sleep & Scheduling
        assert hasattr(view, "short_sleep_menu")
        assert hasattr(view, "no_target_sleep_menu")
        assert hasattr(view, "night_sleep_switch")
        assert hasattr(view, "night_start_menu")
        assert hasattr(view, "night_window_menu")
        assert hasattr(view, "night_duration_menu")

        # Mutate values and save
        view.short_sleep_menu.set("4 Hours")
        view.night_sleep_switch.select()
        view.night_start_menu.set("23:00 (11:00 PM)")
        view.night_window_menu.set("10 Hours")
        view.night_duration_menu.set("6 Hours")

        with patch("src.ui.views.settings_view.show_themed_alert_dialog"):
            ok = view.save_settings()
        assert ok is True

        assert mgr.get_setting("ShortSleepHours") == "4 Hours"
        assert mgr.get_bool("NightSleepEnabled") is True
        assert mgr.get_setting("NightSleepStartTime") == "23:00 (11:00 PM)"
        assert mgr.get_setting("NightSleepWindowHours") == "10 Hours"
        assert mgr.get_setting("NightSleepDurationHours") == "6 Hours"
    finally:
        view.destroy()
