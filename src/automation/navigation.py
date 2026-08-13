import logging
import queue
import time
from typing import Any, Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from ..models.video_task import VideoTask
from ..utils.helpers import human_like_delay, slow_type
from ..utils.logger import emit


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL string."""
    if not url:
        return None
    import re
    patterns = [
        r"(?:v=|\/vi\/|youtu\.be\/|\/v\/|\/embed\/|\/shorts\/)([A-Za-z0-9_\-]{11})",
        r"^([A-Za-z0-9_\-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def navigate_to_video_organically(
    driver: Any,
    task: VideoTask,
    direct_link_fallback: bool = True,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """
    Organic Navigation Strategy:
    1. If search keywords or video title are provided, perform YouTube search.
    2. Scroll search results to find matching video ID or title, then click it.
    3. If search fails or yields no match, use direct_link_fallback if enabled.
    """
    search_query = task.keywords.strip() or task.title.strip()
    target_video_id = extract_video_id(task.url)

    organic_success = False

    if search_query:
        emit(log_queue, f"Starting organic search for query: '{search_query}'")
        try:
            driver.get("https://www.youtube.com")
            human_like_delay(1.5, 3.0)

            # Accept cookies dialog if shown
            try:
                reject_or_accept = driver.find_elements(By.XPATH, "//button[contains(., 'Accept') or contains(., 'I agree')]")
                if reject_or_accept:
                    reject_or_accept[0].click()
                    human_like_delay(1.0, 1.5)
            except Exception:
                pass

            # Search box
            search_box = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@id='search' or @name='search_query']"))
            )
            search_box.clear()
            slow_type(search_box, search_query)
            human_like_delay(0.5, 1.0)
            search_box.submit()
            human_like_delay(2.5, 4.0)

            # Wait for search results
            results = WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ytd-video-renderer a#video-title"))
            )

            emit(log_queue, f"Found {len(results)} initial search results. Searching for target video...")
            
            selected_link = None
            for idx, link in enumerate(results[:15]):
                href = link.get_attribute("href") or ""
                title_text = (link.get_attribute("title") or link.text or "").strip().lower()

                # Check ID match
                if target_video_id and target_video_id in href:
                    selected_link = link
                    emit(log_queue, f"Found target video in search result #{idx + 1} by video ID match!")
                    break

                # Check title match
                if task.title and task.title.lower() in title_text:
                    selected_link = link
                    emit(log_queue, f"Found target video in search result #{idx + 1} by title match!")
                    break

            if selected_link:
                # Scroll link into view & click organically
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", selected_link)
                human_like_delay(1.0, 2.0)
                selected_link.click()
                human_like_delay(3.0, 5.0)
                organic_success = True
                emit(log_queue, "Successfully clicked video from YouTube organic search results!")
                return True
            else:
                emit(log_queue, "Target video not found in top search results.", logging.WARNING)

        except Exception as exc:
            emit(log_queue, f"Organic search attempt failed: {exc}", logging.WARNING)

    # Direct Link Fallback
    if not organic_success:
        if direct_link_fallback and task.url:
            emit(log_queue, f"Falling back to direct video URL: {task.url}")
            try:
                driver.get(task.url)
                human_like_delay(3.0, 5.0)
                return True
            except Exception as exc:
                emit(log_queue, f"Direct link navigation failed: {exc}", logging.ERROR)
                return False
        else:
            emit(log_queue, "Direct link fallback disabled and organic search was unsuccessful.", logging.ERROR)
            return False

    return False
