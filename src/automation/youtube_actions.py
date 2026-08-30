import logging
import queue
import random
import threading
import time
from typing import Dict, Any, Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from ..models.video_task import VideoTask
from ..ai.base import BaseAIProvider
from ..utils.helpers import human_like_delay, slow_type, sanitize_text
from ..utils.logger import emit


def extract_video_context(driver: Any) -> Dict[str, str]:
    """Scrape title, channel, and description snippet from current video page."""
    context = {"title": "", "channel": "", "description": ""}

    try:
        title_elem = driver.find_element(By.CSS_SELECTOR, "h1.ytd-watch-metadata, h1.yt-core-attributed-string")
        context["title"] = title_elem.text.strip()
    except Exception:
        context["title"] = driver.title or ""

    try:
        channel_elem = driver.find_element(By.CSS_SELECTOR, "#owner #channel-name a, #upload-info #channel-name a")
        context["channel"] = channel_elem.text.strip()
    except Exception:
        pass

    try:
        desc_elem = driver.find_element(By.CSS_SELECTOR, "#description-inline-expander, #description")
        context["description"] = desc_elem.text[:400].strip()
    except Exception:
        pass

    return context


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


def like_video(driver: Any, log_queue: Optional[queue.Queue] = None) -> bool:
    """Locate and click YouTube Like button."""
    emit(log_queue, "Attempting to like video...")
    try:
        driver.execute_script("window.scrollBy(0, 150);")
        human_like_delay(1.0, 1.5)

        like_btn = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//segmented-like-dislike-button-view-model//button[1] | "
                    "//like-button-view-model//button | "
                    "//ytd-menu-renderer//button[contains(@aria-label, 'like') or contains(@aria-label, 'Like')] | "
                    "//button[contains(@aria-label, 'like this video') and not(contains(@aria-label, 'dislike'))] | "
                    "//button[contains(@title, 'I like this')]",
                )
            )
        )

        is_already_liked = like_btn.get_attribute("aria-pressed") == "true"
        if is_already_liked:
            emit(log_queue, "Video is already liked.")
            return True

        try:
            like_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", like_btn)

        human_like_delay(1.0, 2.0)
        emit(log_queue, "Successfully liked the video!")
        return True
    except Exception as exc:
        emit(log_queue, f"Could not click Like button: {exc}", logging.WARNING)
        return False


def post_comment(driver: Any, comment_text: str, log_queue: Optional[queue.Queue] = None) -> bool:
    """Scroll to comments section, type comment text, and click post button."""
    clean_comment = sanitize_text(comment_text)
    if not clean_comment:
        emit(log_queue, "Comment text is empty. Skipping.", logging.WARNING)
        return False

    emit(log_queue, f"Posting comment: '{clean_comment}'")

    try:
        # Scroll down to load comments section
        driver.execute_script("window.scrollTo(0, 500);")
        human_like_delay(2.0, 3.5)

        # Locate comment box placeholder
        placeholder = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[@id='placeholder-area'] | //ytd-comment-simplebox-renderer",
                )
            )
        )
        placeholder.click()
        human_like_delay(1.0, 2.0)

        # Editable text area
        editable = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[@id='contenteditable-root' or @contenteditable='true']",
                )
            )
        )
        editable.clear()
        slow_type(editable, clean_comment)
        human_like_delay(1.0, 2.0)

        # Click submit comment button
        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//ytd-button-renderer[@id='submit-button']//button | //button[@aria-label='Comment']",
                )
            )
        )
        submit_btn.click()
        human_like_delay(3.0, 5.0)
        emit(log_queue, "Comment posted successfully!")
        return True

    except Exception as exc:
        emit(log_queue, f"Failed to post comment: {exc}", logging.ERROR)
        return False


def perform_youtube_interactions(
    driver: Any,
    task: VideoTask,
    ai_provider: Optional[BaseAIProvider] = None,
    log_queue: Optional[queue.Queue] = None,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Orchestrate watching, liking, context extraction, and commenting."""
    watch_seconds = task.parse_duration_seconds()
    watch_video(driver, watch_seconds, stop_event=stop_event, log_queue=log_queue)

    if stop_event is not None and stop_event.is_set():
        return False

    if task.like:
        like_video(driver, log_queue=log_queue)

    # Comment section
    comment_text = ""
    if task.comment_type == "Custom" and task.custom_comment:
        comment_text = task.custom_comment
    elif task.comment_type == "AI" and ai_provider is not None:
        emit(log_queue, "Extracting video context for AI comment generation...")
        context = extract_video_context(driver)
        try:
            comment_text = ai_provider.generate_comment(context)
            emit(log_queue, f"AI generated comment: '{comment_text}'")
        except Exception as exc:
            emit(log_queue, f"AI comment generation failed: {exc}", logging.ERROR)
            if task.custom_comment:
                comment_text = task.custom_comment

    if comment_text:
        post_comment(driver, comment_text, log_queue=log_queue)

    return True
