import logging
import os
import queue
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Any
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options

from ..models.account import Account
from ..utils.logger import emit


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


def setup_driver(account: Account, log_queue: Optional[queue.Queue] = None) -> uc.Chrome:
    """Initialize a stealth undetected-chromedriver instance with dedicated account profile."""
    profile_dir = get_runtime_profiles_dir() / account.get_profile_dir_name()
    profile_dir.mkdir(parents=True, exist_ok=True)
    cleanup_profile_locks(profile_dir)

    emit(log_queue, f"Initializing Chrome driver for account: {account.email}")

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-service-autorun")
    options.add_argument("--password-store=basic")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--lang=en-US,en")

    try:
        driver = uc.Chrome(options=options, use_subprocess=True)
        driver.set_page_load_timeout(60)
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
