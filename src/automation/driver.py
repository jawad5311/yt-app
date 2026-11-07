import logging
import os
import queue
import shutil
import subprocess
import threading
import winreg
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import undetected_chromedriver as uc

from ..models.account import Account
from ..utils.logger import emit
from .driver_provisioner import (
    create_session_driver_copy,
    ensure_chromedriver,
    publish_driver_to_cache,
)
from .profile_manager import (
    ProfileBusyError,
    ProfileLock,
    is_chrome_lock_active,
    resolve_profile_dir,
)


def cleanup_profile_locks(profile_path: Path) -> None:
    """Safely check and remove stale lock files left by crashed browser sessions.

    Never terminates unrelated Chrome processes by broad substring match or removes
    locks while an active process is using the profile.
    """
    if is_chrome_lock_active(profile_path):
        raise ProfileBusyError(f"Chrome is actively running in profile '{profile_path.name}'.")

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


DRIVER_INIT_LOCK = threading.Lock()


def sync_reporting_profile(src_dir: Path, dst_dir: Path) -> None:
    """Sync authentication and session state files from main profile to isolated reporting profile."""
    if not src_dir.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    files_to_sync = [
        "Local State",
        "Default/Preferences",
        "Default/Secure Preferences",
        "Default/Network/Cookies",
        "Default/Login Data",
        "Default/Login Data For Account",
        "Default/Web Data",
    ]
    for rel_path in files_to_sync:
        s = src_dir / rel_path
        d = dst_dir / rel_path
        if s.exists():
            try:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
            except Exception:
                pass


def setup_driver(
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    adblock_path: Optional[Path] = None,
    known_accounts: Optional[Union[List[Account], List[Dict[str, Any]]]] = None,
    headless: bool = False,
    is_reporting: bool = False,
) -> uc.Chrome:
    """Initialize a stealth undetected-chromedriver instance with dedicated account profile."""
    resolved_known: Optional[List[Account]] = None
    if known_accounts is None:
        try:
            from ..config.manager import ConfigManager
            raw_accs = ConfigManager().get_accounts()
            resolved_known = [
                Account(email=a["email"], password=a.get("password", "")) if isinstance(a, dict) and "email" in a else a
                for a in raw_accs
                if a
            ]
        except Exception:
            resolved_known = None
    elif known_accounts:
        resolved_known = [
            Account(email=a["email"], password=a.get("password", "")) if isinstance(a, dict) and "email" in a else a
            for a in known_accounts
            if a
        ]

    main_profile_dir = resolve_profile_dir(account, known_accounts=resolved_known)
    if is_reporting:
        profile_dir = main_profile_dir.parent / f"{main_profile_dir.name}_reporting"
        sync_reporting_profile(main_profile_dir, profile_dir)
    else:
        profile_dir = main_profile_dir

    profile_dir.mkdir(parents=True, exist_ok=True)

    profile_lock = ProfileLock(profile_dir)
    profile_lock.acquire()

    try:
        cleanup_profile_locks(profile_dir)

        emit(log_queue, f"Initializing Chrome driver for account: {account.email}")
        emit(log_queue, f"Using persistent Chrome profile: {profile_dir}")

        options = uc.ChromeOptions()
        options.page_load_strategy = "eager"
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--no-first-run")
        options.add_argument("--no-service-autorun")
        options.add_argument("--password-store=basic")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        if headless:
            options.add_argument("--window-size=1920,1080")
        else:
            options.add_argument("--start-maximized")
        options.add_argument("--lang=en-US,en")
        if headless:
            options.add_argument("--headless=new")

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
            cached_path = ensure_chromedriver(chrome_version, log_queue)
            if cached_path is not None:
                driver_path = create_session_driver_copy(cached_path, profile_dir)

        launch_kwargs: Dict[str, Any] = {"use_subprocess": True}
        if chrome_major:
            launch_kwargs["version_main"] = chrome_major
        if driver_path is not None:
            launch_kwargs["driver_executable_path"] = str(driver_path)

        emit(log_queue, "Launching Chrome browser instance...")
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

        if not headless:
            try:
                driver.maximize_window()
            except Exception:
                pass

        # First successful automatic download -> keep a copy for offline launches using safe publication.
        if driver_path is None and chrome_major:
            try:
                downloaded = Path(driver.patcher.executable_path)
                publish_driver_to_cache(downloaded, chrome_major, log_queue)
            except Exception:
                pass

        driver._profile_lock = profile_lock
        emit(log_queue, f"Chrome driver successfully started for {account.email}.")
        return driver
    except Exception as exc:
        profile_lock.release()
        emit(log_queue, f"Failed to initialize Chrome driver: {exc}", logging.ERROR)
        raise


def close_driver(driver: Optional[Any], log_queue: Optional[queue.Queue] = None) -> None:
    """Safely quit driver session and terminate lingering browser processes."""
    if driver is not None:
        profile_lock = getattr(driver, "_profile_lock", None)
        browser_pid = getattr(driver, "browser_pid", None)
        service = getattr(driver, "service", None)
        service_pid = service.process.pid if service and getattr(service, "process", None) else None
        try:
            driver.quit()
        except Exception as exc:
            logging.debug(f"driver.quit() exception: {exc}")
        finally:
            if os.name == "nt":
                for pid in (browser_pid, service_pid):
                    if isinstance(pid, int) and pid > 0:
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True,
                                timeout=5,
                            )
                        except Exception:
                            pass
            if profile_lock:
                try:
                    profile_lock.release()
                except Exception:
                    pass
        emit(log_queue, "Browser session closed.")


try:
    from selenium.common.exceptions import (
        InvalidSessionIdException,
        NoSuchWindowException,
        WebDriverException,
    )
except ImportError:
    InvalidSessionIdException = Exception
    NoSuchWindowException = Exception
    WebDriverException = Exception


def is_session_disconnected_error(exc: Optional[BaseException]) -> bool:
    """Check if an exception indicates the browser session is dead or disconnected."""
    if exc is None:
        return False
    if isinstance(exc, (InvalidSessionIdException, NoSuchWindowException)):
        return True
    msg = (getattr(exc, "msg", None) or str(exc)).lower()
    return any(
        term in msg
        for term in (
            "invalid session id",
            "invalidsessionid",
            "disconnected",
            "not connected to devtools",
            "chrome not reachable",
            "session deleted",
            "target window already closed",
            "no such window",
        )
    )


def is_driver_alive(driver: Optional[Any]) -> bool:
    """Check if the Selenium WebDriver instance has an active, responsive browser session."""
    if driver is None:
        return False
    session_id = getattr(driver, "session_id", None)
    if session_id is None:
        return False
    if isinstance(session_id, str) and not session_id.strip():
        return False
    try:
        driver.execute_script("return 1;")
        return True
    except Exception as exc:
        if is_session_disconnected_error(exc):
            return False
        try:
            url = getattr(driver, "current_url", None)
            return bool(url)
        except Exception:
            return False

