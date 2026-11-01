"""Unit tests for loading YouTube API key from .env file and environment."""

import os
from unittest.mock import MagicMock, patch

from src.utils.channel_fetcher import get_configured_youtube_api_key, load_env_file


def test_env_file_loads_youtube_api_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("YOUTUBE_API_KEY=AIzaSyTestKeyFromEnv1234567\n", encoding="utf-8")

    with patch.dict(os.environ, {}, clear=True):
        loaded = load_env_file(env_file)
        assert loaded.get("YOUTUBE_API_KEY") == "AIzaSyTestKeyFromEnv1234567"

    with patch.dict(os.environ, {}, clear=True), patch("src.utils.helpers.get_project_root", return_value=tmp_path):
        assert get_configured_youtube_api_key() == "AIzaSyTestKeyFromEnv1234567"


def test_env_file_handles_comments_empty_lines_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_content = (
        "# This is a comment\n"
        "\n"
        'YOUTUBE_API_KEY="AIzaSyQuotedKey123"\n'
        "ANOTHER_VAR='some_value'\n"
        "# Another comment\n"
        "SPACED_VAR = unquoted_val \n"
    )
    env_file.write_text(env_content, encoding="utf-8")

    loaded = load_env_file(env_file, populate_environ=False)
    assert loaded.get("YOUTUBE_API_KEY") == "AIzaSyQuotedKey123"
    assert loaded.get("ANOTHER_VAR") == "some_value"
    assert loaded.get("SPACED_VAR") == "unquoted_val"


def test_process_env_takes_precedence_over_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("YOUTUBE_API_KEY=AIzaSyFromFile\n", encoding="utf-8")

    with patch.dict(os.environ, {"YOUTUBE_API_KEY": "AIzaSyFromProcessEnv"}, clear=True):
        with patch("src.utils.helpers.get_project_root", return_value=tmp_path):
            assert get_configured_youtube_api_key() == "AIzaSyFromProcessEnv"


def test_settings_json_fallback_when_no_env(tmp_path):
    mock_mgr = MagicMock()
    mock_mgr.get_setting.side_effect = lambda k, default=None: "AIzaSyFromSettings" if k == "YouTubeApiKey" else default

    with patch.dict(os.environ, {}, clear=True), patch("src.utils.helpers.get_project_root", return_value=tmp_path):
        assert get_configured_youtube_api_key(config_mgr=mock_mgr) == "AIzaSyFromSettings"


def test_missing_env_file_returns_none(tmp_path):
    with patch.dict(os.environ, {}, clear=True), patch("src.utils.helpers.get_project_root", return_value=tmp_path):
        assert load_env_file(tmp_path / ".env") == {}
        assert get_configured_youtube_api_key() is None
