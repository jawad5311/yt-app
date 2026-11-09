from pathlib import Path

from src.config.manager import ConfigManager


def test_docs_exist_and_no_stale_extension_references():
    repo_root = Path(__file__).resolve().parent.parent
    docs = ["README.md", "FEATURES.md", "TESTING.md", "TODO.md"]

    for doc_name in docs:
        doc_path = repo_root / doc_name
        assert doc_path.exists(), f"{doc_name} should exist"
        text = doc_path.read_text(encoding="utf-8")
        assert len(text) > 500, f"{doc_name} should not be empty"

        # uBlock Origin Lite was removed in favor of script-based skip
        assert "uBlock Origin Lite" not in text, f"{doc_name} should not reference uBlock Origin Lite"
        assert "uBOLite" not in text, f"{doc_name} should not reference uBOLite"


def test_config_defaults_match_documented_settings():
    settings = ConfigManager.DEFAULT_DATA["settings"]
    assert settings["DirectLinkFallback"] is False
    assert settings["WatchtimeDuration"] == "Full Video"
    assert settings["FullVideoTimeoutMinutes"] == 0
    assert settings["AutoplayEndBufferSeconds"] == 10
    assert settings["AutomationLoops"] == 1
    assert settings["BrowsingTargetLimit"] == 1
    assert settings["BrowsingUnlimited"] is True
    assert settings["VideoCooldownHours"] == 18
    assert settings["TargetShortsPerCycle"] == 10
    assert settings["NonTargetReelsLimit"] == 12
    assert settings["TotalNonTargetShortsLimit"] == 33
    assert settings["DefaultShortsMaxViews"] == 5
    assert settings["SkipAds"] is True
    assert settings["MaxAdWatchSeconds"] == 9
    assert settings["BlockAds"] is True
    assert settings["EnableHumanInteractivity"] is True
    assert settings["AppearanceMode"] == "Dark"
    assert settings["LikeVideo"] is True
    assert settings["LikeComments"] is True


def test_documentation_contains_disclaimers():
    repo_root = Path(__file__).resolve().parent.parent
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    features = (repo_root / "FEATURES.md").read_text(encoding="utf-8")

    # Local statistics disclaimer
    assert "monetization" in readme.lower() or "monetisation" in readme.lower()
    assert "official" in readme.lower()

    # Browser automation disclaimer
    assert "undetectable" in features.lower()
    assert "monetization" in features.lower() or "monetisation" in features.lower()


def test_dashboard_unlimited_browsing_checked_by_default_on_app_open(ctk_app):
    from unittest.mock import MagicMock

    from src.ui.views.dashboard_view import DashboardView

    app = ctk_app
    app.config_mgr = MagicMock()
    # Even if config manager returned False from older settings
    app.config_mgr.get_bool.return_value = False
    app.config_mgr.get_int.return_value = 1
    app.stats_store = MagicMock()
    app.stats_store.lifetime_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.start_automation = MagicMock()
    app.start_watching = MagicMock()
    app.stop_automation = MagicMock()
    app.toggle_pause_watching = MagicMock()

    view = DashboardView(app, app)
    # Checkbox MUST be checked by default
    assert view.browsing_unlimited_cb.get() == 1
    assert view.browsing_limit_entry.cget("state") == "disabled"

    # Verify unified action button and Stop button are placed in one line (row 0, columns 0, 1)
    start_info = view.start_watching_btn.grid_info()
    stop_info = view.stop_btn.grid_info()

    assert start_info["row"] == 0
    assert start_info["column"] == 0
    assert stop_info["row"] == 0
    assert stop_info["column"] == 1

    view.destroy()


def test_settings_view_removed_loop_and_browsing_target_limits_kept_unlimited_switch(ctk_app):
    from unittest.mock import MagicMock
    from src.ui.views.settings_view import SettingsView

    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "5m"
    app.config_mgr.get_int.return_value = 5
    app.config_mgr.get_bool.return_value = True

    view = SettingsView(app, app)

    # loop_entry and browsing_limit_entry must be removed from SettingsView
    assert not hasattr(view, "loop_entry")
    assert not hasattr(view, "browsing_limit_entry")

    # browsing_unlimited_switch must be present
    assert hasattr(view, "browsing_unlimited_switch")
    assert view.browsing_unlimited_switch.get() == 1

    view.destroy()


def test_test_suite_module_count():
    repo_root = Path(__file__).resolve().parent.parent
    test_files = list((repo_root / "tests").glob("test_*.py"))
    assert len(test_files) >= 47, f"Expected at least 47 test modules, found {len(test_files)}"
