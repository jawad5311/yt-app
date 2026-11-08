import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.quota_tracker import (
    QuotaTracker,
    get_quota_tracker,
    record_api_quota_usage,
    set_quota_tracker,
)


@pytest.fixture(autouse=True)
def cleanup_quota_singleton():
    """Ensure global tracker is cleaned up before and after each test."""
    set_quota_tracker(None)
    yield
    set_quota_tracker(None)


def test_quota_tracker_initialization(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)

    assert tracker.get_used() == 0
    today = datetime.now().strftime("%Y-%m-%d")
    assert tracker.get_date() == today
    assert quota_file.exists()

    with open(quota_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["date"] == today
    assert data["used"] == 0


def test_quota_tracker_increment_and_persistence(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)

    tracker.increment(1)
    assert tracker.get_used() == 1

    tracker.increment(5)
    assert tracker.get_used() == 6

    # Verify reload from disk
    tracker2 = QuotaTracker(storage_file=quota_file)
    assert tracker2.get_used() == 6
    assert tracker2.get_date() == datetime.now().strftime("%Y-%m-%d")


def test_quota_tracker_day_rollover_resets_to_zero(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Seed quota file with yesterday's date and usage
    with open(quota_file, "w", encoding="utf-8") as f:
        json.dump({"date": yesterday, "used": 500}, f)

    # When tracker loads today, it should detect day difference and reset to 0
    tracker = QuotaTracker(storage_file=quota_file)
    assert tracker.get_used() == 0
    today = datetime.now().strftime("%Y-%m-%d")
    assert tracker.get_date() == today

    # Verify persisted on disk as 0 for today
    with open(quota_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["date"] == today
    assert data["used"] == 0


def test_quota_tracker_dynamic_day_rollover_during_runtime(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)
    tracker.increment(42)
    assert tracker.get_used() == 42

    listener_calls = []
    tracker.register_listener(lambda val: listener_calls.append(val))

    # Simulate system clock ticking over to tomorrow
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    with patch.object(QuotaTracker, "_get_current_system_date", return_value=tomorrow):
        # check_day_rollover detects system clock date change
        rolled_over = tracker.check_day_rollover()
        assert rolled_over is True
        assert tracker.get_used() == 0
        assert tracker.get_date() == tomorrow
        assert listener_calls == [0]

    # Subsequent check on same day should not reset
    with patch.object(QuotaTracker, "_get_current_system_date", return_value=tomorrow):
        assert tracker.check_day_rollover() is False


def test_quota_tracker_listener_notification(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)

    updates = []
    tracker.register_listener(lambda u: updates.append(u))

    tracker.increment(1)
    tracker.increment(3)
    assert updates == [1, 4]

    tracker.reset()
    assert updates == [1, 4, 0]


def test_quota_tracker_thread_safety(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)

    def worker():
        for _ in range(50):
            tracker.increment(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.get_used() == 200


def test_quota_tracker_corrupt_file_recovery(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    with open(quota_file, "w", encoding="utf-8") as f:
        f.write("{invalid json content!@#$")

    tracker = QuotaTracker(storage_file=quota_file)
    assert tracker.get_used() == 0
    assert tracker.get_date() == datetime.now().strftime("%Y-%m-%d")


def test_channel_fetcher_records_quota_on_api_calls(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)
    set_quota_tracker(tracker)

    # 1. validate_youtube_api_key
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        from src.utils.channel_fetcher import validate_youtube_api_key
        ok, msg = validate_youtube_api_key("AIzaSyDummyKeyTest123")
        assert ok is True
        assert tracker.get_used() == 1

    # 2. get_playlist_total_results
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"pageInfo": {"totalResults": 45}}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        from src.utils.channel_fetcher import get_playlist_total_results
        tot = get_playlist_total_results("PL12345", api_key="AIzaSyKey")
        assert tot == 45
        assert tracker.get_used() == 2


def test_yt_bot_app_quota_ui_integration(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(storage_file=quota_file)
    tracker.increment(15)
    set_quota_tracker(tracker)

    import tkinter as tk
    from src.config.manager import ConfigManager
    from src.ui.app import YTBotApp

    cfg_file = tmp_path / "settings.json"
    cfg = ConfigManager(config_file=cfg_file)

    app = YTBotApp(config_mgr=cfg)
    try:
        app.update()
        assert hasattr(app, "quota_label")
        assert app.quota_label is not None
        assert app.quota_label.cget("text") == "Today's Quota: 15"

        # Check placement geometry
        info = app.quota_label.place_info()
        assert float(info["relx"]) == 1.0
        assert int(info["x"]) == -115
        assert int(info["y"]) == 7
        assert info["anchor"] == "n"

        # Trigger quota update
        tracker.increment(5)
        app.update()
        assert app.quota_label.cget("text") == "Today's Quota: 20"

        # Simulate day change
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        with patch.object(QuotaTracker, "_get_current_system_date", return_value=tomorrow):
            app._poll_quota_clock()
            app.update()
            assert app.quota_label.cget("text") == "Today's Quota: 0"
    finally:
        try:
            app._safe_exit(force=True)
        except Exception:
            pass
        try:
            if hasattr(app, "winfo_exists") and app.winfo_exists():
                app.destroy()
        except Exception:
            pass
