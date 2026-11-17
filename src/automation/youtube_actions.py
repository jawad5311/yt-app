import logging
import queue
import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Set

from selenium.webdriver.common.by import By

from ..models.watch_stats import WatchResult
from ..utils.helpers import extract_video_id
from ..utils.logger import emit
from .ads import PLAYER_STATE_JS, AdWatchState
from .interactivity import HumanInteractivitySimulator, exit_fullscreen_mode, is_in_fullscreen, like_active_short
from .playback_meter import PlaybackMeter

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
    if (!v.paused && v['current' + 'Time'] > 0) {
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
            "var v = document.querySelector('video'); return v ? (!v.paused && v['current' + 'Time'] > 0) : false;"
        )
        if isinstance(res, dict):
            return bool(res.get("playing", False))
        return bool(res)
    except Exception:
        return False



AD_PLAYBACK_PROBE_JS = """
try {
    var player = document.querySelector('.html5-video-player');
    var isAd = !!(player && (
        player.classList.contains('ad-showing') ||
        player.classList.contains('ytp-showing-ad') ||
        player.classList.contains('ad-interrupting')
    ));
    if (!isAd && player && typeof player.getAdState === 'function') {
        var adState = player.getAdState();
        if (adState === 1 || adState === 2) isAd = true;
    }
    if (!isAd) {
        var pod = document.querySelector('.video-ads');
        if (pod && pod.childElementCount > 0) {
            var adTextCheck = pod.querySelector('.ytp-ad-text, .ytp-ad-preview-text, .ytp-ad-duration-remaining');
            if (adTextCheck && (adTextCheck.offsetParent !== null || (adTextCheck.innerText || '').trim().length > 0)) {
                isAd = true;
            }
            var skipCheck = pod.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, button[id*="skip-button"]');
            if (skipCheck && skipCheck.offsetParent !== null) {
                isAd = true;
            }
        }
    }
    var vids = document.querySelectorAll('video');
    var activeVid = null;
    for (var i = 0; i < vids.length; i++) {
        var s = String(vids[i].currentSrc || vids[i].src || '');
        if (s.indexOf('adformat=') !== -1 || s.indexOf('/api/ads/') !== -1) {
            isAd = true;
            activeVid = vids[i];
            break;
        }
        if (!vids[i].paused && vids[i]['current' + 'Time'] > 0) {
            activeVid = vids[i];
        }
    }
    if (!activeVid && vids.length) activeVid = vids[0];

    var timeCurrentEl = document.querySelector('.ytp-time-current');
    var timeDurationEl = document.querySelector('.ytp-time-duration');
    var adDurEl = document.querySelector('.ytp-ad-duration-remaining');
    var adTextEl = (adDurEl && adDurEl.offsetParent !== null) ? adDurEl : document.querySelector('.video-ads .ytp-ad-text');

    return {
        isAd: isAd,
        current_time: activeVid ? Number(activeVid['current' + 'Time'] || 0) : 0,
        duration: activeVid ? Number(activeVid.duration || 0) : 0,
        paused: activeVid ? Boolean(activeVid.paused) : true,
        timeCurrentText: timeCurrentEl ? (timeCurrentEl.innerText || '').trim() : '',
        timeDurationText: timeDurationEl ? (timeDurationEl.innerText || '').trim() : '',
        adText: (adTextEl && adTextEl.offsetParent !== null) ? (adTextEl.innerText || '').trim() : ''
    };
} catch(e) {
    return { isAd: false, error: String(e) };
}
"""


def check_ad_playback_progress(driver: Any, sample_delay: float = 0.5) -> Dict[str, Any]:
    """Check whether an ad is currently displaying and whether its playback time is actively advancing.

    Samples the ad playback state, waits sample_delay, and samples again to check
    if the time value (currentTime, time text, countdown) is changing.
    Returns:
        {
            "is_ad": bool,
            "is_changing": bool,
            "time1": float,
            "time2": float,
            "time_display": str,
        }
    """
    try:
        sample1 = driver.execute_script(AD_PLAYBACK_PROBE_JS)
    except Exception:
        return {"is_ad": False, "is_changing": False, "time1": 0.0, "time2": 0.0, "time_display": ""}

    if not isinstance(sample1, dict) or not sample1.get("isAd"):
        return {"is_ad": False, "is_changing": False, "time1": 0.0, "time2": 0.0, "time_display": ""}

    if sample_delay > 0:
        time.sleep(sample_delay)

    try:
        sample2 = driver.execute_script(AD_PLAYBACK_PROBE_JS)
    except Exception:
        sample2 = sample1

    if not isinstance(sample2, dict) or not sample2.get("isAd"):
        return {"is_ad": False, "is_changing": True, "time1": 0.0, "time2": 0.0, "time_display": "ad_finished"}

    t1 = float(sample1.get("current_time", sample1.get("currentTime", 0.0)))
    t2 = float(sample2.get("current_time", sample2.get("currentTime", 0.0)))
    txt1 = sample1.get("timeCurrentText", "")
    txt2 = sample2.get("timeCurrentText", "")
    ad_txt1 = sample1.get("adText", "")
    ad_txt2 = sample2.get("adText", "")
    paused2 = bool(sample2.get("paused", False))

    time_delta = t2 - t1
    is_time_advancing = time_delta > 0.05
    is_text_changing = (bool(txt1 and txt2 and txt1 != txt2) or
                        bool(ad_txt1 and ad_txt2 and ad_txt1 != ad_txt2))
    is_playing_normally = (not paused2) and (is_time_advancing or is_text_changing or t2 > t1)

    is_changing = is_time_advancing or is_text_changing or is_playing_normally

    display_str = f"{t1:.1f}s -> {t2:.1f}s"
    if txt2:
        display_str += f" ({txt2})"
    elif ad_txt2:
        display_str += f" ({ad_txt2})"

    return {
        "is_ad": True,
        "is_changing": is_changing,
        "time1": t1,
        "time2": t2,
        "time_display": display_str,
    }

def ensure_video_playing(
    driver: Any,
    log_queue: Optional[queue.Queue] = None,
    verbose: bool = True,
) -> bool:
    """Ensure video playback starts by tapping center play button, control bar, or player element."""
    try:
        ad_check = check_ad_playback_progress(driver, sample_delay=0.4)
        if ad_check.get("is_ad"):
            if ad_check.get("is_changing"):
                if verbose and log_queue is not None:
                    emit(log_queue, f"Ad is already playing (time changing: {ad_check.get('time_display')}). Not clicking play button.")
                return True
            else:
                if verbose and log_queue is not None:
                    emit(log_queue, f"Ad playback is stalled (time not changing: {ad_check.get('time_display')}). Clicking play button to resume.")

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
                if verbose and log_queue is not None:
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
                    if verbose and log_queue is not None:
                        emit(log_queue, "Clicked play button on player controls.")
                    time.sleep(0.4)
                    return True

        # 3. Direct HTML5 video play API call
        try:
            res_play = driver.execute_script("""
            var v = document.querySelector('video');
            if (v && v.paused) {
                var p = v.play();
                if (p && typeof p.catch === 'function') {
                    p.catch(function() {
                        v.muted = true;
                        v.play();
                    });
                }
                return true;
            }
            return false;
            """)
            if res_play:
                time.sleep(0.3)
                is_paused = driver.execute_script("var v = document.querySelector('video'); return !v || v.paused;")
                if not is_paused:
                    if verbose and log_queue is not None:
                        emit(log_queue, "Resumed video playback via HTML5 play.")
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
                if verbose and log_queue is not None:
                    emit(log_queue, "Tapped video player to resume playback.")
                time.sleep(0.4)
                return True

        # 5. Universal keyboard 'k' shortcut fallback (only if still paused, and verify effect)
        try:
            is_paused = driver.execute_script("var v = document.querySelector('video'); return !v || v.paused;")
            if is_paused:
                body = driver.find_element(By.TAG_NAME, "body")
                body.send_keys("k")
                time.sleep(0.4)
                is_still_paused = driver.execute_script("var v = document.querySelector('video'); return !v || v.paused;")
                if not is_still_paused:
                    if verbose and log_queue is not None:
                        emit(log_queue, "Sent play key shortcut to start video.")
                    return True
        except Exception:
            pass

    except Exception as exc:
        logging.debug(f"ensure_video_playing error: {exc}")

    return False


ENSURE_VIDEO_AT_START_JS = """
try {
    var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
    var v = document.querySelector('video');
    if (!v && !player) return { found: false, rewound: false, current_time: 0 };

    var isAd = !!(player && (
        player.classList.contains('ad-showing') ||
        player.classList.contains('ad-interrupting') ||
        player.classList.contains('ytp-showing-ad')
    ));
    if (!isAd) {
        var pod = document.querySelector('.video-ads');
        if (pod && pod.childElementCount > 0) {
            var adText = pod.querySelector('.ytp-ad-text, .ytp-ad-preview-text, .ytp-ad-duration-remaining');
            if (adText && (adText.offsetParent !== null || (adText.innerText || '').trim().length > 0)) {
                isAd = true;
            }
        }
    }
    if (isAd) {
        return { found: true, is_ad: true, rewound: false, current_time: (v ? v['current' + 'Time'] : 0) };
    }

    // Clean any 't=' parameter from current URL bar
    try {
        var url = new URL(window.location.href);
        if (url.searchParams.has('t')) {
            url.searchParams.delete('t');
            window.history.replaceState({}, document.title, url.toString());
        }
    } catch (e) {}

    var currTime = (v && typeof v['current' + 'Time'] === 'number') ? v['current' + 'Time'] : 0;
    var playerTime = (player && typeof player.getCurrentTime === 'function') ? player.getCurrentTime() : currTime;
    var effectiveTime = Math.max(currTime, playerTime);

    var force = (arguments[0] === true);
    var threshold = (typeof arguments[1] === 'number') ? arguments[1] : 180.0;
    var needsRewind = force || (effectiveTime > threshold);
    if (!needsRewind) {
        return { found: true, is_ad: false, rewound: false, current_time: effectiveTime, threshold: threshold };
    }

    var rewound = false;
    // 1. YouTube Player API seekTo(0, true)
    if (player && typeof player.seekTo === 'function') {
        try {
            player.seekTo(0, true);
            rewound = true;
        } catch(e) {}
    }

    // 2. HTML5 Video currentTime = 0
    if (v) {
        try {
            v['current' + 'Time'] = 0;
            rewound = true;
        } catch(e) {}
    }

    // 3. Dispatch '0' keypress event to player / document (YouTube native shortcut to jump to 0:00)
    try {
        var target = player || v || document.body;
        var keyEvt = new KeyboardEvent('keydown', {
            key: '0',
            code: 'Digit0',
            keyCode: 48,
            which: 48,
            bubbles: true,
            cancelable: true
        });
        target.dispatchEvent(keyEvt);
    } catch(e) {}

    // 4. Resume video immediately if paused after seek
    try {
        if (v && v.paused) {
            v.play();
        } else if (player && typeof player.playVideo === 'function') {
            player.playVideo();
        }
    } catch(e) {}

    var finalTime = (v && typeof v['current' + 'Time'] === 'number') ? v['current' + 'Time'] : 0;
    return {
        found: true,
        is_ad: false,
        rewound: rewound,
        previousTime: effectiveTime,
        current_time: finalTime
    };
} catch(e) {
    return { found: false, error: String(e), rewound: false, current_time: 0 };
}
"""


def ensure_video_at_start(
    driver: Any,
    log_queue: Optional[queue.Queue] = None,
    force: bool = False,
    threshold_seconds: float = 180.0,
) -> bool:
    """Ensure the YouTube video starts at 0:00 by rewinding if resumed from history past threshold_seconds (default 3 mins)."""
    try:
        res = driver.execute_script(ENSURE_VIDEO_AT_START_JS, force, threshold_seconds)
        if isinstance(res, dict):
            if res.get("is_ad"):
                return False
            if res.get("rewound"):
                prev_time = res.get("previousTime", 0.0)
                emit(
                    log_queue,
                    f"Video was at {_format_clock(prev_time)} (resumed from account history > {_format_clock(threshold_seconds)}). Rewound to 0:00.",
                    logging.INFO,
                )
                try:
                    ensure_video_playing(driver, log_queue=None, verbose=False)
                except Exception:
                    pass
                return True
        return False
    except Exception as exc:
        logging.debug(f"ensure_video_at_start error: {exc}")
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
            from selenium.webdriver.common.action_chains import ActionChains
            from selenium.webdriver.common.by import By
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
    max_allowed = min(max(1.5, video_duration * 0.12), video_duration * 0.5)
    return min(target_buffer, max_allowed)


def watch_video(
    driver: Any,
    duration_seconds: Optional[int],
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    full_timeout_seconds: int = DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS,
    skip_ads: bool = True,
    max_ad_seconds: int = 9,
    end_buffer_seconds: float = 0.0,
    enable_interactivity: bool = True,
    interactivity_config: Optional[Dict[str, Any]] = None,
    pause_event: Optional[threading.Event] = None,
    completion_ratio: Optional[float] = None,
    should_stop_callback: Optional[Callable[[], bool]] = None,
    on_watch_tick: Optional[Callable[[float], None]] = None,
    ensure_start_at_beginning: bool = True,
) -> WatchResult:
    """Watch the target video for a fixed duration or its full length."""
    is_full_mode = duration_seconds is None or duration_seconds <= 0
    ad_state = AdWatchState()
    target_buffer = (
        random.uniform(7.0, 12.0)
        if (end_buffer_seconds is None or end_buffer_seconds <= 0)
        else float(end_buffer_seconds)
    )

    logged_cutoff = None

    cfg = interactivity_config or {}
    simulator = HumanInteractivitySimulator(
        enabled=enable_interactivity,
        scroll_comments=cfg.get("scroll_comments", True),
        variable_speed=cfg.get("variable_speed", False),
        adjust_quality=cfg.get("adjust_quality", True),
        quality_level=cfg.get("quality_level", "720p"),
        micro_pauses=cfg.get("micro_pauses", True),
        toggle_captions=cfg.get("toggle_captions", True),
        expand_description=cfg.get("expand_description", True),
        like_comments=cfg.get("like_comments", True),
        like_video=cfg.get("like_video", True),
        speed_chance=cfg.get("speed_chance", 0.0),
        quality_chance=cfg.get("quality_chance", 0.85),
        caption_chance=cfg.get("caption_chance", 0.65),
        pause_chance=cfg.get("pause_chance", 0.40),
        fullscreen_theater_mode=cfg.get("fullscreen_theater_mode", True),
    )

    initial_video_id: Optional[str] = None
    try:
        initial_video_id = extract_video_id(getattr(driver, "current_url", None))
    except Exception:
        pass
    autoplay_disabled = False

    video_started = False
    was_ad = False
    previous_progress_time: Optional[float] = None
    started_at = time.monotonic()
    last_tick = started_at
    last_progress_log = 0.0
    last_ad_log = 0.0
    last_ad_check_time = 0.0
    last_ad_progress_time = 0.0
    last_heartbeat_log = 0.0
    video_missing_since: Optional[float] = None
    escape_warned = False
    play_attempts = 0
    last_play_attempt = 0.0
    zero_progress_seconds = 0.0
    consecutive_stalls = 0
    has_refreshed_for_stall = False
    # Lifetime counter for this call: main-video playback seconds only.
    watched_seconds = 0.0
    last_known_duration = 0.0
    last_known_current_time = 0.0
    playback_meter = PlaybackMeter()
    history_rewind_count = 0
    MAX_HISTORY_REWINDS = 3

    # Ensure playback starts initially if paused or displaying center play button
    try:
        ensure_video_playing(driver, log_queue=log_queue)
    except Exception:
        pass

    # Ensure video starts at 0:00 (rewind if opened at a resume position)
    if ensure_start_at_beginning:
        try:
            ensure_video_at_start(driver, log_queue=log_queue)
        except Exception:
            pass

    def _result(
        completed: bool,
        reason: str,
        interrupted: bool = False,
        playback_finished: bool = False,
    ) -> WatchResult:
        if is_in_fullscreen(driver):
            try:
                exit_fullscreen_mode(driver, log_queue=log_queue)
            except Exception:
                pass
        return WatchResult(
            completed=completed,
            interrupted=interrupted,
            watched_seconds=watched_seconds,
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

        if should_stop_callback is not None:
            stop_reason = should_stop_callback()
            if stop_reason:
                reason_str = stop_reason if isinstance(stop_reason, str) else "channel_disabled"
                emit(log_queue, f"Playback stopped: {reason_str.replace('_', ' ')}.")
                is_target_stop = reason_str in ("video_target_achieved", "task_limit_reached", "channel_target_achieved")
                if is_target_stop:
                    return _result(True, reason_str, interrupted=False, playback_finished=True)
                return _result(False, reason_str, interrupted=True)

        # User-requested pause
        if pause_event is not None and pause_event.is_set():
            playback_meter.reset()
            emit(log_queue, "Playback paused by user.")
            pause_start = time.monotonic()
            try:
                driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
            except Exception:
                pass
            while pause_event.is_set():
                if stop_event is not None and stop_event.is_set():
                    emit(log_queue, "Watchtime interrupted by user while paused.")
                    return _result(False, "interrupted", interrupted=True)
                if should_stop_callback is not None:
                    stop_reason = should_stop_callback()
                    if stop_reason:
                        reason_str = stop_reason if isinstance(stop_reason, str) else "channel_disabled"
                        emit(log_queue, f"Playback stopped while paused: {reason_str.replace('_', ' ')}.")
                        is_target_stop = reason_str in ("video_target_achieved", "task_limit_reached", "channel_target_achieved")
                        if is_target_stop:
                            return _result(True, reason_str, interrupted=False, playback_finished=True)
                        return _result(False, reason_str, interrupted=True)
                time.sleep(0.5)
            pause_duration = max(0.0, time.monotonic() - pause_start)
            started_at += pause_duration
            last_tick = time.monotonic()
            emit(log_queue, "Playback resumed by user.")
            try:
                ensure_video_playing(driver, log_queue=log_queue)
            except Exception:
                pass

        now = time.monotonic()
        delta = max(0.0, now - last_tick)
        last_tick = now


        try:
            video_state = driver.execute_script(PLAYER_STATE_JS)
        except Exception:
            video_state = {"found": False}

        # Check identity before crediting this probe's time or metadata.
        if video_state.get("found") and initial_video_id:
            try:
                curr_vid = extract_video_id(getattr(driver, "current_url", ""))
                if curr_vid and curr_vid != initial_video_id:
                    emit(log_queue, f"Video transition detected ({initial_video_id} -> {curr_vid}). Halting task to prevent counting next video.")
                    try:
                        driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
                    except Exception:
                        pass
                    return _result(True, "autoplay_transition", playback_finished=True)
            except Exception:
                pass

        measured_seconds = playback_meter.sample(video_state, time.monotonic())

        is_ad = bool(video_state.get("isAd"))
        paused = bool(video_state.get("paused", False))
        curr_time = float(video_state.get("currentTime", 0.0))
        ended = bool(video_state.get("ended", False)) and not is_ad
        if video_state.get("found"):
            ad_state.tick(is_ad, delta)
        if measured_seconds > 0:
            watched_seconds += measured_seconds
            if on_watch_tick is not None:
                try:
                    on_watch_tick(measured_seconds)
                except Exception:
                    pass
        if video_state.get("found") and not is_ad:
            last_known_duration = float(video_state.get("duration", 0.0)) or last_known_duration
            last_known_current_time = curr_time

        # If video resumed from watch history (position is ahead of actual watched time and > 3 mins), rewind to 0:00.
        # Restrict check to initial startup (watched_seconds < 10.0) so natural playback drift never triggers a mid-stream rewind.
        if (
            ensure_start_at_beginning
            and video_state.get("found")
            and not is_ad
            and watched_seconds < 10.0
            and curr_time > 180.0
            and curr_time > (watched_seconds + 3.0)
            and history_rewind_count < MAX_HISTORY_REWINDS
        ):
            history_rewind_count += 1
            emit(
                log_queue,
                f"Video playback resumed at {_format_clock(curr_time)} from account history (> 3m). Rewinding to 0:00...",
                logging.INFO,
            )
            ensure_video_at_start(driver, log_queue=log_queue, force=True)
            playback_meter.reset()
            previous_progress_time = 0.0
            time.sleep(0.5)
            continue

        # Video watch timer: start only after ads are skipped/completed and the main video is actually playing.
        if not video_started:
            if video_state.get("found") and not is_ad and (not paused or curr_time > 0.0 or measured_seconds > 0):
                video_started = True
                started_at = now
                elapsed = 0.0
                if ad_state.total_ad_seconds > 0:
                    emit(log_queue, "Ad finished/skipped. Video is now playing — started watch timer.")
            else:
                started_at = now
                elapsed = 0.0
        else:
            # Main video has started; if a mid-roll ad appears (or just finished), freeze/extend watch timer
            if is_ad or was_ad:
                started_at += delta
                if was_ad and not is_ad:
                    emit(log_queue, "Ad finished/skipped. Video resumed — watch timer resumed.")
            elapsed = max(0.0, now - started_at)

        was_ad = is_ad

        # Full video safety timeout covers main video playback or total duration on video.
        if is_full_mode and full_timeout_seconds > 0 and not ended:
            if video_started and (elapsed >= full_timeout_seconds or watched_seconds >= full_timeout_seconds):
                emit(log_queue, f"Full video safety timeout reached ({full_timeout_seconds}s). Stopping watch.", logging.WARNING)
                return _result(False, "full_timeout")
            if (now - started_at) >= full_timeout_seconds:
                emit(log_queue, f"Full video safety timeout reached ({full_timeout_seconds}s total). Stopping watch.", logging.WARNING)
                return _result(False, "full_timeout")

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
            disable_player_autoplay(driver)
            autoplay_disabled = True

        paused = bool(video_state.get("paused", False))

        # Periodic watching progress & stalled playback check:
        # Check every 10s if progress is stalled (e.g. stuck at 00:00, paused, or ad displaying), otherwise every 30s
        video_duration = float(video_state.get("duration", 0.0))
        is_stalled = (
            previous_progress_time is not None
            and not ended
            and (
                _format_clock(curr_time) == _format_clock(previous_progress_time)
                or abs(curr_time - previous_progress_time) < 0.5
            )
        )
        progress_check_interval = 10.0 if is_stalled else 30.0

        if now - last_progress_log >= progress_check_interval or last_progress_log == 0.0:
            last_progress_log = now
            if video_duration > 0 and not is_ad:
                percent = int((curr_time / video_duration) * 100)
                emit(
                    log_queue,
                    f"Watching progress: {_format_clock(curr_time)} / "
                    f"{_format_clock(video_duration)} ({percent}%)",
                )
            elif is_ad:
                emit(
                    log_queue,
                    f"Watching progress: {_format_clock(curr_time)} (ad currently displaying)",
                )

            # Check if the watching progress remains the same with the previous watching progress
            if is_stalled:
                curr_clock = _format_clock(curr_time)
                # Determine whether video is currently displaying an ad or not playing/paused
                is_displaying_ad = is_ad
                if not is_displaying_ad:
                    try:
                        ad_check_val = driver.execute_script("""
                            var p = document.querySelector('.html5-video-player');
                            var adShowing = !!(p && (p.classList.contains('ytp-showing-ad') || p.classList.contains('ad-showing') || p.classList.contains('ad-interrupting')));
                            var pod = document.querySelector('.video-ads');
                            var visibleAdText = false;
                            if (pod && pod.childElementCount > 0) {
                                var t = pod.querySelector('.ytp-ad-text, .ytp-ad-preview-text, .ytp-ad-duration-remaining');
                                if (t && t.offsetParent !== null && (t.innerText || '').trim().length > 0) visibleAdText = true;
                            }
                            var skipBtn = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, button[id*="skip-button"]');
                            var skipBtnVisible = !!(skipBtn && skipBtn.offsetParent !== null);
                            return adShowing || visibleAdText || skipBtnVisible;
                        """)
                        if isinstance(ad_check_val, bool) and ad_check_val:
                            is_displaying_ad = True
                    except Exception:
                        pass

                if is_displaying_ad:
                    ad_stat = check_ad_playback_progress(driver, sample_delay=0.3)
                    if ad_stat.get("is_ad") and ad_stat.get("is_changing"):
                        emit(log_queue, f"Watching progress checked at {curr_clock} — ad is already playing (time changing). Not clicking play button.")
                    else:
                        emit(log_queue, f"Watching progress unchanged at {curr_clock} — ad detected. Attempting to skip ad...")
                        if trigger_skip_ad(driver):
                            emit(log_queue, "Clicked Skip Ad button.")
                        emit(log_queue, f"Watching progress unchanged at {curr_clock} — ad time not changing. Pressing play button to resume.")
                        ensure_video_playing(driver, log_queue=log_queue)
                else:
                    consecutive_stalls += 1
                    if consecutive_stalls < 3:
                        emit(log_queue, f"Watching progress stalled at {curr_clock} (video is not playing or paused). Pressing play button.")
                        ensure_video_playing(driver, log_queue=log_queue)
                    elif not has_refreshed_for_stall:
                        has_refreshed_for_stall = True
                        consecutive_stalls = 0
                        emit(log_queue, f"Watching progress stalled at {curr_clock} after multiple attempts. Refreshing page to recover playback...", logging.WARNING)
                        try:
                            driver.refresh()
                        except Exception:
                            pass
                        time.sleep(3.0)
                        try:
                            from .navigation import dismiss_youtube_dialogs
                            dismiss_youtube_dialogs(driver, log_queue=log_queue)
                        except Exception:
                            pass
                        ensure_video_playing(driver, log_queue=log_queue)
                        previous_progress_time = None
                        playback_meter.reset()
                        continue
                    else:
                        emit(log_queue, f"Watching progress still stalled at {curr_clock} after page refresh. Aborting stuck video and returning to YouTube Home.", logging.WARNING)
                        return _result(False, "stalled_unresponsive", interrupted=True)
            else:
                consecutive_stalls = 0

            previous_progress_time = curr_time

        # ------------------------------------------------------------ ads ----
        if is_ad:
            # Highlight skip button with border as soon as detected on page
            try:
                skip_candidate = find_skip_button(driver)
                if skip_candidate is not None:
                    highlight_skip_button(driver, skip_candidate)
            except Exception:
                pass

            # Check if values between times are changing or if ad time is stalled
            if last_ad_check_time > 0:
                time_since_ad = now - last_ad_check_time
                if time_since_ad >= 5.0:
                    ad_diff = abs(curr_time - last_ad_progress_time)
                    if ad_diff < 0.05 and paused:
                        emit(log_queue, f"Ad time not changing at {_format_clock(curr_time)} (ad is stalled). Clicking play button to resume.")
                        ensure_video_playing(driver, log_queue=log_queue)
                    last_ad_progress_time = curr_time
                    last_ad_check_time = now
            else:
                last_ad_progress_time = curr_time
                last_ad_check_time = now

            if now - last_ad_log >= 30:
                last_ad_log = now
                if skip_ads:
                    skip_t = ad_state.get_skip_target(max_ad_seconds)
                    emit(
                        log_queue,
                        f"Ad playing: {ad_state.break_ad_seconds:.0f}s into this break "
                        f"(skip at ~{skip_t:.0f}s).",
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

        # Every minute: heartbeat timer tick
        if now - last_heartbeat_log >= 60.0:
            last_heartbeat_log = now

        if simulator.fullscreen_theater_mode and video_duration > 0:
            threshold_time = min(video_duration * 0.94, max(0.0, video_duration - 1.0))
            if completion_ratio is not None and completion_ratio > 0:
                threshold_time = min(threshold_time, video_duration * min(1.0, completion_ratio))
        else:
            effective_buffer = calculate_end_buffer(video_duration, target_buffer)
            threshold_time = video_duration - effective_buffer
            if completion_ratio is not None and completion_ratio > 0:
                threshold_time = min(threshold_time, video_duration * min(1.0, completion_ratio))
        if is_full_mode and video_duration > 0 and logged_cutoff != threshold_time:
            logged_cutoff = threshold_time
            if simulator.fullscreen_theater_mode:
                emit(
                    log_queue,
                    f"Full-video cutoff: {_format_clock(threshold_time)} / {_format_clock(video_duration)} "
                    f"(target progress: 94%).",
                )
            else:
                emit(
                    log_queue,
                    f"Full-video cutoff: {_format_clock(threshold_time)} / {_format_clock(video_duration)} "
                    f"(stop buffer: {effective_buffer:g}s).",
                )

        if ended:
            if (
                ensure_start_at_beginning
                and watched_seconds < 10.0
                and video_duration > 180.0
                and history_rewind_count < MAX_HISTORY_REWINDS
            ):
                history_rewind_count += 1
                emit(
                    log_queue,
                    f"Video was marked ended from previous watch history ({_format_clock(curr_time)} / {_format_clock(video_duration)}). Rewinding to 0:00 (attempt {history_rewind_count}/{MAX_HISTORY_REWINDS})...",
                    logging.WARNING,
                )
                ensure_video_at_start(driver, log_queue=log_queue, force=True)
                playback_meter.reset()
                previous_progress_time = 0.0
                time.sleep(1.0)
                continue
            return _result(True, "ended", playback_finished=True)
        if is_full_mode and video_duration > 0 and curr_time >= threshold_time:
            return _result(True, "completion_cutoff", playback_finished=False)

        # Paused mid-video or at start -> resume (tap large play button, controls, or player element)
        resume_cutoff = threshold_time if is_full_mode else video_duration
        if paused and not ended and (video_duration == 0 or curr_time < resume_cutoff):
            if is_ad:
                ad_stat = check_ad_playback_progress(driver, sample_delay=0.3)
                if ad_stat.get("is_ad") and ad_stat.get("is_changing"):
                    # Ad is actively playing on screen; do not click play
                    pass
                else:
                    if now - last_play_attempt >= 3.0:
                        last_play_attempt = now
                        play_attempts += 1
                        is_verbose = play_attempts <= 2
                        try:
                            ensure_video_playing(driver, log_queue=log_queue if is_verbose else None, verbose=is_verbose)
                        except Exception as exc:
                            logging.debug(f"Could not resume playback: {exc}")
            else:
                if now - last_play_attempt >= 3.0:
                    last_play_attempt = now
                    play_attempts += 1
                    is_verbose = play_attempts <= 2
                    try:
                        ensure_video_playing(driver, log_queue=log_queue if is_verbose else None, verbose=is_verbose)
                    except Exception as exc:
                        logging.debug(f"Could not resume playback: {exc}")



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
            last_tick = time.monotonic()
            started_at += float(interactive_event["micro_paused"])

        if is_full_mode and interactive_event.get("target_cutoff_reached"):
            return _result(True, "completion_cutoff", playback_finished=True)

        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            return _result(False, "interrupted", interrupted=True)

        time.sleep(3)


def watch_short(
    driver: Any,
    target_loops: Optional[int] = None,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    pause_event: Optional[threading.Event] = None,
    should_stop_callback: Optional[Callable[[], Optional[str]]] = None,
    on_watch_tick: Optional[Callable[[float], None]] = None,
    max_duration_seconds: int = 180,
    like_short: bool = False,
    on_view_counted: Optional[Callable[[int, float, float], None]] = None,
) -> WatchResult:
    """
    Watch a YouTube Short for a random (1-3) or specified number of loop repetitions in a single go.
    Tracks player state and wrap-around transitions when the short loops back to start.
    Optionally likes the Short from targeted channel if not already liked.
    Continuously checks playback time every 5 seconds, and counts 1 view as soon as 80% duration is reached.
    """
    if target_loops is None or target_loops < 1:
        target_loops = random.randint(1, 2)

    emit(log_queue, f"Starting Shorts playback: watching for {target_loops} loop(s) in a single go.")

    playback_meter = PlaybackMeter()
    video_started = False
    started_at = time.monotonic()
    last_tick = started_at
    last_5s_check = started_at
    total_watched_seconds = 0.0
    current_loop = 1
    completed_loops = 0
    highest_time_in_loop = 0.0
    last_known_duration = 0.0
    last_known_current_time = 0.0
    video_missing_since: Optional[float] = None
    last_progress_log = 0.0
    like_attempted = False
    views_counted = 0
    loops_credited: Set[int] = set()

    # Note: Shorts autoplay automatically on navigation and scroll. Do not send play click / 'k' key
    # as pressing play toggles already playing Shorts into a paused state.

    def _result(completed: bool, reason: str, interrupted: bool = False, finished: bool = False) -> WatchResult:
        nonlocal like_attempted
        if like_short and not like_attempted and completed:
            like_attempted = True
            try:
                like_active_short(driver, log_queue=log_queue)
            except Exception:
                pass
        return WatchResult(
            completed=completed,
            interrupted=interrupted,
            watched_seconds=total_watched_seconds,
            ad_seconds=0.0,
            reason=reason,
            video_duration=last_known_duration,
            final_current_time=last_known_current_time,
            playback_finished=finished,
            views_counted=views_counted,
        )

    while True:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Shorts watch interrupted by user.")
            return _result(False, "interrupted", interrupted=True)

        if should_stop_callback is not None:
            stop_reason = should_stop_callback()
            if stop_reason:
                reason_str = stop_reason if isinstance(stop_reason, str) else "playback_stopped"
                emit(log_queue, f"Playback stopped: {reason_str.replace('_', ' ')}.")
                is_target_stop = reason_str in ("video_target_achieved", "task_limit_reached", "channel_target_achieved")
                if is_target_stop:
                    return _result(True, reason_str, interrupted=False, finished=True)
                return _result(False, reason_str, interrupted=True)

        if pause_event is not None and pause_event.is_set():
            playback_meter.reset()
            emit(log_queue, "Short playback paused by user.")
            pause_start = time.monotonic()
            try:
                driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
            except Exception:
                pass
            while pause_event.is_set():
                if stop_event is not None and stop_event.is_set():
                    return _result(False, "interrupted", interrupted=True)
                if should_stop_callback is not None and should_stop_callback():
                    return _result(False, "playback_stopped", interrupted=True)
                time.sleep(0.5)
            started_at += max(0.0, time.monotonic() - pause_start)
            last_tick = time.monotonic()
            emit(log_queue, "Short playback resumed by user.")
            try:
                ensure_video_playing(driver, log_queue=log_queue)
            except Exception:
                pass

        now = time.monotonic()
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
            elif now - video_missing_since >= 15:
                emit(log_queue, "No video element found on Short page. Aborting.", logging.WARNING)
                return _result(False, "no_video")
            time.sleep(1.0)
            continue

        video_missing_since = None

        curr_time = float(video_state.get("currentTime", 0.0))
        duration = float(video_state.get("duration", 0.0))
        ended = bool(video_state.get("ended", False))
        paused = bool(video_state.get("paused", False))

        if duration > 0:
            last_known_duration = duration
        if curr_time > 0:
            last_known_current_time = curr_time

        measured = playback_meter.sample(video_state, now)
        if measured > 0:
            total_watched_seconds += measured
            if on_watch_tick is not None:
                try:
                    on_watch_tick(measured)
                except Exception:
                    pass

        # Like the Short if requested and not already attempted once playback begins
        if like_short and not like_attempted and (total_watched_seconds >= 2.0 or curr_time >= 2.0):
            like_attempted = True
            try:
                like_active_short(driver, log_queue=log_queue)
            except Exception as exc:
                logging.debug(f"like_active_short error: {exc}")

        # If paused unexpectedly, resume via HTML5 video.play() (do not click or press keys to avoid toggle)
        if paused and not ended and elapsed < max_duration_seconds:
            try:
                driver.execute_script("var v = document.querySelector('video'); if (v && v.paused) v.play();")
            except Exception:
                pass

        # Update highest time seen in this loop
        if curr_time > highest_time_in_loop:
            highest_time_in_loop = curr_time

        # Continuous 5-second playback time check & status log
        if now - last_5s_check >= 5.0:
            last_5s_check = now
            if last_known_duration > 0:
                pct = (curr_time / last_known_duration * 100.0)
                emit(
                    log_queue,
                    f"Short time check (5s): {curr_time:.1f}s / {last_known_duration:.1f}s ({pct:.0f}%) — Loop {current_loop}/{target_loops}",
                )

        # 80% View Threshold Check:
        # As soon as the short reaches 80% of its length, consider it a view.
        if last_known_duration > 0 and current_loop not in loops_credited:
            threshold_80 = 0.80 * last_known_duration
            effective_loop_time = max(curr_time, highest_time_in_loop)
            if effective_loop_time >= threshold_80:
                loops_credited.add(current_loop)
                views_counted += 1
                emit(
                    log_queue,
                    f"Short reached 80% of its length ({effective_loop_time:.1f}s / {last_known_duration:.1f}s) — 1 view counted! (Loop {current_loop}/{target_loops})",
                    logging.INFO,
                )
                if on_view_counted is not None:
                    try:
                        on_view_counted(current_loop, effective_loop_time, last_known_duration)
                    except Exception as exc:
                        logging.debug(f"on_view_counted callback error: {exc}")

        # Loop wrap-around detection:
        min_loop_thresh = max(3.0, last_known_duration * 0.70) if last_known_duration > 0 else 5.0
        wrap_around = (highest_time_in_loop >= min_loop_thresh and curr_time < highest_time_in_loop - 2.5)

        # Final loop completion detection (before or at wrap-around or ended):
        final_loop_ended = (
            current_loop == target_loops and
            last_known_duration > 0 and
            (curr_time >= max(1.0, last_known_duration - 1.0) or ended)
        )

        if wrap_around or final_loop_ended or (ended and current_loop < target_loops):
            completed_loops += 1
            emit(
                log_queue,
                f"Completed loop {current_loop}/{target_loops} of Short "
                f"({highest_time_in_loop:.1f}s / {last_known_duration:.1f}s).",
            )
            if current_loop >= target_loops:
                emit(
                    log_queue,
                    f"Finished watching Short ({completed_loops} loop(s) completed, total {total_watched_seconds:.1f}s, {views_counted} view(s) counted).",
                )
                return _result(True, "shorts_loops_completed", finished=True)

            current_loop += 1
            highest_time_in_loop = curr_time

        # Periodic progress logging
        if now - last_progress_log >= 15.0:
            last_progress_log = now
            dur_fmt = _format_clock(last_known_duration) if last_known_duration > 0 else "--:--"
            emit(
                log_queue,
                f"Shorts progress: Loop {current_loop}/{target_loops} — {_format_clock(curr_time)} / {dur_fmt}",
            )

        # Safety timeout based on target loops and duration
        timeout_budget = max(45, int(target_loops * ((last_known_duration or 60) + 10)))
        if elapsed >= min(max_duration_seconds, timeout_budget):
            emit(log_queue, f"Short reached safety limit ({elapsed:.0f}s). Completing.", logging.INFO)
            return _result(True, "shorts_timeout", finished=True)

        time.sleep(0.5)
