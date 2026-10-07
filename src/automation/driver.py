import json
import logging
import queue
import shutil
import threading
import urllib.request
import winreg
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import undetected_chromedriver as uc

from ..models.account import Account
from ..utils.logger import emit
from .adblock import load_extension_cdp


def get_runtime_profiles_dir() -> Path:
    """Path to account Chrome profiles directory."""
    runtime_dir = Path(__file__).resolve().parent.parent.parent / "runtime" / "chrome_profiles"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


def cleanup_profile_locks(profile_path: Path) -> None:
    """Remove lock files from Chrome user-data directory to prevent crash on startup."""
    lock_names = ["SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"]
    for lock in lock_names:
        lock_file = profile_path / lock
        if lock_file.exists():
            try:
                if lock_file.is_dir():
                    shutil.rmtree(lock_file, ignore_errors=True)
                else:
                    lock_file.unlink(missing_ok=True)
            except Exception:
                pass


def get_chrome_version() -> Optional[str]:
    """Detect installed Chrome full version (e.g. ``154.0.8037.98``) from the registry."""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in (
            r"Software\Google\Chrome\BLBeacon",
            r"Software\WOW6432Node\Google\Chrome\BLBeacon",
        ):
            try:
                key = winreg.OpenKey(root, key_path)
                val, _ = winreg.QueryValueEx(key, "version")
                winreg.CloseKey(key)
                if val:
                    return str(val)
            except Exception:
                pass
    return None


def get_chrome_major_version() -> Optional[int]:
    """Detect installed Chrome browser major version from registry or common paths."""
    version = get_chrome_version()
    if version:
        try:
            return int(version.split(".")[0])
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# chromedriver provisioning
#
# undetected-chromedriver deletes its cached chromedriver and re-downloads it
# from Google's CDN on *every* launch; a slow or blocked network therefore
# breaks browser startup entirely. We resolve the driver once per Chrome major
# version into runtime/tools/ and hand it to uc explicitly (its custom-path
# branch patches the binary locally and never touches the network).
# --------------------------------------------------------------------------- #

CFT_CDN = "https://storage.googleapis.com/chrome-for-testing-public"
NPMIRROR_CFT = "https://registry.npmmirror.com/-/binary/chrome-for-testing"
DOWNLOAD_TIMEOUT_SECONDS = 45
MIN_DRIVER_BYTES = 1_000_000

DRIVER_INIT_LOCK = threading.Lock()


def get_chromedriver_cache_dir() -> Path:
    """Directory holding chromedriver copies (one per Chrome major version)."""
    path = Path(__file__).resolve().parent.parent.parent / "runtime" / "tools" / "chromedriver"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chromedriver_url_candidates(chrome_version: str) -> Iterator[str]:
    """Yield chromedriver download URLs, most authoritative source first."""
    major = chrome_version.split(".")[0]
    yield f"{CFT_CDN}/{chrome_version}/win64/chromedriver-win64.zip"
    yield f"{NPMIRROR_CFT}/{chrome_version}/win64/chromedriver-win64.zip"

    # Last resort: newest same-major build available on the mirror (mirrors can
    # lag behind the exact Chrome build; only the major version must match).
    try:
        request = urllib.request.Request(NPMIRROR_CFT + "/", headers={"User-Agent": "yt-tool/2.1"})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            entries = json.loads(response.read().decode("utf-8"))
        versions = []
        for entry in entries:
            name = str(entry.get("name", "")).strip("/")
            if not name.startswith(major + "."):
                continue
            try:
                versions.append((tuple(int(part) for part in name.split(".")), name))
            except ValueError:
                continue
        if versions:
            yield f"{NPMIRROR_CFT}/{max(versions)[1]}/win64/chromedriver-win64.zip"
    except Exception:
        return


def _fetch_url(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "yt-tool/2.1"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        with open(dest, "wb") as handle:
            shutil.copyfileobj(response, handle)


def _extract_driver_exe(archive: Path, target: Path) -> bool:
    """Extract only ``chromedriver.exe`` from a CfT zip into ``target``."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [name for name in bundle.namelist() if name.lower().endswith("chromedriver.exe")]
            if not members:
                return False
            payload = bundle.read(min(members, key=len))
    except (zipfile.BadZipFile, OSError):
        return False

    if len(payload) < MIN_DRIVER_BYTES:
        return False

    temp_target = target.with_suffix(".part")
    temp_target.write_bytes(payload)
    temp_target.replace(target)
    return True


def ensure_chromedriver(chrome_version: str, log_queue: Optional[queue.Queue] = None) -> Optional[Path]:
    """Return a usable chromedriver for this Chrome build, downloading if needed.

    Returns ``None`` when no source is reachable so the caller can fall back to
    undetected-chromedriver's own downloader.
    """
    major = chrome_version.split(".")[0]
    if not major.isdigit():
        return None

    cache_dir = get_chromedriver_cache_dir()
    target = cache_dir / f"chromedriver-{major}.exe"
    if target.is_file() and target.stat().st_size >= MIN_DRIVER_BYTES:
        return target

    archive = cache_dir / "download.part"
    try:
        for url in _chromedriver_url_candidates(chrome_version):
            source = url.split("/")[2]
            try:
                _fetch_url(url, archive)
                if archive.stat().st_size < MIN_DRIVER_BYTES:
                    raise RuntimeError("incomplete download")
                if _extract_driver_exe(archive, target):
                    emit(log_queue, f"chromedriver {major} cached at {target.name} (via {source}).")
                    return target
                emit(log_queue, f"chromedriver archive from {source} has no driver binary.", logging.WARNING)
            except Exception as exc:
                emit(log_queue, f"chromedriver source {source} failed: {exc}", logging.WARNING)
    finally:
        archive.unlink(missing_ok=True)

    return None


def setup_driver(
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    adblock_path: Optional[Path] = None,
) -> uc.Chrome:
    """Initialize a stealth undetected-chromedriver instance with dedicated account profile."""
    profile_dir = get_runtime_profiles_dir() / account.get_profile_dir_name()
    profile_dir.mkdir(parents=True, exist_ok=True)
    cleanup_profile_locks(profile_dir)

    emit(log_queue, f"Initializing Chrome driver for account: {account.email}")

    options = uc.ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-service-autorun")
    options.add_argument("--password-store=basic")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--lang=en-US,en")

    chrome_version = get_chrome_version()
    chrome_major = None
    if chrome_version:
        try:
            chrome_major = int(chrome_version.split(".")[0])
        except ValueError:
            chrome_major = None
        emit(log_queue, f"Detected system Chrome version: {chrome_version}")

    driver_path = None
    if chrome_version:
        driver_path = ensure_chromedriver(chrome_version, log_queue)

    launch_kwargs: Dict[str, Any] = {"use_subprocess": True}
    if chrome_major:
        launch_kwargs["version_main"] = chrome_major
    if driver_path is not None:
        launch_kwargs["driver_executable_path"] = str(driver_path)

    try:
        with DRIVER_INIT_LOCK:
            try:
                driver = uc.Chrome(options=options, **launch_kwargs)
            except Exception:
                if driver_path is None:
                    raise
                # Cached copy rejected (e.g. patched badly) -> uc's own flow.
                emit(log_queue, "Cached chromedriver rejected; retrying automatic download.", logging.WARNING)
                launch_kwargs.pop("driver_executable_path", None)
                driver = uc.Chrome(options=options, **launch_kwargs)
        driver.set_page_load_timeout(45)

        if adblock_path is not None:
            load_extension_cdp(driver, adblock_path, log_queue)

        # First successful automatic download -> keep a copy for offline launches.
        if driver_path is None and chrome_major:
            try:
                downloaded = Path(driver.patcher.executable_path)
                cached = get_chromedriver_cache_dir() / f"chromedriver-{chrome_major}.exe"
                if downloaded.is_file() and downloaded.resolve() != cached.resolve():
                    shutil.copy2(downloaded, cached)
                    emit(log_queue, f"chromedriver {chrome_major} cached for offline launches.")
            except Exception:
                pass

        emit(log_queue, f"Chrome driver successfully started for {account.email}.")
        return driver
    except Exception as exc:
        emit(log_queue, f"Failed to initialize Chrome driver: {exc}", logging.ERROR)
        raise



def close_driver(driver: Optional[Any], log_queue: Optional[queue.Queue] = None) -> None:
    """Safely quit driver session."""
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
        emit(log_queue, "Browser session closed.")
