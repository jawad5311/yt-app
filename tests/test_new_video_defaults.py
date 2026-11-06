"""Tests for default 3x target multiplier and 5 max views when adding/importing new videos."""

from unittest.mock import MagicMock

from src.models.video_task import DEFAULT_MAX_RUNS, DEFAULT_SHORTS_MAX_RUNS, DEFAULT_TARGET_MULTIPLIER
from src.ui.views.channel_import_modal import ChannelImportModal
from src.ui.views.queue_view import QueueView
from src.utils.channel_fetcher import FetchedVideo


def test_channel_import_modal_defaults_to_3x_mult_and_5_views(ctk_app):
    imported_tasks = []
    def _on_import(tasks):
        imported_tasks.extend(tasks)

    fetched = [
        FetchedVideo(video_id="v1", url="https://youtube.com/watch?v=v1", title="Video 1", duration_seconds=600),
        FetchedVideo(video_id="s1", url="https://youtube.com/shorts/s1", title="Short 1", duration_seconds=45, is_short=True),
    ]

    modal = ChannelImportModal(
        ctk_app,
        channel_name="Test Channel",
        channel_url="https://youtube.com/@testchannel",
        fetched_videos=fetched,
        existing_video_ids=set(),
        on_import=_on_import,
    )

    modal._submit_import()

    assert len(imported_tasks) == 2
    v_task = next(t for t in imported_tasks if not t.is_short)
    s_task = next(t for t in imported_tasks if t.is_short)

    # Regular videos default to 3.0x multiplier and 5 max runs
    assert v_task.target_multiplier == DEFAULT_TARGET_MULTIPLIER == 3.0
    assert v_task.max_runs == DEFAULT_MAX_RUNS == 5

    # Shorts default to 0.0 multiplier (no multiplier) and 5 max views
    assert s_task.target_multiplier == 0.0
    assert s_task.max_runs == 5

    modal.destroy()


def test_queue_view_add_video_modal_defaults_to_3x_mult_and_5_views(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_setting.return_value = "Full Video"
    app.config_mgr.get_video_queue.return_value = []
    app.config_mgr.get_channels.return_value = []
    app.on_queue_changed = MagicMock()

    view = QueueView(app, app)
    view.open_video_modal()

    # Find the opened CTkToplevel modal
    import customtkinter as ctk
    modal = next((c for c in view.winfo_children() if isinstance(c, ctk.CTkToplevel)), None)
    assert modal is not None

    # Check entries inside modal
    entries = [c for c in modal.winfo_children() if isinstance(c, ctk.CTkEntry)]
    # Entries: url, title1, title2, title3, target_mult, max_runs
    mult_entry = next((e for e in entries if e.get() == "3"), None)
    runs_entry = next((e for e in entries if e.get() == "5"), None)

    assert mult_entry is not None, "Target multiplier entry did not default to 3"
    assert runs_entry is not None, "Max views entry did not default to 5"

    modal.destroy()
    view.destroy()
