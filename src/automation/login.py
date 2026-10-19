import logging
import queue
import threading
import time
from typing import Any, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..models.account import Account
from ..utils.helpers import human_like_delay, slow_type
from ..utils.logger import emit
from .navigation import safe_get


def is_logged_in(driver: Any) -> bool:
    """Check if the current browser session is logged into Google/YouTube."""
    try:
        url = (driver.current_url or "").lower()
        if "myaccount.google.com" in url:
            if "signin" not in url and "accountchooser" not in url and "challenge" not in url:
                return True
        if "youtube.com" in url:
            if "signin" not in url and "accountchooser" not in url and "challenge" not in url:
                # On youtube.com, confirm session by presence of avatar or absence of sign-in button
                try:
                    driver.find_element(By.CSS_SELECTOR, "#avatar-btn, ytd-topbar-menu-button-renderer img")
                    return True
                except Exception:
                    try:
                        driver.find_element(By.XPATH, "//a[contains(@href, 'signin') or contains(., 'Sign in')]")
                        return False
                    except Exception:
                        return True
        source = (driver.page_source or "").lower()
        if "myaccount.google.com" in source and "signin" not in source and "accountchooser" not in source:
            return True
    except Exception:
        pass
    return False


def _handle_account_chooser(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """
    If Google displays the 'Choose an account' (accountchooser) screen,
    select the matching account tile or click 'Use another account'.
    """
    try:
        url = (driver.current_url or "").lower()
        page_source = (driver.page_source or "").lower()
        if "accountchooser" not in url and "choose an account" not in page_source:
            return False

        emit(log_queue, f"Account chooser screen detected for {account.email}.")
        account_email_lower = account.email.lower()

        account_xpath = (
            f"//*[@data-identifier='{account.email}'] | "
            f"//*[@data-email='{account.email}'] | "
            f"//*[translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='{account_email_lower}'] | "
            f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{account_email_lower}')]"
        )

        tile = None
        matches = driver.find_elements(By.XPATH, account_xpath)
        for el in matches:
            try:
                if el.is_displayed():
                    tile = el
                    break
            except Exception:
                continue

        if tile:
            emit(log_queue, f"Selecting saved account {account.email}...")
            try:
                ancestor = tile.find_element(
                    By.XPATH,
                    "./ancestor-or-self::*[@role='link' or @role='button' or @data-identifier or self::li][1]",
                )
                if ancestor and ancestor.is_displayed():
                    tile = ancestor
            except Exception:
                pass

            try:
                tile.click()
            except Exception:
                driver.execute_script("arguments[0].click();", tile)
            human_like_delay(2.0, 3.5)
            return True

        # Account not listed in chooser -> Click 'Use another account'
        emit(log_queue, f"Account {account.email} not in chooser list; clicking 'Use another account'...")
        use_another = driver.find_elements(
            By.XPATH,
            "//*[contains(text(), 'Use another account') or contains(text(), 'Ein weiteres')] | //div[@id='identifierLink']",
        )
        for btn in use_another:
            try:
                if btn.is_displayed():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    human_like_delay(2.0, 3.0)
                    return True
            except Exception:
                continue

    except Exception as exc:
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Notice during account chooser resolution: {err_msg}", logging.DEBUG)

    return False


def _handle_email_input(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Enter email if email input is present on screen."""
    try:
        email_elements = driver.find_elements(
            By.XPATH,
            "//input[@id='identifierId'] | //input[@name='identifier'] | //input[@aria-label='Email or phone'] | //input[@type='email']",
        )
        email_input = None
        for el in email_elements:
            try:
                if el.is_displayed():
                    email_input = el
                    break
            except Exception:
                continue

        if not email_input:
            return False

        emit(log_queue, f"Entering email for {account.email}...")
        email_input.click()
        email_input.clear()
        slow_type(email_input, account.email)
        human_like_delay(0.5, 1.0)

        try:
            next_btn = driver.find_element(
                By.XPATH,
                "//button[contains(., 'Next') or contains(., 'weiter') or @id='identifierNext'] | //div[@id='identifierNext']//button",
            )
            next_btn.click()
        except Exception:
            email_input.send_keys(Keys.ENTER)

        human_like_delay(2.5, 4.0)
        return True
    except Exception as exc:
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Notice during email input: {err_msg}", logging.DEBUG)
        return False


def _handle_password_input(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Enter password if password input is present or reachable."""
    try:
        # If Passkey / 'Try another way' prompt is shown, attempt selecting password
        try:
            try_another_buttons = driver.find_elements(
                By.XPATH,
                "//button[contains(., 'Try another way')] | //*[text()='Try another way']",
            )
            for btn in try_another_buttons:
                if btn.is_displayed():
                    emit(log_queue, "Passkey prompt detected; choosing password option...")
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    human_like_delay(1.5, 2.5)
                    pwd_choice = driver.find_element(
                        By.XPATH,
                        "//*[contains(text(), 'Enter your password') or contains(text(), 'Passwort eingeben')]",
                    )
                    try:
                        pwd_choice.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", pwd_choice)
                    human_like_delay(1.5, 2.5)
                    break
        except Exception:
            pass

        # Wait up to 15s for password field
        pwd_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//input[@type='password' or @name='Passwd' or @name='password']")
            )
        )
        emit(log_queue, f"Entering password for {account.email}...")
        pwd_input.click()
        pwd_input.clear()
        slow_type(pwd_input, account.password)
        human_like_delay(0.5, 1.0)

        try:
            pwd_next = driver.find_element(
                By.XPATH,
                "//button[contains(., 'Next') or @id='passwordNext'] | //div[@id='passwordNext']//button",
            )
            pwd_next.click()
        except Exception:
            pwd_input.send_keys(Keys.ENTER)

        human_like_delay(3.0, 5.0)
        return True
    except Exception as exc:
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Password input not completed automatically: {err_msg}", logging.DEBUG)
        return False


def _handle_post_login_prompts(driver: Any, log_queue: Optional[queue.Queue] = None) -> None:
    """Dismiss common post-login prompts like 'Not now' on recovery info or passkeys."""
    try:
        dismiss_candidates = driver.find_elements(
            By.XPATH,
            "//button[contains(., 'Not now')] | //*[text()='Not now'] | "
            "//button[contains(., 'Cancel')] | //button[contains(., 'Ask me later')]",
        )
        for btn in dismiss_candidates:
            try:
                if btn.is_displayed():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    human_like_delay(1.0, 2.0)
                    break
            except Exception:
                continue
    except Exception:
        pass


def perform_google_login(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """
    Perform Google sign-in or verify existing active session:
    1. Navigate to https://accounts.google.com/
    2. Check if already signed in (redirect to myaccount.google.com)
    3. If on Account Chooser screen, click the matching account tile or 'Use another account'
    4. If email input is present, enter credentials automatically
    5. Enter password when requested
    6. Wait for verification / manual CAPTCHA / 2FA completion if required
    7. Redirect browser to https://www.youtube.com/
    """
    emit(log_queue, f"Checking Google sign-in status for {account.email}...")

    try:
        safe_get(driver, "https://accounts.google.com/", timeout=30, log_queue=log_queue)
    except Exception:
        pass

    # Pause up to 10 seconds while monitoring if page redirects automatically to myaccount.google.com
    start_wait = time.time()
    while time.time() - start_wait < 10.0:
        if is_logged_in(driver):
            emit(log_queue, f"Account {account.email} is already signed in! Redirecting to YouTube...")
            safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
            human_like_delay(2.0, 3.0)
            return True
        time.sleep(1.0)

    emit(log_queue, f"Attempting sign-in for {account.email}...")

    try:
        # 1. Handle Account Chooser ('Choose an account' / accountchooser)
        _handle_account_chooser(driver, account, log_queue)
        time.sleep(1.0)

        # 2. Handle Email input (if visible/needed)
        _handle_email_input(driver, account, log_queue)

        # 3. Handle Password input
        _handle_password_input(driver, account, log_queue)

        # 4. Handle Post-login prompts ('Not now', recovery reminders)
        _handle_post_login_prompts(driver, log_queue)

    except Exception as exc:
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Automatic login input notice: {err_msg}", logging.WARNING)

    # Wait loop for login completion or manual CAPTCHA / 2FA completion
    end_time = time.time() + 180  # wait up to 3 mins for user manual challenge if needed
    challenge_logged = False

    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Login wait cancelled by user.")
            return False

        if is_logged_in(driver):
            emit(log_queue, f"Login successful for {account.email}! Redirecting to YouTube...")
            safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
            human_like_delay(2.0, 3.0)
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
