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


def test_setup_driver_headless_uses_custom_dimensions_without_maximize(tmp_path):
    """Verify headless Chrome session uses high-res window size and skips maximize_window()."""
    acc = Account(email="test_headless@gmail.com", password="pw")
    mock_driver_instance = MagicMock()

    with patch("src.automation.driver.resolve_profile_dir", return_value=tmp_path / "test_prof_headless"), \
         patch("src.automation.driver.get_chrome_version", return_value="120.0.0.0"), \
         patch("src.automation.driver.ensure_chromedriver", return_value=None), \
         patch("undetected_chromedriver.Chrome", return_value=mock_driver_instance) as mock_uc_chrome:

        driver = setup_driver(acc, headless=True)

        call_kwargs = mock_uc_chrome.call_args[1]
        options = call_kwargs["options"]
        args = getattr(options, "arguments", [])

        assert "--headless=new" in args
        assert "--window-size=1920,1080" in args
        assert "--start-maximized" not in args

        # maximize_window is not invoked for headless sessions
        mock_driver_instance.maximize_window.assert_not_called()

        if hasattr(driver, "_profile_lock"):
            driver._profile_lock.release()
