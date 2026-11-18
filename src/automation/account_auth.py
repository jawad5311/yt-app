"""Standalone account profile authentication and session verification.

Allows users to pre-authenticate Google accounts from the Accounts tab so their
persistent Chrome profile is created, logged in, and stored in
``runtime/chrome_profiles/`` before clicking Start Browsing.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from ..models.account import Account
from ..utils.logger import emit
from .driver import close_driver, is_session_disconnected_error, setup_driver
from .login import (
    LoginState,
    _has_visible_email_input,
    check_login_state,
    perform_google_login,
)
from .navigation import safe_get
from .profile_manager import (
    ProfileBusyError,
    is_chrome_lock_active,
    resolve_profile_dir,
)


class AuthStatus(Enum):
    """Outcome states for an account authentication check / login attempt."""

    ALREADY_LOGGED_IN = "already_logged_in"
    AUTHENTICATED = "authenticated"
    PROFILE_BUSY = "profile_busy"
    WRONG_ACCOUNT = "wrong_account"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class AuthResult:
    """Structured result returned by :func:`authenticate_account_profile`."""

    status: AuthStatus
    message: str
    profile_dir: Optional[Path] = None

    @property
    def is_logged_in(self) -> bool:
        return self.status in (AuthStatus.ALREADY_LOGGED_IN, AuthStatus.AUTHENTICATED)


def has_existing_profile_data(profile_dir: Path) -> bool:
    """Return True if ``profile_dir`` already contains initialized Chrome profile files."""
    if not profile_dir or not profile_dir.is_dir():
        return False

    marker_files = (
        profile_dir / "Local State",
        profile_dir / "Default" / "Preferences",
        profile_dir / "Default" / "Secure Preferences",
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
    )
    return any(p.is_file() for p in marker_files)


def _wait_interruptible(seconds: float, stop_event: Optional[threading.Event]) -> bool:
    """Wait up to ``seconds``. Returns True if ``stop_event`` was set."""
    if stop_event is not None:
        return stop_event.wait(timeout=seconds)
    time.sleep(seconds)
    return False


def _probe_login_state_on_google(
    driver: Any,
    account: Account,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
    timeout: float = 8.0,
) -> LoginState:
    """Navigate to Google Accounts and inspect whether the profile is already signed in."""
    try:
        safe_get(driver, "https://accounts.google.com/", timeout=30, log_queue=log_queue)
    except Exception as exc:
        if is_session_disconnected_error(exc):
            raise
        emit(log_queue, f"[Account Auth] Notice navigating to accounts.google.com: {exc}", logging.DEBUG)

    start_wait = time.time()
    last_state = LoginState.UNKNOWN
    while time.time() - start_wait < timeout:
        if stop_event is not None and stop_event.is_set():
            return LoginState.UNKNOWN

        cur_url = (getattr(driver, "current_url", "") or "").lower()
        if "myaccount.google.com/intro" in cur_url:
            return LoginState.SIGNED_OUT

        state = check_login_state(driver, account)
        last_state = state

        if state == LoginState.SIGNED_IN_MATCH:
            return LoginState.SIGNED_IN_MATCH
        if state == LoginState.SIGNED_IN_OTHER:
            return LoginState.SIGNED_IN_OTHER
        if state == LoginState.SIGNED_OUT:
            if (
                (time.time() - start_wait >= 2.0)
                or _has_visible_email_input(driver)
                or "accountchooser" in cur_url
            ):
                return LoginState.SIGNED_OUT

        if _wait_interruptible(0.5, stop_event):
            return LoginState.UNKNOWN

    return last_state


def check_profile_authenticated_headless(
    account: Account,
    known_accounts: Optional[List[Account]] = None,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
    active_driver_callback: Optional[Callable[[Optional[Any]], None]] = None,
    timeout: float = 8.0,
) -> LoginState:
    """Open Chrome headlessly with the account's persistent profile to check login state."""
    driver = None
    try:
        driver = setup_driver(
            account=account,
            log_queue=log_queue,
            known_accounts=known_accounts,
            headless=True,
            is_reporting=False,
        )
        if active_driver_callback is not None:
            active_driver_callback(driver)

        if stop_event is not None and stop_event.is_set():
            return LoginState.UNKNOWN

        return _probe_login_state_on_google(
            driver=driver,
            account=account,
            log_queue=log_queue,
            stop_event=stop_event,
            timeout=timeout,
        )
    finally:
        if active_driver_callback is not None:
            try:
                active_driver_callback(None)
            except Exception:
                pass
        if driver is not None:
            close_driver(driver, log_queue=log_queue)


def authenticate_account_profile(
    account: Account,
    known_accounts: Optional[Sequence[Account]] = None,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    active_driver_callback: Optional[Callable[[Optional[Any]], None]] = None,
    runtime_dir: Optional[Path] = None,
    headless_check_timeout: float = 8.0,
    lock_settle_seconds: float = 0.5,
) -> AuthResult:
    """Check whether an account's Chrome profile is authenticated, or launch Chrome to sign in.

    Workflow:
    1. Resolves the exact same persistent profile directory used by Start Browsing.
    2. If the profile already has saved Chrome data, checks authentication headlessly first:
       - If already signed in as ``account``, closes headless Chrome and returns ``ALREADY_LOGGED_IN``.
       - If signed into a different account, returns ``WRONG_ACCOUNT``.
       - If signed out / expired, closes headless Chrome and proceeds to Step 3.
    3. Opens a visible Chrome browser window using the persistent profile, creates/stores
       profile data in ``runtime/chrome_profiles/``, and executes ``perform_google_login()``
       (including automatic credential entry and waiting for manual 2FA/CAPTCHA if prompted).
    4. Cleanly closes Chrome so all session cookies and profile state flush to disk.
    """
    def _report(stage: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(stage)
            except Exception:
                pass

    known_list: Optional[List[Account]] = list(known_accounts) if known_accounts is not None else None
    profile_dir = resolve_profile_dir(account, known_accounts=known_list, runtime_dir=runtime_dir)

    if is_chrome_lock_active(profile_dir):
        msg = (
            f"The Chrome profile for '{account.email}' is currently in use by another session.\n\n"
            "Please stop the active session or close the open browser window and try again."
        )
        emit(log_queue, f"[Account Auth] Profile busy for {account.email}: {profile_dir.name}", logging.WARNING)
        return AuthResult(status=AuthStatus.PROFILE_BUSY, message=msg, profile_dir=profile_dir)

    if stop_event is not None and stop_event.is_set():
        return AuthResult(
            status=AuthStatus.CANCELLED,
            message=f"Authentication was cancelled for '{account.email}'.",
            profile_dir=profile_dir,
        )

    profile_existed = has_existing_profile_data(profile_dir)
    headless_check_ran = False
    headless_check_failed = False

    # Phase 1: If a persistent profile already exists, check login state headlessly first
    if profile_existed:
        _report("Checking...")
        emit(log_queue, f"[Account Auth] Checking existing Chrome profile for {account.email}...")
        try:
            headless_check_ran = True
            state = check_profile_authenticated_headless(
                account=account,
                known_accounts=known_list,
                log_queue=log_queue,
                stop_event=stop_event,
                active_driver_callback=active_driver_callback,
                timeout=headless_check_timeout,
            )
            if stop_event is not None and stop_event.is_set():
                return AuthResult(
                    status=AuthStatus.CANCELLED,
                    message=f"Authentication was cancelled for '{account.email}'.",
                    profile_dir=profile_dir,
                )

            if state == LoginState.SIGNED_IN_MATCH:
                emit(
                    log_queue,
                    f"[Account Auth] Account {account.email} is already logged in and authenticated.",
                    logging.INFO,
                )
                return AuthResult(
                    status=AuthStatus.ALREADY_LOGGED_IN,
                    message=(
                        f"Account '{account.email}' is already logged in and authenticated.\n\n"
                        "Its saved Chrome profile is ready to use when you click Start Browsing."
                    ),
                    profile_dir=profile_dir,
                )

            if state == LoginState.SIGNED_IN_OTHER:
                emit(
                    log_queue,
                    f"[Account Auth] Profile for {account.email} is signed into a different Google account.",
                    logging.WARNING,
                )
                return AuthResult(
                    status=AuthStatus.WRONG_ACCOUNT,
                    message=(
                        f"The Chrome profile for '{account.email}' is currently signed into a different Google account.\n\n"
                        "Please switch accounts or clear this profile and try again."
                    ),
                    profile_dir=profile_dir,
                )

            emit(
                log_queue,
                f"[Account Auth] Profile for {account.email} is not signed in (state: {state.value}). Opening browser to authenticate...",
                logging.INFO,
            )
        except ProfileBusyError:
            msg = (
                f"The Chrome profile for '{account.email}' is currently in use by another session.\n\n"
                "Please stop the active session and try again."
            )
            emit(log_queue, f"[Account Auth] Profile busy during check for {account.email}.", logging.WARNING)
            return AuthResult(status=AuthStatus.PROFILE_BUSY, message=msg, profile_dir=profile_dir)
        except Exception as exc:
            headless_check_failed = True
            emit(
                log_queue,
                f"[Account Auth] Headless profile check could not complete for {account.email} ({exc}); falling back to visible browser.",
                logging.WARNING,
            )

        if headless_check_ran and lock_settle_seconds > 0:
            if _wait_interruptible(lock_settle_seconds, stop_event):
                return AuthResult(
                    status=AuthStatus.CANCELLED,
                    message=f"Authentication was cancelled for '{account.email}'.",
                    profile_dir=profile_dir,
                )

    # Phase 2: First time or not logged in -> launch visible Chrome and authenticate
    _report("Opening Chrome...")
    emit(
        log_queue,
        f"[Account Auth] Opening Chrome browser to create/authenticate profile for {account.email}...",
        logging.INFO,
    )

    driver = None
    try:
        driver = setup_driver(
            account=account,
            log_queue=log_queue,
            known_accounts=known_list,
            headless=False,
            is_reporting=False,
        )
        if active_driver_callback is not None:
            active_driver_callback(driver)

        if stop_event is not None and stop_event.is_set():
            return AuthResult(
                status=AuthStatus.CANCELLED,
                message=f"Authentication was cancelled for '{account.email}'.",
                profile_dir=profile_dir,
            )

        # If headless check failed on an existing profile, verify first so we still detect already-logged-in profiles
        if profile_existed and headless_check_failed:
            _report("Checking...")
            visible_state = _probe_login_state_on_google(
                driver=driver,
                account=account,
                log_queue=log_queue,
                stop_event=stop_event,
                timeout=6.0,
            )
            if visible_state == LoginState.SIGNED_IN_MATCH:
                emit(
                    log_queue,
                    f"[Account Auth] Account {account.email} is already logged in and authenticated.",
                    logging.INFO,
                )
                return AuthResult(
                    status=AuthStatus.ALREADY_LOGGED_IN,
                    message=(
                        f"Account '{account.email}' is already logged in and authenticated.\n\n"
                        "Its saved Chrome profile is ready to use when you click Start Browsing."
                    ),
                    profile_dir=profile_dir,
                )
            if visible_state == LoginState.SIGNED_IN_OTHER:
                return AuthResult(
                    status=AuthStatus.WRONG_ACCOUNT,
                    message=(
                        f"The Chrome profile for '{account.email}' is currently signed into a different Google account.\n\n"
                        "Please switch accounts or clear this profile and try again."
                    ),
                    profile_dir=profile_dir,
                )

        _report("Logging In...")
        login_ok = perform_google_login(
            driver=driver,
            account=account,
            log_queue=log_queue,
            stop_event=stop_event,
            config_mgr=None,
            alert_callback=None,
            wait_for_challenge_resolution=True,
        )

        if login_ok:
            # Brief settle delay before quitting so Chrome flushes cookies & session state cleanly
            _wait_interruptible(1.0, stop_event)
            emit(
                log_queue,
                f"[Account Auth] Successfully authenticated {account.email} and saved Chrome profile at {profile_dir}.",
                logging.INFO,
            )
            return AuthResult(
                status=AuthStatus.AUTHENTICATED,
                message=(
                    f"Account '{account.email}' has been authenticated and its Chrome profile has been saved.\n\n"
                    "Next time you click Start Browsing, the app will reuse this logged-in session automatically."
                ),
                profile_dir=profile_dir,
            )

        if stop_event is not None and stop_event.is_set():
            return AuthResult(
                status=AuthStatus.CANCELLED,
                message=f"Authentication was cancelled for '{account.email}'.",
                profile_dir=profile_dir,
            )

        final_state = check_login_state(driver, account)
        if final_state == LoginState.SIGNED_IN_OTHER:
            return AuthResult(
                status=AuthStatus.WRONG_ACCOUNT,
                message=(
                    f"A different Google account is signed into the Chrome profile for '{account.email}'.\n\n"
                    "Please switch to the correct account and try again."
                ),
                profile_dir=profile_dir,
            )

        return AuthResult(
            status=AuthStatus.FAILED,
            message=(
                f"Could not complete authentication for '{account.email}'.\n\n"
                "Please verify the credentials or check the Logs tab for details."
            ),
            profile_dir=profile_dir,
        )

    except ProfileBusyError:
        msg = (
            f"The Chrome profile for '{account.email}' is currently in use by another session.\n\n"
            "Please stop the active session and try again."
        )
        emit(log_queue, f"[Account Auth] Profile busy for {account.email}.", logging.WARNING)
        return AuthResult(status=AuthStatus.PROFILE_BUSY, message=msg, profile_dir=profile_dir)

    except Exception as exc:
        if is_session_disconnected_error(exc):
            emit(
                log_queue,
                f"[Account Auth] Chrome window closed before authentication finished for {account.email}.",
                logging.WARNING,
            )
            return AuthResult(
                status=AuthStatus.CANCELLED,
                message=f"The Chrome browser was closed before authentication completed for '{account.email}'.",
                profile_dir=profile_dir,
            )
        emit(log_queue, f"[Account Auth] Error authenticating {account.email}: {exc}", logging.ERROR)
        return AuthResult(
            status=AuthStatus.FAILED,
            message=f"Authentication failed for '{account.email}':\n{exc}",
            profile_dir=profile_dir,
        )

    finally:
        if active_driver_callback is not None:
            try:
                active_driver_callback(None)
            except Exception:
                pass
        if driver is not None:
            close_driver(driver, log_queue=log_queue)
