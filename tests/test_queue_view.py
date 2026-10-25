import pytest
from unittest.mock import MagicMock, patch
from src.models.video_task import VideoTask
from src.ui.views.queue_view import QueueView


def test_queue_card_inline_entries_behavior():
    view = QueueView.__new__(QueueView)
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.show_popup_notification = MagicMock()
    view._stats = MagicMock(return_value=None)

    task = VideoTask(
        title="Test Video",
        url="https://youtube.com/watch?v=123",
        target_multiplier=3.0,
        max_runs=5,
    )

    with patch("customtkinter.CTkCheckBox"), \
         patch("customtkinter.CTkLabel"), \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkProgressBar"), \
         patch("customtkinter.CTkFont"), \
         patch("customtkinter.CTkEntry") as mock_entry_cls:

        mock_target_entry = MagicMock()
        mock_views_entry = MagicMock()
        mock_entry_cls.side_effect = [mock_target_entry, mock_views_entry]

        mock_parent = MagicMock()
        view._build_task_card(mock_parent, index=0, position=1, task=task, channel_enabled=True)

        # Retrieve bound callbacks
        target_binds = {call[0][0]: call[0][1] for call in mock_target_entry.bind.call_args_list}
        views_binds = {call[0][0]: call[0][1] for call in mock_views_entry.bind.call_args_list}

        assert "<FocusOut>" in target_binds
        assert "<Return>" in target_binds
        assert "<FocusOut>" in views_binds
        assert "<Return>" in views_binds

        # 1. FocusOut on target_entry: user cleared it, but clicks away -> reverts, does not save
        mock_target_entry.get.return_value = ""
        target_binds["<FocusOut>"]()
        assert task.target_multiplier == 3.0
        assert view.save_queue.call_count == 0
        assert view.show_popup_notification.call_count == 0
        mock_target_entry.insert.assert_called_with(0, "3")

        # 2. Return on target_entry with new value "2.5" -> updates, saves, shows popup
        mock_target_entry.get.return_value = "2.5"
        target_binds["<Return>"]()
        assert task.target_multiplier == 2.5
        assert view.save_queue.call_count == 1
        assert view.show_popup_notification.call_count == 1
        assert "2.5x" in view.show_popup_notification.call_args[0][1]

        # 3. Return on target_entry with blank "" -> updates to 0.0, saves, shows popup
        mock_target_entry.get.return_value = ""
        target_binds["<Return>"]()
        assert task.target_multiplier == 0.0
        assert view.save_queue.call_count == 2
        assert "0x" in view.show_popup_notification.call_args[0][1]

        # 4. FocusOut on views_entry: user typed "99", but clicks away -> reverts, does not save
        mock_views_entry.get.return_value = "99"
        views_binds["<FocusOut>"]()
        assert task.max_runs == 5
        assert view.save_queue.call_count == 2
        mock_views_entry.insert.assert_called_with(0, "5")

        # 5. Return on views_entry with new value "10" -> updates, saves, shows popup
        mock_views_entry.get.return_value = "10"
        views_binds["<Return>"]()
        assert task.max_runs == 10
        assert view.save_queue.call_count == 3
        assert "10" in view.show_popup_notification.call_args[0][1]

        # 6. Return on views_entry with blank "" -> updates to 0, saves, shows popup
        mock_views_entry.get.return_value = ""
        views_binds["<Return>"]()
        assert task.max_runs == 0
        assert view.save_queue.call_count == 4
        assert "0 (unlimited)" in view.show_popup_notification.call_args[0][1]
