from unittest.mock import MagicMock, patch

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


def test_bulk_apply_modal_switch_defaults_and_toggles(ctk_app):
    """Test that BulkApplyModal in Video Queue defaults both switches to ON and toggling changes state."""
    t1 = VideoTask(title="v1", channel="Chan1", max_runs=5, target_multiplier=2.0)
    t2 = VideoTask(title="v2", channel="Chan2", max_runs=5, target_multiplier=2.0)

    mock_app = MagicMock()
    parent = MagicMock()
    parent.shorts_only = False

    modal = BulkApplyModal(
        parent=ctk_app,
        tasks=[t1, t2],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal.parent_view = parent

    # Both switches must exist in Video Queue mode
    assert hasattr(modal, "multiplier_switch")
    assert hasattr(modal, "views_switch")

    # By default, both switches must be ON
    assert modal.multiplier_switch.get() == 1
    assert modal.views_switch.get() == 1

    # Both text entries must be editable (state='normal')
    assert str(modal.multiplier_entry.cget("state")) == "normal"
    assert str(modal.max_views_entry.cget("state")) == "normal"

    # Toggle multiplier switch OFF
    modal.multiplier_switch.deselect()
    modal._on_multiplier_toggled()
    assert str(modal.multiplier_entry.cget("state")) == "disabled"
    assert modal.multiplier_entry.get() == "2"

    # Toggle views switch OFF
    modal.views_switch.deselect()
    modal._on_views_toggled()
    assert str(modal.max_views_entry.cget("state")) == "disabled"
    assert modal.max_views_entry.get() == "5"

    # Toggle multiplier switch back ON
    modal.multiplier_switch.select()
    modal._on_multiplier_toggled()
    assert str(modal.multiplier_entry.cget("state")) == "normal"

    # Toggle views switch back ON
    modal.views_switch.select()
    modal._on_views_toggled()
    assert str(modal.max_views_entry.cget("state")) == "normal"


def test_bulk_apply_modal_selective_updates(ctk_app):
    """Test that only checked/ON switches update values, while unchecked/OFF remain untouched."""
    t1 = VideoTask(title="v1", channel="Chan1", max_runs=5, target_multiplier=2.0)
    t2 = VideoTask(title="v2", channel="Chan2", max_runs=5, target_multiplier=2.0)

    mock_app = MagicMock()
    parent = MagicMock()
    parent.shorts_only = False

    # Scenario A: ONLY max views switch is ON, multiplier switch is OFF
    modal = BulkApplyModal(
        parent=ctk_app,
        tasks=[t1, t2],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal.parent_view = parent

    modal.multiplier_switch.deselect()
    modal._on_multiplier_toggled()

    modal.max_views_entry.delete(0, "end")
    modal.max_views_entry.insert(0, "15")

    modal._apply()

    # Only max_runs updated; multiplier untouched!
    assert t1.max_runs == 15
    assert t2.max_runs == 15
    assert t1.target_multiplier == 2.0
    assert t2.target_multiplier == 2.0

    # Scenario B: ONLY multiplier switch is ON, max views switch is OFF
    modal2 = BulkApplyModal(
        parent=ctk_app,
        tasks=[t1, t2],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal2.parent_view = parent

    modal2.views_switch.deselect()
    modal2._on_views_toggled()

    modal2.multiplier_entry.delete(0, "end")
    modal2.multiplier_entry.insert(0, "5.5")

    modal2._apply()

    # Only multiplier updated; max_runs untouched!
    assert t1.target_multiplier == 5.5
    assert t2.target_multiplier == 5.5
    assert t1.max_runs == 15
    assert t2.max_runs == 15

    # Scenario C: BOTH switches are ON
    modal3 = BulkApplyModal(
        parent=ctk_app,
        tasks=[t1, t2],
        app_controller=mock_app,
        shorts_only=False,
    )
    modal3.parent_view = parent

    modal3.multiplier_entry.delete(0, "end")
    modal3.multiplier_entry.insert(0, "1.5")
    modal3.max_views_entry.delete(0, "end")
    modal3.max_views_entry.insert(0, "25")

    modal3._apply()

    # Both updated!
    assert t1.target_multiplier == 1.5
    assert t2.target_multiplier == 1.5
    assert t1.max_runs == 25
    assert t2.max_runs == 25


def test_bulk_apply_modal_shorts_only_mode(ctk_app):
    """Test that Shorts Queue mode presents only views_switch and respects toggle."""
    s1 = VideoTask(title="s1", channel="Chan1", max_runs=5, is_short_override=True)
    s2 = VideoTask(title="s2", channel="Chan2", max_runs=5, is_short_override=True)

    mock_app = MagicMock()
    parent = MagicMock()
    parent.shorts_only = True

    modal = BulkApplyModal(
        parent=ctk_app,
        tasks=[s1, s2],
        app_controller=mock_app,
        shorts_only=True,
    )
    modal.parent_view = parent

    # Verify multiplier_switch does NOT exist in shorts_only mode
    assert not hasattr(modal, "multiplier_switch")

    # views_switch is present and ON by default
    assert hasattr(modal, "views_switch")
    assert modal.views_switch.get() == 1
    assert str(modal.max_views_entry.cget("state")) == "normal"

    # Turn views switch OFF -> entry disabled
    modal.views_switch.deselect()
    modal._on_views_toggled()
    assert str(modal.max_views_entry.cget("state")) == "disabled"

    modal._apply()
    # Not updated since switch was OFF
    assert s1.max_runs == 5
    assert s2.max_runs == 5
