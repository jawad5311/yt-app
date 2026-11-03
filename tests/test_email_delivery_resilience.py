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


    def test_clean_error_msg_strips_stacktrace(self):
        from src.automation.email_reporter import _clean_error_msg
        from selenium.common.exceptions import ElementNotInteractableException

        raw_stack = (
            "Message: element not interactable\n"
            "  (Session info: chrome=156.0.8078.48)\n"
            "Stacktrace:\n"
            "\tchromedriver!GetHandleVerifier [0x7ff698183ee5+4165]\n"
            "\tchromedriver!(No symbol) [0x7ff698084f30]\n"
        )
        exc = ElementNotInteractableException(raw_stack)
        cleaned = _clean_error_msg(exc)
        assert cleaned == "Message: element not interactable"
        assert "chromedriver" not in cleaned
        assert "\n" not in cleaned

    @patch("time.sleep", return_value=None)
    def test_thread_reply_js_click_fallback_on_element_not_interactable(self, mock_sleep):
        from selenium.common.exceptions import ElementNotInteractableException

        mock_driver = MagicMock()
        mock_driver.current_url = "https://mail.google.com/mail/u/0/#sent/KtbxLwgVRHqhTPzrzfqMxCKsCZTClpSZDV"

        mock_reply_btn = MagicMock()
        mock_reply_btn.is_displayed.return_value = True
        mock_reply_btn.click.side_effect = ElementNotInteractableException("element not interactable")

        mock_body = MagicMock()
        mock_body.is_displayed.return_value = True

        mock_send_btn = MagicMock()
        mock_send_btn.is_displayed.return_value = True

        mock_toast = MagicMock()
        mock_toast.is_displayed.return_value = True

        def find_elements_mock(by, selector):
            if "Reply" in selector:
                return [mock_reply_btn]
            if "role='textbox'" in selector:
                return [mock_body]
            if "@data-tooltip" in selector and "Send" in selector:
                return [mock_send_btn]
            if "sent" in selector.lower():
                return [mock_toast]
            return []

        mock_driver.find_elements.side_effect = find_elements_mock

        log_q = queue.Queue()
        orig_thread = EmailReporter.last_thread_url
        try:
            ok = EmailReporter._deliver_via_gmail_thread_reply(
                driver=mock_driver,
                plain_text="Reply body",
                thread_url="https://mail.google.com/mail/u/0/#sent/KtbxLwgVRHqhTPzrzfqMxCKsCZTClpSZDV",
                subject="Test",
                recipient_email="test@example.com",
                log_queue=log_q,
            )

            assert ok is True
            mock_driver.execute_script.assert_any_call("arguments[0].click();", mock_reply_btn)
        finally:
            EmailReporter.last_thread_url = orig_thread
