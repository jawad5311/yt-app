"""Regression tests for Issue 28: Require positive login and expected-account verification."""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.automation.login import (
    LoginState,
    check_login_state,
    is_logged_in,
    is_trusted_url,
    perform_google_login,
)
from src.automation.worker import AutomationWorker
from src.models.account import Account


@pytest.fixture(autouse=True)
def mock_delays():
    with patch("src.automation.login.human_like_delay"), \
         patch("src.automation.login.time.sleep"):
        yield


def test_incomplete_youtube_page_with_no_relevant_buttons_remains_unknown():
    """Incomplete YouTube page with neither sign-in button nor avatar remains unknown."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    driver.find_element.side_effect = Exception("Element not found")
    driver.find_elements.return_value = []
    driver.page_source = "<html><body>Loading YouTube...</body></html>"

    state = check_login_state(driver)
    assert state == LoginState.UNKNOWN
    assert is_logged_in(driver) is False


def test_explicit_signin_ui_indicates_signed_out():
    """Explicit sign-in UI indicates confirmed signed out."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    signin_btn = MagicMock()
    signin_btn.is_displayed.return_value = True

    # find_element fails for avatar, find_elements returns signin button
    driver.find_element.side_effect = Exception("No avatar")
    driver.find_elements.return_value = [signin_btn]

    state = check_login_state(driver)
    assert state == LoginState.SIGNED_OUT
    assert is_logged_in(driver) is False


def test_generic_avatar_alone_does_not_prove_expected_account():
    """A generic avatar alone without identity confirmation does not prove the requested account."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    avatar_elem = MagicMock()
    avatar_elem.is_displayed.return_value = True
    driver.find_element.return_value = avatar_elem
    # No email information in DOM
    driver.find_elements.return_value = []
    driver.page_source = "<html><body>Generic avatar shown</body></html>"

    expected = Account(email="target@gmail.com", password="pw")
    state = check_login_state(driver, expected)
    assert state == LoginState.UNKNOWN
    assert is_logged_in(driver, expected) is False


def test_confirmed_matching_account_succeeds():
    """Confirmed matching account via visible DOM account information succeeds."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    avatar_elem = MagicMock()
    avatar_elem.is_displayed.return_value = True
    driver.find_element.return_value = avatar_elem

    # Active user button with email
    user_elem = MagicMock()
    user_elem.get_attribute.side_effect = lambda attr: "Google Account: Target.User+tag@gmail.com" if attr == "aria-label" else None
    driver.find_elements.return_value = [user_elem]

    expected = Account(email="target.user+tag@gmail.com", password="pw")
    state = check_login_state(driver, expected)
    assert state == LoginState.SIGNED_IN_MATCH
    assert is_logged_in(driver, expected) is True


def test_different_active_account_is_rejected_or_reported():
    """A session logged into a different account is rejected and reported."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    avatar_elem = MagicMock()
    avatar_elem.is_displayed.return_value = True
    driver.find_element.return_value = avatar_elem

    user_elem = MagicMock()
    user_elem.get_attribute.side_effect = lambda attr: "other_user@gmail.com" if attr == "data-identifier" else None
    driver.find_elements.return_value = [user_elem]

    expected = Account(email="my_account@gmail.com", password="pw")
    state = check_login_state(driver, expected)
    assert state == LoginState.SIGNED_IN_OTHER
    assert is_logged_in(driver, expected) is False

    # perform_google_login rejects and does not proceed
    q = queue.Queue()
    with patch("src.automation.login.safe_get"):
        res = perform_google_login(driver, expected, log_queue=q)
        assert res is False


def test_account_identifiers_compare_using_shared_normalization_policy():
    """Account identifiers normalize whitespace and casing consistently without discarding dots or tags."""
    driver = MagicMock()
    driver.current_url = "https://www.youtube.com/"
    avatar_elem = MagicMock()
    avatar_elem.is_displayed.return_value = True
    driver.find_element.return_value = avatar_elem

    user_elem = MagicMock()
    user_elem.get_attribute.side_effect = lambda attr: "first.last+promo@gmail.com" if attr == "data-identifier" else None
    driver.find_elements.return_value = [user_elem]

    # Expected account has irregular casing and whitespace
    expected = Account(email="  FIRST.LAST+promo@GMAIL.COM  ", password="pw")
    assert check_login_state(driver, expected) == LoginState.SIGNED_IN_MATCH


def test_unrelated_hosts_containing_trusted_domain_text_are_rejected():
    """Unrelated hosts containing trusted-domain strings in query or path are rejected."""
    assert is_trusted_url("https://evil.com/phish?target=https://myaccount.google.com") is False
    assert is_trusted_url("https://youtube.com.attacker.com/") is False
    assert is_trusted_url("https://www.youtube.com/watch?v=123") is True
    assert is_trusted_url("https://accounts.google.com/signin") is True

    driver = MagicMock()
    driver.current_url = "https://evil.com/fake?ref=myaccount.google.com"
    assert check_login_state(driver) == LoginState.UNKNOWN


def test_captcha_or_two_step_verification_does_not_count_as_successful_login():
    """CAPTCHA or 2FA challenge URLs are recognized as pending/unauthenticated."""
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/challenge/pwd"
    assert check_login_state(driver) == LoginState.UNKNOWN
    assert is_logged_in(driver) is False

    driver.current_url = "https://accounts.google.com/signin/v2/challenge/selection"
    assert check_login_state(driver) == LoginState.UNKNOWN

    driver.current_url = "https://accounts.google.com/captcha"
    assert check_login_state(driver) == LoginState.UNKNOWN


def test_verification_timeout_produces_clear_failure():
    """Timeout during login verification returns clear failure."""
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/identifier"
    account = Account(email="timeout@gmail.com", password="pw")
    q = queue.Queue()

    curr_time = [1000.0]

    def advance_time():
        curr_time[0] += 50.0
        return curr_time[0]

    with patch("src.automation.login.time.time", side_effect=advance_time), \
         patch("src.automation.login.safe_get"):
        res = perform_google_login(driver, account, log_queue=q)
        assert res is False


def test_stop_interrupts_verification_wait():
    """Stop event immediately interrupts verification wait."""
    driver = MagicMock()
    driver.current_url = "https://accounts.google.com/v3/signin/identifier"
    account = Account(email="user@gmail.com", password="pw")
    stop_event = threading.Event()
    stop_event.set()

    with patch("src.automation.login.safe_get"):
        res = perform_google_login(driver, account, stop_event=stop_event)
        assert res is False


def test_failed_verification_prevents_task_execution():
    """Failed login verification halts the worker without starting any video tasks."""
    account = Account(email="fail@gmail.com", password="pw")
    config_mgr = MagicMock()
    stats_store = MagicMock()
    dispatcher = MagicMock()
    stop_event = threading.Event()

    worker = AutomationWorker(
        account=account,
        video_tasks=[],
        config_mgr=config_mgr,
        stop_event=stop_event,
        stats_store=stats_store,
        dispatcher=dispatcher,
    )

    with patch("src.automation.worker.setup_driver") as mock_setup, \
         patch("src.automation.worker.perform_google_login", return_value=False), \
         patch("src.automation.worker.close_driver"):
        mock_driver = MagicMock()
        mock_setup.return_value = mock_driver

        worker.run()

        # Task dispatcher was never asked for tasks
        dispatcher.get_next_task.assert_not_called()
