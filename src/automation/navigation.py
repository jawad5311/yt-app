import logging
import queue
from typing import Any, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..models.video_task import VideoTask
from ..utils.helpers import extract_video_id, human_like_delay, slow_type
from ..utils.logger import emit


def safe_get(driver: Any, url: str, timeout: int = 45, log_queue: Optional[queue.Queue] = None) -> None:
    """Navigate to URL safely, stopping sub-resource loading if network times out."""
    driver.set_page_load_timeout(timeout)
    try:
        driver.get(url)
    except Exception as exc:
        if "timeout" in str(exc).lower():
            emit(log_queue, "Page DOM ready; stopping background stream timeout and continuing...", logging.WARNING)
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        else:
            raise


def navigate_to_video_organically(
    driver: Any,
    task: VideoTask,
    direct_link_fallback: bool = True,
    log_queue: Optional[queue.Queue] = None,
    search_query_override: Optional[str] = None,
) -> bool:
    """
    Organic Navigation Strategy:
    1. Query & Video ID Parsing
    2. Stealth Navigation to YouTube Home
    3. Simulated Human Typing & Search Submission
    4. Search Result Scanning & Matching:
       - Match by Video URL/ID first (do not match titles on pass 1)
       - If not found by URL, increase timeout and check by Title
    5. Smooth Scroll & Organic Click
    6. Direct Link Fallback
    """
    # 1. Query & Video ID Parsing
    if search_query_override and search_query_override.strip():
        search_query = search_query_override.strip()
    else:
        search_query = task.keywords.strip() or task.title.strip()
    target_video_id = extract_video_id(task.url)

    emit(
        log_queue,
        f"[Step 1/6] Query & ID Parsing: Target Video ID = '{target_video_id or 'N/A'}', Search Query = '{search_query or 'None'}'",
    )

    organic_success = False

    if search_query:
        emit(log_queue, f"Starting organic search for query: '{search_query}'")
        try:
            # 2. Stealth Navigation to YouTube Home
            emit(log_queue, "[Step 2/6] Navigating to YouTube homepage (https://www.youtube.com)...")
            safe_get(driver, "https://www.youtube.com", timeout=45, log_queue=log_queue)
            human_like_delay(2.0, 3.5)

            # Accept cookies dialog if shown
            try:
                reject_or_accept = driver.find_elements(By.XPATH, "//button[contains(., 'Accept') or contains(., 'I agree')]")
                if reject_or_accept:
                    reject_or_accept[0].click()
                    emit(log_queue, "[Step 2/6] Accepted cookie/consent banner.")
                    human_like_delay(1.0, 1.5)
            except Exception:
                pass

            # 3. Simulated Human Typing
            emit(log_queue, "[Step 3/6] Locating search input box...")
            search_box = WebDriverWait(driver, 25).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//textarea[@name='search_query'] | //input[@name='search_query'] | //*[contains(@class, 'yt-searchbox-input')] | //input[@id='search'] | //textarea[@id='search'] | //ytd-searchbox//input | //ytd-searchbox//textarea | //form[contains(@class, 'SearchForm')]//textarea | //form[contains(@class, 'SearchForm')]//input",
                    )
                )
            )
            search_box.click()
            search_box.clear()
            emit(log_queue, f"[Step 3/6] Typing search query '{search_query}' character-by-character with human micro-delays...")
            slow_type(search_box, search_query)
            human_like_delay(0.5, 1.0)

            emit(log_queue, "[Step 3/6] Submitting search query via ENTER key...")
            search_box.send_keys(Keys.ENTER)
            human_like_delay(3.0, 4.0)

            # Verify if submission navigated or if search button click is required
            try:
                if "results" not in str(getattr(driver, "current_url", "") or ""):
                    search_btns = driver.find_elements(
                        By.XPATH,
                        "//button[@id='search-icon-legacy' or @aria-label='Search' or contains(@class, 'yt-searchbox-button')]",
                    )
                    if search_btns:
                        search_btns[0].click()
                        human_like_delay(2.0, 3.0)
            except Exception:
                pass

            # 4. Search Result Matching
            # Increased timeout to 35 seconds to prevent premature failure
            emit(log_queue, "[Step 4/6] Waiting for video search results to load...")
            results = WebDriverWait(driver, 35).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "ytd-video-renderer a#video-title, ytd-video-renderer a#video-title-link, a#video-title")
                )
            )

            emit(log_queue, f"[Step 4/6] Found {len(results)} search results. Scanning top results for matching Video URL/ID...")

            selected_link = None

            # Pass 1: Match by Video URL / Video ID ONLY (do not match titles yet)
            if target_video_id:
                for idx, link in enumerate(results[:25]):
                    href = link.get_attribute("href") or ""
                    if target_video_id in href:
                        selected_link = link
                        emit(log_queue, f"[Step 4/6] Matched search result #{idx + 1} by Video ID/URL ('{target_video_id}')!")
                        break

            # Pass 2: If not found using URL, increase timeout/wait and check by Titles
            if not selected_link and task.title:
                emit(
                    log_queue,
                    "[Step 4/6] Video not found by URL in initial scan. Increasing timeout and checking for Title match...",
                    logging.INFO,
                )
                try:
                    driver.execute_script("window.scrollBy(0, 600);")
                except Exception:
                    pass
                human_like_delay(2.5, 3.5)

                try:
                    more_results = WebDriverWait(driver, 15).until(
                        EC.presence_of_all_elements_located(
                            (By.CSS_SELECTOR, "ytd-video-renderer a#video-title, ytd-video-renderer a#video-title-link, a#video-title")
                        )
                    )
                    results = more_results
                except Exception:
                    pass

                # Check if video ID appeared after scrolling
                if target_video_id:
                    for idx, link in enumerate(results[:35]):
                        href = link.get_attribute("href") or ""
                        if target_video_id in href:
                            selected_link = link
                            emit(log_queue, f"[Step 4/6] Matched search result #{idx + 1} by Video ID/URL ('{target_video_id}') after scroll!")
                            break

                # If still not found by URL, check by Title
                if not selected_link:
                    target_title_lower = task.title.strip().lower()
                    for idx, link in enumerate(results[:35]):
                        title_text = (link.get_attribute("title") or link.text or "").strip().lower()
                        if target_title_lower and (target_title_lower in title_text or title_text in target_title_lower):
                            selected_link = link
                            emit(log_queue, f"[Step 4/6] Matched search result #{idx + 1} by Title ('{task.title}')!")
                            break

            if selected_link:
                # 5. Smooth Scroll & Organic Click
                emit(log_queue, "[Step 5/6] Smooth scrolling target video into view...")
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", selected_link)
                human_like_delay(1.5, 2.5)

                emit(log_queue, "[Step 5/6] Clicking target video link organically...")
                selected_link.click()
                human_like_delay(3.0, 5.0)
                organic_success = True
                emit(log_queue, "[Step 5/6] Successfully opened video from YouTube organic search results!")
                return True
            else:
                emit(log_queue, "[Step 4/6] Target video not found in search results by URL or Title.", logging.WARNING)

        except Exception as exc:
            emit(log_queue, f"Organic search attempt failed: {exc}", logging.WARNING)

    # 6. Direct Link Fallback
    if not organic_success:
        if direct_link_fallback and task.url:
            emit(log_queue, f"[Step 6/6] Direct Link Fallback: Navigating directly to URL: {task.url}")
            try:
                safe_get(driver, task.url, timeout=35, log_queue=log_queue)
                human_like_delay(3.0, 5.0)
                return True
            except Exception as exc:
                emit(log_queue, f"[Step 6/6] Direct link navigation failed: {exc}", logging.ERROR)
                return False
        else:
            emit(log_queue, "[Step 6/6] Direct link fallback disabled and organic search was unsuccessful.", logging.ERROR)
            return False

    return False
