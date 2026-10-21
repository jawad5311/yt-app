"""Natural Human Interactivity Simulation for YouTube playback.

Provides human-like behavioral simulation during video playback:
1. Randomized Scrolling & Pausing (scrolling down to read description/comments, pausing, and scrolling back).
2. Variable Playback Speeds (occasionally 1.25x) & Bandwidth-Saving Quality (360p / 720p).
3. Random Micro-Pauses (brief 2-5s pauses midway through long videos simulating human distraction).
"""

import queue
import random
import threading
import time
from typing import Any, Dict, Optional

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
var target = arguments[0]; // e.g. "360p" or "720p"
var level = arguments[1];  // e.g. "medium" or "hd720"
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


def set_playback_speed(driver: Any, speed: float = 1.25) -> bool:
    """Set the playback rate on the active YouTube player (e.g. 1.25x)."""
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


def set_video_quality(driver: Any, quality_label: str = "360p") -> bool:
    """Adjust video quality to save bandwidth (e.g. '360p' or '720p')."""
    quality_label = quality_label.strip().lower()
    # YouTube quality mapping
    if "720" in quality_label:
        level = "hd720"
        label_norm = "720p"
    elif "480" in quality_label:
        level = "large"
        label_norm = "480p"
    elif "240" in quality_label:
        level = "small"
        label_norm = "240p"
    elif "144" in quality_label:
        level = "tiny"
        label_norm = "144p"
    elif "auto" in quality_label:
        level = "auto"
        label_norm = "Auto"
    else:
        # Default bandwidth saver
        level = "medium"
        label_norm = "360p"

    try:
        driver.execute_script(SET_QUALITY_JS, label_norm, level)
        return True
    except Exception:
        return False


def simulate_reading_comments_or_description(
    driver: Any,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
    min_read_seconds: float = 4.0,
    max_read_seconds: float = 8.0,
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

    # Step 2: 35% chance to click expand description if present
    if random.random() < 0.35:
        try:
            driver.execute_script("""
            var exp = document.querySelector('#expand, #description-inline-expander #expand, tp-yt-paper-button#expand');
            if (exp && exp.offsetParent !== null) exp.click();
            """)
        except Exception:
            pass

    # Step 3: Pause down there to read
    read_duration = random.uniform(min_read_seconds, max_read_seconds)
    half_time = read_duration / 2.0
    if _interruptible_sleep(half_time, stop_event):
        return False

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
    """Briefly pause the video for 2-5 seconds midway to simulate human distraction.

    Returns the actual seconds paused, or 0.0 if interrupted/failed.
    """
    if stop_event is not None and stop_event.is_set():
        return 0.0

    if pause_duration <= 0.0:
        pause_duration = random.uniform(2.0, 5.0)

    emit(
        log_queue,
        f"Natural interaction: brief micro-pause ({pause_duration:.1f}s) midway through video to simulate human distraction...",
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
    except Exception:
        pass

    return pause_duration


class HumanInteractivitySimulator:
    """Orchestrates natural human-like interactivity during video playback."""

    def __init__(
        self,
        enabled: bool = True,
        scroll_comments: bool = True,
        variable_speed: bool = True,
        adjust_quality: bool = True,
        quality_level: str = "360p",
        micro_pauses: bool = True,
        speed_chance: float = 0.35,
        quality_chance: float = 0.60,
    ):
        self.enabled = enabled
        self.scroll_comments = scroll_comments
        self.variable_speed = variable_speed
        self.adjust_quality = adjust_quality
        self.quality_level = quality_level
        self.micro_pauses = micro_pauses
        self.speed_chance = speed_chance
        self.quality_chance = quality_chance

        # Session state
        self.planned = False
        self.is_long_video = False
        self.speed_applied = False
        self.quality_applied = False
        self.micro_pause_done = False
        self.scroll_comments_done = False

        self.will_change_speed = False
        self.will_adjust_quality = False
        self.micro_pause_target_time = 0.0
        self.scroll_target_time = 0.0

    def plan_session(self, video_duration: float, duration_budget: Optional[int] = None) -> None:
        """Schedule natural interaction events for the current video."""
        if not self.enabled:
            return

        effective_duration = (
            video_duration if video_duration > 0 else float(duration_budget or 300)
        )
        # Consider a video "long" if duration or budget is at least 90s (or Full Video mode)
        self.is_long_video = effective_duration >= 90.0 or duration_budget is None

        # Determine variable speed (e.g. ~35% chance to set 1.25x)
        self.will_change_speed = random.random() < self.speed_chance

        # Determine quality adjustment (~60% chance or if bandwidth saving enabled)
        self.will_adjust_quality = random.random() < self.quality_chance

        if self.is_long_video:
            # Micro-pause midway through the video (between 35% and 65% of length)
            midway_min = max(25.0, effective_duration * 0.35)
            midway_max = min(effective_duration * 0.65, effective_duration - 15.0)
            if midway_max > midway_min:
                self.micro_pause_target_time = random.uniform(midway_min, midway_max)
            else:
                self.micro_pause_target_time = midway_min

            # Scrolling down to read comments/description (between 30s and 70% of length)
            scroll_min = min(35.0, effective_duration * 0.25)
            scroll_max = min(effective_duration * 0.75, 240.0)
            if scroll_max > scroll_min:
                self.scroll_target_time = random.uniform(scroll_min, scroll_max)
            else:
                self.scroll_target_time = scroll_min

            # Ensure micro-pause and scrolling do not trigger at the same time
            if abs(self.scroll_target_time - self.micro_pause_target_time) < 20.0:
                self.scroll_target_time = self.micro_pause_target_time + 25.0

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

        # Plan the session once duration or initial timeline progress is known
        if not self.planned and (video_duration > 0 or curr_time >= 3.0):
            self.plan_session(video_duration, duration_budget)

        if not self.planned:
            return result

        # ---------------- Early playback: Quality & Speed adjustment --------
        if curr_time >= 6.0 and not paused:
            # Quality adjustment (360p / 720p)
            if self.adjust_quality and self.will_adjust_quality and not self.quality_applied:
                # Select target quality (360p to save bandwidth, or 720p)
                target_q = self.quality_level if self.quality_level in ("360p", "720p") else "360p"
                if set_video_quality(driver, target_q):
                    emit(
                        log_queue,
                        f"Natural interaction: adjusted video quality to {target_q} (bandwidth saving).",
                    )
                self.quality_applied = True

            # Variable playback speed (1.25x)
            if self.variable_speed and self.will_change_speed and not self.speed_applied:
                target_speed = 1.25
                if set_playback_speed(driver, target_speed):
                    emit(
                        log_queue,
                        f"Natural interaction: adjusted playback speed to {target_speed}x.",
                    )
                self.speed_applied = True

        # ---------------- Midway micro-pause (human distraction) -----------
        if (
            self.micro_pauses
            and self.is_long_video
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

        # ---------------- Scrolling down to read comments/description ------
        if (
            self.scroll_comments
            and self.is_long_video
            and not self.scroll_comments_done
            and not paused
            and curr_time >= self.scroll_target_time
        ):
            scroll_ok = simulate_reading_comments_or_description(
                driver,
                stop_event=stop_event,
                log_queue=log_queue,
            )
            self.scroll_comments_done = True
            if scroll_ok:
                result["scrolled_comments"] = True
                return result

        # ---------------- Subtle minor micro-scroll while watching ----------
        if not paused and random.random() < 0.15:
            try:
                scroll_amount = random.randint(80, 220)
                driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
                time.sleep(random.uniform(0.8, 1.6))
                driver.execute_script(f"window.scrollBy(0, -{scroll_amount});")
            except Exception:
                pass

        return result
