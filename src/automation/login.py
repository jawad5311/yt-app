import logging
import queue
import threading
import time
from typing import Any, Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from ..models.account import Account
from ..utils.helpers import human_like_delay, slow_type
from ..utils.logger import emit


def is_logged_in(driver: Any) -> bool:
    """Check if the current browser session is logged into Google/YouTube."""
    try:
        url = (driver.current_url or "").lower()
        if "myaccount.google.com" in url or "youtube.com" in url:
            if "signin" not in url and "accountchooser" not in url and "challenge" not in url:
                return True
        source = (driver.page_source or "").lower()
        if "myaccount.google.com" in source:
            return True
    except Exception:
        pass
    return False


def perform_google_login(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Perform Google sign-in or verify existing active session."""
    emit(log_queue, f"Checking Google sign-in status for {account.email}...")

    driver.get("https://accounts.google.com/")
    human_like_delay(1.5, 2.5)

    if is_logged_in(driver):
        emit(log_queue, f"Account {account.email} is already signed in!")
        return True

    emit(log_queue, f"Attempting sign-in for {account.email}...")

    try:
        # Email field
        email_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//input[@type='email' or @name='identifier']"))
        )
        email_input.clear()
        slow_type(email_input, account.email)
        human_like_delay(0.5, 1.0)
        
        # Click Next
        next_btn = driver.find_element(By.XPATH, "//button[contains(., 'Next') or contains(., 'weiter') or @id='identifierNext']")
        next_btn.click()
        human_like_delay(2.0, 3.5)

        # Password field
        pwd_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//input[@type='password' or @name='Passwd']"))
        )
        pwd_input.clear()
        slow_type(pwd_input, account.password)
        human_like_delay(0.5, 1.0)

        # Click Next
        pwd_next = driver.find_element(By.XPATH, "//button[contains(., 'Next') or @id='passwordNext']")
        pwd_next.click()
        human_like_delay(3.0, 5.0)

    except Exception as exc:
        emit(log_queue, f"Automatic login input phase hit notice/checkpoint: {exc}", logging.WARNING)

    # Wait loop for login completion or manual CAPTCHA / 2FA completion
    end_time = time.time() + 180  # wait up to 3 mins for user manual challenge if needed
    challenge_logged = False

    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Login wait cancelled by user.")
            return False

        if is_logged_in(driver):
            emit(log_queue, f"Login successful for {account.email}!")
            return True

        url = (driver.current_url or "").lower()
        if not challenge_logged and ("challenge" in url or "captcha" in url or "2step" in url):
            emit(
                log_queue,
                "Google requested CAPTCHA / 2-Step Verification! Please complete it manually in the browser window.",
                logging.WARNING,
            )
            challenge_logged = True

        time.sleep(2)

    emit(log_queue, f"Sign-in timed out for {account.email}.", logging.ERROR)
    return False
