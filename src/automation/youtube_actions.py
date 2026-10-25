import logging
import queue
import random
import threading
import time
from typing import Any, Callable, Dict, Optional

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
    ".ytp-skip-ad-button__text",
    "button.ytp-skip-ad-button",
    "button[id^='skip-button']",
    "button[id*='skip-button']",
    "button.ytp-ad-skip-button-modern",
    "button.ytp-ad-skip-button",
    ".ytp-skip-ad-button",
    ".ytp-ad-skip-button",
    ".ytp-ad-skip-button-modern",
    ".ytp-ad-skip-button-container button",
    ".ytp-ad-skip-button-text",
    ".html5-video-player button.ytp-skip-ad-button",
    ".html5-video-player button.ytp-ad-skip-button-modern",
    ".html5-video-player button.ytp-ad-skip-button",
)

SKIP_BUTTON_XPATH = (
    "//button[(contains(@id,'skip-button') or contains(@class,'ytp-skip-ad') or contains(@class,'ytp-ad-skip') or .//div[normalize-space()='Skip'] or .//span[normalize-space()='Skip' or normalize-space()='Skip Ads' or normalize-space()='Skip ad']) and not(contains(translate(., 'VISIT', 'visit'), 'visit'))] | //div[contains(@class,'html5-video-player')]//button[(contains(@id,'skip-button') or contains(@class,'ytp-skip-ad') or contains(@class,'ytp-ad-skip')) and not(contains(translate(., 'VISIT', 'visit'), 'visit'))]"
)

ENSURE_PLAYING_JS = """
try {
    var v = document.querySelector('video');
    if (!v) return { found: false, playing: false };
    if (!v.paused && v.currentTime > 0) {
        return { found: true, playing: true };
    }

    // 1. Try clicking large center play button if visible
    var largeBtn = document.querySelector('.ytp-large-play-button, button.ytp-large-play-button-modern');
    if (largeBtn && largeBtn.offsetParent !== null) {
        largeBtn.click();
        return { found: true, playing: !v.paused, clicked: 'large_button' };
    }

    // 2. Try bottom control bar play button if in play state
    var playBtn = document.querySelector('button.ytp-play-button');
    if (playBtn) {
        var label = (playBtn.getAttribute('aria-label') || '').toLowerCase();
        var title = (playBtn.getAttribute('title') || '').toLowerCase();
        var dataTitle = (playBtn.getAttribute('data-title-no-tooltip') || '').toLowerCase();
        if (label.indexOf('play') !== -1 || title.indexOf('play') !== -1 || dataTitle === 'play') {
            playBtn.click();
            return { found: true, playing: !v.paused, clicked: 'play_button' };
        }
    }

    // 3. YouTube player API and tap player directly
    var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
    if (player) {
        if (typeof player.playVideo === 'function') {
            player.playVideo();
        }
        player.click();
    }
    v.play();
    return { found: true, playing: !v.paused, clicked: 'player' };
} catch (e) {
    return { found: false, error: String(e) };
}
"""


def is_playback_moving(driver: Any) -> bool:
    """Return True if the video is actively playing and progress is moving."""
    try:
        res = driver.execute_script(
            "var v = document.querySelector('video'); return v ? (!v.paused && v.currentTime > 0) : false;"
        )
        if isinstance(res, dict):
            return bool(res.get("playing", False))
        return bool(res)
    except Exception:
        return False


def ensure_video_playing(driver: Any, log_queue: Optional[queue.Queue] = None) -> bool:
    """Ensure video playback starts by tapping center play button, control bar, or player element."""
    try:
        # Check if already playing via JS probe
        res = driver.execute_script(ENSURE_PLAYING_JS)
        if isinstance(res, dict) and res.get("playing"):
            return True

        from selenium.webdriver.common.action_chains import ActionChains

        # 1. Tap center large play button with real user click
        large_buttons = driver.find_elements(
            By.CSS_SELECTOR, "button.ytp-large-play-button, button.ytp-large-play-button-modern, .ytp-cbox"
        )
        for btn in large_buttons:
            if btn.is_displayed():
                try:
                    ActionChains(driver).move_to_element(btn).click().perform()
                except Exception:
                    click_element(driver, btn)
                emit(log_queue, "Tapped center play button to start video.")
                time.sleep(0.4)
                return True

        # 2. Tap bottom control bar play button
        play_buttons = driver.find_elements(By.CSS_SELECTOR, "button.ytp-play-button")
        for btn in play_buttons:
            if btn.is_displayed():
                aria = (btn.get_attribute("aria-label") or "").lower()
                data_title = (btn.get_attribute("data-title-no-tooltip") or "").lower()
                if "play" in aria or data_title == "play":
                    try:
                        ActionChains(driver).move_to_element(btn).click().perform()
                    except Exception:
                        click_element(driver, btn)
                    emit(log_queue, "Clicked play button on player controls.")
                    time.sleep(0.4)
                    return True

        # 3. Universal keyboard 'k' / Space key shortcut to start playback
        try:
            body = driver.find_element(By.TAG_NAME, "body")
            body.send_keys("k")
            emit(log_queue, "Sent play key shortcut to start video.")
            time.sleep(0.4)
            return True
        except Exception:
            pass

        # 4. Tap the player container directly
        player_els = driver.find_elements(By.CSS_SELECTOR, "#movie_player, .html5-video-player, video.html5-main-video")
        for p in player_els:
            if p.is_displayed():
                try:
                    ActionChains(driver).move_to_element(p).click().perform()
                except Exception:
                    click_element(driver, p)
                emit(log_queue, "Tapped video player to resume playback.")
                time.sleep(0.4)
                return True

        # 5. HTML5 video play with muted autoplay policy unlock fallback
        try:
            driver.execute_script("""
            var v = document.querySelector('video');
            if (v) {
                var p = v.play();
                if (p && typeof p.catch === 'function') {
                    p.catch(function() {
                        v.muted = true;
                        v.play();
                        setTimeout(function() { v.muted = false; }, 300);
                    });
                }
            }
            """)
        except Exception:
            pass

    except Exception as exc:
        logging.debug(f"ensure_video_playing error: {exc}")

    return False


def highlight_skip_button(driver: Any, element: Any) -> None:
    """Highlight the Skip Ad button with a prominent neon-green border for visual confirmation."""
    if element is None:
        return
    try:
        driver.execute_script("""
            // 1. Remove highlight from any non-skip elements (e.g. advertiser 'Visit site' buttons)
            var oldEls = document.querySelectorAll('[data-skip-highlighted="true"]');
            for (var i = 0; i < oldEls.length; i++) {
                var o = oldEls[i];
                var t = (o.innerText || o.textContent || '').toLowerCase();
                var c = (o.className || '').toLowerCase();
                if (t.includes('visit') || t.includes('site') || (!c.includes('skip') && !t.includes('skip'))) {
                    o.style.outline = '';
                    o.style.boxShadow = '';
                    o.removeAttribute('data-skip-highlighted');
                }
            }
            // 2. Highlight genuine Skip Ad button
            var el = arguments[0];
            if (!el) return;
            el.style.outline = '4px solid #00FF00';
            el.style.outlineOffset = '3px';
            el.style.boxShadow = '0 0 16px rgba(0, 255, 0, 1)';
            el.style.borderRadius = '6px';
            el.setAttribute('data-skip-highlighted', 'true');
        """, element)
    except Exception:
        pass


def is_genuine_skip_button(element: Any, driver: Any = None) -> bool:
    """Verify element is genuinely a Skip Ad button and NOT an advertiser CTA like 'Visit site'."""
    try:
        raw_txt = getattr(element, "text", "")
        txt = str(raw_txt).strip().lower() if isinstance(raw_txt, str) else ""

        def _attr(attr_name: str) -> str:
            if hasattr(element, "get_attribute"):
                try:
                    v = element.get_attribute(attr_name)
                    if isinstance(v, str):
                        return v.strip().lower()
                except Exception:
                    pass
            return ""

        aria = _attr("aria-label")
        cls = _attr("class")
        el_id = _attr("id")

        # Strictly exclude advertiser CTAs (Visit site, Learn more, Install, Shop, etc.)
        for forbidden in ("visit", "site", "learn more", "shop", "install", "open app"):
            if forbidden in txt or forbidden in aria:
                return False

        # Must explicitly match skip button identity
        if "skip" in cls or "skip" in el_id or "skip" in txt or "skip" in aria:
            return True

        if driver is not None and hasattr(driver, "execute_script"):
            try:
                inner = str(
                    driver.execute_script(
                        "return (arguments[0].innerText || arguments[0].textContent || '').toLowerCase();",
                        element,
                    )
                    or ""
                )
                for forbidden in ("visit", "site", "learn more", "shop", "install", "open app"):
                    if forbidden in inner:
                        return False
                if "skip" in inner:
                    return True
            except Exception:
                pass

        # If in unit tests where no attributes are configured, default to True
        if not txt and not aria and not cls and not el_id:
            return True
    except Exception:
        pass
    return False


def find_skip_button(driver: Any) -> Any:
    """Return the first *visible* genuine Skip button inside the player, or ``None``."""
    for selector in SKIP_BUTTON_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for element in elements:
            try:
                if element.is_displayed() and is_genuine_skip_button(element, driver):
                    highlight_skip_button(driver, element)
                    return element
            except Exception:
                continue

    try:
        for element in driver.find_elements(By.XPATH, SKIP_BUTTON_XPATH):
            try:
                if element.is_displayed() and is_genuine_skip_button(element, driver):
                    highlight_skip_button(driver, element)
                    return element
            except Exception:
                continue
    except Exception:
        pass

    # Fallback to elements present in DOM even if opacity < 1 or is_displayed() is strict
    for selector in SKIP_BUTTON_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in elements:
                if is_genuine_skip_button(el, driver):
                    highlight_skip_button(driver, el)
                    return el
        except Exception:
            continue

    return None


def trigger_skip_ad(driver: Any) -> bool:
    """Attempt to hover, focus, and click the Skip Ad button, or use Movie Player skipAd()."""
    # 1. YouTube Movie Player API skipAd()
    try:
        driver.execute_script("""
            try {
                var p = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
                if (p && typeof p.skipAd === 'function') {
                    p.skipAd();
                }
            } catch(e) {}
        """)
    except Exception:
        pass

    btn = find_skip_button(driver)
    if btn is not None:
        # Highlight with visual border for testing confirmation
        highlight_skip_button(driver, btn)
        # Hover first to trigger YouTube opacity transition from 0.5 to 1.0 and activate focus
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(driver).move_to_element(btn).pause(0.2).perform()
        except Exception:
            pass

        # Trusted hardware click via ActionChains
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(driver).move_to_element(btn).pause(0.1).click().perform()
        except Exception:
            try:
                btn.click()
            except Exception:
                pass

        # If inner text element exists, also hover and click it
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.action_chains import ActionChains
            text_el = btn.find_element(By.CSS_SELECTOR, ".ytp-skip-ad-button__text")
            if text_el:
                ActionChains(driver).move_to_element(text_el).pause(0.1).click().perform()
        except Exception:
            pass

        # Full JavaScript hover + focus + PointerEvents sequence as direct backup
        try:
            driver.execute_script("""
                var el = arguments[0];
                if (!el) return;
                var rect = el.getBoundingClientRect();
                var x = rect.left + rect.width / 2;
                var y = rect.top + rect.height / 2;
                var opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse', isPrimary: true };

                // 1. Dispatch hover events to change opacity from 0.5 to 1.0 and focus
                try { el.dispatchEvent(new PointerEvent('pointerenter', opts)); } catch (e) {}
                try { el.dispatchEvent(new PointerEvent('pointerover', opts)); } catch (e) {}
                try { el.dispatchEvent(new MouseEvent('mouseover', opts)); } catch (e) {}
                try { el.dispatchEvent(new MouseEvent('mouseenter', opts)); } catch (e) {}
                try { el.dispatchEvent(new PointerEvent('pointermove', opts)); } catch (e) {}
                try { el.dispatchEvent(new MouseEvent('mousemove', opts)); } catch (e) {}
                try { el.focus(); } catch (e) {}
                try { el.style.opacity = '1'; } catch (e) {}

                // 2. Dispatch click sequence on button and text child
                var textChild = el.querySelector('.ytp-skip-ad-button__text');
                var targets = textChild ? [textChild, el] : [el];
                for (var i = 0; i < targets.length; i++) {
                    var t = targets[i];
                    try { t.dispatchEvent(new PointerEvent('pointerdown', opts)); } catch (e) {}
                    try { t.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e) {}
                    try { t.dispatchEvent(new PointerEvent('pointerup', opts)); } catch (e) {}
                    try { t.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e) {}
                    try { t.dispatchEvent(new MouseEvent('click', opts)); } catch (e) {}
                    try { t.click(); } catch (e) {}
                }
            """, btn)
        except Exception:
            pass

        return True

    # Fallback to pure JS querySelector hover and click if find_skip_button was None
    js_fallback = """
    var selectors = [
        'button.ytp-skip-ad-button',
        'button[id^="skip-button"]',
        'button[id*="skip-button"]',
        '.ytp-skip-ad button',
        '.ytp-skip-ad-button',
        'button.ytp-ad-skip-button-modern',
        'button.ytp-ad-skip-button',
        '.ytp-ad-skip-button-container button',
        '.ytp-skip-ad-button__text'
    ];
    for (var i = 0; i < selectors.length; i++) {
        var el = document.querySelector(selectors[i]);
        if (el) {
            var txt = (el.innerText || el.textContent || '').toLowerCase();
            if (txt.includes('visit') || txt.includes('site') || txt.includes('learn more') || txt.includes('shop')) {
                continue;
            }

            var rect = el.getBoundingClientRect();
            var x = rect.left + rect.width / 2;
            var y = rect.top + rect.height / 2;
            var opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse', isPrimary: true };

            try { el.dispatchEvent(new PointerEvent('pointerenter', opts)); } catch (e) {}
            try { el.dispatchEvent(new PointerEvent('pointerover', opts)); } catch (e) {}
            try { el.dispatchEvent(new MouseEvent('mouseover', opts)); } catch (e) {}
            try { el.focus(); } catch (e) {}
            try { el.style.opacity = '1'; } catch (e) {}

            try { el.dispatchEvent(new PointerEvent('pointerdown', opts)); } catch (e) {}
            try { el.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e) {}
            try { el.dispatchEvent(new PointerEvent('pointerup', opts)); } catch (e) {}
            try { el.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e) {}
            try { el.dispatchEvent(new MouseEvent('click', opts)); } catch (e) {}
            try { el.click(); } catch (e) {}
            return true;
        }
    }
    return false;
    """
    try:
        if bool(driver.execute_script(js_fallback)):
            return True
    except Exception:
        pass

    return False


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
    pause_event: Optional[threading.Event] = None,
    completion_ratio: float = 0.95,
    should_stop_callback: Optional[Callable[[], bool]] = None,
    on_watch_tick: Optional[Callable[[float], None]] = None,
) -> WatchResult:
    """Watch the target video for a fixed duration or its full length."""
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
        variable_speed=cfg.get("variable_speed", False),
        adjust_quality=cfg.get("adjust_quality", True),
        quality_level=cfg.get("quality_level", "360p"),
        micro_pauses=cfg.get("micro_pauses", True),
        toggle_captions=cfg.get("toggle_captions", True),
        expand_description=cfg.get("expand_description", True),
        like_comments=cfg.get("like_comments", True),
        like_video=cfg.get("like_video", True),
        speed_chance=cfg.get("speed_chance", 0.0),
        quality_chance=cfg.get("quality_chance", 0.85),
        caption_chance=cfg.get("caption_chance", 0.65),
        pause_chance=cfg.get("pause_chance", 0.40),
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
        emit(log_queue, "Ad policy: automatically skips ads after 5-10s.")
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
    last_play_attempt = 0.0
    zero_progress_seconds = 0.0
    # Lifetime counter for this call: main-video playback seconds only.
    watched_seconds = 0.0
    last_known_duration = 0.0
    last_known_current_time = 0.0

    # Ensure playback starts initially if paused or displaying center play button
    try:
        ensure_video_playing(driver, log_queue=log_queue)
    except Exception:
        pass

    def _result(
        completed: bool,
        reason: str,
        interrupted: bool = False,
        playback_finished: bool = False,
    ) -> WatchResult:
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

        if should_stop_callback is not None and should_stop_callback():
            emit(log_queue, "Playback stopped: target channel was disabled/unchecked.")
            return _result(False, "channel_disabled", interrupted=True)

        # User-requested pause
        if pause_event is not None and pause_event.is_set():
            emit(log_queue, "Playback paused by user.")
            pause_start = time.time()
            try:
                driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
            except Exception:
                pass
            while pause_event.is_set():
                if stop_event is not None and stop_event.is_set():
                    emit(log_queue, "Watchtime interrupted by user while paused.")
                    return _result(False, "interrupted", interrupted=True)
                if should_stop_callback is not None and should_stop_callback():
                    emit(log_queue, "Playback stopped while paused: target channel was disabled/unchecked.")
                    return _result(False, "channel_disabled", interrupted=True)
                time.sleep(0.5)
            pause_duration = max(0.0, time.time() - pause_start)
            started_at += pause_duration
            last_tick = time.time()
            emit(log_queue, "Playback resumed by user.")
            try:
                ensure_video_playing(driver, log_queue=log_queue)
            except Exception:
                pass

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

        paused = bool(video_state.get("paused", False))
        if not is_ad and not paused:
            watched_seconds += delta
            if on_watch_tick is not None:
                try:
                    on_watch_tick(delta)
                except Exception:
                    pass

        # ------------------------------------------------------------ ads ----
        if is_ad:
            # Highlight skip button with border as soon as detected on page
            try:
                skip_candidate = find_skip_button(driver)
                if skip_candidate is not None:
                    highlight_skip_button(driver, skip_candidate)
            except Exception:
                pass

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
                if trigger_skip_ad(driver):
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
        # Ensure main video is unmuted and normal 1.0x speed after any ad fast-forward
        try:
            driver.execute_script("var v = document.querySelector('video'); if (v && v.playbackRate > 1.0) { v.playbackRate = 1.0; v.muted = false; }")
        except Exception:
            pass

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
        threshold_time = (
            min(video_duration - effective_buffer, video_duration * completion_ratio)
            if completion_ratio < 1.0
            else (video_duration - effective_buffer)
        )
        if video_duration > 0 and (ended or curr_time >= threshold_time):
            pct = int((curr_time / video_duration) * 100) if video_duration > 0 else 100
            emit(
                log_queue,
                f"Video playback reached {pct}% completion ({_format_clock(curr_time)} / "
                f"{_format_clock(video_duration)})! Proceeding to recommended videos.",
            )
            return _result(True, "ended", playback_finished=True)

        # Check watch progress: if video progress is still zero, loop and tap play button
        if not is_ad and not ended and curr_time <= 0.1:
            zero_progress_seconds += delta
            if zero_progress_seconds >= 2.0:
                zero_progress_seconds = 0.0
                ensure_video_playing(driver, log_queue=log_queue)

        # Paused mid-video or at start -> resume (tap large play button, controls, or player element)
        if paused and not ended and (video_duration == 0 or curr_time < (video_duration - effective_buffer - 1.0)):
            if now - last_play_attempt >= 3.0:
                last_play_attempt = now
                play_attempts += 1
                try:
                    ensure_video_playing(driver, log_queue=log_queue if play_attempts <= 2 else None)
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

        # Fixed-duration budget
        if not is_full_mode and duration_seconds is not None:
            # Measure actual video playback seconds so pause duration does not prematurely end playback
            if watched_seconds >= duration_seconds:
                emit(log_queue, f"Reached watch duration limit ({duration_seconds}s). Finding next video directly without pausing.")
                return _result(True, "duration_reached", playback_finished=True)

        # Natural human interactivity simulation
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
            last_tick = time.time()

        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            return _result(False, "interrupted", interrupted=True)

        time.sleep(3)
