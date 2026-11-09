from unittest.mock import MagicMock, patch
import customtkinter as ctk
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView, WATCHTIME_OPTIONS


class TestSettingsPlaybackVideoSeparation:
    @pytest.fixture
    def settings_view(self, ctk_app, tmp_path):
        settings_file = tmp_path / "settings.json"
        mgr = ConfigManager(settings_file)
        mgr.set_setting("SystemName", "TestPC")
        app = ctk_app
        app.config_mgr = mgr
        app.apply_appearance_mode = MagicMock()

        view = SettingsView(app, app)
        yield view
        try:
            view.destroy()
        except Exception:
            pass

    def test_settings_tabs_exact_order(self, settings_view):
        """Verify the exact tab order: General, Playback, Video, Shorts, Ads, Interactivity, Email & Reports."""
        tab_names = list(settings_view.tabview._segmented_button._buttons_dict.keys())
        expected_order = [
            "General",
            "Playback",
            "Video",
            "Shorts",
            "Ads",
            "Interactivity",
            "Email & Reports",
        ]
        assert tab_names == expected_order, f"Expected {expected_order}, got {tab_names}"

    def test_tabview_backward_compatibility_aliases(self, settings_view):
        """Verify backward compatibility aliases 'Playback & Video' and 'Playback & Queue' exist in tab dict."""
        assert "Playback & Video" in settings_view.tabview._tab_dict
        assert "Playback & Queue" in settings_view.tabview._tab_dict
        assert "Playback" in settings_view.tabview._tab_dict
        assert "Video" in settings_view.tabview._tab_dict

    def test_playback_tab_widgets_presence(self, settings_view):
        """Verify widgets belonging to playback are instantiated and properly configured."""
        assert hasattr(settings_view, "duration_menu")
        assert hasattr(settings_view, "browsing_unlimited_switch")
        assert hasattr(settings_view, "full_timeout_entry")
        assert hasattr(settings_view, "stop_buffer_entry")
        assert hasattr(settings_view, "no_target_sleep_menu")

        # Verify default / loaded values
        assert settings_view.duration_menu.get() in WATCHTIME_OPTIONS
        assert settings_view.full_timeout_entry.get() == "0"
        assert settings_view.stop_buffer_entry.get() == "10"
        assert settings_view.no_target_sleep_menu.get() == "2 Hours"

    def test_video_tab_widgets_presence(self, settings_view):
        """Verify widgets belonging to long-form video are instantiated and properly configured."""
        assert hasattr(settings_view, "default_video_views_entry")
        assert hasattr(settings_view, "default_video_max_views_entry")
        assert hasattr(settings_view, "cooldown_entry")

        # Verify default / loaded values
        assert settings_view.default_video_views_entry.get() == "3"
        assert settings_view.default_video_max_views_entry.get() == "5"
        assert settings_view.cooldown_entry.get() == "18"

    def test_save_settings_from_both_playback_and_video_tabs(self, settings_view):
        """Verify settings from both separate tabs save and persist properly into configuration."""
        # 1. Modify Playback settings
        settings_view.duration_menu.set("5 minutes")
        settings_view.browsing_unlimited_switch.select()
        settings_view.full_timeout_entry.delete(0, "end")
        settings_view.full_timeout_entry.insert(0, "15")
        settings_view.stop_buffer_entry.delete(0, "end")
        settings_view.stop_buffer_entry.insert(0, "12")
        settings_view.no_target_sleep_menu.set("3 Hours")

        # 2. Modify Video settings
        settings_view.default_video_views_entry.delete(0, "end")
        settings_view.default_video_views_entry.insert(0, "5")
        settings_view.default_video_max_views_entry.delete(0, "end")
        settings_view.default_video_max_views_entry.insert(0, "30")
        settings_view.cooldown_entry.delete(0, "end")
        settings_view.cooldown_entry.insert(0, "2.5")

        with patch("src.ui.views.settings_view.show_themed_alert_dialog"):
            settings_view.save_settings()

        assert settings_view.last_settings_updated is True

        cfg = settings_view.config_mgr
        # Playback assertions
        assert cfg.get_setting("WatchtimeDuration") == "5 minutes"
        assert cfg.get_bool("BrowsingUnlimited") is True
        assert cfg.get_int("FullVideoTimeoutMinutes") == 15
        assert cfg.get_int("AutoplayEndBufferSeconds") == 12
        assert cfg.get_setting("NoTargetFoundSleepHours") == "3 Hours"

        # Video assertions
        assert cfg.get_float("DefaultVideoTargetViews") == 5.0
        assert cfg.get_int("DefaultVideoMaxViews") == 30
        assert cfg.get_float("VideoCooldownHours") == 2.5
