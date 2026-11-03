from unittest.mock import MagicMock, patch
import queue
import pytest
import customtkinter as ctk

from src.ui.app import YTBotApp
from src.ui.views.timeout_confirm_modal import FullVideoTimeoutConfirmModal
from src.config.manager import ConfigManager


def test_timeout_modal_confirm(ctk_app):
    """Test that clicking Confirm sets confirmed to True and closes modal."""
    modal = FullVideoTimeoutConfirmModal(ctk_app, timeout_minutes=5)
    assert modal.timeout_minutes == 5
    assert modal.confirmed is False

    # Simulate confirm
    modal._on_confirm()
    assert modal.confirmed is True


def test_timeout_modal_cancel(ctk_app):
    """Test that clicking Cancel sets confirmed to False and closes modal."""
    modal = FullVideoTimeoutConfirmModal(ctk_app, timeout_minutes=2)
    assert modal.timeout_minutes == 2

    # Simulate cancel
    modal._on_cancel()
    assert modal.confirmed is False


def test_app_timeout_check_zero_minutes(ctk_app, tmp_path):
    """When FullVideoTimeoutMinutes is 0, no confirmation popup is displayed."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 0)

    app = ctk_app
    app.config_mgr = mgr
    app.log_queue = queue.Queue()

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal") as mock_modal_cls:
        result = YTBotApp._confirm_full_video_timeout_if_needed(app)
        assert result is True
        mock_modal_cls.assert_not_called()


def test_app_timeout_check_non_zero_user_confirms(ctk_app, tmp_path):
    """When FullVideoTimeoutMinutes is non-zero (e.g. 2), modal appears and user confirms."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 2)

    app = ctk_app
    app.config_mgr = mgr
    app.log_queue = queue.Queue()

    mock_modal_instance = MagicMock()
    mock_modal_instance.show.return_value = True

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal", return_value=mock_modal_instance) as mock_modal_cls:
        result = YTBotApp._confirm_full_video_timeout_if_needed(app)
        assert result is True
        mock_modal_cls.assert_called_once_with(app, timeout_minutes=2)
        mock_modal_instance.show.assert_called_once()


def test_app_timeout_check_non_zero_user_cancels(ctk_app, tmp_path):
    """When FullVideoTimeoutMinutes is non-zero (e.g. 10), modal appears and user cancels."""
    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("FullVideoTimeoutMinutes", 10)

    app = ctk_app
    app.config_mgr = mgr
    app.log_queue = queue.Queue()

    mock_modal_instance = MagicMock()
    mock_modal_instance.show.return_value = False

    with patch("src.ui.views.timeout_confirm_modal.FullVideoTimeoutConfirmModal", return_value=mock_modal_instance) as mock_modal_cls:
        result = YTBotApp._confirm_full_video_timeout_if_needed(app)
        assert result is False
        mock_modal_cls.assert_called_once_with(app, timeout_minutes=10)
        mock_modal_instance.show.assert_called_once()
