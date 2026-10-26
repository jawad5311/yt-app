import time
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


def dismiss_youtube_dialogs(driver: Any, log_queue: Optional[queue.Queue] = None) -> None:
    """Dismiss YouTube consent, sign-in promo, or 'Still watching?' / modal dialogs."""
    try:
        # 1. Cookie / consent buttons
        consent_buttons = driver.find_elements(
            By.XPATH,
            "//button[contains(., 'Accept all') or contains(., 'I agree') or contains(., 'Accept the use') or contains(., 'Reject all')]",
        )
        for btn in consent_buttons:
            if btn.is_displayed():
                btn.click()
                emit(log_queue, "Dismissed YouTube consent banner.")
                break
    except Exception:
        pass

    try:
        # 2. 'No thanks' / 'Dismiss' promos (e.g. YouTube Premium promo, sign-in promo)
        dismiss_buttons = driver.find_elements(
            By.XPATH,
            "//ytd-button-renderer[contains(@id, 'dismiss-button')]//button | //button[normalize-space()='No thanks' or normalize-space()='Not now' or normalize-space()='Dismiss']",
        )
        for btn in dismiss_buttons:
            if btn.is_displayed():
                btn.click()
                emit(log_queue, "Dismissed YouTube promotional dialog.")
                break
    except Exception:
        pass

    try:
        # 3. 'Still watching? / Video paused' prompt
        confirm_buttons = driver.find_elements(
            By.XPATH,
            "//yt-confirm-dialog-renderer//button[contains(., 'Yes') or contains(., 'Continue')]",
        )
        for btn in confirm_buttons:
            if btn.is_displayed():
                btn.click()
                emit(log_queue, "Dismissed 'Still watching?' prompt.")
                break
    except Exception:
        pass


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
    start_url = str(getattr(driver, "current_url", "") or "")
    start_video_id = extract_video_id(start_url)

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

            # Dismiss any consent dialogs or overlays
            dismiss_youtube_dialogs(driver, log_queue=log_queue)

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
            try:
                search_box.click()
            except Exception:
                driver.execute_script("arguments[0].focus();", search_box)

            # Clear search box reliably across browsers
            try:
                search_box.clear()
            except Exception:
                pass
            try:
                search_box.send_keys(Keys.CONTROL + "a")
                search_box.send_keys(Keys.BACKSPACE)
            except Exception:
                pass

            emit(log_queue, f"[Step 3/6] Typing search query '{search_query}' character-by-character with human micro-delays...")
            slow_type(search_box, search_query)
            human_like_delay(0.5, 1.0)

            emit(log_queue, "[Step 3/6] Submitting search query via ENTER key...")
            search_box.send_keys(Keys.ENTER)
            human_like_delay(2.5, 3.5)

            # Check if ENTER triggered navigation; if not, click the search button
            current_url = str(getattr(driver, "current_url", "") or "")
            if "results" not in current_url:
                search_btns = driver.find_elements(
                    By.XPATH,
                    "//button[@id='search-icon-legacy' or @aria-label='Search' or contains(@class, 'yt-searchbox-button')] | //button[contains(@class, 'search-icon')]",
                )
                if search_btns:
                    try:
                        search_btns[0].click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", search_btns[0])
                    human_like_delay(2.5, 3.5)

            # Dismiss any popup/dialog that might have appeared after searching
            dismiss_youtube_dialogs(driver, log_queue=log_queue)

            # 4. Search Result Matching
            emit(log_queue, "[Step 4/6] Waiting for video search results to load...")
            results_selector = (
                "ytd-video-renderer a#video-title, "
                "ytd-video-renderer a#video-title-link, "
                "ytd-item-section-renderer a#video-title, "
                "a#video-title"
            )
            results = WebDriverWait(driver, 35).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, results_selector))
            )

            emit(log_queue, f"[Step 4/6] Found {len(results)} search results. Scanning top results for matching Video URL/ID...")

            selected_link = None

            # Pass 1: Match by parsed Video ID ONLY (compare parsed IDs, not substrings)
            if target_video_id:
                for idx, link in enumerate(results[:25]):
                    try:
                        href = link.get_attribute("href") or ""
                        link_vid_id = extract_video_id(href)
                        if link_vid_id and link_vid_id == target_video_id:
                            selected_link = link
                            emit(log_queue, f"[Step 4/6] Matched search result #{idx + 1} by Video ID ('{target_video_id}')!")
                            break
                    except Exception:
                        continue

            # Pass 2: If not found in top results, scroll down and check more results
            candidate_titles = task.get_title_queries()
            if not selected_link:
                if target_video_id:
                    emit(
                        log_queue,
                        "[Step 4/6] Video not found by ID in initial scan. Scrolling down and checking for match...",
                        logging.INFO,
                    )
                    try:
                        driver.execute_script("window.scrollBy(0, 600);")
                    except Exception:
                        pass
                    human_like_delay(2.0, 3.0)

                    try:
                        more_results = WebDriverWait(driver, 15).until(
                            EC.presence_of_all_elements_located((By.CSS_SELECTOR, results_selector))
                        )
                        results = more_results
                    except Exception:
                        pass

                    for idx, link in enumerate(results[:35]):
                        try:
                            href = link.get_attribute("href") or ""
                            link_vid_id = extract_video_id(href)
                            if link_vid_id and link_vid_id == target_video_id:
                                selected_link = link
                                emit(log_queue, f"[Step 4/6] Matched search result #{idx + 1} by Video ID ('{target_video_id}') after scroll!")
                                break
                        except Exception:
                            continue

                # Pass 2b: ONLY for tasks without an explicit video ID, match by candidate titles
                elif candidate_titles:
                    for cand_title in candidate_titles:
                        target_title_lower = cand_title.strip().lower()
                        if not target_title_lower:
                            continue
                        for idx, link in enumerate(results[:35]):
                            try:
                                title_text = (link.get_attribute("title") or link.text or "").strip().lower()
                                if not title_text:
                                    continue  # Reject empty result titles
                                if target_title_lower in title_text or (len(title_text) >= 5 and title_text in target_title_lower):
                                    selected_link = link
                                    emit(log_queue, f"[Step 4/6] Matched search result #{idx + 1} by Title ('{cand_title}')!")
                                    break
                            except Exception:
                                continue
                        if selected_link:
                            break

            if selected_link:
                # 5. Smooth Scroll & Organic Click
                emit(log_queue, "[Step 5/6] Smooth scrolling target video into view...")
                try:
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", selected_link)
                except Exception:
                    pass
                human_like_delay(1.5, 2.5)

                emit(log_queue, "[Step 5/6] Clicking target video link organically...")
                try:
                    selected_link.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", selected_link)
                human_like_delay(2.0, 3.5)

                # Confirm navigation actually occurred and identifies the selected video
                start_confirm = time.time()
                confirmed = False
                while time.time() - start_confirm < 10.0:
                    cur_url = str(getattr(driver, "current_url", "") or "")
                    cur_id = extract_video_id(cur_url)
                    if target_video_id:
                        if cur_id == target_video_id:
                            confirmed = True
                            break
                    else:
                        if cur_id and cur_url != start_url and "/watch" in cur_url:
                            confirmed = True
                            break
                    time.sleep(0.5)

                final_url = str(getattr(driver, "current_url", "") or "")
                final_id = extract_video_id(final_url)

                if target_video_id:
                    if final_id == target_video_id:
                        emit(log_queue, f"[Step 5/6] Successfully verified opened video ID: '{target_video_id}'.")
                        return True
                    else:
                        emit(
                            log_queue,
                            f"[Step 5/6] Navigation verification failed: expected video ID '{target_video_id}', observed '{final_id or final_url}'.",
                            logging.WARNING,
                        )
                        organic_success = False
                else:
                    if confirmed and final_id:
                        emit(log_queue, f"[Step 5/6] Successfully opened video for title task (verified video ID: '{final_id}').")
                        return True
                    else:
                        emit(
                            log_queue,
                            f"[Step 5/6] Navigation verification failed for title task (current URL: '{final_url}').",
                            logging.WARNING,
                        )
                        organic_success = False
            else:
                emit(log_queue, "[Step 4/6] Target video not found in search results by URL or Title.", logging.WARNING)

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}".strip()
            if err_msg.endswith(":"):
                err_msg = type(exc).__name__
            emit(log_queue, f"Organic search attempt failed: {err_msg}", logging.WARNING)

    # 6. Direct Link Fallback
    if not organic_success:
        if direct_link_fallback and task.url:
            emit(log_queue, f"[Step 6/6] Direct Link Fallback: Navigating directly to URL: {task.url}")
            try:
                safe_get(driver, task.url, timeout=35, log_queue=log_queue)
                human_like_delay(2.0, 3.5)

                # Confirm direct navigation arrived at expected video ID
                cur_url = str(getattr(driver, "current_url", "") or "")
                cur_id = extract_video_id(cur_url)
                if target_video_id:
                    if cur_id == target_video_id:
                        emit(log_queue, f"[Step 6/6] Successfully verified direct navigation to video ID '{target_video_id}'.")
                        return True
                    else:
                        emit(
                            log_queue,
                            f"[Step 6/6] Direct navigation verification failed: expected video ID '{target_video_id}', observed '{cur_id or cur_url}'.",
                            logging.ERROR,
                        )
                        return False
                else:
                    if cur_id and "/watch" in cur_url:
                        return True
                    emit(log_queue, f"[Step 6/6] Direct navigation verification failed for URL: '{cur_url}'.", logging.ERROR)
                    return False
            except Exception as exc:
                emit(log_queue, f"[Step 6/6] Direct link navigation failed: {exc}", logging.ERROR)
                return False
        else:
            emit(log_queue, "[Step 6/6] Direct link fallback disabled and organic search was unsuccessful.", logging.ERROR)
            return False

    return False
