import logging
import queue
import re
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..models.account import Account, normalize_account_id
from ..utils.helpers import human_like_delay, slow_type
from ..utils.logger import emit
from .driver import is_session_disconnected_error
from .navigation import safe_get


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _strip_scripts_and_styles(html: str) -> str:
    """Remove <script> and <style> blocks so JS/CSS text never triggers false challenge matches."""
    if not html or not isinstance(html, str):
        return ""
    return _SCRIPT_STYLE_RE.sub(" ", html)


def _get_visible_page_text(driver: Any, page_source: str = "") -> str:
    """Return lowercased visible text on the page, falling back to stripped HTML for mocks."""
    try:
        inner_text = driver.execute_script(
            "return (document && document.body && document.body.innerText) ? document.body.innerText : '';"
        )
        if isinstance(inner_text, str) and inner_text.strip():
            return inner_text.lower()
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
    return _strip_scripts_and_styles(page_source).lower()


def _has_visible_email_input(driver: Any) -> bool:
    """Return True if a Google sign-in email/identifier input box is currently visible."""
    try:
        boxes = driver.find_elements(
            By.XPATH,
            "//input[@id='identifierId' or @name='identifier' or @type='email' or @aria-label='Email or phone']",
        )
        return any(getattr(b, "is_displayed", lambda: False)() is True for b in boxes)
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
        return False


def _is_email_input_empty(driver: Any) -> bool:
    """Return True if a visible email input box exists and currently has an empty value."""
    try:
        boxes = driver.find_elements(
            By.XPATH,
            "//input[@id='identifierId' or @name='identifier' or @type='email' or @aria-label='Email or phone']",
        )
        for box in boxes:
            try:
                if getattr(box, "is_displayed", lambda: False)() is True:
                    val = box.get_attribute("value")
                    if val is None or (isinstance(val, str) and not val.strip()):
                        return True
                    return False
            except Exception:
                continue
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
    return False


def _check_captcha_dom_visibility(driver: Any) -> str:
    """Inspect DOM CAPTCHA elements and return 'visible', 'hidden', or 'unknown'.

    Google's standard /v3/signin/identifier page often includes hidden/empty
    <img id="captchaimg"> or hidden captcha input elements in page_source.
    Checking actual DOM visibility prevents false-positive CAPTCHA detection
    on the normal email entry screen.
    """
    try:
        captcha_els = driver.find_elements(
            By.CSS_SELECTOR,
            "#captchaimg, input[name='captcha'], input[name='ca'], iframe[src*='recaptcha']",
        )
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
        captcha_els = []

    if captcha_els:
        for el in captcha_els:
            try:
                if getattr(el, "is_displayed", lambda: False)() is not True:
                    continue
                tag = (getattr(el, "tag_name", "") or "").lower()
                if tag == "img":
                    src = el.get_attribute("src")
                    # An empty src placeholder img is not an active CAPTCHA challenge
                    if isinstance(src, str) and not src.strip():
                        continue
                return "visible"
            except Exception:
                continue
        return "hidden"

    # No CAPTCHA DOM nodes found; if the normal email input is visible, this is the email entry screen
    if _has_visible_email_input(driver):
        return "hidden"

    return "unknown"


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
        # Note: Do not treat standard password entry challenge (/challenge/pwd) as unknown non-signin
        if any(c in full_url_lower for c in ("captcha", "2step", "speedbump", "challenge/totp", "challenge/ipp", "challenge/selection")):
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
    attempt_count: int = 0,
) -> bool:
    """If Google displays the 'Choose an account' screen, select matching tile or 'Use another account'."""
    try:
        url = (getattr(driver, "current_url", "") or "").lower()
        page_source = (getattr(driver, "page_source", "") or "").lower()
        if "accountchooser" not in url and "choose an account" not in page_source:
            return False

        emit(log_queue, f"Account chooser screen detected for {account.email}.")
        account_email_lower = account.email.lower()

        # If previous attempts to click the saved account tile did not advance, try 'Use another account'
        if attempt_count >= 2:
            emit(
                log_queue,
                f"Saved account {account.email} tile did not advance after multiple clicks. Attempting 'Use another account'...",
                logging.WARNING,
            )
            use_another = driver.find_elements(
                By.XPATH,
                "//*[contains(text(), 'Use another account') or contains(text(), 'Ein weiteres') or contains(., 'Use another account')] | //div[@id='identifierLink']",
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
            return False

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
                try:
                    driver.execute_script("arguments[0].click();", tile)
                except Exception:
                    pass
            try:
                driver.execute_script(
                    """
                    var el = arguments[0];
                    try {
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                    } catch(e) {}
                    """,
                    tile,
                )
            except Exception:
                pass
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


def _handle_confirm_identifier(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """If Google displays the 'confirmidentifier' verification screen, click the Next button."""
    try:
        url = (getattr(driver, "current_url", "") or "").lower()
        page_source = ""
        try:
            page_source = (getattr(driver, "page_source", "") or "").lower()
        except Exception:
            pass

        # Check if URL or page indicates the confirm identifier screen
        is_confirm_identifier = (
            "confirmidentifier" in url
            or (
                "accounts.google.com" in url
                and "identifiernext" in page_source
                and "identifierid" not in page_source
            )
        )
        if not is_confirm_identifier:
            return False

        emit(log_queue, f"Confirm identifier screen detected for {account.email}; clicking Next...")

        next_selectors = [
            "//div[@id='identifierNext']//button",
            "//button[@id='identifierNext']",
            "//*[@id='identifierNext']//button",
            "//button[.//span[normalize-space(text())='Next' or normalize-space(text())='Weiter']]",
            "//*[@id='identifierNext']",
        ]

        next_btn = None
        for sel in next_selectors:
            try:
                elements = driver.find_elements(By.XPATH, sel)
                for el in elements:
                    if el.is_displayed():
                        next_btn = el
                        break
                if next_btn:
                    break
            except Exception:
                continue

        if not next_btn:
            try:
                next_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//div[@id='identifierNext']//button | //*[@id='identifierNext']//button | //button[.//span[text()='Next']]")
                    )
                )
            except Exception:
                pass

        if next_btn:
            try:
                next_btn.click()
            except Exception:
                driver.execute_script(
                    """
                    var el = arguments[0];
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    el.click();
                    """,
                    next_btn,
                )
            human_like_delay(2.0, 3.5)
            return True

        emit(log_queue, "Could not find Next button on confirm identifier screen.", logging.WARNING)
        return False
    except Exception as exc:
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Notice during confirm identifier resolution: {err_msg}", logging.DEBUG)
        return False


def _handle_email_input(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Enter email if email input is present on screen."""
    email_xpath = "//input[@id='identifierId'] | //input[@name='identifier'] | //input[@aria-label='Email or phone'] | //input[@type='email']"
    try:
        email_elements = driver.find_elements(By.XPATH, email_xpath)
        email_input = None
        for el in email_elements:
            try:
                if el.is_displayed():
                    email_input = el
                    break
            except Exception:
                continue

        if not email_input:
            cur_u = (getattr(driver, "current_url", "") or "").lower()
            if "accounts.google.com" in cur_u and "confirmidentifier" not in cur_u:
                try:
                    email_input = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, email_xpath))
                    )
                except Exception:
                    email_input = None

        if not email_input:
            return False

        emit(log_queue, f"Entering email for {account.email}...")
        try:
            email_input.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].focus(); arguments[0].click();", email_input)
            except Exception:
                pass
        try:
            email_input.clear()
        except Exception:
            pass
        slow_type(email_input, account.email)

        # Verify typed value did not get swallowed by page transition/focus race
        try:
            typed_val = email_input.get_attribute("value")
            if isinstance(typed_val, str) and not typed_val.strip():
                email_input.send_keys(account.email)
        except Exception:
            pass

        human_like_delay(0.5, 1.0)

        try:
            next_btn = driver.find_element(
                By.XPATH,
                "//div[@id='identifierNext']//button | //button[@id='identifierNext'] | //button[contains(., 'Next') or contains(., 'weiter') or @id='identifierNext']",
            )
            try:
                next_btn.click()
            except Exception:
                driver.execute_script(
                    """
                    var el = arguments[0];
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    el.click();
                    """,
                    next_btn,
                )
        except Exception:
            email_input.send_keys(Keys.ENTER)

        human_like_delay(2.5, 4.0)
        return True
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
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
                (By.XPATH, "//input[@type='password' or @name='Passwd' or @name='password' or @aria-label='Enter your password']")
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
                "//div[@id='passwordNext']//button | //button[@id='passwordNext'] | //button[contains(., 'Next') or @id='passwordNext'] | //button[.//span[normalize-space(text())='Next' or normalize-space(text())='Weiter']]",
            )
            try:
                pwd_next.click()
            except Exception:
                driver.execute_script(
                    """
                    var el = arguments[0];
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    el.click();
                    """,
                    pwd_next,
                )
        except Exception:
            pwd_input.send_keys(Keys.ENTER)

        human_like_delay(3.0, 5.0)
        return True
    except Exception as exc:
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Password input not completed automatically: {err_msg}", logging.DEBUG)
        return False


def _perform_automated_login_steps(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Execute sequential automated sign-in steps (Account Chooser -> Confirm Identifier -> Email -> Password -> Prompts)."""
    if not getattr(account, "password", ""):
        return False

    max_steps = 6
    password_entered = False
    account_chooser_attempts = 0

    for _ in range(max_steps):
        if stop_event is not None and stop_event.is_set():
            return False

        # If already signed in, stop
        state = check_login_state(driver, account)
        if state == LoginState.SIGNED_IN_MATCH:
            return True

        current_url = (getattr(driver, "current_url", "") or "").lower()
        page_source = ""
        try:
            page_source = (getattr(driver, "page_source", "") or "").lower()
        except Exception:
            pass

        # Stop if a genuine bot/CAPTCHA or phone 2FA challenge is present
        # Note: Do not treat normal email entry, confirmidentifier, or password entry as manual challenge
        visible_text = _get_visible_page_text(driver, page_source)
        stripped_source = _strip_scripts_and_styles(page_source).lower()
        captcha_vis = _check_captcha_dom_visibility(driver)
        has_captcha_el = captcha_vis == "visible" or (
            captcha_vis == "unknown" and "captchaimg" in stripped_source
        )
        is_bot = (
            "/sorry/index" in current_url
            or "challenge/recaptcha" in current_url
            or "signin/challenge/az" in current_url
            or has_captcha_el
            or "unusual traffic" in visible_text
            or "show you're not a robot" in visible_text
        )
        is_phone_2fa = (
            any(c in current_url for c in ("challenge/totp", "challenge/ipp", "challenge/selection", "2step"))
            or any(p in visible_text for p in ("check your phone", "google sent a notification to your phone", "enter the 2-digit number", "tap yes on your phone"))
        )
        if is_bot or is_phone_2fa:
            break

        action_taken = False

        # 1. Account chooser
        if "accountchooser" in current_url or "choose an account" in visible_text:
            if _handle_account_chooser(driver, account, log_queue, attempt_count=account_chooser_attempts):
                account_chooser_attempts += 1
                action_taken = True
                human_like_delay(2.0, 3.5)
                continue
            elif account_chooser_attempts >= 2:
                # Could not advance even after trying 'Use another account'
                break

        # 2. Confirm identifier screen
        if "confirmidentifier" in current_url or ("accounts.google.com" in current_url and "identifiernext" in stripped_source and "identifierid" not in stripped_source):
            if _handle_confirm_identifier(driver, account, log_queue):
                action_taken = True
                human_like_delay(2.0, 3.5)
                continue

        # 3. Email input screen
        has_email_box = _has_visible_email_input(driver)
        on_email_identifier_url = (
            "accounts.google.com" in current_url
            and any(p in current_url for p in ("/identifier", "/signin/v2", "/v3/signin", "servicelogin"))
            and "confirmidentifier" not in current_url
            and "challenge/" not in current_url
            and "accountchooser" not in current_url
        )

        if has_email_box or on_email_identifier_url:
            if _handle_email_input(driver, account, log_queue):
                action_taken = True
                human_like_delay(2.0, 3.5)
                continue

        # 4. Password input screen
        has_pwd_box = False
        try:
            pwd_boxes = driver.find_elements(By.XPATH, "//input[@type='password' or @name='Passwd' or @name='password']")
            has_pwd_box = any(b.is_displayed() for b in pwd_boxes)
        except Exception:
            pass

        if ("challenge/pwd" in current_url or has_pwd_box) and not password_entered:
            if _handle_password_input(driver, account, log_queue):
                password_entered = True
                action_taken = True
                human_like_delay(2.5, 4.0)
                _handle_post_login_prompts(driver, log_queue)
                continue

        # 5. Post login prompts
        _handle_post_login_prompts(driver, log_queue)

        if not action_taken:
            break

    return password_entered


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



def _dispatch_login_alert(
    alert_type: str,
    details: str,
    account: Account,
    config_mgr: Optional[Any] = None,
    alert_callback: Optional[Callable[..., None]] = None,
    driver: Optional[Any] = None,
    log_queue: Optional[queue.Queue] = None,
    force: bool = False,
) -> None:
    """Dispatch an alert via custom worker callback or standard EmailReporter."""
    if alert_callback:
        try:
            import inspect
            sig = inspect.signature(alert_callback)
            if "force" in sig.parameters:
                alert_callback(alert_type, details, force=force)
            else:
                alert_callback(alert_type, details)
            return
        except Exception as exc:
            emit(log_queue, f"Notice during alert callback dispatch: {exc}", logging.DEBUG)

    if config_mgr:
        try:
            from .email_reporter import EmailReporter
            EmailReporter.send_alert_notification(
                account=account,
                alert_type=alert_type,
                message_details=details,
                config_mgr=config_mgr,
                log_queue=log_queue,
                driver=driver,
                force=force,
            )
        except Exception as exc:
            emit(log_queue, f"Notice during email alert dispatch: {exc}", logging.DEBUG)


def detect_manual_challenge(driver: Any) -> Optional[Tuple[str, str]]:
    """Detect if Google or YouTube is currently presenting a challenge requiring manual action.

    Returns (alert_type, details) if manual action is required, or None if normal.
    """
    try:
        url = (getattr(driver, "current_url", "") or "").lower()
        page_source = ""
        try:
            page_source = (getattr(driver, "page_source", "") or "").lower()
        except Exception as exc:
            if is_session_disconnected_error(exc):
                raise

        visible_text = _get_visible_page_text(driver, page_source)
        stripped_source = _strip_scripts_and_styles(page_source).lower()

        # 1. Google / YouTube Bot Block & CAPTCHA (sorry/index, unusual traffic, bot checks)
        # Check URL path or specific challenge phrases in visible text (never raw <script>/<style> blocks).
        # Note: Do NOT match bare 'recaptcha' in page_source because normal YouTube pages include
        # '.grecaptcha-badge{visibility:hidden}' in their default stylesheet.
        is_bot_challenge = (
            "/sorry/index" in url
            or "google.com/sorry" in url
            or "youtube.com/sorry" in url
            or any(phrase in visible_text for phrase in (
                "sorry/index",
                "unusual traffic from your computer network",
                "our systems have detected unusual traffic",
                "please show you're not a robot",
                "sign in to confirm you’re not a bot",
                "sign in to confirm you're not a bot",
                "solve the puzzle",
                "type the characters you see in the picture",
                "to continue, please type the characters below",
            ))
        )
        if is_bot_challenge:
            return (
                "Manual Action Required - Google CAPTCHA / Bot Challenge",
                "Google presented a CAPTCHA or bot verification challenge. Manual completion in the browser is required.",
            )

        # 2. Authentication challenges and 2FA on Google Account sign-in pages
        if "accounts.google.com" in url:
            # CAPTCHA on Google sign-in — verify actual DOM visibility so hidden/pre-rendered
            # <img id="captchaimg"> templates on /v3/signin/identifier do not trigger false positives.
            captcha_vis = _check_captcha_dom_visibility(driver)
            has_captcha_dom = captcha_vis == "visible" or (
                captcha_vis == "unknown"
                and any(
                    el in stripped_source
                    for el in ('id="captchaimg"', "id='captchaimg'", 'name="captcha"', "name='captcha'")
                )
            )
            if any(c in url for c in ("challenge/recaptcha", "signin/challenge/az")) or has_captcha_dom:
                return (
                    "Manual Action Required - Google CAPTCHA / Bot Challenge",
                    "Google presented a CAPTCHA or bot verification challenge. Manual completion in the browser is required.",
                )

            has_email_input = _has_visible_email_input(driver)
            is_confirm_identifier = "confirmidentifier" in url or ("identifiernext" in stripped_source and "identifierid" not in stripped_source)
            has_pwd_input = "challenge/pwd" in url or 'type="password"' in stripped_source or "type='password'" in stripped_source
            has_wrong_password = any(p in visible_text for p in ("wrong password", "falsches passwort", "incorrect password"))

            if has_wrong_password:
                return (
                    "Manual Action Required - Incorrect Password",
                    "Google rejected the password for this account. Please update credentials or sign in manually.",
                )

            # 2-Step Verification / Security Challenge / Phone prompt
            # Note: challenge/pwd and normal email entry are NOT 2FA challenges
            if any(c in url for c in ("2step", "challenge/totp", "challenge/ipp", "challenge/selection", "speedbump")) or \
               any(phrase in visible_text for phrase in (
                   "2-step verification",
                   "check your phone",
                   "google sent a notification to your phone",
                   "enter the 2-digit number",
                   "get a verification code",
                   "tap yes on your phone",
               )) or (
                   not has_email_input and not is_confirm_identifier and not has_pwd_input and "verify it's you" in visible_text
               ):
                return (
                    "Manual Action Required - 2-Step Verification / Security Challenge",
                    "Google presented a 2-Step Verification prompt or security challenge. Please verify on your device or browser manually.",
                )

            # Re-authentication / password re-entry (only when not on an actionable email, confirmidentifier, or password input screen)
            if not has_email_input and not is_confirm_identifier and not has_pwd_input:
                if any(phrase in visible_text for phrase in (
                    "sign in again",
                    "your session has expired",
                    "re-enter your password",
                    "confirm your password",
                    "verify your identity",
                )):
                    return (
                        "Manual Action Required - Google Re-Authentication Required",
                        "Google / Gmail is asking for sign-in or password confirmation again. Manual re-authentication is required.",
                    )

        return None
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
        return None
def perform_google_login(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
    config_mgr: Optional[Any] = None,
    alert_callback: Optional[Callable[[str, str], None]] = None,
    wait_for_challenge_resolution: bool = True,
) -> bool:
    """Perform Google sign-in or verify existing active session matching the requested account."""
    is_system_profile = (getattr(driver, "_is_system_profile", False) is True) or (
        config_mgr
        and hasattr(config_mgr, "get_use_system_chrome_profile")
        and config_mgr.get_use_system_chrome_profile()
        and not getattr(account, "password", "").strip()
    )
    if is_system_profile:
        emit(log_queue, f"Using existing Personal Chrome Profile session for {account.email} (already signed in).")
        current_url = (getattr(driver, "current_url", "") or "").lower()
        if "youtube.com" not in current_url:
            try:
                safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
                human_like_delay(1.5, 2.5)
            except Exception:
                pass
        return True

    emit(log_queue, f"Checking Google sign-in status for {account.email}...")

    current_url = (getattr(driver, "current_url", "") or "").lower()
    already_on_signin = "accounts.google.com" in current_url and any(
        p in current_url for p in ("signin", "confirmidentifier", "accountchooser", "identifier", "challenge")
    )
    if not already_on_signin:
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

        # Probe current_url directly so external browser closure raises immediately
        _ = getattr(driver, "current_url", "")

        state = check_login_state(driver, account)
        if state == LoginState.SIGNED_IN_MATCH:
            emit(log_queue, f"Account {account.email} is already signed in! Redirecting to YouTube...")
            safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
            human_like_delay(2.0, 3.0)
            return True
        elif state == LoginState.SIGNED_IN_OTHER:
            details = f"Browser on this PC is signed into a different account than {account.email}. Please switch accounts manually."
            emit(log_queue, details, logging.ERROR)
            _dispatch_login_alert("Manual Action Required - Wrong Account Signed In", details, account, config_mgr, alert_callback, driver, log_queue)
            return False
        elif already_on_signin and state == LoginState.SIGNED_OUT:
            break
        elif (time.time() - start_wait >= 2.0) and state == LoginState.SIGNED_OUT and _has_visible_email_input(driver):
            break

        time.sleep(1.0)

    emit(log_queue, f"Attempting sign-in for {account.email}...")

    try:
        _perform_automated_login_steps(driver, account, log_queue, stop_event=stop_event)
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
        err_msg = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        emit(log_queue, f"Automatic login input notice: {err_msg}", logging.WARNING)

    # Check if driver is running in headless mode
    _is_h = getattr(driver, "_is_headless", False)
    _is_r = getattr(driver, "_is_reporting", False)
    is_headless = _is_h is True or _is_r is True
    if not is_headless:
        try:
            caps = getattr(driver, "capabilities", {}) or {}
            if isinstance(caps, dict):
                chrome_opts = caps.get("chrome", {}) or caps.get("goog:chromeOptions", {})
                if isinstance(chrome_opts, dict):
                    args = chrome_opts.get("args", [])
                    if isinstance(args, (list, tuple)) and any("--headless" in a for a in args):
                        is_headless = True
        except Exception:
            pass
    if not is_headless:
        try:
            ua = driver.execute_script("return navigator.userAgent") or ""
            if isinstance(ua, str) and "headless" in ua.lower():
                is_headless = True
        except Exception as exc:
            if is_session_disconnected_error(exc):
                raise

    # Wait loop for login completion or manual CAPTCHA / 2FA completion
    # In headless mode or when wait_for_challenge_resolution is False, wait at most 5s since manual user interaction is not possible or was already unattended
    max_wait = 5.0 if (is_headless or not wait_for_challenge_resolution) else 600.0
    end_time = time.time() + max_wait
    challenge_logged = False
    last_reminder = time.time()
    email_wait_attempts = 0

    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Login wait cancelled by user.")
            return False

        # Probe current_url directly so external browser closure raises immediately
        cur_u = (getattr(driver, "current_url", "") or "").lower()

        state = check_login_state(driver, account)
        if state == LoginState.SIGNED_IN_MATCH:
            if challenge_logged:
                emit(log_queue, f"[Manual Action Required] Manual challenge resolved for {account.email}! Sign-in verified.", logging.INFO)
                _dispatch_login_alert(
                    "Manual Challenge Resolved - Resuming Session",
                    f"Google CAPTCHA / manual verification challenge has been resolved successfully for account {account.email} on this PC. Sign-in verified.",
                    account,
                    config_mgr,
                    alert_callback,
                    driver,
                    log_queue,
                    force=True,
                )
            emit(log_queue, f"Login successful for {account.email}! Redirecting to YouTube...")
            safe_get(driver, "https://www.youtube.com/", timeout=30, log_queue=log_queue)
            human_like_delay(2.0, 3.0)
            return True
        elif state == LoginState.SIGNED_IN_OTHER:
            details = f"Signed in as a different account than expected ({account.email}). Aborting login."
            emit(log_queue, details, logging.ERROR)
            _dispatch_login_alert("Manual Action Required - Wrong Account Signed In", details, account, config_mgr, alert_callback, driver, log_queue)
            return False

        # In case email input, confirmidentifier, or password appears dynamically during the wait loop
        if "confirmidentifier" in cur_u:
            if _handle_confirm_identifier(driver, account, log_queue):
                human_like_delay(2.0, 3.5)
                continue
        elif "challenge/pwd" in cur_u:
            if _handle_password_input(driver, account, log_queue):
                human_like_delay(2.5, 4.0)
                _handle_post_login_prompts(driver, log_queue)
                continue
        elif (
            "accounts.google.com" in cur_u
            and email_wait_attempts < 1
            and _is_email_input_empty(driver)
            and _check_captcha_dom_visibility(driver) != "visible"
        ):
            if _handle_email_input(driver, account, log_queue):
                email_wait_attempts += 1
                human_like_delay(2.0, 3.5)
                continue

        challenge_info = detect_manual_challenge(driver)
        if not challenge_logged and challenge_info is not None:
            alert_type, det = challenge_info
            if is_headless or not wait_for_challenge_resolution:
                if not wait_for_challenge_resolution:
                    emit(log_queue, f"Manual verification required by Google ({account.email}); skipping extended wait as challenge was already pending/unattended.", logging.WARNING)
                else:
                    emit(log_queue, f"Manual verification required by Google ({account.email}); cannot be completed in headless reporting browser.", logging.DEBUG)
                return False
            full_details = f"{det} Account: {account.email}."
            emit(
                log_queue,
                f"Google requested manual challenge / verification: {det} Please complete it manually in the browser window.",
                logging.WARNING,
            )
            challenge_logged = True
            _dispatch_login_alert(alert_type, full_details, account, config_mgr, alert_callback, driver, log_queue)
        elif not challenge_logged:
            url = (getattr(driver, "current_url", "") or "").lower()
            if "challenge" in url or "captcha" in url or "2step" in url:
                if is_headless or not wait_for_challenge_resolution:
                    if not wait_for_challenge_resolution:
                        emit(log_queue, f"Manual verification required by Google ({account.email}); skipping extended wait as challenge was already pending/unattended.", logging.WARNING)
                    else:
                        emit(log_queue, f"Manual verification required by Google ({account.email}); cannot be completed in headless reporting browser.", logging.DEBUG)
                    return False
                det = f"Google requested CAPTCHA or 2-Step Verification for account {account.email} on this PC. Please complete it manually in the browser window."
                emit(log_queue, det, logging.WARNING)
                challenge_logged = True
                _dispatch_login_alert("Manual Action Required - Google Challenge / CAPTCHA", det, account, config_mgr, alert_callback, driver, log_queue)

        if challenge_logged and time.time() - last_reminder >= 30.0:
            rem = int(max(0, end_time - time.time()))
            emit(log_queue, f"[Manual Action Required] Still waiting for manual challenge resolution ({rem}s remaining)...", logging.INFO)
            last_reminder = time.time()

        time.sleep(2)

    if not (stop_event is not None and stop_event.is_set()):
        if not is_headless:
            emit(log_queue, f"Sign-in timed out for {account.email}.", logging.ERROR)
            if not challenge_logged:
                det = f"Google sign-in timed out after 600 seconds for {account.email} on this PC. ID is stuck at signing in."
                _dispatch_login_alert("Manual Action Required - Sign-In Stuck / Timed Out", det, account, config_mgr, alert_callback, driver, log_queue)
        else:
            emit(log_queue, f"Headless sign-in incomplete for {account.email}.", logging.DEBUG)
    return False


def check_account_logged_in(
    driver: Any,
    account: Optional[Account],
    config_mgr: Optional[Any] = None,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
    alert_callback: Optional[Callable[[str, str], None]] = None,
    content_type: str = "video",
    content_title: str = "",
    wait_for_challenge_resolution: bool = True,
    challenge_timeout_seconds: Optional[float] = None,
) -> bool:
    """Verify that the account is logged in before playing a video or short.

    If Google requests CAPTCHA, 2FA, re-sign in, or if the account was logged out
    and re-login fails, dispatches an alert email to notify the user which PC is stuck,
    and pauses/waits for manual resolution in the browser before proceeding.
    """
    if account is None or not getattr(account, "email", "").strip():
        return True

    if stop_event is not None and stop_event.is_set():
        return False

    # 1. Check for immediate manual challenges (CAPTCHA, 2FA prompt, etc.)
    challenge = detect_manual_challenge(driver)
    if challenge is not None:
        alert_type, details = challenge
        full_details = f"{details} Issue detected on this PC before playing {content_type} '{content_title or 'item'}' for account {account.email}."
        emit(log_queue, f"[Manual Action Required] {full_details}", logging.ERROR)
        _dispatch_login_alert(alert_type, full_details, account, config_mgr, alert_callback, driver, log_queue)

        timeout = challenge_timeout_seconds
        if timeout is None:
            timeout = (
                float(config_mgr.get_setting("ChallengeTimeoutSeconds", 600.0) or 600.0)
                if config_mgr
                else 0.0
            )

        if not wait_for_challenge_resolution or timeout <= 0.0:
            return False

        emit(
            log_queue,
            f"[Manual Action Required] Notification email sent. Waiting up to {int(timeout)}s ({int(timeout / 60)}m) for manual CAPTCHA / challenge resolution in the browser on this PC...",
            logging.WARNING,
        )

        start_wait = time.time()
        last_logged = start_wait
        while time.time() - start_wait < timeout:
            if stop_event is not None and stop_event.is_set():
                emit(log_queue, "Manual challenge wait cancelled by user.")
                return False

            time.sleep(2.0)

            cur_chal = detect_manual_challenge(driver)
            if cur_chal is None:
                emit(
                    log_queue,
                    f"[Manual Action Required] Manual challenge resolved for {account.email}! Resuming {content_type} playback.",
                    logging.INFO,
                )
                _dispatch_login_alert(
                    "Manual Challenge Resolved - Resuming Session",
                    f"Google CAPTCHA / manual verification challenge has been resolved successfully for account {account.email} on this PC. Resuming {content_type} playback.",
                    account,
                    config_mgr,
                    alert_callback,
                    driver,
                    log_queue,
                    force=True,
                )
                human_like_delay(1.5, 2.5)
                return True

            if time.time() - last_logged >= 30.0:
                elapsed = int(time.time() - start_wait)
                remaining = int(timeout - elapsed)
                emit(
                    log_queue,
                    f"[Manual Action Required] Still waiting for manual challenge resolution ({elapsed}s elapsed, {remaining}s remaining)...",
                    logging.INFO,
                )
                last_logged = time.time()

        emit(
            log_queue,
            f"[Manual Action Required] Timed out after {int(timeout)}s waiting for manual challenge resolution for {account.email}.",
            logging.ERROR,
        )
        return False

    # 2. Check YouTube logged-in status via YouTube client config (fast, zero DOM delay)
    try:
        yt_logged_in = driver.execute_script(
            "try { return window.ytcfg ? window.ytcfg.get('LOGGED_IN') : (window.yt && window.yt.config_ ? window.yt.config_.LOGGED_IN : null); } catch(e) { return null; }"
        )
    except Exception:
        yt_logged_in = None

    if yt_logged_in is True:
        emit(log_queue, f"[Account Check] Confirmed {account.email} is logged in before playing {content_type}.", logging.DEBUG)
        return True
    elif yt_logged_in is False:
        emit(
            log_queue,
            f"[Account Check] Account {account.email} is signed out before playing {content_type} '{content_title or ''}'. Attempting automatic re-login...",
            logging.WARNING,
        )
        login_ok = perform_google_login(
            driver,
            account,
            log_queue=log_queue,
            stop_event=stop_event,
            config_mgr=config_mgr,
            alert_callback=alert_callback,
        )
        if login_ok:
            emit(log_queue, f"[Account Check] Re-login successful for {account.email}. Resuming {content_type} playback.", logging.INFO)
            return True
        if stop_event is not None and stop_event.is_set():
            return False

        alert_type = "Manual Action Required - Account Logged Out / Re-Login Failed"
        details = (
            f"Account {account.email} automatically logged out on this PC and automatic re-login failed "
            f"before playing {content_type} '{content_title or 'item'}'. Manual sign-in is required."
        )
        emit(log_queue, f"[Manual Action Required] {details}", logging.ERROR)
        _dispatch_login_alert(alert_type, details, account, config_mgr, alert_callback, driver, log_queue)
        return False

    # 3. Fallback: inspect DOM login state
    state = check_login_state(driver, account)
    if state == LoginState.SIGNED_IN_MATCH:
        return True
    elif state == LoginState.SIGNED_IN_OTHER:
        alert_type = "Manual Action Required - Wrong Account Signed In"
        details = f"Browser on this PC is signed into a different account than expected ({account.email})."
        emit(log_queue, f"[Manual Action Required] {details}", logging.ERROR)
        _dispatch_login_alert(alert_type, details, account, config_mgr, alert_callback, driver, log_queue)
        return False
    elif state == LoginState.SIGNED_OUT:
        emit(
            log_queue,
            f"[Account Check] Account {account.email} is signed out before playing {content_type} '{content_title or ''}'. Attempting automatic re-login...",
            logging.WARNING,
        )
        login_ok = perform_google_login(
            driver,
            account,
            log_queue=log_queue,
            stop_event=stop_event,
            config_mgr=config_mgr,
            alert_callback=alert_callback,
        )
        if login_ok:
            return True
        if stop_event is not None and stop_event.is_set():
            return False

        alert_type = "Manual Action Required - Account Logged Out / Re-Login Failed"
        details = (
            f"Account {account.email} automatically logged out on this PC and automatic re-login failed "
            f"before playing {content_type} '{content_title or 'item'}'. Manual sign-in is required."
        )
        emit(log_queue, f"[Manual Action Required] {details}", logging.ERROR)
        _dispatch_login_alert(alert_type, details, account, config_mgr, alert_callback, driver, log_queue)
        return False
    else:
        # UNKNOWN: check if avatar or sign-in buttons exist
        try:
            avatar_els = driver.find_elements(By.CSS_SELECTOR, "#avatar-btn, ytd-topbar-menu-button-renderer img")
            if any(getattr(el, "is_displayed", lambda: True)() for el in avatar_els):
                return True
            signin_els = driver.find_elements(
                By.XPATH,
                "//a[contains(@href, 'signin') or contains(., 'Sign in') or contains(., 'Anmelden')]"
            )
            if any(getattr(b, "is_displayed", lambda: True)() for b in signin_els):
                login_ok = perform_google_login(
                    driver,
                    account,
                    log_queue=log_queue,
                    stop_event=stop_event,
                    config_mgr=config_mgr,
                    alert_callback=alert_callback,
                )
                if not login_ok and (stop_event is None or not stop_event.is_set()):
                    alert_type = "Manual Action Required - Account Logged Out / Re-Login Failed"
                    details = f"Account {account.email} is signed out and re-login failed before playing {content_type}."
                    _dispatch_login_alert(alert_type, details, account, config_mgr, alert_callback, driver, log_queue)
                return login_ok
        except Exception:
            pass
        return True
