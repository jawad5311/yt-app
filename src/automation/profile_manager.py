import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..models.account import Account
from ..utils.helpers import get_project_root


class ProfileLockError(Exception):
    """Base exception for profile locking errors."""
    pass


class ProfileBusyError(ProfileLockError):
    """Raised when a Chrome profile is currently in use by another session or process."""
    pass


def get_runtime_profiles_dir() -> Path:
    """Return the absolute path to the accounts Chrome profiles directory."""
    runtime_dir = (get_project_root() / "runtime" / "chrome_profiles").resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


def get_system_chrome_user_data_dir() -> Path:
    """Return the absolute path to the operating system's default Google Chrome User Data directory."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / "Google" / "Chrome" / "User Data").resolve()
        return (Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data").resolve()
    elif sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "Google" / "Chrome").resolve()
    else:
        return (Path.home() / ".config" / "google-chrome").resolve()


def get_system_chrome_profiles() -> List[Dict[str, Any]]:
    """Scan and return all installed Chrome profiles from the system Chrome User Data directory.

    Parses ``Local State`` to extract folder names, profile names, and signed-in Google account emails.
    """
    user_data_dir = get_system_chrome_user_data_dir()
    profiles: List[Dict[str, Any]] = []

    if not user_data_dir.exists():
        return profiles

    local_state_file = user_data_dir / "Local State"
    if local_state_file.is_file():
        try:
            with open(local_state_file, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            info_cache = data.get("profile", {}).get("info_cache", {})
            if isinstance(info_cache, dict):
                for folder_name, prof_data in info_cache.items():
                    if not isinstance(prof_data, dict):
                        continue
                    email = prof_data.get("user_name", "") or ""
                    display_name = prof_data.get("name", "") or folder_name
                    gaia_name = prof_data.get("gaia_name", "") or ""
                    is_primary = bool(prof_data.get("is_consented_primary_account", folder_name == "Default"))

                    label = f"{display_name} ({email})" if email else display_name
                    profiles.append({
                        "folder": folder_name,
                        "name": display_name,
                        "email": email,
                        "gaia_name": gaia_name,
                        "is_primary": is_primary,
                        "label": label,
                        "path": user_data_dir / folder_name,
                    })
        except Exception as exc:
            logging.debug(f"Failed to read Chrome Local State file: {exc}")

    # Fallback if no profiles found in Local State but Default folder exists on disk
    if not profiles:
        default_folder = user_data_dir / "Default"
        if default_folder.is_dir():
            profiles.append({
                "folder": "Default",
                "name": "Default Profile",
                "email": "",
                "gaia_name": "",
                "is_primary": True,
                "label": "Default Profile",
                "path": default_folder,
            })

    return profiles


def get_active_system_profile(profile_name: str = "Default") -> Dict[str, Any]:
    """Return the profile info dictionary for ``profile_name``, or Default profile if available."""
    profiles = get_system_chrome_profiles()
    for p in profiles:
        if p["folder"].lower() == profile_name.lower():
            return p
    if profiles:
        return profiles[0]
    user_data = get_system_chrome_user_data_dir()
    return {
        "folder": profile_name,
        "name": profile_name,
        "email": "",
        "gaia_name": "",
        "is_primary": True,
        "label": profile_name,
        "path": user_data / profile_name,
    }


def is_process_running(process_name: str) -> bool:
    """Return True if a process matching process_name is currently running.

    Uses pure in-memory Windows Toolhelp32Snapshot to avoid spawning external
    console processes (like tasklist.exe) which cause GUI window flickering/blinking.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            TH32CS_SNAPPROCESS = 0x00000002

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            h_snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if h_snap != wintypes.HANDLE(-1).value and h_snap != -1:
                try:
                    entry = PROCESSENTRY32W()
                    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                    target = process_name.lower()
                    if ctypes.windll.kernel32.Process32FirstW(h_snap, ctypes.byref(entry)):
                        while True:
                            if entry.szExeFile.lower() == target:
                                return True
                            if not ctypes.windll.kernel32.Process32NextW(h_snap, ctypes.byref(entry)):
                                break
                finally:
                    ctypes.windll.kernel32.CloseHandle(h_snap)
        except Exception:
            pass
    return False


def is_system_chrome_running() -> bool:
    """Return True if Google Chrome is currently open on the computer.

    Uses lock file inspection on the system User Data directory and in-memory Win32 process enumeration.
    Spawns zero subprocesses to prevent GUI flickering.
    """
    user_data_dir = get_system_chrome_user_data_dir()
    if user_data_dir.exists():
        for lock_name in ("lockfile", "SingletonLock"):
            lock_path = user_data_dir / lock_name
            if lock_path.exists():
                try:
                    with open(lock_path, "r+b"):
                        pass
                except (PermissionError, OSError):
                    return True

    return is_process_running("chrome.exe")


def resolve_profile_dir(
    account: Account,
    known_accounts: Optional[Sequence[Account]] = None,
    runtime_dir: Optional[Path] = None,
) -> Path:
    """Resolve the profile directory for an account.

    Preserves existing persistent sessions when ownership is unambiguous,
    prevents cross-account session copying on collision, and guarantees all paths
    remain strictly inside the runtime profiles directory.
    """
    if runtime_dir is None:
        runtime_dir = get_runtime_profiles_dir()
    else:
        runtime_dir = runtime_dir.resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)

    collision_safe_name = account.get_profile_dir_name()
    collision_safe_path = (runtime_dir / collision_safe_name).resolve()

    # Boundary verification: path must reside within runtime_dir
    try:
        collision_safe_path.relative_to(runtime_dir)
    except ValueError:
        raise ValueError(f"Profile path '{collision_safe_path}' escapes runtime directory '{runtime_dir}'")

    if collision_safe_path.exists():
        return collision_safe_path

    legacy_name = account.get_legacy_profile_dir_name()
    legacy_path = (runtime_dir / legacy_name).resolve()
    try:
        legacy_path.relative_to(runtime_dir)
    except ValueError:
        raise ValueError(f"Legacy profile path '{legacy_path}' escapes runtime directory '{runtime_dir}'")

    if legacy_path.exists():
        if known_accounts:
            # Normalize known accounts to Account instances
            norm_accounts: List[Account] = []
            for item in known_accounts:
                if isinstance(item, Account):
                    norm_accounts.append(item)
                elif isinstance(item, dict) and "email" in item:
                    try:
                        norm_accounts.append(Account(email=item["email"], password=item.get("password", "")))
                    except Exception:
                        pass

            # Check how many known accounts map to this legacy folder
            matching = [
                a for a in norm_accounts
                if a.get_legacy_profile_dir_name() == legacy_name
            ]
            if len(matching) == 1 and matching[0].email == account.email:
                # Unambiguous ownership: reuse the existing legacy session
                return legacy_path
            elif len(matching) > 1:
                # Ambiguous ownership: multiple accounts share this legacy name
                logging.warning(
                    f"Ambiguous legacy profile ownership: accounts {[a.email for a in matching]} "
                    f"share legacy directory '{legacy_name}'. Not copying or reusing shared data. "
                    f"Assigning clean independent profile to '{account.email}'."
                )
                return collision_safe_path
        else:
            # No other accounts provided to check; reuse unambiguous legacy profile
            return legacy_path

    return collision_safe_path


def is_chrome_lock_active(profile_dir: Path) -> bool:
    """Check if Chrome's lock file is actively held by a live process."""
    if not profile_dir or not profile_dir.is_dir():
        return False

    for lock_name in ("SingletonLock", "lockfile"):
        lock_file = profile_dir / lock_name
        if not lock_file.exists():
            continue

        # On Windows / Unix, try opening in read-write mode.
        # If Chrome holds it open, the OS raises PermissionError / OSError.
        try:
            if lock_file.is_file():
                with open(lock_file, "r+b"):
                    pass
        except (PermissionError, OSError):
            return True

        if lock_file.is_symlink():
            try:
                target = os.readlink(lock_file)
                parts = target.split("-")
                if len(parts) >= 2 and parts[-1].isdigit():
                    pid = int(parts[-1])
                    try:
                        os.kill(pid, 0)
                        return True
                    except (OSError, ProcessLookupError):
                        pass
            except Exception:
                pass

    return False


class ProfileLock:
    """Acquires exclusive cross-thread and cross-process ownership of a profile directory."""

    _thread_locks: Dict[Path, threading.Lock] = {}
    _thread_lock_owners: Dict[Path, threading.Thread] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir.resolve()
        self.lock_file_path = self.profile_dir / ".app_profile.lock"
        self._file_handle: Optional[Any] = None
        self._thread_lock: Optional[threading.Lock] = None
        self.is_locked = False

    def acquire(self, timeout: float = 0.0) -> bool:
        """Acquire both thread-level and OS process-level locks.

        Raises ProfileBusyError if the profile is already in use.
        """
        if self.is_locked:
            return True

        with self._thread_locks_guard:
            prev_owner = self._thread_lock_owners.get(self.profile_dir)
            if prev_owner is not None and not prev_owner.is_alive():
                # Previous owner thread terminated without releasing lock! Reclaim cleanly.
                self._thread_locks[self.profile_dir] = threading.Lock()
                self._thread_lock_owners.pop(self.profile_dir, None)

            if self.profile_dir not in self._thread_locks:
                self._thread_locks[self.profile_dir] = threading.Lock()
            self._thread_lock = self._thread_locks[self.profile_dir]

        blocking = timeout > 0
        wait_arg = timeout if blocking else -1
        acquired_thread = self._thread_lock.acquire(blocking=blocking, timeout=wait_arg)
        if not acquired_thread:
            with self._thread_locks_guard:
                prev_owner = self._thread_lock_owners.get(self.profile_dir)
                if prev_owner is not None and not prev_owner.is_alive():
                    self._thread_locks[self.profile_dir] = threading.Lock()
                    self._thread_lock = self._thread_locks[self.profile_dir]
                    self._thread_lock_owners.pop(self.profile_dir, None)
                    acquired_thread = self._thread_lock.acquire(blocking=False)
            if not acquired_thread:
                raise ProfileBusyError(
                    f"Profile '{self.profile_dir.name}' is currently in use by another session in this application."
                )

        with self._thread_locks_guard:
            self._thread_lock_owners[self.profile_dir] = threading.current_thread()

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._file_handle = open(self.lock_file_path, "a+b")
            self._file_handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    self._file_handle.close()
                    self._file_handle = None
                    with self._thread_locks_guard:
                        self._thread_lock_owners.pop(self.profile_dir, None)
                    self._thread_lock.release()
                    raise ProfileBusyError(
                        f"Profile '{self.profile_dir.name}' is currently locked by another application instance."
                    ) from exc
            else:
                import fcntl
                try:
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError) as exc:
                    self._file_handle.close()
                    self._file_handle = None
                    with self._thread_locks_guard:
                        self._thread_lock_owners.pop(self.profile_dir, None)
                    self._thread_lock.release()
                    raise ProfileBusyError(
                        f"Profile '{self.profile_dir.name}' is currently locked by another application instance."
                    ) from exc

            self._file_handle.seek(0)
            self._file_handle.truncate(0)
            self._file_handle.write(f"pid={os.getpid()}\n".encode("utf-8"))
            self._file_handle.flush()
            self.is_locked = True
            return True
        except Exception:
            with self._thread_locks_guard:
                self._thread_lock_owners.pop(self.profile_dir, None)
            if self._file_handle and not self._file_handle.closed:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None
            if self._thread_lock and self._thread_lock.locked():
                try:
                    self._thread_lock.release()
                except Exception:
                    pass
            raise

    def release(self) -> None:
        """Release both OS process-level and thread-level locks."""
        if not self.is_locked:
            return

        try:
            if self._file_handle and not self._file_handle.closed:
                if os.name == "nt":
                    import msvcrt
                    try:
                        self._file_handle.seek(0)
                        msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None
        finally:
            with self._thread_locks_guard:
                self._thread_lock_owners.pop(self.profile_dir, None)
            if self._thread_lock and self._thread_lock.locked():
                try:
                    self._thread_lock.release()
                except Exception:
                    pass
            self.is_locked = False

    def __enter__(self) -> "ProfileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
