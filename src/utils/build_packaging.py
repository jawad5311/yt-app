"""Helpers for release packaging, staging, and sensitive file exclusion."""
import shutil
from pathlib import Path
from typing import List, Sequence, Tuple

SENSITIVE_FILENAMES = {
    "credentials.json",
    "accounts.txt",
    "settings.json",
    "stats.json",
}


def is_sensitive_filename(name: str) -> bool:
    """Check if a filename is sensitive (credentials, state, backups, temp files)."""
    base = Path(name).name.lower()
    if base in SENSITIVE_FILENAMES:
        return True
    if base.endswith(".bak") or base.endswith(".tmp") or ".backup_" in base:
        return True
    return False


def filter_datas(datas: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Filter PyInstaller data items to prevent sensitive files from being bundled."""
    filtered = []
    for src, dst in datas:
        if is_sensitive_filename(src):
            continue
        filtered.append((src, dst))
    return filtered


def stage_release(
    source_dir: Path,
    staging_dir: Path,
    dist_dir: Path,
    assets: Sequence[str] = (),
) -> List[str]:
    """Isolate build artifacts into staging and sync to dist without leaking secrets or deleting user data."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    copied_manifest: List[str] = []

    # 1. Stage only safe files from source_dir into staging_dir
    for item in source_dir.iterdir():
        if item.is_file():
            if is_sensitive_filename(item.name):
                continue
            dest = staging_dir / item.name
            shutil.copy2(item, dest)
            copied_manifest.append(item.name)

    # 2. Sync staging to dist without touching existing user data in dist
    for staged_item in staging_dir.iterdir():
        if staged_item.is_file():
            target = dist_dir / staged_item.name
            shutil.copy2(staged_item, target)

    return copied_manifest
