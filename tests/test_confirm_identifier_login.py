"""Tests for Google sign-in confirm identifier screen and subsequent password entry."""

import queue
import threading
from unittest.mock import MagicMock, call, patch

import pytest
from selenium.webdriver.common.by import By

from src.automation.login import (
    LoginState,
    _handle_confirm_identifier,
    _handle_password_input,
    _perform_automated_login_steps,
    check_login_state,
    detect_manual_challenge,
    perform_google_login,
)
from src.models.account import Account


@pytest.fixture(autouse=True)
def mock_delays():
    with patch("src.automation.login.human_like_delay"), \
         patch("src.automation.login.time.sleep"):
        yield


def test_handle_confirm_identifier_detects_url_and_clicks_next():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/confirmidentifier?continue=https%3A%2F%2Fmail.google.com"
    driver.page_source = "<html><body>Confirm your account</body></html>"

    account = Account(email="chillecode@gmail.com", password="secret_password")
    next_btn = MagicMock()
    next_btn.is_displayed.return_value = True
    driver.find_elements.return_value = [next_btn]

    q = queue.Queue()
    res = _handle_confirm_identifier(driver, account, log_queue=q)

    assert res is True
    next_btn.click.assert_called_once()


def test_handle_confirm_identifier_page_source_fallback():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/signin/v2/identifier"
    driver.page_source = '<div id="identifierNext"><button>Next</button></div>'

    account = Account(email="chillecode@gmail.com", password="secret_password")
    next_btn = MagicMock()
    next_btn.is_displayed.return_value = True
    driver.find_elements.return_value = [next_btn]

    res = _handle_confirm_identifier(driver, account)
    assert res is True
    next_btn.click.assert_called_once()


def test_handle_confirm_identifier_uses_js_click_fallback():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/confirmidentifier"
    driver.page_source = "<html><body>Confirm identifier</body></html>"

    account = Account(email="chillecode@gmail.com", password="secret_password")
    next_btn = MagicMock()
    next_btn.is_displayed.return_value = True
    next_btn.click.side_effect = Exception("Element click intercepted")
    driver.find_elements.return_value = [next_btn]

    res = _handle_confirm_identifier(driver, account)
    assert res is True
    driver.execute_script.assert_called()


def test_handle_confirm_identifier_returns_false_when_not_confirm_identifier():
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    driver.page_source = "<html><body>YouTube Home</body></html>"

    account = Account(email="chillecode@gmail.com", password="secret_password")
    assert _handle_confirm_identifier(driver, account) is False


def test_detect_manual_challenge_ignores_confirm_identifier():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/confirmidentifier"
    driver.page_source = "<html><body>To continue, first verify it's you<div id='identifierNext'></div></body></html>"

    assert detect_manual_challenge(driver) is None


def test_detect_manual_challenge_ignores_challenge_pwd():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/challenge/pwd"
    driver.page_source = "<html><body>Hi Chill <input type='password' name='Passwd'></body></html>"

    assert detect_manual_challenge(driver) is None


def test_detect_manual_challenge_detects_wrong_password_on_challenge_pwd():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/challenge/pwd"
    driver.page_source = "<html><body><input type='password'>Wrong password. Try again or click Forgot password</body></html>"

    res = detect_manual_challenge(driver)
    assert res is not None
    alert_type, det = res
    assert "Incorrect Password" in alert_type
    assert "rejected the password" in det


def test_check_login_state_treats_confirm_identifier_and_challenge_pwd_as_signed_out():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/confirmidentifier"
    assert check_login_state(driver) == LoginState.SIGNED_OUT

    driver.current_url = "https://accounts.google.com/v3/signin/challenge/pwd"
    assert check_login_state(driver) == LoginState.SIGNED_OUT


def test_perform_google_login_transitions_through_confirm_identifier_and_password():
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/confirmidentifier"
    driver.page_source = "<html><body><div id='identifierNext'><button>Next</button></div></body></html>"

    account = Account(email="chillecode@gmail.com", password="my_password")
    q = queue.Queue()

    # When confirm identifier Next is clicked, page transitions to challenge/pwd
    next_btn = MagicMock()
    next_btn.is_displayed.return_value = True

    pwd_input = MagicMock()
    pwd_input.is_displayed.return_value = True

    pwd_next_btn = MagicMock()
    pwd_next_btn.is_displayed.return_value = True

    step = 0
    def mock_find_elements(by, value):
        nonlocal step
        if "identifierNext" in value:
            return [next_btn]
        if "type='password'" in value:
            return [pwd_input]
        return []

    def on_confirm_next_click():
        nonlocal step
        step = 1
        driver.current_url = "https://accounts.google.com/v3/signin/challenge/pwd"
        driver.page_source = "<html><body><input type='password' name='Passwd'></body></html>"

    next_btn.click.side_effect = on_confirm_next_click

    def on_pwd_next_click():
        nonlocal step
        step = 2
        driver.current_url = "https://myaccount.google.com/"
        driver.page_source = "<html><body>Welcome to your Google Account chillecode@gmail.com</body></html>"

    pwd_next_btn.click.side_effect = on_pwd_next_click

    def mock_find_element(by, value):
        if "passwordNext" in value or "Next" in value:
            return pwd_next_btn
        return MagicMock()

    driver.find_elements.side_effect = mock_find_elements
    driver.find_element.side_effect = mock_find_element

    with patch("src.automation.login.WebDriverWait") as mock_wait, \
         patch("src.automation.login.slow_type") as mock_slow_type, \
         patch("src.automation.login.safe_get") as mock_safe_get:
        mock_wait.return_value.until.return_value = pwd_input

        res = perform_google_login(driver, account, log_queue=q)

        assert res is True
        assert next_btn.click.called
        assert pwd_input.click.called
        mock_slow_type.assert_called_once_with(pwd_input, "my_password")
        assert pwd_next_btn.click.called
        mock_safe_get.assert_called_with(driver, "https://www.youtube.com/", timeout=30, log_queue=q)
