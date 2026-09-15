import logging
import queue
import random
import threading
import time
from typing import Any, Optional

from ..utils.helpers import human_like_delay
from ..utils.logger import emit


def watch_video(
    driver: Any,
    duration_seconds: Optional[int],
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
) -> None:
    """Watch target video for the specified duration (or full video if duration_seconds is None) with human-like interactions."""
    is_full_mode = duration_seconds is None or duration_seconds <= 0

    if is_full_mode:
        emit(log_queue, "Watching full video until playback finishes...")
    else:
        emit(log_queue, f"Watching video for up to {duration_seconds} seconds...")

    # Ensure video is playing (only trigger play if currently paused)
    try:
        is_paused = driver.execute_script("var v = document.querySelector('video'); return v ? v.paused : false;")
        if is_paused:
            emit(log_queue, "Video is paused, initiating playback...")
            driver.execute_script("var v = document.querySelector('video'); if (v) { v.play(); }")
            human_like_delay(1.0, 1.5)
            # Fallback click play button if JS play was blocked
            still_paused = driver.execute_script("var v = document.querySelector('video'); return v ? v.paused : false;")
            if still_paused:
                play_btn = driver.find_elements(By.CSS_SELECTOR, "button.ytp-play-button")
                if play_btn:
                    play_btn[0].click()
    except Exception as exc:
        logging.debug(f"Error checking/triggering video playback: {exc}")

    start_time = time.time()
    last_log_time = 0.0

    while True:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            break

        elapsed = time.time() - start_time

        # Check video playback state via JavaScript
        try:
            video_state = driver.execute_script("""
                var v = document.querySelector('video');
                if (!v) return { found: false };
                var isAd = false;
                var adElem = document.querySelector('.ad-showing, .ytp-ad-player-overlay, .video-ads');
                if (adElem) isAd = true;

                return {
                    found: true,
                    paused: v.paused,
                    ended: v.ended,
                    currentTime: v.currentTime || 0,
                    duration: v.duration || 0,
                    isAd: isAd
                };
            """)
        except Exception:
            video_state = {"found": False}

        # Check if Skip Ad button is visible and click it
        try:
            skip_btns = driver.find_elements(
                By.CSS_SELECTOR,
                ".ytp-ad-skip-button, .ytp-skip-ad-button, button.ytp-ad-skip-button-modern, .ytp-ad-skip-button-text"
            )
            for s_btn in skip_btns:
                if s_btn.is_displayed():
                    emit(log_queue, "Skip Ad button detected. Clicking Skip Ad...")
                    driver.execute_script("arguments[0].click();", s_btn)
                    break
        except Exception:
            pass

        if video_state.get("found"):
            is_ad = video_state.get("isAd", False)
            ended = video_state.get("ended", False)
            curr_time = video_state.get("currentTime", 0.0)
            v_dur = video_state.get("duration", 0.0)
            paused = video_state.get("paused", False)

            # If not an ad and playback finished (ended flag set or currentTime reached duration)
            if not is_ad and v_dur > 0:
                if ended or curr_time >= (v_dur - 1.5):
                    dur_formatted = f"{int(v_dur // 60):02d}:{int(v_dur % 60):02d}"
                    emit(log_queue, f"Video playback finished! (Watched full length: {dur_formatted})")
                    break

            # If paused mid-video (and not an ad and not ended), attempt to resume playback
            if paused and not ended and not is_ad and (v_dur == 0 or curr_time < (v_dur - 2)):
                try:
                    driver.execute_script("var v = document.querySelector('video'); if (v) v.play();")
                except Exception:
                    pass

            # Log periodic status for Full Video mode
            if is_full_mode and (time.time() - last_log_time >= 30):
                last_log_time = time.time()
                if is_ad:
                    emit(log_queue, "Ad is playing, waiting for main video...")
                elif v_dur > 0:
                    curr_fmt = f"{int(curr_time // 60):02d}:{int(curr_time % 60):02d}"
                    dur_fmt = f"{int(v_dur // 60):02d}:{int(v_dur % 60):02d}"
                    percent = int((curr_time / v_dur) * 100) if v_dur > 0 else 0
                    emit(log_queue, f"Watching full video progress: {curr_fmt} / {dur_fmt} ({percent}%)")

        # For fixed duration mode, stop if elapsed exceeds target duration
        if not is_full_mode and duration_seconds is not None:
            if elapsed >= duration_seconds:
                emit(log_queue, f"Reached watch duration limit ({duration_seconds}s).")
                break

        time.sleep(3)

        # Occasional random micro-scroll
        if random.random() < 0.2:
            try:
                scroll_amount = random.randint(100, 300)
                driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
                time.sleep(random.uniform(1.0, 2.0))
                driver.execute_script(f"window.scrollBy(0, -{scroll_amount});")
            except Exception:
                pass
