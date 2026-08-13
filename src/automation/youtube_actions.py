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
    duration_seconds: int,
    stop_event: Optional[threading.Event] = None,
    log_queue: Optional[queue.Queue] = None,
) -> None:
    """Watch target video for the specified duration with human-like interactions."""
    emit(log_queue, f"Watching video for {duration_seconds} seconds...")

    # Play video if paused
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys("k")  # Toggle play shortcut in YouTube
    except Exception:
        pass

    start_time = time.time()
    end_time = start_time + duration_seconds

    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            emit(log_queue, "Watchtime interrupted by user.")
            break

        time.sleep(min(5, max(1, end_time - time.time())))

        # Occasional random micro-scroll
        if random.random() < 0.25:
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
        like_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//like-button-view-model//button | //button[contains(@aria-label, 'like this video') and not(contains(@aria-label, 'dislike'))]",
                )
            )
        )

        is_already_liked = like_btn.get_attribute("aria-pressed") == "true"
        if is_already_liked:
            emit(log_queue, "Video is already liked.")
            return True

        like_btn.click()
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
