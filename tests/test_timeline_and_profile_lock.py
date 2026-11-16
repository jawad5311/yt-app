"""Tests for 3-minute timeline start check and ProfileLock thread death recovery."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.automation.profile_manager import ProfileBusyError, ProfileLock
from src.automation.youtube_actions import (
    ENSURE_VIDEO_AT_START_JS,
    ensure_video_at_start,
)


def test_ensure_video_at_start_skips_when_at_or_under_3_minutes():
    """Video starting at <= 3 minutes (180s) must NOT be rewound."""
    driver = MagicMock()
    # Mock JS returning found without rewinding because position is 120s (<= 180s)
    driver.execute_script.return_value = {
        "found": True,
        "is_ad": False,
        "rewound": False,
        "currentTime": 120.0,
        "threshold": 180.0,
    }

    rewound = ensure_video_at_start(driver, threshold_seconds=180.0)
    assert rewound is False
    driver.execute_script.assert_called_once_with(ENSURE_VIDEO_AT_START_JS, False, 180.0)


def test_ensure_video_at_start_rewinds_when_over_3_minutes():
    """Video starting at > 3 minutes (180s) must be rewound to 0:00."""
    driver = MagicMock()
    driver.execute_script.return_value = {
        "found": True,
        "is_ad": False,
        "rewound": True,
        "previousTime": 240.0,
        "currentTime": 0.0,
    }

    rewound = ensure_video_at_start(driver, threshold_seconds=180.0)
    assert rewound is True
    assert driver.execute_script.call_args_list[0][0] == (ENSURE_VIDEO_AT_START_JS, False, 180.0)


def test_profile_lock_reclaims_when_owner_thread_dies(tmp_path: Path):
    """When a thread that acquired a ProfileLock dies without release, the next thread safely reclaims it."""
    profile_dir = tmp_path / "test_profile"
    profile_dir.mkdir()

    error_box = []

    def dead_thread_worker():
        try:
            lock = ProfileLock(profile_dir)
            lock.acquire()
            # Thread finishes and terminates without calling lock.release()
        except Exception as e:
            error_box.append(e)

    t = threading.Thread(target=dead_thread_worker)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert len(error_box) == 0

    # Main thread now attempts to acquire the profile lock
    main_lock = ProfileLock(profile_dir)
    # Previously this raised ProfileBusyError ("currently in use by another session")
    # Now it reclaims the orphaned lock from the dead thread!
    acquired = main_lock.acquire(timeout=0.5)
    assert acquired is True
    main_lock.release()
