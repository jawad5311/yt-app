import queue
from unittest.mock import MagicMock, patch
import pytest

from src.automation.email_reporter import EmailReporter, SessionReport


class TestGmailComposeResilience:
    @patch("time.sleep", return_value=None)
    def test_deliver_auto_accepts_confirmation_dialog_and_detects_toast(self, mock_sleep):
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True
        mock_body.text = ""

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        mock_dialog_btn = MagicMock()
        mock_dialog_btn.is_displayed.return_value = True

        mock_toast = MagicMock()
        mock_toast.is_displayed.return_value = True

        def find_elements_mock(by, selector):
            if "@role='dialog'" in selector:
                return [mock_dialog_btn]
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock
        mock_driver.find_element.return_value = MagicMock(get_attribute=lambda attr: "")

        log_q = queue.Queue()
        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Test Subject",
            plain_text="Test Body",
            recipient_email="user@example.com",
            log_queue=log_q,
        )

        assert ok is True
        mock_send_btn.click.assert_called_once()
        mock_dialog_btn.click.assert_called_once()
        # Verify document.execCommand was called to insert text
        mock_driver.execute_script.assert_called()

    @patch("time.sleep", return_value=None)
    @patch("time.time")
    def test_deliver_unconfirmed_fails_gracefully(self, mock_time, mock_sleep):
        t = 100.0

        def advance_time():
            nonlocal t
            t += 6.0
            return t

        mock_time.side_effect = advance_time

        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#inbox"

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True
        mock_body.text = "Existing"

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        def find_elements_mock(by, selector):
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        log_q = queue.Queue()
        ok = EmailReporter._deliver_via_gmail_compose(
            driver=mock_driver,
            subject="Test Subject",
            plain_text="Test Body",
            recipient_email="user@example.com",
            log_queue=log_q,
        )

        assert ok is False
        messages = []
        while not log_q.empty():
            messages.append(log_q.get())
        assert any("unconfirmed" in str(m).lower() for m in messages)
