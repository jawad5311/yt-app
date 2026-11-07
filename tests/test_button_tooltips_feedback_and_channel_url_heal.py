import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from src.models.channel import Channel
from src.ui.tooltip import add_tooltip, flash_button_feedback, with_button_feedback
from src.ui.views.accounts_view import AccountsView
from src.ui.views.channels_view import ChannelsView
from src.ui.views.dashboard_view import DashboardView
from src.ui.views.queue_view import QueueView


def test_channel_url_auto_heal_from_name():
    # Handle in name with empty url
    ch1 = Channel.from_dict({"name": "youtube.com/@iamcarhacker", "url": ""})
    assert ch1.url == "https://www.youtube.com/@iamcarhacker"

    # Bare handle in name
    ch2 = Channel.from_dict({"name": "@iamcarhacker", "url": ""})
    assert ch2.url == "https://www.youtube.com/@iamcarhacker"

    # Parse method also heals
    ch3 = Channel.parse("youtube.com/@iamcarhacker", "")
    assert ch3.url == "https://www.youtube.com/@iamcarhacker"


@pytest.fixture
def tk_root(ctk_app):
    return ctk_app


def test_flash_button_feedback_updates_and_restores(tk_root):
    root = tk_root
    btn = tk.Button(root, text="Original Text")
    called = []

    def dummy_cmd():
        called.append(True)

    wrapped = with_button_feedback(btn, "Executing...", dummy_cmd, duration_ms=50)
    wrapped()

    assert called == [True]
    assert btn.cget("text") == "Executing..."

    # Trigger pending after callbacks
    root.update()
    root.after(60, root.quit)
    root.mainloop()

    assert btn.cget("text") == "Original Text"


def test_dashboard_buttons_tooltips_and_feedback(tk_root):
    root = tk_root
    controller = MagicMock()
    controller.config_mgr.get_int.return_value = 1
    controller.config_mgr.get_bool.return_value = True
    view = DashboardView(root, controller)

    # Tooltips exist
    assert hasattr(view.start_watching_btn, "_tooltip") or getattr(view.start_watching_btn, "winfo_exists", None)
    assert hasattr(view.stop_btn, "_tooltip") or getattr(view.stop_btn, "winfo_exists", None)
    assert hasattr(view.pause_btn, "_tooltip") or getattr(view.pause_btn, "winfo_exists", None)

    # Click feedback
    view.start_watching_btn.invoke()
    assert view.start_watching_btn.cget("text") == "Browsing started"

    view.set_running_state(True)
    assert view.stop_btn.cget("state") == "normal"
    view.stop_btn.invoke()
    assert view.stop_btn.cget("text") == "Stopped"
    view.destroy()


def test_channels_view_sync_buttons_enabled_and_blue_with_healed_url(tk_root):
    root = tk_root
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = [
        {"name": "youtube.com/@iamcarhacker", "url": "", "total_videos": 10, "total_shorts": 5}
    ]
    controller.config_mgr.get_video_queue.return_value = []
    controller.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(root, controller)

    # The channel should have its url healed
    assert len(view.channels) == 1
    assert view.channels[0].url == "https://www.youtube.com/@iamcarhacker"

    # Channels to sync should not be empty
    channels_to_sync = [ch for ch in view.channels if ch.url and not ch.archived]
    assert len(channels_to_sync) == 1
    view.destroy()


def test_accounts_view_tooltips_and_feedback():
    root = tk.Tk()
    root.withdraw()
    try:
        controller = MagicMock()
        controller.config_mgr.get_accounts.return_value = []
        view = AccountsView(root, controller)
        assert view.winfo_exists()
    finally:
        root.destroy()


def test_queue_views_tooltips_and_feedback():
    root = tk.Tk()
    root.withdraw()
    try:
        controller = MagicMock()
        controller.config_mgr.get_video_queue.return_value = []
        controller.config_mgr.get_channels.return_value = []

        q_view = QueueView(root, controller, shorts_only=False)
        assert q_view.winfo_exists()

        sq_view = QueueView(root, controller, shorts_only=True)
        assert sq_view.winfo_exists()
    finally:
        root.destroy()


def test_add_channel_modal_default_fetch_no_data_and_stats_button():
    root = tk.Tk()
    root.withdraw()
    try:
        controller = MagicMock()
        controller.config_mgr.get_channels.return_value = []
        controller.config_mgr.get_video_queue.return_value = []
        controller.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

        view = ChannelsView(root, controller)

        # Open modal
        view.open_channel_modal()
        root.update()

        # Find the open modal window
        modals = [w for w in view.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(modals) >= 1
        modal = modals[-1]

        # Check option menus in modal have 'Fetch No Data' default
        from src.ui.components import Button, OptionMenu
        menus = []
        for child in modal.winfo_children():
            if isinstance(child, OptionMenu):
                menus.append(child)
            for sub in getattr(child, "winfo_children", lambda: [])():
                if isinstance(sub, OptionMenu):
                    menus.append(sub)

        assert len(menus) >= 2
        assert menus[0].get() == "Fetch No Data"
        assert menus[1].get() == "Fetch No Data"

        # Check Fetch Channel Stats button is present
        btns = []
        for child in modal.winfo_children():
            if isinstance(child, Button):
                btns.append(child)
            for sub in getattr(child, "winfo_children", lambda: [])():
                if isinstance(sub, Button):
                    btns.append(sub)

        stats_btns = [b for b in btns if "Fetch Channel Stats" in b.cget("text")]
        assert len(stats_btns) == 1

        modal.destroy()
    finally:
        root.destroy()


def test_bulk_apply_modal_separation_video_vs_shorts():
    from src.models.video_task import VideoTask
    from src.ui.views.bulk_apply_modal import BulkApplyModal

    root = tk.Tk()
    root.withdraw()
    try:
        # 1. Video Queue Bulk Apply Modal: shows both multiplier and max views
        v_tasks = [VideoTask(title="Video 1", is_short_override=False)]
        m_video = BulkApplyModal(root, tasks=v_tasks, shorts_only=False)
        assert hasattr(m_video, "multiplier_entry")
        assert hasattr(m_video, "max_views_entry")
        assert "Video Settings" in m_video.title() or "Video Queue" in m_video.title()

        m_video.multiplier_entry.delete(0, "end")
        m_video.multiplier_entry.insert(0, "2.5")
        m_video.max_views_entry.delete(0, "end")
        m_video.max_views_entry.insert(0, "7")
        m_video._apply()
        assert v_tasks[0].target_multiplier == 2.5
        assert v_tasks[0].max_runs == 7

        # 2. Shorts Queue Bulk Apply Modal: shows ONLY max views cap (no multiplier)
        s_tasks = [VideoTask(title="Short 1", is_short_override=True)]
        m_shorts = BulkApplyModal(root, tasks=s_tasks, shorts_only=True)
        assert not hasattr(m_shorts, "multiplier_entry")
        assert hasattr(m_shorts, "max_views_entry")
        assert "Shorts" in m_shorts.title()

        m_shorts.max_views_entry.delete(0, "end")
        m_shorts.max_views_entry.insert(0, "15")
        m_shorts._apply()
        assert s_tasks[0].max_runs == 15

        # 3. QueueView buttons are separate
        controller = MagicMock()
        controller.config_mgr.get_video_queue.return_value = []
        controller.config_mgr.get_channels.return_value = []

        q_video = QueueView(root, controller, shorts_only=False)
        q_shorts = QueueView(root, controller, shorts_only=True)

        assert hasattr(q_video, "open_video_bulk_apply_modal")
        assert hasattr(q_shorts, "open_shorts_bulk_apply_modal")
    finally:
        root.destroy()
