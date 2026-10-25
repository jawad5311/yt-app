from unittest.mock import MagicMock, patch
import pytest

from src.ui.views.logs_view import LogsView


class TestLogsView:
    def test_logs_view_tag_config_and_append(self):
        controller = MagicMock()
        with patch("customtkinter.CTkFrame.__init__", return_value=None), \
             patch("customtkinter.CTkLabel.__init__", return_value=None), \
             patch("customtkinter.CTkButton.__init__", return_value=None):

            view = LogsView.__new__(LogsView)
            view.app_controller = controller

            mock_textbox = MagicMock()
            view.log_textbox = mock_textbox

            # Test appending formatted video block
            sample_entry = (
                "Channel: Test Channel\n"
                "URL: https://www.youtube.com/watch?v=sample123\n"
                "Title: Sample Video Title"
            )
            view.append_log(sample_entry)

            mock_textbox.configure.assert_any_call(state="normal")
            mock_textbox.configure.assert_any_call(state="disabled")

            inserted = [call[0] for call in mock_textbox.insert.call_args_list]
            # Check Channel label & value
            assert any("Channel: " in arg for arg in inserted)
            assert any("val_channel" in arg for arg in inserted)

            # Check URL label & value
            assert any("URL: " in arg for arg in inserted)
            assert any("val_url" in arg for arg in inserted)

            # Check Title label & value
            assert any("Title: " in arg for arg in inserted)
            assert any("val_title" in arg for arg in inserted)

    def test_logs_view_skip_and_banner_tags(self):
        controller = MagicMock()
        view = LogsView.__new__(LogsView)
        view.app_controller = controller

        mock_textbox = MagicMock()
        view.log_textbox = mock_textbox

        view.append_log("=== Starting Watch Session ===")
        inserted = [call[0] for call in mock_textbox.insert.call_args_list]
        assert any("session_header" in arg for arg in inserted)

        mock_textbox.insert.reset_mock()
        view.append_log("Skipping video 'Done Video' from channel 'Test Channel': already reached run limit.")
        inserted = [call[0] for call in mock_textbox.insert.call_args_list]
        assert any("skip" in arg for arg in inserted)

    def test_logs_view_auto_scroll_behavior_on_append(self):
        """Verify auto-scroll only happens when at the bottom, and does NOT auto-scroll when scrolled up."""
        controller = MagicMock()
        view = LogsView.__new__(LogsView)
        view.app_controller = controller

        mock_textbox = MagicMock()
        view.log_textbox = mock_textbox

        # Case 1: At the bottom -> auto-scrolling SHOULD occur
        mock_textbox.yview.return_value = (0.7, 1.0)
        view.is_scroll_btn_visible = False
        view.append_log("Bottom log message")
        mock_textbox.see.assert_called_with("end")

        # Case 2: Scrolled up checking previous logs -> auto-scrolling MUST NOT occur
        mock_textbox.see.reset_mock()
        mock_textbox.yview.return_value = (0.1, 0.45)
        view.is_scroll_btn_visible = True
        view.append_log("Log while reading previous entries")
        mock_textbox.see.assert_not_called()

    def test_logs_view_scroll_button_visibility_transitions(self):
        """Verify scroll button appears when scrolled up and hides when at bottom."""
        controller = MagicMock()
        view = LogsView.__new__(LogsView)
        view.app_controller = controller
        view.is_scroll_btn_visible = False
        view._anim_job = None

        mock_btn = MagicMock()
        view.scroll_btn = mock_btn

        # When at bottom (first=0.8, last=1.0)
        view._on_text_scroll("0.8", "1.0")
        assert view.is_scroll_btn_visible is False

        # When scrolled up towards top (first=0.0, last=0.25)
        view._on_text_scroll("0.0", "0.25")
        assert view.is_scroll_btn_visible is True

        # When user reaches bottom again (last=0.99)
        view._on_text_scroll("0.7", "0.99")
        assert view.is_scroll_btn_visible is False

    def test_logs_view_scroll_to_bottom(self):
        """Verify scroll_to_bottom triggers textbox see('end') and hides button."""
        controller = MagicMock()
        view = LogsView.__new__(LogsView)
        view.app_controller = controller
        view.is_scroll_btn_visible = True
        view._anim_job = None
        view._scroll_job = None

        mock_textbox = MagicMock()
        mock_textbox.yview.return_value = (0.5, 1.0)
        view.log_textbox = mock_textbox

        mock_btn = MagicMock()
        view.scroll_btn = mock_btn

        view.scroll_to_bottom()
        mock_textbox.see.assert_called_with("end")
        assert view.is_scroll_btn_visible is False
