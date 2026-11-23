import logging
import os
import queue
import shutil
import subprocess
import sys
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
    get_active_system_profile,
    get_runtime_profiles_dir,
    get_system_chrome_user_data_dir,
    is_chrome_lock_active,
    is_system_chrome_running,
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


def copy_shared_file(src: Path, dst: Path) -> bool:
    """Copy a file even if locked by an active Chrome process on Windows."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            FILE_SHARE_DELETE = 0x00000004
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80

            CreateFileW = ctypes.windll.kernel32.CreateFileW
            CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
            ]
            CreateFileW.restype = wintypes.HANDLE

            CloseHandle = ctypes.windll.kernel32.CloseHandle
            CloseHandle.argtypes = [wintypes.HANDLE]
            CloseHandle.restype = wintypes.BOOL

            ReadFile = ctypes.windll.kernel32.ReadFile
            ReadFile.argtypes = [
                wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID
            ]
            ReadFile.restype = wintypes.BOOL

            h = CreateFileW(str(src), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
            if h != wintypes.HANDLE(-1).value and h != -1:
                try:
                    size = ctypes.windll.kernel32.GetFileSize(h, None)
                    if size > 0:
                        buf = ctypes.create_string_buffer(size)
                        read = wintypes.DWORD()
                        if ReadFile(h, buf, size, ctypes.byref(read), None):
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            with open(dst, "wb") as f_dst:
                                f_dst.write(buf.raw[:read.value])
                            return True
                finally:
                    CloseHandle(h)
        except Exception:
            pass

    try:
        with open(src, "rb") as f_src:
            data = f_src.read()
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "wb") as f_dst:
            f_dst.write(data)
        return True
    except Exception:
        return False


def sync_cookies_between_drivers(src_driver: Any, dst_driver: Any) -> bool:
    """Transfer live in-memory cookies from src_driver to dst_driver via CDP."""
    if src_driver is None or dst_driver is None:
        return False
    try:
        if not is_driver_alive(src_driver) or not is_driver_alive(dst_driver):
            return False
        raw = src_driver.execute_cdp_cmd("Network.getAllCookies", {})
        cookies = raw.get("cookies", [])
        if not cookies and hasattr(src_driver, "get_cookies"):
            cookies = src_driver.get_cookies()
        if cookies:
            cleaned = []
            for c in cookies:
                name = c.get("name")
                val = c.get("value")
                if not name:
                    continue
                cookie_dict = {
                    "name": name,
                    "value": val or "",
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                    "secure": bool(c.get("secure", False)),
                    "httpOnly": bool(c.get("httpOnly", False)),
                }
                same_site = c.get("sameSite")
                if same_site in ("Strict", "Lax", "None"):
                    cookie_dict["sameSite"] = same_site
                exp = c.get("expires")
                if exp is not None and isinstance(exp, (int, float)) and exp > 0:
                    cookie_dict["expires"] = exp
                cleaned.append(cookie_dict)
            if cleaned:
                try:
                    dst_driver.execute_cdp_cmd("Network.setCookies", {"cookies": cleaned})
                    return True
                except Exception:
                    applied = False
                    for ck in cleaned:
                        try:
                            dst_driver.execute_cdp_cmd("Network.setCookie", ck)
                            applied = True
                        except Exception:
                            pass
                    return applied
    except Exception as exc:
        logging.debug("CDP cookie transfer error: %s", exc)
    return False


def sync_reporting_profile(src_dir: Path, dst_dir: Path, profile_folder: str = "Default") -> None:
    """Sync authentication/session state into an isolated Chrome user-data directory."""
    if not src_dir.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    profile_prefix = str(profile_folder or "Default").strip("/\\") or "Default"
    files_to_sync = ["Local State"] + [
        f"{profile_prefix}/{name}" for name in (
            "Preferences", "Secure Preferences", "Account Web Data",
            "Account Web Data-journal", "Account Web Data-wal",
            "Network/Cookies", "Network/Cookies-journal", "Network/Cookies-wal",
            "Network/Cookies-shm", "Network/Network Persistent State",
            "Network/Device Bound Sessions", "Network/Device Bound Sessions-journal",
            "Network/Trust Tokens", "Network/Trust Tokens-journal",
            "Cookies", "Cookies-journal", "Cookies-wal", "Login Data",
            "Login Data-journal", "Login Data For Account",
            "Login Data For Account-journal", "Web Data", "Web Data-journal",
        )
    ]
    for rel_path in files_to_sync:
        s = src_dir / rel_path
        d = dst_dir / rel_path
        if s.exists():
            copy_shared_file(s, d)

    dirs_to_sync = [f"{profile_prefix}/Session Storage", f"{profile_prefix}/Sessions"]
    for rel_dir in dirs_to_sync:
        s_dir = src_dir / rel_dir
        if s_dir.is_dir():
            d_dir = dst_dir / rel_dir
            d_dir.mkdir(parents=True, exist_ok=True)
            for item in s_dir.glob("*"):
                if item.is_file():
                    copy_shared_file(item, d_dir / item.name)


def maximize_browser_window(driver: Any) -> None:
    """Ensure the browser window is maximized via WebDriver and Win32."""
    if driver is None:
        return
    try:
        if hasattr(driver, "maximize_window"):
            driver.maximize_window()
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            browser_pid = getattr(driver, "browser_pid", None)
            if not browser_pid or browser_pid <= 0:
                service = getattr(driver, "service", None)
                if service and getattr(service, "process", None):
                    browser_pid = service.process.pid

            if browser_pid and browser_pid > 0:
                SW_MAXIMIZE = 3

                def enum_windows_callback(hwnd: Any, _lparam: Any) -> bool:
                    if user32.IsWindowVisible(hwnd):
                        pid = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value == browser_pid:
                            user32.ShowWindow(hwnd, SW_MAXIMIZE)
                    return True

                WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
        except Exception:
            pass


def setup_driver(
    account: Optional[Account] = None,
    log_queue: Optional[queue.Queue] = None,
    adblock_path: Optional[Path] = None,
    known_accounts: Optional[Union[List[Account], List[Dict[str, Any]]]] = None,
    headless: bool = False,
    is_reporting: bool = False,
    use_system_profile: Optional[bool] = None,
    system_profile_name: Optional[str] = None,
) -> uc.Chrome:
    """Initialize a stealth undetected-chromedriver instance.

    Supports dedicated account profiles in ``runtime/chrome_profiles/`` or directly
    reusing the user's personal installed Google Chrome profile.
    """
    if use_system_profile is None:
        try:
            from ..config.manager import ConfigManager
            use_system_profile = ConfigManager().get_bool("UseSystemChromeProfile", False)
        except Exception:
            use_system_profile = False

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

    if use_system_profile and not is_reporting:
        if is_system_chrome_running():
            raise ProfileBusyError(
                "Google Chrome is currently open on this computer.\n\n"
                "Chrome only allows one program to access your personal profile at a time.\n"
                "Please close Google Chrome completely before starting."
            )

        if not system_profile_name:
            try:
                from ..config.manager import ConfigManager
                system_profile_name = ConfigManager().get_setting("SystemChromeProfileName", "Default")
            except Exception:
                system_profile_name = "Default"

        active_prof = get_active_system_profile(system_profile_name)
        prof_folder = active_prof.get("folder", "Default")
        sys_user_data_dir = get_system_chrome_user_data_dir()
        profile_dir = sys_user_data_dir

        acc_email = getattr(account, "email", "") or active_prof.get("email", "") or "System Chrome Profile"
        emit(log_queue, f"Initializing Chrome driver with Personal Chrome Profile: {prof_folder} ({acc_email})")
        emit(log_queue, f"Using system Chrome User Data: {sys_user_data_dir}")

        profile_lock = ProfileLock(sys_user_data_dir)
        profile_lock.acquire(timeout=2.0)
        launch_system_profile = True
    else:
        if account is None:
            account = Account(email="default_user@gmail.com", password="")
        main_profile_dir = resolve_profile_dir(account, known_accounts=resolved_known)
        if is_reporting:
            profile_dir = main_profile_dir.parent / f"{main_profile_dir.name}_reporting"
            sync_reporting_profile(main_profile_dir, profile_dir)
        else:
            profile_dir = main_profile_dir

        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_lock = ProfileLock(profile_dir)
        profile_lock.acquire(timeout=2.0)
        cleanup_profile_locks(profile_dir)

        emit(log_queue, f"Initializing Chrome driver for account: {account.email}")
        emit(log_queue, f"Using persistent Chrome profile: {profile_dir}")
        launch_system_profile = False

    try:
        launch_profile_directory: Optional[str] = None

        def _build_options() -> uc.ChromeOptions:
            opts = uc.ChromeOptions()
            opts.page_load_strategy = "eager"
            if launch_system_profile:
                opts.add_argument(f"--user-data-dir={sys_user_data_dir}")
                opts.add_argument(f"--profile-directory={prof_folder}")
            else:
                opts.add_argument(f"--user-data-dir={profile_dir}")
                if launch_profile_directory:
                    opts.add_argument(f"--profile-directory={launch_profile_directory}")

            opts.add_argument("--no-first-run")
            opts.add_argument("--no-service-autorun")
            opts.add_argument("--password-store=basic")
            # GPU acceleration is enabled for maximum authenticity (disable-gpu removed)
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--start-maximized")
            opts.add_argument("--lang=en-US,en")
            return opts

        options = _build_options()

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
                driver_working_dir = (get_runtime_profiles_dir() / "system_driver_bin") if launch_system_profile else profile_dir
                driver_path = create_session_driver_copy(cached_path, driver_working_dir)

        launch_kwargs: Dict[str, Any] = {"use_subprocess": True, "headless": False}
        if chrome_major:
            launch_kwargs["version_main"] = chrome_major
        if driver_path is not None:
            launch_kwargs["driver_executable_path"] = str(driver_path)

        emit(log_queue, "Launching Chrome browser instance...")
        with DRIVER_INIT_LOCK:
            try:
                driver = uc.Chrome(options=options, **launch_kwargs)
            except Exception as first_exc:
                if driver_path is not None:
                    # Cached copy rejected (e.g. patched badly) -> uc's own flow with fresh ChromeOptions.
                    emit(log_queue, f"Cached chromedriver rejected ({first_exc}); retrying automatic download.", logging.WARNING)
                    launch_kwargs.pop("driver_executable_path", None)
                    try:
                        driver = uc.Chrome(options=_build_options(), **launch_kwargs)
                    except Exception as retry_exc:
                        if not launch_system_profile:
                            raise retry_exc
                        first_exc = retry_exc
                    else:
                        first_exc = None
                if launch_system_profile and first_exc is not None:
                    # The real Chrome profile may reject automation even when Chrome
                    # appears closed (stale locks, profile corruption, or a Chrome
                    # update). Use an isolated copy so the saved Google session is
                    # preserved without sharing Chrome's live profile lock.
                    safe_name = "".join(ch if ch.isalnum() else "_" for ch in prof_folder).strip("_") or "Default"
                    fallback_root = get_runtime_profiles_dir() / f"system_profile_{safe_name}"
                    emit(
                        log_queue,
                        f"Personal profile launch failed ({first_exc}); retrying with an isolated copy at {fallback_root}.",
                        logging.WARNING,
                    )
                    profile_lock.release()
                    sync_reporting_profile(sys_user_data_dir, fallback_root, profile_folder=prof_folder)
                    profile_dir = fallback_root
                    profile_lock = ProfileLock(profile_dir)
                    profile_lock.acquire(timeout=2.0)
                    launch_system_profile = False
                    launch_profile_directory = prof_folder
                    launch_kwargs.pop("driver_executable_path", None)
                    driver = uc.Chrome(options=_build_options(), **launch_kwargs)

        driver.keep_user_data_dir = True
        driver.set_page_load_timeout(45)

        try:
            maximize_browser_window(driver)
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
        driver._is_headless = False
        driver._is_reporting = bool(is_reporting)
        driver._is_system_profile = bool(launch_system_profile)

        # Ensure calling driver.quit() directly also safely releases the profile lock
        orig_quit = getattr(driver, "quit", None)
        if callable(orig_quit):
            def _safe_quit(*args: Any, **kwargs: Any) -> Any:
                try:
                    return orig_quit(*args, **kwargs)
                finally:
                    lock = getattr(driver, "_profile_lock", None)
                    if lock is not None:
                        try:
                            lock.release()
                        except Exception:
                            pass
            driver.quit = _safe_quit

        acc_name = getattr(account, "email", "") or (active_prof.get("email") if use_system_profile and not is_reporting else "profile")
        emit(log_queue, f"Chrome driver successfully started for {acc_name}.")
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

