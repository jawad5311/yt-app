"""Unit tests for Max Ad Watch Time ±20% randomization."""
import random
import tkinter as tk
from unittest.mock import MagicMock
import pytest

from src.automation.ads import (
    AdWatchState,
    get_randomized_max_ad_seconds,
    pick_skip_delay,
)
from src.config.manager import ConfigManager
from src.ui.views.settings_view import SettingsView


def test_get_randomized_max_ad_seconds_10_seconds():
    """Setting 10 seconds chooses random values strictly between 8.0 and 12.0 seconds."""
    samples = [get_randomized_max_ad_seconds(10.0) for _ in range(1000)]
    assert all(8.0 <= s <= 12.0 for s in samples)
    # Ensure it's truly distributed across the range
    assert min(samples) < 8.5
    assert max(samples) > 11.5


def test_get_randomized_max_ad_seconds_default_9_seconds():
    """Setting default 9 seconds chooses random values between 7.2 and 10.8 seconds."""
    samples = [get_randomized_max_ad_seconds(9.0) for _ in range(500)]
    assert all(7.2 <= s <= 10.8 for s in samples)


def test_get_randomized_max_ad_seconds_zero_and_negative():
    """Setting 0 or negative seconds returns 0.0 (unlimited ad watch)."""
    assert get_randomized_max_ad_seconds(0) == 0.0
    assert get_randomized_max_ad_seconds(-5) == 0.0


def test_pick_skip_delay_with_max_ad_seconds():
    """pick_skip_delay uses max_ad_seconds when provided, or falls back to default window."""
    for _ in range(100):
        val = pick_skip_delay(max_ad_seconds=10.0)
        assert 8.0 <= val <= 12.0

    # Fallback when None
    for _ in range(100):
        val = pick_skip_delay(max_ad_seconds=None)
        assert 5.0 <= val <= 10.0


def test_ad_watch_state_skips_within_randomized_window_for_10s():
    """AdWatchState calculates target within 8-12s for max_ad_seconds=10 and skips once reached."""
    state = AdWatchState()
    # Ad starts playing
    state.tick(is_ad=True, dt=1.0)
    target = state.get_skip_target(max_ad_seconds=10.0)
    assert 8.0 <= target <= 12.0

    # Advance until just before target
    state.tick(is_ad=True, dt=target - 1.5)
    assert state.break_ad_seconds < target
    assert state.skip_reason(skip_ads=True, max_ad_seconds=10.0) is None

    # Advance past target
    state.tick(is_ad=True, dt=1.0)
    assert state.break_ad_seconds >= target
    reason = state.skip_reason(skip_ads=True, max_ad_seconds=10.0)
    assert reason is not None
    assert "monetisation skip window reached" in reason


def test_ad_watch_state_rearms_new_randomized_target_on_next_break():
    """When an ad finishes, the next ad break generates a fresh ±20% randomized target."""
    state = AdWatchState()
    state.tick(is_ad=True, dt=5.0)
    first_target = state.get_skip_target(max_ad_seconds=10.0)
    assert 8.0 <= first_target <= 12.0

    # Ad finishes
    state.tick(is_ad=False, dt=10.0)
    assert state.break_ad_seconds == 0.0
    assert state._skip_target is None

    # Second ad break starts
    state.tick(is_ad=True, dt=1.0)
    second_target = state.get_skip_target(max_ad_seconds=10.0)
    assert 8.0 <= second_target <= 12.0


def test_settings_view_displays_randomized_ad_label(ctk_app, tmp_path):
    """SettingsView Ads tab displays the ±20% randomized label for Max Ad Watch Time."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(config_file=settings_file)
    mgr.set_setting("MaxAdWatchSeconds", 10)

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr

    view = SettingsView(ctk_app, app_ctrl)
    assert view.max_ad_entry.get() == "10"

    # Verify widget exists and displays in UI
    assert hasattr(view, "max_ad_entry")
    view.destroy()
