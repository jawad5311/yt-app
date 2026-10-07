import logging
import queue
import random
import threading
import time
from typing import Any, Optional

from selenium.webdriver.common.by import By

from ..utils.logger import emit
from .ads import PLAYER_STATE_JS, AdWatchState

# Abort the watch loop when no <video> element is present for this many seconds.
VIDEO_MISSING_LIMIT_SECONDS = 60

# Safety net for "Full Video" mode so a player that never reaches `ended`
# cannot hang a worker forever. Overridable via the FullVideoTimeoutMinutes setting.
DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS = 6 * 3600

# Skip-button discovery: modern + legacy class names, scoped to the player so a
# generic "Skip" button elsewhere on the page is never clicked.
SKIP_BUTTON_SELECTORS = (
    ".html5-video-player button.ytp-ad-skip-button-modern",
    ".html5-video-player button.ytp-skip-ad-button",
    ".html5-video-player button.ytp-ad-skip-button",
    ".html5-video-player .ytp-ad-skip-button-container button",
    ".html5-video-player .ytp-ad-skip-button-text",
    "button.ytp-ad-skip-button-modern",
    ".ytp-ad-skip-button",
    ".ytp-skip-ad-button",
)

SKIP_BUTTON_XPATH = (
    "//div[contains(@class,'html5-video-player')]//button["
    "@id='skip-button' or contains(@class,'ytp-ad-skip') or contains(@class,'ytp-skip-ad')"
    " or .//span[normalize-space()='Skip' or normalize-space()='Skip Ads'"
    " or normalize-space()='Skip ad' or normalize-space()='Überspringen'"
    " or normalize-space()='Anzeigen überspringen']]"
)


def find_skip_button(driver: Any) -> Any:
    """Return the first *visible* Skip button inside the player, or ``None``."""
    for selector in SKIP_BUTTON_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue

    try:
        for element in driver.find_elements(By.XPATH, SKIP_BUTTON_XPATH):
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue
    except Exception:
        pass

    return None


def click_element(driver: Any, element: Any) -> bool:
    """Click an element, preferring a synthetic click so overlays cannot swallow it."""
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        try:
            element.click()
            return True
        except Exception:
            return False


def _format_clock(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def watch_video(
    driver: Any,
    duration_seconds: Optional[int],
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    full_timeout_seconds: int = DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS,
    skip_ads: bool = True,
    max_ad_seconds: int = 32,
) -> bool:
    """Watch the target video for a fixed duration or its full length.

    Args:
        duration_seconds: fixed watch budget in seconds, or ``None`` for Full Video.
        full_timeout_seconds: hard cap for Full Video mode (``0`` disables it).
        skip_ads: ``True`` -> ad blocker + fallback skip after the 30-35s
            monetisation window; ``False`` -> watch ads, escaping only once
            cumulative ad time reaches ``max_ad_seconds`` (``0`` = never).
        max_ad_seconds: cumulative ad-watch cap used when ``skip_ads`` is off.

    Returns:
        True  -> the watch finished normally (duration reached, video finished,
                 or the Full Video safety timeout tripped).
        False -> the watch was interrupted by the user or failed (e.g. no video
                 element could be found); callers may retry.
    """
    is_full_mode = duration_seconds is None or duration_seconds <= 0
    ad_state = AdWatchState()

    if is_full_mode:
        emit(log_queue, "Watching full video until playback finishes...")
        if full_timeout_seconds > 0:
            emit(log_queue, f"Full-video safety timeout: {full_timeout_seconds // 60} minutes.")
    else:
        emit(log_queue, f"Watching video for up to {duration_seconds} seconds...")

    if skip_ads:
        emit(log_queue, "Ad policy: blocker ON, fallback skips ads after the 30-35s monetisation window.")
    elif max_ad_seconds > 0:
        emit(log_queue, f"Ad policy: watching all ads, escape cap at {max_ad_seconds}s.")
    else:
        emit(log_queue, "Ad policy: watching all ads, no escape cap.")

    started_at = time.time()
    last_tick = started_at
    last_progress_log = 0.0
    last_ad_log = 0.0
    video_missing_since: Optional[float] = None
    escape_warned = False
    play_attempts = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            return False

        now = time.time()
        delta = max(0.0, now - last_tick)
        last_tick = now
        elapsed = now - started_at

        try:
            video_state = driver.execute_script(PLAYER_STATE_JS)
        except Exception:
            video_state = {"found": False}

        if not video_state.get("found"):
            if video_missing_since is None:
                video_missing_since = now
            elif now - video_missing_since >= VIDEO_MISSING_LIMIT_SECONDS:
                emit(
                    log_queue,
                    f"No video element found for {VIDEO_MISSING_LIMIT_SECONDS}s. Aborting watch.",
                    logging.ERROR,
                )
                return False
            time.sleep(3)
            continue

        video_missing_since = None
        is_ad = bool(video_state.get("isAd"))
        ad_state.tick(is_ad, delta)

        # ------------------------------------------------------------ ads ----
        if is_ad:
            if now - last_ad_log >= 30:
                last_ad_log = now
                if skip_ads:
                    emit(
                        log_queue,
                        f"Ad playing: {ad_state.break_ad_seconds:.0f}s into this break "
                        f"(skip at ~{ad_state.skip_target:.0f}s).",
                    )
                else:
                    emit(
                        log_queue,
                        f"Ad playing: {ad_state.break_ad_seconds:.0f}s break / "
                        f"{ad_state.total_ad_seconds:.0f}s total (cap {max_ad_seconds}s). Watching it.",
                    )

            reason = ad_state.skip_reason(skip_ads, max_ad_seconds)
            if reason is not None:
                button = find_skip_button(driver)
                if button is not None and click_element(driver, button):
                    emit(log_queue, f"Clicked Skip Ad - {reason}.")
                    escape_warned = False
                elif not skip_ads and not escape_warned:
                    escape_warned = True
                    emit(
                        log_queue,
                        "Ad cap reached but no Skip button found (unskippable ad). Waiting for it to finish.",
                        logging.WARNING,
                    )

            if is_full_mode and full_timeout_seconds > 0 and elapsed >= full_timeout_seconds:
                emit(
                    log_queue,
                    f"Full video safety timeout reached ({full_timeout_seconds}s). Stopping watch.",
                    logging.WARNING,
                )
                return True

            time.sleep(3)
            continue

        # ----------------------------------------------------- main video ----
        escape_warned = False
        ended = bool(video_state.get("ended", False))
        curr_time = float(video_state.get("currentTime", 0.0))
        video_duration = float(video_state.get("duration", 0.0))
        paused = bool(video_state.get("paused", False))

        # Playback finished
        if video_duration > 0 and (ended or curr_time >= (video_duration - 1.5)):
            emit(log_queue, f"Video playback finished! (Watched full length: {_format_clock(video_duration)})")
            return True

        # Paused mid-video -> resume (JS first, real play button as fallback)
        if paused and not ended and (video_duration == 0 or curr_time < (video_duration - 2)):
            try:
                driver.execute_script("var v = document.querySelector('video'); if (v) v.play();")
                play_attempts += 1
                if play_attempts <= 3:
                    still_paused = driver.execute_script(
                        "var v = document.querySelector('video'); return v ? v.paused : true;"
                    )
                    if still_paused:
                        play_buttons = driver.find_elements(By.CSS_SELECTOR, "button.ytp-play-button")
                        if play_buttons:
                            click_element(driver, play_buttons[0])
            except Exception as exc:
                logging.debug(f"Could not resume playback: {exc}")

        # Periodic progress
        if now - last_progress_log >= 30:
            last_progress_log = now
            if video_duration > 0:
                percent = int((curr_time / video_duration) * 100)
                mode = "full video progress" if is_full_mode else "watch progress"
                emit(
                    log_queue,
                    f"Watching {mode}: {_format_clock(curr_time)} / "
                    f"{_format_clock(video_duration)} ({percent}%)",
                )

        # Full Video safety timeout
        if is_full_mode and full_timeout_seconds > 0 and elapsed >= full_timeout_seconds:
            emit(
                log_queue,
                f"Full video safety timeout reached ({full_timeout_seconds}s). Stopping watch.",
                logging.WARNING,
            )
            return True

        # Fixed-duration budget counts *watching* time only: seconds spent in
        # ads are excluded so "watch all ads" mode cannot eat the whole budget.
        if not is_full_mode and duration_seconds is not None:
            watched_elapsed = elapsed - ad_state.total_ad_seconds
            if watched_elapsed >= duration_seconds:
                emit(log_queue, f"Reached watch duration limit ({duration_seconds}s).")
                return True

        time.sleep(3)

        # Occasional random micro-scroll while the main video plays
        if random.random() < 0.2:
            try:
                scroll_amount = random.randint(100, 300)
                driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
                time.sleep(random.uniform(1.0, 2.0))
                driver.execute_script(f"window.scrollBy(0, -{scroll_amount});")
            except Exception:
                pass
