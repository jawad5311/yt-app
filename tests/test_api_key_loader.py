"""Unit tests for loading YouTube API key from pointer file."""

from pathlib import Path
from unittest.mock import patch
from src.utils.channel_fetcher import get_configured_youtube_api_key, load_api_key_from_pointer_file


def test_pointer_file_reads_external_key(tmp_path):
    # Create external key file
    key_file = tmp_path / "secret_key.txt"
    key_file.write_text("AIzaSyTestKeyFromExternalFile1234567\n", encoding="utf-8")

    # Create pointer file
    pointer_file = tmp_path / "api_key_path.txt"
    pointer_file.write_text(f"# My API path\n{str(key_file)}\n", encoding="utf-8")

    with patch("src.utils.helpers.get_project_root", return_value=tmp_path):
        key = load_api_key_from_pointer_file()
        assert key == "AIzaSyTestKeyFromExternalFile1234567"

        # Also verify get_configured_youtube_api_key returns it
        assert get_configured_youtube_api_key() == "AIzaSyTestKeyFromExternalFile1234567"


def test_pointer_file_missing_or_comment_only(tmp_path):
    pointer_file = tmp_path / "api_key_path.txt"
    pointer_file.write_text("# Only comments\n\n", encoding="utf-8")

    with patch("src.utils.helpers.get_project_root", return_value=tmp_path):
        assert load_api_key_from_pointer_file() is None


def test_pointer_file_target_does_not_exist(tmp_path):
    pointer_file = tmp_path / "api_key_path.txt"
    pointer_file.write_text(str(tmp_path / "non_existent_key.txt"), encoding="utf-8")

    with patch("src.utils.helpers.get_project_root", return_value=tmp_path):
        assert load_api_key_from_pointer_file() is None
