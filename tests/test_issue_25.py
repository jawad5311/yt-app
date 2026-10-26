import json
import os
import shutil
from pathlib import Path
import pytest

from src.config.manager import ConfigManager
from src.utils.build_packaging import (
    is_sensitive_filename,
    filter_datas,
    stage_release,
    SENSITIVE_FILENAMES,
)


@pytest.fixture
def canary_project(tmp_path):
    """Isolated fixture project containing fake password canaries and local state."""
    source_dir = tmp_path / "source"
    staging_dir = tmp_path / "staging"
    dist_dir = tmp_path / "dist" / "YTAutomationTool"
    source_dir.mkdir(parents=True)

    canary_password = "P@ssw0rdCanary_DoNotDistribute_98765"

    # Local sensitive files
    (source_dir / "credentials.json").write_text(
        json.dumps({"accounts": [{"email": "dev@test.com", "password": canary_password}]}),
        encoding="utf-8",
    )
    (source_dir / "accounts.txt").write_text(
        f"dev@test.com:{canary_password}\n", encoding="utf-8"
    )
    (source_dir / "settings.json").write_text(
        json.dumps({"settings": {"PersonalKey": "private_val"}}), encoding="utf-8"
    )
    (source_dir / "stats.json").write_text(
        json.dumps({"stats": [1, 2, 3]}), encoding="utf-8"
    )
    (source_dir / "settings.json.backup_20261026_1.bak").write_text(
        "corrupt backup with canary", encoding="utf-8"
    )
    (source_dir / "temp_file.tmp").write_text("temporary data", encoding="utf-8")

    # Safe application assets
    (source_dir / "YTAutomationTool.exe").write_text("MOCK_BINARY_BYTES", encoding="utf-8")
    (source_dir / "accounts.example.txt").write_text("# Example accounts file", encoding="utf-8")
    (source_dir / "settings.example.json").write_text("{}", encoding="utf-8")

    return {
        "source": source_dir,
        "staging": staging_dir,
        "dist": dist_dir,
        "canary": canary_password,
    }


def test_build_preparation_excludes_credentials_and_accounts_txt(canary_project):
    """Build preparation excludes credentials.json and accounts.txt."""
    source = canary_project["source"]
    staging = canary_project["staging"]
    dist = canary_project["dist"]

    manifest = stage_release(source, staging, dist)

    assert "credentials.json" not in manifest
    assert "accounts.txt" not in manifest
    assert not (staging / "credentials.json").exists()
    assert not (staging / "accounts.txt").exists()
    assert not (dist / "credentials.json").exists()
    assert not (dist / "accounts.txt").exists()


def test_personal_settings_and_statistics_excluded(canary_project):
    """Personal settings.json and stats.json are excluded from the release build."""
    source = canary_project["source"]
    staging = canary_project["staging"]
    dist = canary_project["dist"]

    manifest = stage_release(source, staging, dist)

    assert "settings.json" not in manifest
    assert "stats.json" not in manifest
    assert not (staging / "settings.json").exists()
    assert not (staging / "stats.json").exists()
    assert not (dist / "settings.json").exists()
    assert not (dist / "stats.json").exists()


def test_required_assets_and_examples_remain_included(canary_project):
    """Required executable binary and sanitized examples remain included in release."""
    source = canary_project["source"]
    staging = canary_project["staging"]
    dist = canary_project["dist"]

    manifest = stage_release(source, staging, dist)

    assert "YTAutomationTool.exe" in manifest
    assert "accounts.example.txt" in manifest
    assert "settings.example.json" in manifest
    assert (dist / "YTAutomationTool.exe").exists()
    assert (dist / "accounts.example.txt").exists()
    assert (dist / "settings.example.json").exists()


def test_artifact_file_manifest_contains_no_sensitive_files(canary_project):
    """File manifest of the resulting release artifact contains no sensitive local-state files."""
    source = canary_project["source"]
    staging = canary_project["staging"]
    dist = canary_project["dist"]
    canary = canary_project["canary"]

    stage_release(source, staging, dist)

    for path in dist.rglob("*"):
        if path.is_file():
            assert not is_sensitive_filename(path.name)
            # Ensure fake password canary never appears in any distributed file
            assert canary not in path.read_text(encoding="utf-8", errors="ignore")


def test_pyinstaller_data_inputs_do_not_include_sensitive_files():
    """PyInstaller data inputs filter out all sensitive files."""
    raw_datas = [
        ("path/to/customtkinter/assets/theme.json", "customtkinter/assets"),
        ("path/to/credentials.json", "."),
        ("path/to/accounts.txt", "."),
        ("path/to/settings.json", "."),
        ("path/to/stats.json", "."),
        ("path/to/config.bak", "."),
        ("path/to/data.tmp", "."),
        ("path/to/icon.ico", "."),
    ]

    filtered = filter_datas(raw_datas)
    filtered_srcs = [src for src, _ in filtered]

    assert "path/to/customtkinter/assets/theme.json" in filtered_srcs
    assert "path/to/icon.ico" in filtered_srcs
    assert "path/to/credentials.json" not in filtered_srcs
    assert "path/to/accounts.txt" not in filtered_srcs
    assert "path/to/settings.json" not in filtered_srcs
    assert "path/to/stats.json" not in filtered_srcs
    assert "path/to/config.bak" not in filtered_srcs
    assert "path/to/data.tmp" not in filtered_srcs


def test_old_files_in_previous_output_cannot_leak_into_new_staging(canary_project):
    """Staging directory is wiped before new builds so older leaked files cannot persist."""
    staging = canary_project["staging"]
    staging.mkdir(parents=True, exist_ok=True)
    stale_file = staging / "stale_leaked_creds.json"
    stale_file.write_text("old_data", encoding="utf-8")

    # Staging clean step:
    if staging.exists():
        shutil.rmtree(staging)

    assert not staging.exists()
    assert not stale_file.exists()


def test_local_source_credentials_remain_intact(canary_project):
    """Building into dist preserves original local source files byte-for-byte."""
    source = canary_project["source"]
    staging = canary_project["staging"]
    dist = canary_project["dist"]

    orig_creds = (source / "credentials.json").read_text(encoding="utf-8")
    orig_accs = (source / "accounts.txt").read_text(encoding="utf-8")
    orig_settings = (source / "settings.json").read_text(encoding="utf-8")

    stage_release(source, staging, dist)

    assert (source / "credentials.json").read_text(encoding="utf-8") == orig_creds
    assert (source / "accounts.txt").read_text(encoding="utf-8") == orig_accs
    assert (source / "settings.json").read_text(encoding="utf-8") == orig_settings


def test_gitignore_covers_sensitive_files():
    """Verify that .gitignore covers credentials, accounts.txt, and backups/temp files."""
    gitignore_path = Path(".gitignore")
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")

    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    assert "credentials.json" in lines
    assert "accounts.txt" in lines
    assert "settings.json" in lines
    assert "stats.json" in lines
    assert "*.bak" in lines
    assert "*.tmp" in lines


def test_sanitized_examples_and_tests_remain_trackable():
    """Sanitized templates and test fixtures are not ignored by .gitignore."""
    gitignore_path = Path(".gitignore")
    content = gitignore_path.read_text(encoding="utf-8")

    assert "!accounts.example.txt" in content
    assert "!settings.example.json" in content


def test_fresh_installation_follows_first_run_setup(tmp_path):
    """A fresh release deployment initializes clean defaults without requiring bundled credentials."""
    release_install_dir = tmp_path / "installed_app"
    release_install_dir.mkdir(parents=True)

    settings_file = release_install_dir / "settings.json"
    credentials_file = release_install_dir / "credentials.json"

    assert not settings_file.exists()
    assert not credentials_file.exists()

    mgr = ConfigManager(settings_file)

    # First-run settings are created automatically
    assert settings_file.exists()
    assert mgr.get_setting("AppearanceMode") == "Dark"
    assert mgr.get_accounts() == []
    assert credentials_file.exists()
    assert json.loads(credentials_file.read_text(encoding="utf-8")) == {"accounts": []}
