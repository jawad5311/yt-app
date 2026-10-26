import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

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
            # Check how many known accounts map to this legacy folder
            matching = [
                a for a in known_accounts
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
    lock_file = profile_dir / "SingletonLock"
    if not lock_file.exists():
        return False

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
                    return False
        except Exception:
            pass

    return False


class ProfileLock:
    """Acquires exclusive cross-thread and cross-process ownership of a profile directory."""

    _thread_locks: Dict[Path, threading.Lock] = {}
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
            if self.profile_dir not in self._thread_locks:
                self._thread_locks[self.profile_dir] = threading.Lock()
            self._thread_lock = self._thread_locks[self.profile_dir]

        blocking = timeout > 0
        wait_arg = timeout if blocking else -1
        acquired_thread = self._thread_lock.acquire(blocking=blocking, timeout=wait_arg)
        if not acquired_thread:
            raise ProfileBusyError(
                f"Profile '{self.profile_dir.name}' is currently in use by another session in this application."
            )

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
            if self._file_handle and not self._file_handle.closed:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None
            if self._thread_lock and self._thread_lock.locked():
                self._thread_lock.release()
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
