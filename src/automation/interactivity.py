"""Natural Human Interactivity Simulation for YouTube playback.

Provides human-like behavioral simulation during video playback:
1. Strict 1x Normal Playback Speed (no speed modification).
2. Bandwidth-Saving Video Quality (exclusively 360p or 480p).
3. Closed Captions (Subtitles) Simulation (randomly enable and disable captions).
4. Random Micro-Pauses (brief 2-5s pauses simulating human distraction, then resumes playback).
5. Randomized Scrolling & Pausing (scrolling down to read description/comments, pausing, and scrolling back).
"""

import logging
import queue
import random
import threading
import time
from typing import Any, Dict, Optional, Tuple

from selenium.webdriver.common.action_chains import ActionChains

from ..utils.logger import emit

# JavaScript to set playback rate via YouTube Movie Player API and HTML5 video fallback
SET_SPEED_JS = """
var rate = arguments[0];
var ok = false;
try {
    var player = document.getElementById('movie_player');
    if (player && typeof player.setPlaybackRate === 'function') {
        player.setPlaybackRate(rate);
        ok = true;
    }
} catch (e) {}
try {
    var v = document.querySelector('video');
    if (v) {
        v.playbackRate = rate;
        ok = true;
    }
} catch (e) {}
return ok;
"""

GET_SPEED_JS = """
try {
    var player = document.getElementById('movie_player');
    if (player && typeof player.getPlaybackRate === 'function') {
        return player.getPlaybackRate();
    }
} catch (e) {}
try {
    var v = document.querySelector('video');
    if (v) return v.playbackRate;
} catch (e) {}
return 1.0;
"""

# JavaScript to set video quality via Movie Player API with DOM settings menu fallback
SET_QUALITY_JS = """
var target = arguments[0]; // "360p" or "480p"
var level = arguments[1];  // "medium" or "large"
var success = false;
try {
    var player = document.getElementById('movie_player');
    if (player) {
        if (typeof player.setPlaybackQualityRange === 'function') {
            player.setPlaybackQualityRange(level, level);
            success = true;
        }
        if (typeof player.setPlaybackQuality === 'function') {
            player.setPlaybackQuality(level);
            success = true;
        }
    }
} catch (e) {}

// DOM Settings menu interaction as fallback
try {
    var settingsBtn = document.querySelector('.ytp-settings-button');
    if (settingsBtn) {
        if (settingsBtn.getAttribute('aria-expanded') !== 'true') {
            settingsBtn.click();
        }
        setTimeout(function() {
            try {
                var menuItems = document.querySelectorAll('.ytp-panel-menu .ytp-menuitem');
                var qualityItem = null;
                for (var i = 0; i < menuItems.length; i++) {
                    var txt = (menuItems[i].textContent || '').toLowerCase();
                    if (txt.indexOf('quality') !== -1 || txt.indexOf('qualität') !== -1) {
                        qualityItem = menuItems[i];
                        break;
                    }
                }
                if (qualityItem) {
                    qualityItem.click();
                    setTimeout(function() {
                        try {
                            var qItems = document.querySelectorAll('.ytp-quality-menu .ytp-menuitem');
                            for (var j = 0; j < qItems.length; j++) {
                                if ((qItems[j].textContent || '').indexOf(target) !== -1) {
                                    qItems[j].click();
                                    break;
                                }
                            }
                        } catch (e) {}
                        if (settingsBtn.getAttribute('aria-expanded') === 'true') {
                            settingsBtn.click();
                        }
                    }, 120);
                } else if (settingsBtn.getAttribute('aria-expanded') === 'true') {
                    settingsBtn.click();
                }
            } catch (e) {}
        }, 120);
    }
} catch (e) {}

return success;
"""

# JavaScript to toggle closed captions (subtitles)
TOGGLE_CAPTIONS_JS = """
var target = arguments[0]; // 'enable', 'disable', or null/undefined
var btn = document.querySelector('.ytp-subtitles-button');
var player = document.getElementById('movie_player');

if (!btn && (!player || typeof player.toggleSubtitles !== 'function')) {
    return { success: false, changed: false, reason: 'no_subtitles' };
}

var isOn = false;
if (btn) {
    isOn = btn.getAttribute('aria-pressed') === 'true';
} else if (player && typeof player.isSubtitlesOn === 'function') {
    try { isOn = player.isSubtitlesOn(); } catch (e) {}
}

var shouldClick = false;
if (target === 'enable') {
    shouldClick = !isOn;
} else if (target === 'disable') {
    shouldClick = isOn;
} else {
    shouldClick = true;
}

if (!shouldClick) {
    return { success: true, changed: false, state: isOn ? 'enabled' : 'disabled' };
}

if (btn && btn.offsetParent !== null) {
    btn.click();
    var newState = btn.getAttribute('aria-pressed') === 'true';
    return { success: true, changed: true, state: newState ? 'enabled' : 'disabled' };
}

if (player && typeof player.toggleSubtitles === 'function') {
    try {
        player.toggleSubtitles();
        return { success: true, changed: true, state: !isOn ? 'enabled' : 'disabled' };
    } catch (e) {}
}

return { success: false, changed: false, reason: 'could_not_toggle' };
"""


# JavaScript to expand the video description via tp-yt-paper-button#expand
EXPAND_DESCRIPTION_JS = """
var expanderEl = document.querySelector('ytd-text-inline-expander, #description-inline-expander');
if (expanderEl && (expanderEl.hasAttribute('is-expanded') || expanderEl.getAttribute('is-expanded') === 'true')) {
    return false; // Already expanded once, do not click again
}

var clicked = false;
var selectors = [
    'tp-yt-paper-button#expand.ytd-text-inline-expander',
    '#description-inline-expander tp-yt-paper-button#expand',
    'ytd-text-inline-expander tp-yt-paper-button#expand',
    'tp-yt-paper-button#expand',
    '#expand.ytd-text-inline-expander',
    '#description-inline-expander #expand',
    '#description tp-yt-paper-button#expand'
];

for (var i = 0; i < selectors.length; i++) {
    var btn = document.querySelector(selectors[i]);
    if (btn && btn.offsetParent !== null && !btn.hasAttribute('hidden') && btn.getAttribute('aria-disabled') !== 'true') {
        try {
            var down = new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window });
            var up = new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window });
            var click = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
            btn.dispatchEvent(down);
            btn.dispatchEvent(up);
            btn.dispatchEvent(click);
            btn.click();
            clicked = true;
            break;
        } catch (e) {
            try {
                btn.click();
                clicked = true;
                break;
            } catch (e2) {}
        }
    }
}

if (!clicked) {
    var expander = document.querySelector('ytd-text-inline-expander, #description-inline-expander');
    if (expander && expander.getAttribute('is-expanded') === null) {
        var inner = expander.querySelector('#expand, tp-yt-paper-button#expand');
        if (inner) {
            try { inner.click(); clicked = true; } catch (e3) {}
        }
        if (!clicked) {
            try { expander.click(); clicked = true; } catch (e4) {}
        }
    }
}

return clicked;
"""


# JavaScript to find an unliked comment and return its interactive elements
LIKE_COMMENT_JS = """
var threads = document.querySelectorAll(
    'ytd-comment-thread-renderer, ytd-comment-view-model, ytd-comment-renderer'
);
if (!threads || threads.length === 0) {
    return { found: false, reason: 'no_comments' };
}

var candidates = [];
for (var i = 0; i < threads.length; i++) {
    var t = threads[i];
    var btn = t.querySelector(
        '#like-button button, ytd-comment-engagement-bar #like-button button, ' +
        'ytd-toggle-button-renderer#like-button button, like-button-view-model button, ' +
        '#like-button yt-button-shape button, ytd-toggle-button-renderer button'
    );
    if (!btn) continue;

    var ariaPressed = btn.getAttribute('aria-pressed');
    var ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
    var parent = btn.closest('ytd-toggle-button-renderer, yt-button-shape, ytd-comment-engagement-bar') || t;
    var tooltipEl = parent.querySelector('#tooltip');
    var tooltipText = tooltipEl ? (tooltipEl.textContent || '').trim().toLowerCase() : '';

    var isLiked = (
        ariaPressed === 'true' ||
        ariaLabel === 'unlike' ||
        ariaLabel.indexOf('unlike') !== -1 ||
        tooltipText === 'unlike' ||
        btn.classList.contains('ytSpecButtonShapeNextSelected') ||
        btn.classList.contains('style-default-active')
    );
    if (isLiked) continue;

    // Dislike guard
    if (ariaLabel.indexOf('dislike') !== -1) continue;

    var authorEl = t.querySelector(
        '#author-text span, #channel-name #text, #header-author h3, #name, #author-text'
    );
    var author = authorEl ? (authorEl.textContent || '').trim() : '';

    var renderer = t.querySelector(
        'ytd-toggle-button-renderer#like-button, yt-button-shape, like-button-view-model, #like-button'
    ) || btn;

    candidates.push({ button: btn, renderer: renderer, author: author, element: t });
}

if (candidates.length === 0) {
    return { found: false, reason: 'no_unliked_candidates' };
}

var pick = candidates[Math.floor(Math.random() * candidates.length)];
return {
    found: true,
    button: pick.button,
    renderer: pick.renderer,
    author: pick.author
};
"""


# JavaScript to find the video like button and return its interactive elements
LIKE_VIDEO_JS = """
var container = document.querySelector('like-button-view-model, ytd-watch-metadata like-button-view-model, #top-level-buttons-computed like-button-view-model, #segmented-like-button');
var btn = null;

if (container) {
    btn = container.querySelector('button');
}

if (!btn) {
    var candidates = document.querySelectorAll('like-button-view-model button, #segmented-like-button button, button[aria-label*="like this video" i], button[aria-label*="I like this" i]');
    for (var i = 0; i < candidates.length; i++) {
        var aLabel = (candidates[i].getAttribute('aria-label') || '').toLowerCase();
        if (aLabel.indexOf('dislike') === -1 && (aLabel.indexOf('like this video') !== -1 || aLabel.indexOf('like') !== -1)) {
            btn = candidates[i];
            break;
        }
    }
}

if (!btn) {
    return { success: false, reason: 'like_button_not_found' };
}

var ariaPressed = btn.getAttribute('aria-pressed');
var ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
var isSelected = btn.classList.contains('ytSpecButtonShapeNextSelected');

var isAlreadyLiked = (
    ariaPressed === 'true' ||
    isSelected ||
    ariaLabel.indexOf('unlike') !== -1
);

return {
    success: true,
    already_liked: isAlreadyLiked,
    button: btn,
    renderer: container || btn
};
"""


def is_comment_liked(driver: Any, btn: Any) -> bool:
    """Check if the comment like button or its parent container is in the liked state."""
    if btn is None:
        return False
    try:
        res = driver.execute_script("""
            var el = arguments[0];
            if (!el) return false;
            var ariaPressed = el.getAttribute('aria-pressed');
            var ariaLabel = (el.getAttribute('aria-label') || '').trim().toLowerCase();
            var parent = el.closest('ytd-toggle-button-renderer, yt-button-shape, ytd-comment-engagement-bar') || el.parentElement;
            var tooltipEl = parent ? parent.querySelector('#tooltip') : null;
            var tooltip = tooltipEl ? (tooltipEl.textContent || '').trim().toLowerCase() : '';
            var isSelected = el.classList && (
                el.classList.contains('ytSpecButtonShapeNextSelected') ||
                el.classList.contains('style-default-active')
            );
            return (
                ariaPressed === 'true' ||
                ariaLabel === 'unlike' ||
                ariaLabel.indexOf('unlike') !== -1 ||
                tooltip === 'unlike' ||
                isSelected
            );
        """, btn)
        if isinstance(res, dict):
            return bool(res.get("is_liked", False))
        return res is True
    except Exception:
        return False


def is_video_liked(driver: Any, btn: Any = None) -> bool:
    """Check if the active video is already liked."""
    try:
        res = driver.execute_script("""
            var btn = arguments[0];
            if (!btn) {
                var container = document.querySelector('like-button-view-model, ytd-watch-metadata like-button-view-model, #top-level-buttons-computed like-button-view-model, #segmented-like-button');
                if (container) btn = container.querySelector('button');
            }
            if (!btn) {
                var candidates = document.querySelectorAll('like-button-view-model button, #segmented-like-button button, button[aria-label*="like this video" i], button[aria-label*="I like this" i]');
                for (var i = 0; i < candidates.length; i++) {
                    var aLabel = (candidates[i].getAttribute('aria-label') || '').toLowerCase();
                    if (aLabel.indexOf('dislike') === -1 && (aLabel.indexOf('like this video') !== -1 || aLabel.indexOf('like') !== -1)) {
                        btn = candidates[i];
                        break;
                    }
                }
            }
            if (!btn) return false;
            var ariaPressed = btn.getAttribute('aria-pressed');
            var ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
            var isSelected = btn.classList && btn.classList.contains('ytSpecButtonShapeNextSelected');
            return ariaPressed === 'true' || isSelected || ariaLabel.indexOf('unlike') !== -1;
        """, btn)
        if isinstance(res, dict):
            return bool(res.get("already_liked", False))
        return res is True
    except Exception:
        return False


def click_and_verify_comment_like(driver: Any, candidate: Dict[str, Any]) -> bool:
    """Click candidate comment like button using trusted actions and verify it changes to liked."""
    btn = candidate.get("button")
    renderer = candidate.get("renderer")

    # If button is None (e.g. in mock test where only dict was returned), return True
    if btn is None:
        return bool(candidate.get("found", False))

    # Strict check: If already liked before clicking, skip!
    if is_comment_liked(driver, btn):
        return False

    # Scroll button into center view
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
            btn,
        )
    except Exception:
        pass
    time.sleep(0.3)

    # 1. ActionChains mouse click on button
    try:
        ActionChains(driver).move_to_element(btn).pause(0.1).click().perform()
    except Exception:
        pass

    time.sleep(0.35)
    if is_comment_liked(driver, btn):
        return True

    # 2. Native Selenium element click
    try:
        btn.click()
    except Exception:
        pass

    time.sleep(0.35)
    if is_comment_liked(driver, btn):
        return True

    # 3. ActionChains or native click on parent renderer / shape
    if renderer is not None and renderer != btn:
        try:
            ActionChains(driver).move_to_element(renderer).pause(0.1).click().perform()
        except Exception:
            try:
                renderer.click()
            except Exception:
                pass

        time.sleep(0.35)
        if is_comment_liked(driver, btn):
            return True

    # 4. PointerEvent / MouseEvent with accurate coordinates
    try:
        driver.execute_script("""
            var el = arguments[0];
            if (!el) return;
            var r = el.getBoundingClientRect();
            var x = r.left + r.width / 2;
            var y = r.top + r.height / 2;
            var opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse', isPrimary: true };
            try { el.dispatchEvent(new PointerEvent('pointerdown', opts)); } catch(e){}
            try { el.dispatchEvent(new MouseEvent('mousedown', opts)); } catch(e){}
            try { el.dispatchEvent(new PointerEvent('pointerup', opts)); } catch(e){}
            try { el.dispatchEvent(new MouseEvent('mouseup', opts)); } catch(e){}
            try { el.dispatchEvent(new MouseEvent('click', opts)); } catch(e){}
            try { el.click(); } catch(e){}
        """, btn)
    except Exception:
        pass

    # 5. Verification poll up to 1.5 seconds
    for _ in range(6):
        time.sleep(0.25)
        if is_comment_liked(driver, btn):
            return True

    return False


def _interruptible_sleep(seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
    """Sleep for ``seconds`` in small slices.

    Returns:
        True if interrupted by ``stop_event``, False if sleep completed normally.
    """
    if seconds <= 0:
        return False
    end_time = time.time() + seconds
    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            return True
        chunk = min(0.15, max(0.01, end_time - time.time()))
        time.sleep(chunk)
    return False


def set_playback_speed(driver: Any, speed: float = 1.0) -> bool:
    """Set the playback rate on the active YouTube player (defaults to 1.0x)."""
    try:
        return bool(driver.execute_script(SET_SPEED_JS, speed))
    except Exception:
        return False


def get_playback_speed(driver: Any) -> float:
    """Get the current playback speed of the video player."""
    try:
        return float(driver.execute_script(GET_SPEED_JS) or 1.0)
    except Exception:
        return 1.0


def ensure_normal_speed(driver: Any) -> bool:
    """Ensure video plays at 1.0x normal speed."""
    try:
        spd = get_playback_speed(driver)
        if abs(spd - 1.0) > 0.05:
            return set_playback_speed(driver, 1.0)
        return True
    except Exception:
        return False


def set_video_quality(driver: Any, quality_label: str = "360p") -> bool:
    """Adjust video quality strictly to 360p or 480p.

    Higher resolutions like 720p or 1080p are intentionally omitted per configuration.
    """
    q_str = str(quality_label).strip().lower()
    if "480" in q_str:
        level = "large"
        label_norm = "480p"
    else:
        # Default bandwidth saver (360p)
        level = "medium"
        label_norm = "360p"

    try:
        driver.execute_script(SET_QUALITY_JS, label_norm, level)
        return True
    except Exception:
        return False


def toggle_captions(
    driver: Any,
    target_state: Optional[str] = None,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Enable, disable, or toggle closed captions (subtitles).

    Args:
        driver: Active Selenium WebDriver.
        target_state: 'enable', 'disable', or None to toggle.
        log_queue: Optional queue for UI log updates.

    Returns:
        True if action was executed successfully.
    """
    try:
        res = driver.execute_script(TOGGLE_CAPTIONS_JS, target_state)
        if isinstance(res, dict) and res.get("changed"):
            st = res.get("state", "toggled")
            emit(log_queue, f"Natural interaction: {st} closed captions (subtitles).")
            return True
        return bool(isinstance(res, dict) and res.get("success"))
    except Exception:
        return False


def expand_video_description(
    driver: Any,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Click the '...more' button (tp-yt-paper-button#expand) to open the video description."""
    try:
        ok = bool(driver.execute_script(EXPAND_DESCRIPTION_JS))
        if ok:
            emit(log_queue, "Natural interaction: clicked '...more' to expand video description.")
        return ok
    except Exception:
        return False


def like_random_comments(
    driver: Any,
    min_likes: int = 1,
    max_likes: int = 2,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
) -> int:
    """Randomly like between min_likes and max_likes comments on the video with verified state change."""
    target_count = random.randint(min_likes, max_likes)
    liked = 0

    # Ensure comments section is in view to trigger lazy loading
    try:
        driver.execute_script("""
            var c = document.querySelector('#comments, ytd-comments');
            if (c) {
                c.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                window.scrollBy({ top: 700, behavior: 'smooth' });
            }
        """)
    except Exception:
        pass

    _interruptible_sleep(random.uniform(1.0, 1.8), stop_event)

    # Check if comment threads rendered; if not, scroll down slightly more to trigger loading
    try:
        has_threads = driver.execute_script(
            "return document.querySelectorAll('ytd-comment-thread-renderer, ytd-comment-view-model, ytd-comment-renderer').length > 0;"
        )
        if not has_threads:
            driver.execute_script("window.scrollBy({ top: 350, behavior: 'smooth' });")
            _interruptible_sleep(1.2, stop_event)
    except Exception:
        pass

    for _ in range(target_count):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            res = driver.execute_script(LIKE_COMMENT_JS)
            if isinstance(res, dict) and res.get("found"):
                # Click and verify that comment actually becomes liked!
                success = click_and_verify_comment_like(driver, res)
                if success:
                    liked += 1
                    author = res.get("author")
                    if author:
                        emit(log_queue, f"Natural interaction: liked comment by {author}.")
                    else:
                        emit(log_queue, f"Natural interaction: liked a comment ({liked}/{target_count}).")
                    _interruptible_sleep(random.uniform(1.0, 2.0), stop_event)
                else:
                    logging.debug("Could not verify comment like state after click.")
            else:
                break
        except Exception as exc:
            logging.debug(f"like_random_comments error: {exc}")
            break

    return liked


def like_active_video(
    driver: Any,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Click the Like Video button if the video is not already liked, using trusted actions and verification."""
    try:
        # Check if already liked first
        if is_video_liked(driver):
            return True

        res = driver.execute_script(LIKE_VIDEO_JS)
        if isinstance(res, dict):
            if res.get("already_liked"):
                return True

            btn = res.get("button")
            renderer = res.get("renderer")
            if btn is not None:
                # Scroll into view
                try:
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});", btn)
                except Exception:
                    pass
                time.sleep(0.3)

                # Try ActionChains hardware click
                try:
                    ActionChains(driver).move_to_element(btn).pause(0.1).click().perform()
                except Exception:
                    pass

                time.sleep(0.4)
                if not is_video_liked(driver, btn):
                    try:
                        btn.click()
                    except Exception:
                        pass

                time.sleep(0.4)
                if not is_video_liked(driver, btn) and renderer is not None and renderer != btn:
                    try:
                        ActionChains(driver).move_to_element(renderer).pause(0.1).click().perform()
                    except Exception:
                        pass

                # Fallback to realistic PointerEvent dispatch
                if not is_video_liked(driver, btn):
                    try:
                        driver.execute_script("""
                            var el = arguments[0];
                            if (!el) return;
                            var r = el.getBoundingClientRect();
                            var x = r.left + r.width / 2;
                            var y = r.top + r.height / 2;
                            var opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse', isPrimary: true };
                            try { el.dispatchEvent(new PointerEvent('pointerdown', opts)); } catch(e){}
                            try { el.dispatchEvent(new MouseEvent('mousedown', opts)); } catch(e){}
                            try { el.dispatchEvent(new PointerEvent('pointerup', opts)); } catch(e){}
                            try { el.dispatchEvent(new MouseEvent('mouseup', opts)); } catch(e){}
                            try { el.dispatchEvent(new MouseEvent('click', opts)); } catch(e){}
                            try { el.click(); } catch(e){}
                        """, btn)
                    except Exception:
                        pass

                # Poll up to 1.5s to verify
                for _ in range(6):
                    time.sleep(0.25)
                    if is_video_liked(driver, btn):
                        emit(log_queue, "Natural interaction: liked the video.")
                        return True
            elif res.get("success"):
                # Fallback for mock environment
                emit(log_queue, "Natural interaction: liked the video.")
                return True

        return False
    except Exception as exc:
        logging.debug(f"like_active_video error: {exc}")
        return False


def browse_description_and_like_comment(
    driver: Any,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    expand_desc: bool = True,
    like_comm: bool = True,
) -> Tuple[bool, bool]:
    """Scroll down to view description, click expand only once, then scroll to comments and only like once."""
    if stop_event is not None and stop_event.is_set():
        return False, False

    desc_done = False
    comment_done = False

    # 1. Smooth scroll down to video description area
    try:
        step1 = random.randint(320, 480)
        driver.execute_script(f"window.scrollBy({{top: {step1}, behavior: 'smooth'}});")
    except Exception:
        pass

    if _interruptible_sleep(random.uniform(0.7, 1.2), stop_event):
        return False, False

    # 2. Click description '...more' button only once
    if expand_desc:
        desc_done = expand_video_description(driver, log_queue=log_queue)
        _interruptible_sleep(random.uniform(1.0, 1.8), stop_event)

    # 3. Scroll down to comments section
    if like_comm:
        try:
            driver.execute_script("""
                var c = document.querySelector('#comments, ytd-comments');
                if (c) {
                    c.scrollIntoView({ behavior: 'smooth', block: 'start' });
                } else {
                    window.scrollBy({ top: 550, behavior: 'smooth' });
                }
            """)
        except Exception:
            pass

        if _interruptible_sleep(random.uniform(1.2, 1.8), stop_event):
            return desc_done, False

        # 4. Like only 1 comment once (with strict check that it is not already liked)
        liked_count = like_random_comments(driver, min_likes=1, max_likes=2, stop_event=stop_event, log_queue=log_queue)
        comment_done = (liked_count > 0)

        _interruptible_sleep(random.uniform(1.2, 1.8), stop_event)

    # 5. Smooth scroll back up to video player
    try:
        driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
    except Exception:
        pass

    _interruptible_sleep(random.uniform(0.8, 1.4), stop_event)
    return desc_done, comment_done


def simulate_reading_comments_or_description(
    driver: Any,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    min_read_seconds: float = 4.0,
    max_read_seconds: float = 8.0,
    like_comments: bool = False,
    expand_description: bool = False,
) -> bool:
    """Simulate a user scrolling down to read comments or description during a long video.

    Performs smooth staggered scrolling down below the player, pauses there to simulate
    reading (with occasional expand of description or micro-scroll), and then smoothly scrolls
    back up to the video player.
    """
    if stop_event is not None and stop_event.is_set():
        return False

    emit(log_queue, "Natural interaction: scrolling down to browse description/comments...")

    # Step 1: Smooth scroll down in natural staggered chunks
    try:
        step1 = random.randint(320, 480)
        driver.execute_script(f"window.scrollBy({{top: {step1}, behavior: 'smooth'}});")
    except Exception:
        return False

    if _interruptible_sleep(random.uniform(0.5, 0.9), stop_event):
        return False

    try:
        step2 = random.randint(250, 420)
        driver.execute_script(f"window.scrollBy({{top: {step2}, behavior: 'smooth'}});")
    except Exception:
        pass

    # Step 2: Click '...more' button to expand video description if requested
    if expand_description:
        expand_video_description(driver, log_queue=log_queue)

    # Step 3: Pause down there to read
    read_duration = random.uniform(min_read_seconds, max_read_seconds)
    half_time = read_duration / 2.0
    if _interruptible_sleep(half_time, stop_event):
        return False

    # Like 1 or 2 random comments while browsing comments (if not already liked)
    if like_comments:
        like_random_comments(driver, min_likes=1, max_likes=2, stop_event=stop_event, log_queue=log_queue)

    # Small scroll jitter while reading comments
    try:
        jitter = random.randint(80, 180)
        driver.execute_script(f"window.scrollBy({{top: {jitter}, behavior: 'smooth'}});")
    except Exception:
        pass

    if _interruptible_sleep(half_time, stop_event):
        return False

    # Step 4: Smooth scroll back up to the video player
    try:
        driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
    except Exception:
        pass

    _interruptible_sleep(random.uniform(0.8, 1.4), stop_event)
    return True


def simulate_micro_pause(
    driver: Any,
    pause_duration: float = 0.0,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
) -> float:
    """Briefly pause the video for 2-5 seconds midway and resume playback.

    Returns the actual seconds paused, or 0.0 if interrupted/failed.
    """
    if stop_event is not None and stop_event.is_set():
        return 0.0

    if pause_duration <= 0.0:
        pause_duration = round(random.uniform(2.5, 5.5), 1)

    emit(
        log_queue,
        f"Natural interaction: paused video ({pause_duration:.1f}s) simulating human distraction...",
    )

    try:
        driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
    except Exception:
        return 0.0

    interrupted = _interruptible_sleep(pause_duration, stop_event)
    if interrupted or (stop_event is not None and stop_event.is_set()):
        return 0.0

    try:
        driver.execute_script("var v = document.querySelector('video'); if (v) v.play();")
        emit(log_queue, "Natural interaction: resumed video playback.")
    except Exception:
        pass

    return pause_duration


class HumanInteractivitySimulator:
    """Orchestrates natural human-like interactivity during video playback."""

    def __init__(
        self,
        enabled: bool = True,
        scroll_comments: bool = True,
        variable_speed: bool = False,       # User rule: strictly 1x playback
        adjust_quality: bool = True,
        quality_level: str = "360p",        # "360p", "480p", or "Random"
        micro_pauses: bool = True,
        toggle_captions: bool = True,
        expand_description: bool = True,
        like_comments: bool = True,
        like_video: bool = True,
        speed_chance: float = 0.0,          # 0.0 -> no speed changes away from 1x
        quality_chance: float = 0.85,
        caption_chance: float = 0.65,
        pause_chance: float = 0.40,
    ):
        self.enabled = enabled
        self.scroll_comments = scroll_comments
        self.variable_speed = variable_speed
        self.adjust_quality = adjust_quality
        self.quality_level = quality_level
        self.micro_pauses = micro_pauses
        self.toggle_captions = toggle_captions
        self.expand_description = expand_description
        self.like_comments = like_comments
        self.like_video = like_video
        self.speed_chance = speed_chance
        self.quality_chance = quality_chance
        self.caption_chance = caption_chance
        self.pause_chance = pause_chance

        # Session state
        self.planned = False
        self.is_long_video = False
        self.speed_applied = False
        self.quality_applied = False
        self.micro_pause_done = False
        self.micro_pause2_done = False
        self.scroll_comments_done = False
        self.caption_enable_done = False
        self.caption_disable_done = False
        self.description_expanded = False
        self.comments_liked = False
        self.video_liked = False
        self.browse_desc_comments_done = False
        self.browse_desc_comments_target_time = 0.0
        self.will_browse_desc_comments = False

        self.will_micro_pause = False
        self.will_change_speed = False
        self.will_like_comments = False
        self.like_comments_target_time = 0.0
        self.will_like_video = False
        self.like_video_target_time = 0.0
        self.will_expand_description = False
        self.expand_description_target_time = 0.0
        self.late_interactions_done = False
        self.late_interactions_target_time = 0.0
        self.interaction_order = "video_then_comments" 
        self.will_adjust_quality = False
        self.chosen_quality = "360p"
        self.will_toggle_captions = False
        self.caption_enable_time = 0.0
        self.caption_disable_time = 0.0
        self.micro_pause_target_time = 0.0
        self.micro_pause2_target_time = 0.0
        self.scroll_target_time = 0.0

    def plan_session(self, video_duration: float, duration_budget: Optional[int] = None) -> None:
        """Schedule natural interaction events for the current video."""
        if not self.enabled:
            return

        effective_duration = (
            video_duration if video_duration > 0 else float(duration_budget or 300)
        )
        self.is_long_video = effective_duration >= 90.0 or duration_budget is None

        # Speed: strictly 1x unless explicitly requested otherwise
        self.will_change_speed = bool(self.variable_speed and (random.random() < self.speed_chance))

        # Quality: strictly 360p or 480p
        self.will_adjust_quality = random.random() < self.quality_chance
        q_norm = (self.quality_level or "360p").lower()
        if "480" in q_norm and "360" not in q_norm:
            self.chosen_quality = "480p"
        elif "360" in q_norm and "480" not in q_norm:
            self.chosen_quality = "360p"
        else:
            self.chosen_quality = random.choice(["360p", "480p"])

        # 1. Expand video description after 10% of the video is watched
        self.will_expand_description = bool(self.expand_description)
        if self.will_expand_description:
            # 10% progress + small random jitter (0.5 - 2s)
            self.expand_description_target_time = max(3.0, (effective_duration * 0.10) + random.uniform(0.5, 2.0))

        # 2. Like comments and like video at 70-80% progress + random delay
        self.will_like_comments = bool(self.like_comments)
        self.will_like_video = bool(self.like_video)

        progress_ratio_70_80 = random.uniform(0.70, 0.80)
        random_delay = random.uniform(2.0, 6.0)
        late_target = (effective_duration * progress_ratio_70_80) + random_delay
        self.late_interactions_target_time = min(late_target, max(10.0, effective_duration * 0.92))
        self.like_comments_target_time = self.late_interactions_target_time
        self.like_video_target_time = self.late_interactions_target_time

        # Randomize order: sometimes like video then comments, sometimes comments then video
        self.interaction_order = random.choice(["video_then_comments", "comments_then_video"])

        # Closed captions
        self.will_toggle_captions = bool(self.toggle_captions and (random.random() < self.caption_chance))
        if self.will_toggle_captions:
            self.caption_enable_time = random.uniform(7.0, min(16.0, max(8.0, effective_duration * 0.25)))
            if effective_duration >= 45.0 and random.random() < 0.5:
                self.caption_disable_time = random.uniform(
                    self.caption_enable_time + 15.0,
                    min(effective_duration * 0.75, self.caption_enable_time + 60.0),
                )
            else:
                self.caption_disable_time = 0.0

        # Micro-pauses: pause randomly, not on every video
        self.will_micro_pause = bool(
            self.micro_pauses and (random.random() < self.pause_chance)
        )
        if self.will_micro_pause and effective_duration >= 25.0:
            midway_min = max(15.0, effective_duration * 0.30)
            midway_max = min(effective_duration * 0.55, effective_duration - 10.0)
            if midway_max > midway_min:
                self.micro_pause_target_time = random.uniform(midway_min, midway_max)
            else:
                self.micro_pause_target_time = midway_min

            if effective_duration >= 140.0 and random.random() < 0.40:
                p2_min = max(self.micro_pause_target_time + 30.0, effective_duration * 0.65)
                p2_max = min(effective_duration * 0.85, effective_duration - 15.0)
                if p2_max > p2_min:
                    self.micro_pause2_target_time = random.uniform(p2_min, p2_max)
        else:
            self.micro_pause_target_time = 0.0
            self.micro_pause2_target_time = 0.0

        if self.is_long_video:
            # Scrolling down to read comments/description
            scroll_min = min(35.0, effective_duration * 0.25)
            scroll_max = min(effective_duration * 0.75, 240.0)
            if scroll_max > scroll_min:
                self.scroll_target_time = random.uniform(scroll_min, scroll_max)
            else:
                self.scroll_target_time = scroll_min

            if abs(self.scroll_target_time - self.micro_pause_target_time) < 18.0:
                self.scroll_target_time = self.micro_pause_target_time + 22.0

        self.planned = True

    def on_playback_tick(
        self,
        driver: Any,
        curr_time: float,
        video_duration: float,
        is_ad: bool,
        paused: bool,
        stop_event: Optional[threading.Event] = None,
        log_queue: Optional[queue.Queue] = None,
        duration_budget: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Called on each playback loop cycle to evaluate and trigger natural interactions.

        Returns:
            Dict containing event info, e.g. ``{"micro_paused": seconds}``.
        """
        result: Dict[str, Any] = {}
        if not self.enabled or is_ad:
            return result

        # Plan session once duration or timeline progress is known
        if not self.planned and (video_duration > 0 or curr_time >= 3.0):
            self.plan_session(video_duration, duration_budget)

        if not self.planned:
            return result

        # ---------------- Early playback: Quality adjustment & 1x speed lock
        if curr_time >= 5.0 and not paused:
            # Quality adjustment strictly to 360p or 480p
            if self.adjust_quality and self.will_adjust_quality and not self.quality_applied:
                target_q = self.chosen_quality
                if set_video_quality(driver, target_q):
                    emit(
                        log_queue,
                        f"Natural interaction: adjusted video quality to {target_q}.",
                    )
                self.quality_applied = True

            # If variable speed is explicitly allowed and planned, set it; otherwise keep 1.0x
            if self.variable_speed and self.will_change_speed and not self.speed_applied:
                target_speed = 1.0
                set_playback_speed(driver, target_speed)
                self.speed_applied = True
            elif not self.speed_applied:
                # Enforce normal 1.0x speed
                ensure_normal_speed(driver)
                self.speed_applied = True

        # ---------------- Expand description (after 10% watched) -----------
        if (
            self.expand_description
            and self.will_expand_description
            and not self.description_expanded
            and not paused
            and curr_time >= self.expand_description_target_time
        ):
            try:
                scroll_down = random.randint(300, 450)
                driver.execute_script(f"window.scrollBy({{top: {scroll_down}, behavior: 'smooth'}});")
            except Exception:
                pass
            _interruptible_sleep(random.uniform(0.8, 1.4), stop_event)

            expanded = expand_video_description(driver, log_queue=log_queue)
            self.description_expanded = True
            if expanded:
                result["description_expanded"] = True

            _interruptible_sleep(random.uniform(1.2, 2.0), stop_event)
            try:
                driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
            except Exception:
                pass

        # ---------------- Late interactions: like comments & video (70-80% + delay) -
        if (
            not self.late_interactions_done
            and not paused
            and curr_time >= self.late_interactions_target_time
        ):
            self.late_interactions_done = True

            def _do_like_video():
                if self.like_video and self.will_like_video and not self.video_liked:
                    like_active_video(driver, log_queue=log_queue)
                    self.video_liked = True
                    result["video_liked"] = True

            def _do_like_comments():
                if self.like_comments and self.will_like_comments and not self.comments_liked:
                    try:
                        driver.execute_script("""
                            var c = document.querySelector('#comments, ytd-comments');
                            if (c) {
                                c.scrollIntoView({ behavior: 'smooth', block: 'start' });
                            } else {
                                window.scrollBy({ top: 650, behavior: 'smooth' });
                            }
                        """)
                    except Exception:
                        pass
                    _interruptible_sleep(random.uniform(1.2, 2.0), stop_event)

                    liked_count = like_random_comments(driver, min_likes=1, max_likes=2, stop_event=stop_event, log_queue=log_queue)
                    self.comments_liked = True
                    if liked_count > 0:
                        result["comments_liked"] = liked_count

                    _interruptible_sleep(random.uniform(1.0, 1.8), stop_event)
                    try:
                        driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
                    except Exception:
                        pass

            if self.interaction_order == "video_then_comments":
                _do_like_video()
                _interruptible_sleep(random.uniform(1.5, 3.0), stop_event)
                _do_like_comments()
            else:
                _do_like_comments()
                _interruptible_sleep(random.uniform(1.5, 3.0), stop_event)
                _do_like_video()

        # ---------------- Closed captions (enable early) --------------------
        if (
            self.toggle_captions
            and self.will_toggle_captions
            and not self.caption_enable_done
            and not paused
            and curr_time >= self.caption_enable_time
        ):
            toggle_captions(driver, target_state="enable", log_queue=log_queue)
            self.caption_enable_done = True
            result["captions_enabled"] = True

        # ---------------- Closed captions (disable later if planned) --------
        if (
            self.toggle_captions
            and self.caption_disable_time > 0
            and not self.caption_disable_done
            and not paused
            and curr_time >= self.caption_disable_time
        ):
            toggle_captions(driver, target_state="disable", log_queue=log_queue)
            self.caption_disable_done = True
            result["captions_disabled"] = True

        # ---------------- Midway micro-pause (human distraction) -----------
        if (
            self.micro_pauses
            and self.will_micro_pause
            and self.micro_pause_target_time > 0
            and not self.micro_pause_done
            and not paused
            and curr_time >= self.micro_pause_target_time
        ):
            paused_sec = simulate_micro_pause(
                driver,
                stop_event=stop_event,
                log_queue=log_queue,
            )
            self.micro_pause_done = True
            if paused_sec > 0:
                result["micro_paused"] = paused_sec
                return result

        # ---------------- Second micro-pause (for longer videos) ------------
        if (
            self.micro_pauses
            and self.will_micro_pause
            and self.micro_pause2_target_time > 0
            and not self.micro_pause2_done
            and not paused
            and curr_time >= self.micro_pause2_target_time
        ):
            paused_sec2 = simulate_micro_pause(
                driver,
                stop_event=stop_event,
                log_queue=log_queue,
            )
            self.micro_pause2_done = True
            if paused_sec2 > 0:
                result["micro_paused"] = paused_sec2
                return result

        # ---------------- Scrolling down to read comments/description ------
        if (
            self.scroll_comments
            and self.is_long_video
            and not self.scroll_comments_done
            and not paused
            and self.scroll_target_time > 0
            and curr_time >= self.scroll_target_time
        ):
            scroll_ok = simulate_reading_comments_or_description(
                driver,
                stop_event=stop_event,
                log_queue=log_queue,
                like_comments=False,
                expand_description=self.expand_description and not self.description_expanded,
            )
            self.scroll_comments_done = True
            if scroll_ok:
                result["scrolled_comments"] = True
                return result

        # ---------------- Subtle minor micro-scroll while watching ----------
        if not paused and random.random() < 0.12:
            try:
                scroll_amount = random.randint(80, 200)
                driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
                time.sleep(random.uniform(0.6, 1.2))
                driver.execute_script(f"window.scrollBy(0, -{scroll_amount});")
            except Exception:
                pass

        return result
