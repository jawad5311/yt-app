import io
import json
import socket
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from src.utils.channel_fetcher import validate_youtube_api_key


class TestValidateYoutubeApiKey:
    """Test the standalone YouTube Data API v3 key validator function."""

    def test_empty_api_key(self):
        ok, msg = validate_youtube_api_key("")
        assert ok is False
        assert "cannot be empty" in msg.lower()

        ok, msg = validate_youtube_api_key("   ")
        assert ok is False
        assert "cannot be empty" in msg.lower()

    def test_valid_api_key_http_200(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "kind": "youtube#videoListResponse",
            "items": [{"id": "dQw4w9WgXcQ"}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, msg = validate_youtube_api_key("AIzaSyValidDummyKey123456789")
            assert ok is True
            assert "valid and working" in msg

    def test_invalid_api_key_http_400(self):
        err_body = json.dumps({
            "error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "errors": [{"message": "API key not valid. Please pass a valid API key.", "reason": "badRequest"}]
            }
        }).encode("utf-8")

        mock_err = urllib.error.HTTPError(
            url="https://www.googleapis.com/youtube/v3/videos",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(err_body)
        )

        with patch("urllib.request.urlopen", side_effect=mock_err):
            ok, msg = validate_youtube_api_key("AIzaSyInvalidKey")
            assert ok is False
            assert "Invalid API key" in msg

    def test_api_not_enabled_http_403(self):
        err_body = json.dumps({
            "error": {
                "code": 403,
                "message": "YouTube Data API v3 has not been used in project 1234 before or it is disabled.",
                "errors": [{"reason": "accessNotConfigured"}]
            }
        }).encode("utf-8")

        mock_err = urllib.error.HTTPError(
            url="https://www.googleapis.com/youtube/v3/videos",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(err_body)
        )

        with patch("urllib.request.urlopen", side_effect=mock_err):
            ok, msg = validate_youtube_api_key("AIzaSyDisabledKey")
            assert ok is False
            assert "YouTube Data API v3 is not enabled" in msg

    def test_quota_exceeded_http_403(self):
        err_body = json.dumps({
            "error": {
                "code": 403,
                "message": "The request cannot be completed because you have exceeded your quota.",
                "errors": [{"reason": "quotaExceeded"}]
            }
        }).encode("utf-8")

        mock_err = urllib.error.HTTPError(
            url="https://www.googleapis.com/youtube/v3/videos",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(err_body)
        )

        with patch("urllib.request.urlopen", side_effect=mock_err):
            ok, msg = validate_youtube_api_key("AIzaSyQuotaExceededKey")
            assert ok is False
            assert "quota limit exceeded" in msg.lower()

    def test_network_connection_error(self):
        mock_err = urllib.error.URLError(reason="Name or service not known")
        with patch("urllib.request.urlopen", side_effect=mock_err):
            ok, msg = validate_youtube_api_key("AIzaSyAnyKey")
            assert ok is False
            assert "Network connection failed" in msg

    def test_timeout_error(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
            ok, msg = validate_youtube_api_key("AIzaSyAnyKey")
            assert ok is False
            assert "timed out" in msg.lower()


class DummySettingsView:
    """Mock harness for SettingsView to test YouTube API Test button logic."""
    def __init__(self, key=""):
        self.youtube_api_key_entry = MagicMock()
        self.youtube_api_key_entry.get.return_value = key
        self.btn_test_api = MagicMock()
        self.btn_test_api.cget.return_value = "Test"
        self.btn_test_api.winfo_exists.return_value = True

        self._bubble_notification_calls = []
        self._scheduled_callbacks = []
        self._api_test_reset_timer = None

    def _show_bubble_notification(self, message: str, success: bool = True, duration_ms: int = 3000):
        self._bubble_notification_calls.append((message, success, duration_ms))

    def after(self, ms, callback, *args):
        job_id = f"timer_{len(self._scheduled_callbacks)}"
        self._scheduled_callbacks.append((job_id, ms, callback, args))
        return job_id

    def after_cancel(self, job_id):
        self._scheduled_callbacks = [c for c in self._scheduled_callbacks if c[0] != job_id]


class TestSettingsViewApiKeyButton:
    """Test button behavior, async validation, and UI notifications."""

    def test_empty_api_key_shows_warning_and_does_not_call_network(self):
        from src.ui.views.settings_view import SettingsView

        view = DummySettingsView(key="")
        view._on_api_key_modified = SettingsView._on_api_key_modified.__get__(view)
        view.test_youtube_api_key = SettingsView.test_youtube_api_key.__get__(view)
        view._on_api_test_completed = SettingsView._on_api_test_completed.__get__(view)

        with patch("src.ui.views.settings_view.validate_youtube_api_key") as mock_val:
            view.test_youtube_api_key()

            mock_val.assert_not_called()
            view.btn_test_api.configure.assert_called_with(text="⚠️ Enter Key", style="Warning.TButton")
            assert len(view._bubble_notification_calls) == 1
            msg, success, _ = view._bubble_notification_calls[0]
            assert "Please enter a YouTube API key first" in msg
            assert success is False
            assert view._api_test_reset_timer is not None

    def test_valid_api_key_updates_button_to_valid_and_shows_success_bubble(self):
        from src.ui.views.settings_view import SettingsView

        view = DummySettingsView(key="AIzaSyValidKey123")
        view._on_api_key_modified = SettingsView._on_api_key_modified.__get__(view)
        view.test_youtube_api_key = SettingsView.test_youtube_api_key.__get__(view)
        view._on_api_test_completed = SettingsView._on_api_test_completed.__get__(view)

        with patch("src.ui.views.settings_view.validate_youtube_api_key", return_value=(True, "API key is valid and working.")), \
             patch("threading.Thread") as mock_thread:

            view.test_youtube_api_key()

            # Button set to testing state
            view.btn_test_api.configure.assert_called_with(
                text="Testing...", style="MutedAction.TButton", state="disabled"
            )
            assert mock_thread.called

            # Simulate completion callback
            view._on_api_test_completed(True, "API key is valid and working.")

            view.btn_test_api.configure.assert_called_with(
                text="✓ Valid", style="Success.TButton", state="normal"
            )
            assert len(view._bubble_notification_calls) == 1
            msg, success, _ = view._bubble_notification_calls[0]
            assert success is True
            assert "is valid" in msg
            assert view._api_test_reset_timer is not None

    def test_invalid_api_key_updates_button_to_invalid_and_shows_error_bubble(self):
        from src.ui.views.settings_view import SettingsView

        view = DummySettingsView(key="AIzaSyBadKey")
        view._on_api_key_modified = SettingsView._on_api_key_modified.__get__(view)
        view.test_youtube_api_key = SettingsView.test_youtube_api_key.__get__(view)
        view._on_api_test_completed = SettingsView._on_api_test_completed.__get__(view)

        view._on_api_test_completed(False, "Invalid API key. Please verify the key in Google Cloud Console.")

        view.btn_test_api.configure.assert_called_with(
            text="✕ Invalid", style="Danger.TButton", state="normal"
        )
        assert len(view._bubble_notification_calls) == 1
        msg, success, _ = view._bubble_notification_calls[0]
        assert success is False
        assert "Invalid API key" in msg

    def test_key_modified_event_resets_button_to_default(self):
        from src.ui.views.settings_view import SettingsView

        view = DummySettingsView(key="edited_key")
        view.btn_test_api.cget.return_value = "✓ Valid"
        view._on_api_key_modified = SettingsView._on_api_key_modified.__get__(view)

        view._on_api_key_modified()

        view.btn_test_api.configure.assert_called_with(
            text="Test", style="Primary.TButton", state="normal"
        )
