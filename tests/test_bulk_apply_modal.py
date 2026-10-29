from unittest.mock import MagicMock, patch
import pytest

from src.models.video_task import VideoTask
from src.ui.views.bulk_apply_modal import BulkApplyModal
from src.ui.views.queue_view import QueueView


def test_bulk_apply_modal_apply():
    """Applying changes in BulkApplyModal should update target_multiplier and max_runs on all tasks across all channels."""
    modal = BulkApplyModal.__new__(BulkApplyModal)

    mock_app = MagicMock()
    modal.app_controller = mock_app

    t1 = VideoTask(title="v1", channel="ChannelOne", max_runs=1, target_multiplier=1.0)
    t2 = VideoTask(title="v2", channel="ChannelTwo", max_runs=2, target_multiplier=2.0)
    t3 = VideoTask(title="v3", channel="", max_runs=0, target_multiplier=0.0)
    modal.tasks = [t1, t2, t3]

    modal.parent_view = MagicMock()
    modal.on_applied = MagicMock()
    modal.destroy = MagicMock()

    mock_run_entry = MagicMock()
    mock_run_entry.get.return_value = "8"
    modal.max_views_entry = mock_run_entry
    modal.run_entry = mock_run_entry

    mock_mult_entry = MagicMock()
    mock_mult_entry.get.return_value = "4.5"
    modal.multiplier_entry = mock_mult_entry

    mock_err = MagicMock()
    modal.err_lbl = mock_err

    modal._apply()

    assert t1.max_runs == 8
    assert t2.max_runs == 8
    assert t3.max_runs == 8

    assert t1.target_multiplier == 4.5
    assert t2.target_multiplier == 4.5
    assert t3.target_multiplier == 4.5

    modal.parent_view.save_queue.assert_called_once()
    modal.parent_view.refresh_list.assert_called_once()
    modal.on_applied.assert_called_once()
    modal.destroy.assert_called_once()


def test_bulk_apply_modal_validation():
    """BulkApplyModal should validate target_multiplier and max_views entries."""
    modal = BulkApplyModal.__new__(BulkApplyModal)
    modal.app_controller = MagicMock()
    modal.parent_view = MagicMock()
    modal.destroy = MagicMock()
    modal.tasks = [VideoTask(title="v1")]

    mock_err = MagicMock()
    modal.err_lbl = mock_err

    # Invalid multiplier: negative
    modal.multiplier_entry = MagicMock()
    modal.multiplier_entry.get.return_value = "-1.5"
    modal._apply()
    mock_err.configure.assert_called_with(text="Target multiplier cannot be negative.")
    modal.destroy.assert_not_called()

    # Invalid multiplier: non-numeric string
    modal.multiplier_entry.get.return_value = "abc"
    modal._apply()
    mock_err.configure.assert_called_with(text="Please enter a valid number for target multiplier.")
    modal.destroy.assert_not_called()

    # Valid multiplier, invalid views: negative
    modal.multiplier_entry.get.return_value = "3.0"
    modal.max_views_entry = MagicMock()
    modal.max_views_entry.get.return_value = "-5"
    modal._apply()
    mock_err.configure.assert_called_with(text="Max views cap cannot be negative.")
    modal.destroy.assert_not_called()

    # Valid multiplier, invalid views: non-integer string
    modal.max_views_entry.get.return_value = "xyz"
    modal._apply()
    mock_err.configure.assert_called_with(text="Please enter a valid whole number for max views.")
    modal.destroy.assert_not_called()


def test_queue_view_open_bulk_apply_modal():
    """QueueView.open_bulk_apply_modal opens BulkApplyModal when tasks exist, or shows messagebox if empty."""
    view = QueueView.__new__(QueueView)
    view.shorts_only = False
    view.video_tasks = []
    view.app_controller = MagicMock()
    view.refresh_list = MagicMock()

    with patch("tkinter.messagebox.showinfo") as mock_info:
        view.open_bulk_apply_modal()
        mock_info.assert_called_once()
        assert "Empty" in mock_info.call_args[0][0]

    # Now with tasks in queue
    t1 = VideoTask(title="v1", url="https://youtube.com/watch?v=123")
    t2 = VideoTask(title="s1", url="https://youtube.com/shorts/456")
    view.video_tasks = [t1, t2]

    with patch("src.ui.views.bulk_apply_modal.BulkApplyModal") as mock_modal_cls:
        view.open_bulk_apply_modal()
        mock_modal_cls.assert_called_once()
        # Should only pass regular video tasks when shorts_only is False
        passed_tasks = mock_modal_cls.call_args[1].get("tasks")
        assert passed_tasks == [t1]
