"""Unit tests verifying the Dashboard Sleep & Nap mode toggle switch,
its persistence in ConfigManager, interactive locking during sessions,
and its enforcement in WatchingWorker.
"""
import queue
import threading
import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.ui.theme import setup_ttk_styles
from src.ui.views.dashboard_view import DashboardView


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


def test_dashboard_sleep_nap_toggle_ui_and_persistence(tk_root):
    """Verify that Sleep & Nap toggle switch exists on dashboard, defaults to ON,
    updates ConfigManager when clicked, and reports get_sleep_nap_enabled().
    """
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.side_effect = lambda k, default=True: True if k in ("BrowsingUnlimited", "SleepNapEnabled") else default

    view = DashboardView(tk_root, controller)

    # 1. Switch exists and defaults to selected (1 / True)
    assert hasattr(view, "browsing_sleep_nap_cb")
    assert view.browsing_sleep_nap_cb.get() == 1
    assert view.get_sleep_nap_enabled() is True
    assert view.browsing_sleep_nap_cb.cget("interactive") is True

    # 2. Toggle OFF
    view.browsing_sleep_nap_cb._on_click()
    assert view.browsing_sleep_nap_cb.get() == 0
    assert view.get_sleep_nap_enabled() is False
    controller.config_mgr.update_settings.assert_called_with({"SleepNapEnabled": False})

    # 3. Toggle back ON
    view.browsing_sleep_nap_cb._on_click()
    assert view.browsing_sleep_nap_cb.get() == 1
    assert view.get_sleep_nap_enabled() is True
    controller.config_mgr.update_settings.assert_called_with({"SleepNapEnabled": True})


def test_dashboard_sleep_nap_toggle_locked_during_active_browsing(tk_root):
    """Verify that Sleep & Nap switch is locked (interactive=False) while browsing is active."""
    controller = MagicMock()
    controller.is_running = False
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True

    view = DashboardView(tk_root, controller)

    # Start session
    view.set_running_state(True)
    controller.is_running = True

    assert view.browsing_sleep_nap_cb.cget("interactive") is False
    initial_val = view.browsing_sleep_nap_cb.get()

    # Attempting to click while running must NOT change value
    view.browsing_sleep_nap_cb._on_click()
    assert view.browsing_sleep_nap_cb.get() == initial_val

    # Stop session
    view.set_running_state(False)
    controller.is_running = False

    assert view.browsing_sleep_nap_cb.cget("interactive") is True
    # Now clickable again
    view.browsing_sleep_nap_cb._on_click()
    assert view.browsing_sleep_nap_cb.get() != initial_val


def test_watching_worker_sleep_nap_disabled_bypasses_sleep():
    """Verify that WatchingWorker with sleep_nap_enabled=False bypasses night sleep
    and cleanly stops without sleeping when no target content is found.
    """
    account = Account(email="test@gmail.com", password="pwd")
    channel = Channel(name="Test Channel", enabled=True)
    config_mgr = MagicMock()
    config_mgr.get_bool.return_value = False
    config_mgr.get_int.return_value = 10
    config_mgr.get_short_sleep_hours.return_value = 2.0

    log_queue = queue.Queue()
    stop_event = threading.Event()

    worker = WatchingWorker(
        account=account,
        channels=[channel],
        config_mgr=config_mgr,
        log_queue=log_queue,
        stop_event=stop_event,
        target_limit=10,
        is_unlimited=False,
        sleep_nap_enabled=False,
    )

    assert worker.sleep_nap_enabled is False

    # 1. Night sleep check returns False immediately
    worker.night_scheduler = MagicMock()
    worker.night_scheduler.is_enabled.return_value = True
    assert worker._check_and_enter_night_sleep(set()) is False

    # 2. No target content sleep returns False, sets stop_event, and does NOT sleep
    woke_up = worker._enter_no_target_sleep_cycle(set())
    assert woke_up is False
    assert stop_event.is_set()


def test_watching_worker_sleep_nap_enabled_allows_nap_and_night_sleep():
    """Verify that WatchingWorker with sleep_nap_enabled=True preserves normal sleep behavior."""
    account = Account(email="test@gmail.com", password="pwd")
    channel = Channel(name="Test Channel", enabled=True)
    config_mgr = MagicMock()
    config_mgr.get_bool.side_effect = lambda k, default=False: True if k == "SleepNapEnabled" else default
    config_mgr.get_int.return_value = 10

    log_queue = queue.Queue()
    stop_event = threading.Event()

    worker = WatchingWorker(
        account=account,
        channels=[channel],
        config_mgr=config_mgr,
        log_queue=log_queue,
        stop_event=stop_event,
        target_limit=10,
        is_unlimited=False,
        sleep_nap_enabled=True,
    )

    assert worker.sleep_nap_enabled is True
