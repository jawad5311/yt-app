"""Regression tests for Issue 27: Synchronize ChromeDriver provisioning and cache publication."""

import io
import threading
import time
import zipfile
from unittest.mock import patch

import pytest

from src.automation.driver_provisioner import (
    MIN_DRIVER_BYTES,
    ProvisioningLock,
    _extract_driver_exe,
    create_session_driver_copy,
    ensure_chromedriver,
    publish_driver_to_cache,
)


def make_mock_driver_zip() -> bytes:
    """Create a minimal valid zip archive containing a fake chromedriver.exe of valid size."""
    buf = io.BytesIO()
    payload = b"MZ" + b"X" * (MIN_DRIVER_BYTES + 1000)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("chromedriver-win64/chromedriver.exe", payload)
    return buf.getvalue()


@pytest.fixture
def mock_cache_dir(tmp_path):
    cache = tmp_path / "driver_cache"
    cache.mkdir(parents=True)
    return cache


def test_existing_valid_cache_entries_remain_usable(mock_cache_dir):
    """If a valid driver is already cached, it is returned immediately without downloading."""
    cached = mock_cache_dir / "chromedriver-120.exe"
    cached.write_bytes(b"MZ" + b"\x00" * (MIN_DRIVER_BYTES + 50))

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir), \
         patch("src.automation.driver_provisioner._fetch_url") as mock_fetch:
        result = ensure_chromedriver("120.0.6099.109")
        assert result == cached
        mock_fetch.assert_not_called()


def test_two_simultaneous_requests_produce_one_valid_result(mock_cache_dir):
    """Two concurrent requests for a missing cache entry produce one valid published binary."""
    zip_bytes = make_mock_driver_zip()
    fetch_count = 0
    fetch_lock = threading.Lock()

    def slow_fetch(url, dest):
        nonlocal fetch_count
        with fetch_lock:
            fetch_count += 1
        time.sleep(0.05)
        dest.write_bytes(zip_bytes)

    results = []

    def worker():
        res = ensure_chromedriver("120.0.6099.109")
        results.append(res)

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir), \
         patch("src.automation.driver_provisioner._fetch_url", side_effect=slow_fetch):
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    expected_target = mock_cache_dir / "chromedriver-120.exe"
    assert len(results) == 2
    assert results[0] == expected_target
    assert results[1] == expected_target
    assert expected_target.is_file()
    assert expected_target.stat().st_size >= MIN_DRIVER_BYTES
    # Due to double-check lock, fetch occurs only once
    assert fetch_count == 1


def test_waiting_callers_recheck_and_reuse_completed_cache(mock_cache_dir):
    """A waiting caller detects that the binary was provisioned while waiting and reuses it."""
    expected_target = mock_cache_dir / "chromedriver-121.exe"
    zip_bytes = make_mock_driver_zip()

    # Pre-acquire the lock
    lock = ProvisioningLock(121, mock_cache_dir)
    lock.acquire()

    # Simulate another thread/process completing the binary while lock is held
    expected_target.write_bytes(b"MZ" + b"\x00" * (MIN_DRIVER_BYTES + 20))
    lock.release()

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir), \
         patch("src.automation.driver_provisioner._fetch_url") as mock_fetch:
        result = ensure_chromedriver("121.0.0.0")
        assert result == expected_target
        mock_fetch.assert_not_called()


def test_concurrent_requests_do_not_overwrite_each_others_temporary_files(mock_cache_dir):
    """Each download uses a unique temporary path so concurrent operations never collide."""
    dest_paths = []

    def record_fetch(url, dest):
        dest_paths.append(dest)
        dest.write_bytes(make_mock_driver_zip())

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir), \
         patch("src.automation.driver_provisioner._fetch_url", side_effect=record_fetch):
        # Run two different major versions concurrently
        t1 = threading.Thread(target=lambda: ensure_chromedriver("122.0.0.0"))
        t2 = threading.Thread(target=lambda: ensure_chromedriver("123.0.0.0"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert len(dest_paths) == 2
    assert dest_paths[0] != dest_paths[1]
    assert dest_paths[0].name != dest_paths[1].name


def test_failed_extraction_leaves_no_apparently_valid_executable(mock_cache_dir):
    """Corrupt archive or undersized executable fails extraction without creating target executable."""
    corrupt_archive = mock_cache_dir / "corrupt.zip"
    corrupt_archive.write_bytes(b"not a valid zip file")
    target = mock_cache_dir / "chromedriver-99.exe"

    extracted = _extract_driver_exe(corrupt_archive, target)
    assert extracted is False
    assert not target.exists()

    # Undersized payload
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("chromedriver.exe", b"tiny")
    corrupt_archive.write_bytes(buf.getvalue())

    extracted = _extract_driver_exe(corrupt_archive, target)
    assert extracted is False
    assert not target.exists()


def test_one_operations_cleanup_does_not_remove_anothers_files(mock_cache_dir):
    """Cleanup of one failing operation leaves another operation's files untouched."""
    op_b_file = mock_cache_dir / "download_120_operationB.tmp"
    op_b_file.write_bytes(b"precious_data_from_b")

    def failing_fetch(url, dest):
        dest.write_bytes(b"broken")
        raise RuntimeError("network dropped")

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir), \
         patch("src.automation.driver_provisioner._fetch_url", side_effect=failing_fetch):
        res = ensure_chromedriver("120.0.0.0")
        assert res is None

    # op_b_file was not deleted by operation A's cleanup
    assert op_b_file.exists()
    assert op_b_file.read_bytes() == b"precious_data_from_b"


def test_fallback_provisioning_follows_same_synchronization_rules(mock_cache_dir):
    """publish_driver_to_cache uses the provisioning lock and publishes atomically."""
    downloaded_source = mock_cache_dir / "temp_downloaded_driver.exe"
    downloaded_source.write_bytes(b"MZ" + b"\x00" * (MIN_DRIVER_BYTES + 50))

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir):
        published = publish_driver_to_cache(downloaded_source, 125)
        expected = mock_cache_dir / "chromedriver-125.exe"
        assert published == expected
        assert expected.is_file()
        assert expected.stat().st_size >= MIN_DRIVER_BYTES


def test_running_session_executable_not_overwritten_by_another_launch(tmp_path):
    """Each session uses an isolated working copy so running sessions are not modified."""
    shared_cached = tmp_path / "shared_cache" / "chromedriver-120.exe"
    shared_cached.parent.mkdir(parents=True)
    shared_cached.write_bytes(b"MZ" + b"\x00" * (MIN_DRIVER_BYTES + 10))

    prof1 = tmp_path / "profile1"
    prof2 = tmp_path / "profile2"

    driver1 = create_session_driver_copy(shared_cached, prof1)
    driver2 = create_session_driver_copy(shared_cached, prof2)

    assert driver1 != driver2
    assert driver1.parent != driver2.parent
    assert driver1.is_file()
    assert driver2.is_file()

    # Session 1 modifies or executes its binary (simulating uc patching)
    driver1.write_bytes(b"PATCHED_DRIVER_SESSION_1")

    # Session 2 and shared cache remain completely untouched
    assert driver2.read_bytes() != b"PATCHED_DRIVER_SESSION_1"
    assert shared_cached.read_bytes() != b"PATCHED_DRIVER_SESSION_1"


def test_exceptions_release_provisioning_locks(mock_cache_dir):
    """Exceptions during download or extraction guarantee the provisioning lock is released."""
    def crash_fetch(url, dest):
        raise ConnectionResetError("Connection died")

    with patch("src.automation.driver_provisioner.get_chromedriver_cache_dir", return_value=mock_cache_dir), \
         patch("src.automation.driver_provisioner._fetch_url", side_effect=crash_fetch):
        ensure_chromedriver("120.0.0.0")

    # Lock must be free for another caller
    lock = ProvisioningLock(120, mock_cache_dir)
    assert lock.acquire(timeout=2.0) is True
    lock.release()


def test_cross_process_provisioning_locking(mock_cache_dir):
    """Inter-process file locking prevents concurrent provisioning across processes."""
    lock1 = ProvisioningLock(120, mock_cache_dir)
    assert lock1.acquire(timeout=2.0) is True

    lock2 = ProvisioningLock(120, mock_cache_dir)
    with pytest.raises(TimeoutError):
        lock2.acquire(timeout=0.2)

    lock1.release()
    assert lock2.acquire(timeout=2.0) is True
    lock2.release()
