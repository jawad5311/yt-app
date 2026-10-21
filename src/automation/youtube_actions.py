import logging
import queue
import random
import threading
import time
from typing import Any, Dict, Optional

from selenium.webdriver.common.by import By

from ..models.watch_stats import WatchResult
from ..utils.helpers import extract_video_id
from ..utils.logger import emit
from .ads import PLAYER_STATE_JS, AdWatchState
from .interactivity import HumanInteractivitySimulator

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


DISABLE_AUTOPLAY_JS = """
try {
    var btn = document.querySelector('.ytp-autonav-toggle-button, button[data-tooltip-target-id="ytp-autonav-toggle-button"]');
    if (btn) {
        var isChecked = btn.getAttribute('aria-checked') === 'true' || btn.classList.contains('ytp-autonav-toggle-button-checked');
        if (isChecked) {
            btn.click();
            return true;
        }
    }
} catch (e) {}
try {
    var player = document.getElementById('movie_player');
    if (player && typeof player.setAutonavState === 'function') {
        player.setAutonavState(1);
        return true;
    }
} catch (e) {}
return false;
"""


def disable_player_autoplay(driver: Any) -> bool:
    """Turn off YouTube native Autoplay toggle if it is currently enabled."""
    try:
        return bool(driver.execute_script(DISABLE_AUTOPLAY_JS))
    except Exception:
        return False


def calculate_end_buffer(video_duration: float, target_buffer: float = 10.0) -> float:
    """Determine how many seconds before video end to stop playback.

    Stops 5-15 seconds early so YouTube's autoplay countdown overlay and next-video
    transition never trigger. Scales down smoothly for short videos (< 60s)
    so at least ~85-90% of the video is watched.
    """
    if video_duration <= 0:
        return 1.5
    # Cap buffer to 12% of duration for shorter videos, with a minimum of 1.5s
    max_allowed = max(1.5, video_duration * 0.12)
    return min(target_buffer, max_allowed)


def watch_video(
    driver: Any,
    duration_seconds: Optional[int],
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    full_timeout_seconds: int = DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS,
    skip_ads: bool = True,
    max_ad_seconds: int = 32,
    end_buffer_seconds: float = 0.0,
    enable_interactivity: bool = True,
    interactivity_config: Optional[Dict[str, Any]] = None,
) -> WatchResult:
    """Watch the target video for a fixed duration or its full length.

    Args:
        duration_seconds: fixed watch budget in seconds, or ``None`` for Full Video.
        full_timeout_seconds: hard cap for Full Video mode (``0`` disables it).
        skip_ads: ``True`` -> ad blocker + fallback skip after the 30-35s
            monetisation window; ``False`` -> watch ads, escaping only once
            cumulative ad time reaches ``max_ad_seconds`` (``0`` = never).
        max_ad_seconds: cumulative ad-watch cap used when ``skip_ads`` is off.
        end_buffer_seconds: early stop buffer before video ends (5-15s) to prevent
            YouTube autoplay. ``0`` selects a randomized 7-12s buffer.
        enable_interactivity: whether to simulate human interactivity.
        interactivity_config: optional dict configuring interactivity features.

    Returns:
        A :class:`WatchResult`; ``bool(result)`` is True when the watch finished
        normally (duration reached, video finished, or the Full Video safety
        timeout tripped) and False when it was interrupted or failed (e.g. no
        video element could be found). ``watched_seconds`` counts main-video
        playback only: ad seconds and paused gaps are excluded.
    """
    is_full_mode = duration_seconds is None or duration_seconds <= 0
    ad_state = AdWatchState()
    target_buffer = (
        random.uniform(7.0, 12.0)
        if (end_buffer_seconds is None or end_buffer_seconds <= 0)
        else float(end_buffer_seconds)
    )

    cfg = interactivity_config or {}
    simulator = HumanInteractivitySimulator(
        enabled=enable_interactivity,
        scroll_comments=cfg.get("scroll_comments", True),
        variable_speed=cfg.get("variable_speed", True),
        adjust_quality=cfg.get("adjust_quality", True),
        quality_level=cfg.get("quality_level", "360p"),
        micro_pauses=cfg.get("micro_pauses", True),
        speed_chance=cfg.get("speed_chance", 0.35),
        quality_chance=cfg.get("quality_chance", 0.60),
    )

    initial_video_id: Optional[str] = None
    try:
        initial_video_id = extract_video_id(getattr(driver, "current_url", None))
    except Exception:
        pass
    autoplay_disabled = False

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
    last_heartbeat_log = 0.0
    video_missing_since: Optional[float] = None
    escape_warned = False
    play_attempts = 0
    # Lifetime counter for this call: main-video playback seconds only.
    watched_seconds = 0.0
    last_known_duration = 0.0
    last_known_current_time = 0.0

    def _result(
        completed: bool,
        reason: str,
        interrupted: bool = False,
        playback_finished: bool = False,
    ) -> WatchResult:
        # Determine final effective watch seconds for this single run:
        # If currentTime is greater than watched_seconds, use currentTime
        # (reflects exact point reached on timeline).
        final_watched = max(watched_seconds, last_known_current_time)
        return WatchResult(
            completed=completed,
            interrupted=interrupted,
            watched_seconds=final_watched,
            ad_seconds=ad_state.total_ad_seconds,
            reason=reason,
            video_duration=last_known_duration,
            final_current_time=last_known_current_time,
            playback_finished=playback_finished,
        )

    while True:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            return _result(False, "interrupted", interrupted=True)

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
                return _result(False, "no_video")
            time.sleep(3)
            continue

        video_missing_since = None

        # Proactively turn off YouTube's native autoplay switch once playback starts
        if not autoplay_disabled:
            if disable_player_autoplay(driver):
                emit(log_queue, "Disabled YouTube native autoplay toggle.")
            autoplay_disabled = True

        # Safety net: detect if YouTube navigated away to an autoplayed next video
        if initial_video_id:
            try:
                curr_url = getattr(driver, "current_url", "")
                curr_vid = extract_video_id(curr_url)
                if curr_vid and curr_vid != initial_video_id:
                    emit(
                        log_queue,
                        f"Video transition detected ({initial_video_id} -> {curr_vid}). "
                        "Halting task to prevent counting next video.",
                    )
                    try:
                        driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
                    except Exception:
                        pass
                    return _result(True, "autoplay_transition")
            except Exception:
                pass

        is_ad = bool(video_state.get("isAd"))
        ad_state.tick(is_ad, delta)

        # Watch time is main-video playback only: ad seconds and paused gaps
        # are excluded so "watch all ads" mode cannot inflate the total.
        paused = bool(video_state.get("paused", False))
        if not is_ad and not paused:
            watched_seconds += delta

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
                return _result(True, "full_timeout")

            time.sleep(3)
            continue

        # ----------------------------------------------------- main video ----
        escape_warned = False
        ended = bool(video_state.get("ended", False))
        curr_time = float(video_state.get("currentTime", 0.0))
        video_duration = float(video_state.get("duration", 0.0))
        if video_duration > 0:
            last_known_duration = video_duration
        if curr_time > 0:
            last_known_current_time = curr_time

        # Every minute: log live playback timeline heartbeat
        if now - last_heartbeat_log >= 60.0:
            last_heartbeat_log = now
            cur_fmt = _format_clock(curr_time)
            dur_fmt = _format_clock(video_duration) if video_duration > 0 else "unknown"
            emit(
                log_queue,
                f"Playback timeline update: video currently playing at {cur_fmt} / {dur_fmt} (watchtime counter: {int(watched_seconds // 60)}m {int(watched_seconds % 60):02d}s)",
            )

        # Playback finished (stops before autoplay countdown or next-video navigation triggers)
        effective_buffer = calculate_end_buffer(video_duration, target_buffer)
        if video_duration > 0 and (ended or curr_time >= (video_duration - effective_buffer)):
            remaining = max(0.0, video_duration - curr_time)
            emit(
                log_queue,
                f"Video playback finished! (Watched: {_format_clock(curr_time)} / "
                f"{_format_clock(video_duration)}, completed {remaining:.1f}s early to prevent autoplay)",
            )
            try:
                driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
            except Exception:
                pass
            return _result(True, "ended", playback_finished=True)

        # Paused mid-video -> resume (JS first, real play button as fallback)
        if paused and not ended and (video_duration == 0 or curr_time < (video_duration - effective_buffer - 1.0)):
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
            return _result(True, "full_timeout")

        # Fixed-duration budget counts *watching* time only: seconds spent in
        # ads are excluded so "watch all ads" mode cannot eat the whole budget.
        if not is_full_mode and duration_seconds is not None:
            watched_elapsed = elapsed - ad_state.total_ad_seconds
            if watched_elapsed >= duration_seconds:
                emit(log_queue, f"Reached watch duration limit ({duration_seconds}s).")
                return _result(True, "duration_reached", playback_finished=True)

        # Natural human interactivity simulation (scrolling down to comments, variable speed/quality, micro-pauses)
        interactive_event = simulator.on_playback_tick(
            driver,
            curr_time=curr_time,
            video_duration=video_duration,
            is_ad=is_ad,
            paused=paused,
            stop_event=stop_event,
            log_queue=log_queue,
            duration_budget=duration_seconds,
        )
        if interactive_event.get("micro_paused"):
            # Micro-pause took 2-5s with video paused. Reset last_tick so paused
            # seconds are not accumulated as active watch playback time.
            last_tick = time.time()

        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            return _result(False, "interrupted", interrupted=True)

        time.sleep(3)
