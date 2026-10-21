import queue
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.automation.login import (
    _handle_account_chooser,
    _handle_email_input,
    _handle_password_input,
    is_logged_in,
    perform_google_login,
)
from src.models.account import Account


@pytest.fixture(autouse=True)
def mock_delays():
    with patch("src.automation.login.human_like_delay"), \
         patch("src.automation.login.time.sleep"):
        yield


def test_is_logged_in_myaccount_success():
    driver = MagicMock()
    driver.current_url = "https://myaccount.google.com/intro"
    driver.page_source = "Welcome to your Google Account"
    assert is_logged_in(driver) is True


def test_is_logged_in_accountchooser_false():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/accountchooser?continue=..."
    driver.page_source = "Choose an account"
    assert is_logged_in(driver) is False


def test_is_logged_in_youtube_with_avatar():
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    avatar_elem = MagicMock()
    driver.find_element.return_value = avatar_elem
    assert is_logged_in(driver) is True


def test_handle_account_chooser_selects_matching_account():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/accountchooser"
    driver.page_source = "Choose an account"

    account = Account(email="test@gmail.com", password="password123")
    tile_mock = MagicMock()
    tile_mock.is_displayed.return_value = True

    ancestor_mock = MagicMock()
    ancestor_mock.is_displayed.return_value = True
    tile_mock.find_element.return_value = ancestor_mock

    driver.find_elements.return_value = [tile_mock]

    result = _handle_account_chooser(driver, account)
    assert result is True
    ancestor_mock.click.assert_called_once()


def test_handle_account_chooser_clicks_use_another_account():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/accountchooser"
    driver.page_source = "Choose an account"

    account = Account(email="other@gmail.com", password="password123")
    use_another_btn = MagicMock()
    use_another_btn.is_displayed.return_value = True

    # First call: matching account search returns empty list
    # Second call: use_another search returns list with button
    driver.find_elements.side_effect = [[], [use_another_btn]]

    result = _handle_account_chooser(driver, account)
    assert result is True
    use_another_btn.click.assert_called_once()


def test_handle_email_input_types_and_submits():
    driver = MagicMock()
    account = Account(email="test@gmail.com", password="password123")

    email_input = MagicMock()
    email_input.is_displayed.return_value = True
    driver.find_elements.return_value = [email_input]

    next_btn = MagicMock()
    driver.find_element.return_value = next_btn

    with patch("src.automation.login.slow_type") as mock_slow_type:
        result = _handle_email_input(driver, account)
        assert result is True
        mock_slow_type.assert_called_once_with(email_input, "test@gmail.com")
        next_btn.click.assert_called_once()


def test_handle_password_input_types_and_submits():
    driver = MagicMock()
    account = Account(email="test@gmail.com", password="secret_password")

    pwd_input = MagicMock()
    pwd_input.is_displayed.return_value = True

    next_btn = MagicMock()
    driver.find_element.return_value = next_btn

    with patch("src.automation.login.WebDriverWait") as mock_wait, \
         patch("src.automation.login.slow_type") as mock_slow_type:
        mock_wait.return_value.until.return_value = pwd_input
        result = _handle_password_input(driver, account)
        assert result is True
        mock_slow_type.assert_called_once_with(pwd_input, "secret_password")
        next_btn.click.assert_called_once()


def test_perform_google_login_already_logged_in():
    driver = MagicMock()
    driver.current_url = "https://myaccount.google.com/"
    account = Account(email="alreadyin@gmail.com", password="pw")
    q: queue.Queue[Any] = queue.Queue()

    with patch("src.automation.login.safe_get") as mock_safe_get:
        res = perform_google_login(driver, account, log_queue=q)
        assert res is True
        # Redirected to youtube
        mock_safe_get.assert_any_call(driver, "https://www.youtube.com/", timeout=30, log_queue=q)
