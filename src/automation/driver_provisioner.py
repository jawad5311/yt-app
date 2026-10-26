"""Thread-safe and process-safe ChromeDriver provisioning and cache management."""

import json
import logging
import os
import queue
import shutil
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ..utils.helpers import get_project_root
from ..utils.logger import emit

CFT_CDN = "https://storage.googleapis.com/chrome-for-testing-public"
NPMIRROR_CFT = "https://registry.npmmirror.com/-/binary/chrome-for-testing"
DOWNLOAD_TIMEOUT_SECONDS = 45
MIN_DRIVER_BYTES = 1_000_000


def get_chromedriver_cache_dir() -> Path:
    """Directory holding shared chromedriver copies (one per Chrome major version)."""
    path = get_project_root() / "runtime" / "tools" / "chromedriver"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ProvisioningLock:
    """Inter-thread and inter-process lock for ChromeDriver provisioning."""

    _thread_locks: Dict[int, threading.Lock] = {}
    _guard = threading.Lock()

    def __init__(self, major: int, cache_dir: Path):
        self.major = major
        self.cache_dir = cache_dir.resolve()
        self.lock_file_path = self.cache_dir / f".provision_{major}.lock"
        self._thread_lock: Optional[threading.Lock] = None
        self._file_handle: Optional[Any] = None
        self.is_locked = False

    def acquire(self, timeout: float = 60.0) -> bool:
        if self.is_locked:
            return True

        with self._guard:
            if self.major not in self._thread_locks:
                self._thread_locks[self.major] = threading.Lock()
            self._thread_lock = self._thread_locks[self.major]

        acquired = self._thread_lock.acquire(blocking=True, timeout=timeout)
        if not acquired:
            raise TimeoutError(f"Timeout waiting for thread lock on ChromeDriver {self.major}")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._file_handle = open(self.lock_file_path, "a+b")
            start_time = time.time()

            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        self._file_handle.seek(0)
                        msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.time() - start_time > timeout:
                            raise TimeoutError(f"Timeout waiting for process lock on ChromeDriver {self.major}")
                        time.sleep(0.05)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except (OSError, IOError):
                        if time.time() - start_time > timeout:
                            raise TimeoutError(f"Timeout waiting for process lock on ChromeDriver {self.major}")
                        time.sleep(0.05)

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
                try:
                    self._thread_lock.release()
                except Exception:
                    pass
            raise

    def release(self) -> None:
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

    def __enter__(self) -> "ProvisioningLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def _chromedriver_url_candidates(chrome_version: str) -> Iterator[str]:
    """Yield chromedriver download URLs, most authoritative source first."""
    major = chrome_version.split(".")[0]
    yield f"{CFT_CDN}/{chrome_version}/win64/chromedriver-win64.zip"
    yield f"{NPMIRROR_CFT}/{chrome_version}/win64/chromedriver-win64.zip"

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


def _extract_driver_exe(archive: Path, target: Path, unique_id: Optional[str] = None) -> bool:
    """Extract only chromedriver.exe from archive into target atomically using a unique tmp file."""
    if unique_id is None:
        unique_id = uuid.uuid4().hex[:10]
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

    temp_target = target.parent / f"{target.stem}_{unique_id}.tmp"
    try:
        temp_target.write_bytes(payload)
        if temp_target.stat().st_size < MIN_DRIVER_BYTES:
            return False
        temp_target.replace(target)
        return True
    finally:
        temp_target.unlink(missing_ok=True)


def ensure_chromedriver(chrome_version: str, log_queue: Optional[queue.Queue] = None) -> Optional[Path]:
    """Return a usable chromedriver for this Chrome build, downloading with synchronization if needed."""
    major_str = chrome_version.split(".")[0]
    if not major_str.isdigit():
        return None
    major = int(major_str)

    cache_dir = get_chromedriver_cache_dir()
    target = cache_dir / f"chromedriver-{major}.exe"

    # Fast-path: recheck existing cache without lock
    if target.is_file() and target.stat().st_size >= MIN_DRIVER_BYTES:
        return target

    # Coordinate provisioning per major version across threads and processes
    with ProvisioningLock(major, cache_dir):
        # Recheck after acquiring lock in case another worker provisioned it
        if target.is_file() and target.stat().st_size >= MIN_DRIVER_BYTES:
            return target

        unique_id = uuid.uuid4().hex[:12]
        archive = cache_dir / f"download_{major}_{unique_id}.tmp"
        try:
            for url in _chromedriver_url_candidates(chrome_version):
                source = url.split("/")[2]
                try:
                    _fetch_url(url, archive)
                    if archive.stat().st_size < MIN_DRIVER_BYTES:
                        raise RuntimeError("incomplete download")
                    if _extract_driver_exe(archive, target, unique_id=unique_id):
                        emit(log_queue, f"chromedriver {major} cached at {target.name} (via {source}).")
                        return target
                    emit(log_queue, f"chromedriver archive from {source} has no driver binary.", logging.WARNING)
                except Exception as exc:
                    emit(log_queue, f"chromedriver source {source} failed: {exc}", logging.WARNING)
        finally:
            archive.unlink(missing_ok=True)

    return None


def publish_driver_to_cache(source_path: Path, major: int, log_queue: Optional[queue.Queue] = None) -> Optional[Path]:
    """Atomically publish a downloaded driver binary into shared cache using synchronization."""
    if not source_path.is_file() or source_path.stat().st_size < MIN_DRIVER_BYTES:
        return None

    cache_dir = get_chromedriver_cache_dir()
    target = cache_dir / f"chromedriver-{major}.exe"

    with ProvisioningLock(major, cache_dir):
        if target.is_file() and target.stat().st_size >= MIN_DRIVER_BYTES:
            return target

        unique_id = uuid.uuid4().hex[:12]
        tmp_target = cache_dir / f"publish_{major}_{unique_id}.tmp"
        try:
            shutil.copy2(source_path, tmp_target)
            if tmp_target.stat().st_size >= MIN_DRIVER_BYTES:
                tmp_target.replace(target)
                emit(log_queue, f"chromedriver {major} cached for offline launches.")
                return target
        finally:
            tmp_target.unlink(missing_ok=True)

    return target if (target.is_file() and target.stat().st_size >= MIN_DRIVER_BYTES) else None


def create_session_driver_copy(cached_driver: Path, profile_dir: Path) -> Path:
    """Create a separate working copy of the driver executable for an individual browser session.

    This keeps the shared cache pristine and prevents a running session's executable
    from being modified or overwritten by another concurrent launch.
    """
    drivers_dir = profile_dir / "driver_bin"
    drivers_dir.mkdir(parents=True, exist_ok=True)
    session_driver = drivers_dir / "chromedriver.exe"

    if not session_driver.exists() or session_driver.stat().st_size != cached_driver.stat().st_size:
        unique_id = uuid.uuid4().hex[:8]
        tmp_driver = drivers_dir / f"driver_{unique_id}.tmp"
        try:
            shutil.copy2(cached_driver, tmp_driver)
            tmp_driver.replace(session_driver)
        finally:
            tmp_driver.unlink(missing_ok=True)

    return session_driver
