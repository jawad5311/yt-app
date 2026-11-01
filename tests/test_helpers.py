import sys
from unittest.mock import patch

from src.utils.helpers import (
    clean_youtube_url,
    extract_video_id,
    get_project_root,
    sanitize_text,
)


def test_get_project_root_unfrozen():
    root = get_project_root()
    assert (root / "main.py").exists()
    assert (root / "src").is_dir()


def test_get_project_root_frozen_standalone(tmp_path):
    fake_exe = tmp_path / "app.exe"
    with patch.object(sys, "frozen", True, create=True), patch.object(
        sys, "executable", str(fake_exe)
    ):
        root = get_project_root()
        assert root == tmp_path


def test_get_project_root_frozen_in_repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "main.py").write_text("# main", encoding="utf-8")
    (repo_root / "settings.json").write_text("{}", encoding="utf-8")
    dist_dir = repo_root / "dist" / "YTAutomationTool"
    dist_dir.mkdir(parents=True)
    fake_exe = dist_dir / "YTAutomationTool.exe"

    with patch.object(sys, "frozen", True, create=True), patch.object(
        sys, "executable", str(fake_exe)
    ):
        root = get_project_root()
        assert root == repo_root


def test_extract_video_id():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("not-a-valid-video-url") is None
    assert extract_video_id("") is None


def test_clean_youtube_url():
    assert clean_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert clean_youtube_url("https://youtu.be/dQw4w9WgXcQ?si=abcdef") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert clean_youtube_url("") == ""


def test_sanitize_text():
    assert sanitize_text("Hello World!") == "Hello World!"
    assert sanitize_text("Emoji \U0001F600 test") == "Emoji  test"
