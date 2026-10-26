import logging
import queue
import re
import threading
import time
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..models.account import Account, normalize_account_id
from ..utils.helpers import human_like_delay, slow_type
from ..utils.logger import emit
from .navigation import safe_get


class LoginState(Enum):
    """Possible outcomes when evaluating session authentication state."""
    UNKNOWN = "unknown"
    SIGNED_OUT = "signed_out"
    SIGNED_IN_MATCH = "signed_in_match"
    SIGNED_IN_OTHER = "signed_in_other"


TRUSTED_DOMAINS = ("google.com", "youtube.com")
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def is_trusted_url(url: str) -> bool:
    """Verify that a URL belongs strictly to a trusted Google or YouTube host."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        return any(hostname == d or hostname.endswith("." + d) for d in TRUSTED_DOMAINS)
    except Exception:
        return False


def extract_active_account_identifier(driver: Any) -> Optional[str]:
    """Inspect visible DOM elements to extract the active signed-in Google account identifier."""
    # 1. Search for data-identifier or data-email
    try:
        elements = driver.find_elements(By.XPATH, "//*[@data-identifier or @data-email]")
        for el in elements:
            try:
                ident = el.get_attribute("data-identifier") or el.get_attribute("data-email")
                if ident and isinstance(ident, str) and "@" in ident:
                    return normalize_account_id(ident)
            except Exception:
                continue
    except Exception:
        pass

    # 2. Search for aria-label or title containing an email address
    try:
        elements = driver.find_elements(
            By.XPATH,
            "//*[contains(@aria-label, '@') or contains(@title, '@')]"
        )
        for el in elements:
            try:
                for attr in ("aria-label", "title"):
                    val = el.get_attribute(attr)
                    if val and isinstance(val, str) and "@" in val:
                        matches = EMAIL_PATTERN.findall(val)
                        if matches:
                            return normalize_account_id(matches[0])
            except Exception:
                continue
    except Exception:
        pass

    # 3. Search for visible text elements containing an email address
    try:
        elements = driver.find_elements(
            By.XPATH,
            "//div[contains(text(), '@')] | //span[contains(text(), '@')] | //p[contains(text(), '@')]"
        )
        for el in elements:
            try:
                text = el.text
                if text and isinstance(text, str) and "@" in text:
                    matches = EMAIL_PATTERN.findall(text)
                    if matches:
                        return normalize_account_id(matches[0])
            except Exception:
                continue
    except Exception:
        pass

    return None


def check_login_state(driver: Any, expected_account: Optional[Account] = None) -> LoginState:
    """Evaluate current browser state, distinguishing signed out, matching account, other account, or unknown."""
    try:
        url = getattr(driver, "current_url", "")
        if not isinstance(url, str) or not is_trusted_url(url):
            return LoginState.UNKNOWN

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        full_url_lower = url.lower()

        # Challenges, CAPTCHA, or 2-Step Verification pages do not count as logged in
        if any(c in full_url_lower for c in ("challenge", "captcha", "2step", "speedbump")):
            return LoginState.UNKNOWN

        expected_email = normalize_account_id(expected_account.email) if expected_account else None

        # Check for explicit Google sign-in / account chooser UI
        if "accounts.google.com" in hostname:
            if any(p in path for p in ("/v3/signin", "/signin")):
                return LoginState.SIGNED_OUT
            if "accountchooser" in path:
                return LoginState.SIGNED_OUT

        active_email = extract_active_account_identifier(driver)

        # On myaccount.google.com
        if hostname == "myaccount.google.com" or hostname.endswith(".myaccount.google.com"):
            if expected_email:
                if active_email:
                    if active_email == expected_email:
                        return LoginState.SIGNED_IN_MATCH
                    else:
                        return LoginState.SIGNED_IN_OTHER
                source = getattr(driver, "page_source", "")
                if isinstance(source, str) and expected_email in source.lower():
                    return LoginState.SIGNED_IN_MATCH
                if not active_email:
                    return LoginState.SIGNED_IN_MATCH
                return LoginState.UNKNOWN
            else:
                return LoginState.SIGNED_IN_MATCH

        # On youtube.com
        if "youtube.com" in hostname:
            avatar_present = False
            try:
                el = driver.find_element(By.CSS_SELECTOR, "#avatar-btn, ytd-topbar-menu-button-renderer img")
                if el is not None:
                    is_disp = getattr(el, "is_displayed", None)
                    if is_disp is None or is_disp():
                        avatar_present = True
            except Exception:
                avatar_present = False

            if avatar_present:
                if expected_email:
                    if active_email:
                        if active_email == expected_email:
                            return LoginState.SIGNED_IN_MATCH
                        else:
                            return LoginState.SIGNED_IN_OTHER
                    source = getattr(driver, "page_source", "")
                    if isinstance(source, str) and expected_email in source.lower():
                        return LoginState.SIGNED_IN_MATCH
                    # Generic avatar alone without confirmed expected account identity
                    return LoginState.UNKNOWN
                else:
                    return LoginState.SIGNED_IN_MATCH

            # If no avatar is present, check for explicit sign-in button
            try:
                sign_in_btns = driver.find_elements(
                    By.XPATH,
                    "//a[contains(@href, 'signin') or contains(., 'Sign in') or contains(., 'Anmelden')]"
                )
                if any(b.is_displayed() for b in sign_in_btns):
                    return LoginState.SIGNED_OUT
            except Exception:
                pass

            # Incomplete page: neither sign-in button nor avatar present
            return LoginState.UNKNOWN

        # General Google host with confirmed identity
        if active_email and expected_email:
            if active_email == expected_email:
                return LoginState.SIGNED_IN_MATCH
            else:
                return LoginState.SIGNED_IN_OTHER

        return LoginState.UNKNOWN
    except Exception:
        return LoginState.UNKNOWN


def is_logged_in(driver: Any, expected_account: Optional[Account] = None) -> bool:
    """Check if the current browser session is logged into Google/YouTube."""
    state = check_login_state(driver, expected_account)
    return state == LoginState.SIGNED_IN_MATCH


def _handle_account_chooser(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """If Google displays the 'Choose an account' screen, select matching tile or 'Use another account'."""
    try:
        url = (getattr(driver, "current_url", "") or "").lower()
        page_source = (getattr(driver, "page_source", "") or "").lower()
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
    """Perform Google sign-in or verify existing active session matching the requested account."""
    emit(log_queue, f"Checking Google sign-in status for {account.email}...")

    try:
        safe_get(driver, "https://accounts.google.com/", timeout=30, log_queue=log_queue)
    except Exception:
        pass

    # Pause up to 10 seconds while monitoring if page redirects automatically to myaccount.google.com
    start_wait = time.time()
    while time.time() - start_wait < 10.0:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Login check cancelled by user.")
            return False

        state = check_login_state(driver, account)
        if state == LoginState.SIGNED_IN_MATCH:
            emit(log_queue, f"Account {account.email} is already signed in! Redirecting to YouTube...")
            safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
            human_like_delay(2.0, 3.0)
            return True
        elif state == LoginState.SIGNED_IN_OTHER:
            emit(
                log_queue,
                f"Browser is signed into a different account than {account.email}. Please switch accounts manually.",
                logging.ERROR,
            )
            return False

        time.sleep(1.0)

    emit(log_queue, f"Attempting sign-in for {account.email}...")

    try:
        _handle_account_chooser(driver, account, log_queue)
        time.sleep(1.0)
        _handle_email_input(driver, account, log_queue)
        _handle_password_input(driver, account, log_queue)
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

        state = check_login_state(driver, account)
        if state == LoginState.SIGNED_IN_MATCH:
            emit(log_queue, f"Login successful for {account.email}! Redirecting to YouTube...")
            safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
            human_like_delay(2.0, 3.0)
            return True
        elif state == LoginState.SIGNED_IN_OTHER:
            emit(
                log_queue,
                f"Signed in as a different account than expected ({account.email}). Aborting login.",
                logging.ERROR,
            )
            return False

        url = (getattr(driver, "current_url", "") or "").lower()
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
