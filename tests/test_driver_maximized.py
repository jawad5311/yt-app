"""Unit tests verifying Chrome driver launches maximized for visible browsing sessions."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.automation.driver import setup_driver
from src.models.account import Account


def test_setup_driver_launches_maximized_for_visible_session(tmp_path):
    """Verify visible Chrome browser session configures --start-maximized and calls maximize_window()."""
    acc = Account(email="test@gmail.com", password="pw")
    mock_driver_instance = MagicMock()

    with patch("src.automation.driver.resolve_profile_dir", return_value=tmp_path / "test_prof"), \
         patch("src.automation.driver.get_chrome_version", return_value="120.0.0.0"), \
         patch("src.automation.driver.ensure_chromedriver", return_value=None), \
         patch("undetected_chromedriver.Chrome", return_value=mock_driver_instance) as mock_uc_chrome:

        driver = setup_driver(acc, headless=False)

        # 1. Verify uc.Chrome was initialized
        mock_uc_chrome.assert_called_once()
        call_kwargs = mock_uc_chrome.call_args[1]
        options = call_kwargs["options"]

        # 2. Check Chrome argument contains --start-maximized and NOT 1280,800
        args = getattr(options, "arguments", [])
        assert "--start-maximized" in args
        assert not any("--window-size=1280,800" in a for a in args)

        # 3. Verify driver.maximize_window() was called to ensure full screen coverage
        mock_driver_instance.maximize_window.assert_called_once()

        # Clean up lock
        if hasattr(driver, "_profile_lock"):
            driver._profile_lock.release()




def test_setup_driver_unheadless_enforces_start_maximized(tmp_path):
    """Verify Chrome session always runs unheadless with --start-maximized and maximize_browser_window."""
    acc = Account(email="test_unheadless@gmail.com", password="pw")
    mock_driver_instance = MagicMock()

    with patch("src.automation.driver.resolve_profile_dir", return_value=tmp_path / "test_prof_unheadless"), \
         patch("src.automation.driver.get_chrome_version", return_value="120.0.0.0"), \
         patch("src.automation.driver.ensure_chromedriver", return_value=None), \
         patch("undetected_chromedriver.Chrome", return_value=mock_driver_instance) as mock_uc_chrome:

        driver = setup_driver(acc, headless=True, use_system_profile=False)

        call_kwargs = mock_uc_chrome.call_args[1]
        options = call_kwargs["options"]
        args = getattr(options, "arguments", [])

        assert "--headless=new" not in args
        assert "--headless" not in args
        assert "--start-maximized" in args

        mock_driver_instance.maximize_window.assert_called_once()

        if hasattr(driver, "_profile_lock"):
            driver._profile_lock.release()


def test_maximize_browser_window_handles_none_and_exceptions():
    from src.automation.driver import maximize_browser_window

    # None driver is handled gracefully
    maximize_browser_window(None)

    # Driver with failing maximize_window is handled gracefully
    failing_driver = MagicMock()
    failing_driver.maximize_window.side_effect = Exception("maximize failed")
    maximize_browser_window(failing_driver)
    failing_driver.maximize_window.assert_called_once()


def test_maximize_browser_window_invokes_driver_maximize():
    from src.automation.driver import maximize_browser_window

    mock_driver = MagicMock()
    mock_driver.browser_pid = 12345

    with patch("ctypes.windll.user32.EnumWindows", return_value=True):
        maximize_browser_window(mock_driver)

    mock_driver.maximize_window.assert_called_once()


def test_setup_driver_retry_on_cached_driver_failure_creates_fresh_options(tmp_path):
    """Verify that when the cached driver fails on first launch, uc.Chrome retries with fresh options."""
    acc = Account(email="retry_test@gmail.com", password="pw")
    mock_driver_instance = MagicMock()
    mock_driver_instance.patcher.executable_path = str(tmp_path / "downloaded_chromedriver.exe")

    options_seen = []

    def mock_uc_chrome(options=None, **kwargs):
        options_seen.append(options)
        if len(options_seen) == 1:
            # Simulate uc marking options as used before failing
            options._session = "first_attempt_session"
            raise RuntimeError("Cached chromedriver rejected by system")
        # Second attempt receives fresh options without _session!
        if getattr(options, "_session", None) is not None:
            raise RuntimeError("you cannot reuse the ChromeOptions object")
        return mock_driver_instance

    with patch("src.automation.driver.resolve_profile_dir", return_value=tmp_path / "test_prof_retry"), \
         patch("src.automation.driver.get_chrome_version", return_value="157.0.8103.80"), \
         patch("src.automation.driver.ensure_chromedriver", return_value=tmp_path / "cached_chromedriver.exe"), \
         patch("src.automation.driver.create_session_driver_copy", return_value=tmp_path / "session_driver.exe"), \
         patch("undetected_chromedriver.Chrome", side_effect=mock_uc_chrome):

        driver = setup_driver(acc, headless=False, use_system_profile=False)

        assert len(options_seen) == 2
        assert options_seen[0]._session == "first_attempt_session"
        assert getattr(options_seen[1], "_session", None) is None
        assert driver == mock_driver_instance

        if hasattr(driver, "_profile_lock"):
            driver._profile_lock.release()


