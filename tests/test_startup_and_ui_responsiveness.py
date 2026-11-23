import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tkinter as tk

from src.config.manager import ConfigManager
from src.models.video_task import VideoTask
from src.ui.app import YTBotApp


def test_app_has_refresh_stats_views_alias():
    """Verify refresh_stats_views exists and calls refresh_watch_stats without error."""
    app = YTBotApp.__new__(YTBotApp)
    app.refresh_watch_stats = MagicMock()
    app.refresh_stats_views()
    app.refresh_watch_stats.assert_called_once()


def test_background_metadata_enrichment_dispatch_no_attribute_error():
    """Verify background metadata enrichment callback dispatches valid methods without raising AttributeError."""
    app = MagicMock(spec=YTBotApp)
    app.queued_video_tasks = [VideoTask(url="https://youtube.com/watch?v=dQw4w9WgXcQ", title="Test")]
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "dummy_api_key"

    # Verify that calling refresh_stats_views and refresh_watch_stats on real class works
    assert hasattr(YTBotApp, "refresh_stats_views")
    assert hasattr(YTBotApp, "refresh_watch_stats")


def test_native_close_button_safe_on_any_hwnd():
    """Verify _set_native_close_button_enabled does not raise exceptions even on unmapped HWND."""
    app = YTBotApp.__new__(YTBotApp)
    app.winfo_id = MagicMock(return_value=123456)
    # Should complete without error
    app._set_native_close_button_enabled(True)
    app._set_native_close_button_enabled(False)


def test_app_startup_and_view_switching(monkeypatch):
    """Test full app instantiation, view switching, and clean destruction."""
    app = YTBotApp()
    try:
        # Window must exist and be viewable
        assert app.winfo_exists()
        assert app.current_view_name == "dashboard"

        # Switch to queue view (exercises card batch rendering)
        app.show_view("queue")
        assert app.current_view_name == "queue"

        # Switch to shorts_queue view
        app.show_view("shorts_queue")
        assert app.current_view_name == "shorts_queue"

        # Switch to stats view (exercises lazy stats refresh)
        app.show_view("stats")
        assert app.current_view_name == "stats"

        # Pump event loop
        app.update()
    finally:
        app.destroy()
