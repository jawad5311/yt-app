"""Tests for the logger: secret redaction and queue emission."""

import queue

from src.utils.logger import emit, redact_secrets


def test_redacts_key_value_pairs():
    assert redact_secrets("using key=ABCD1234efgh") == "using [REDACTED_API_KEY]"


def test_redacts_openai_style_keys():
    assert redact_secrets("token sk-abc123DEF_456") == "token [REDACTED_API_KEY]"


def test_redacts_google_api_keys():
    masked = redact_secrets("google key AIzaSyABCDEFG1234567890")
    assert "AIzaSyABCDEFG1234567890" not in masked
    assert "[REDACTED_API_KEY]" in masked


def test_leaves_normal_text_alone():
    message = "Session completed for user@example.com: 3/3 tasks watched."
    assert redact_secrets(message) == message


def test_emit_pushes_redacted_message_to_queue():
    log_queue: queue.Queue = queue.Queue()
    emit(log_queue, "starting with key=TOPSECRETVALUE")
    assert log_queue.get_nowait() == "starting with [REDACTED_API_KEY]"


def test_emit_without_queue_does_not_raise():
    emit(None, "no queue attached")
