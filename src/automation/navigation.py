import logging
import queue
import random
import time
from typing import Any, Dict, List, Optional, Sequence
import urllib.parse

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
    direct_link_fallback: bool = False,
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
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains
                        ActionChains(driver).move_to_element(selected_link).click().perform()
                    except Exception:
                        pass
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


def perform_youtube_search(
    driver: Any,
    query: str,
    log_queue: Optional[queue.Queue] = None,
) -> bool:
    """Execute a search on YouTube with human-like typing and submission.

    Works from any YouTube page (home, watch, or results). If already on YouTube,
    it interacts with the masthead search box. If not on YouTube, navigates to home first.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        emit(log_queue, "Search query is empty. Skipping search.", logging.WARNING)
        return False

    current_url = str(getattr(driver, "current_url", "") or "")
    if "youtube.com" not in current_url:
        emit(log_queue, "Navigating to YouTube home page for search...")
        safe_get(driver, "https://www.youtube.com", timeout=45, log_queue=log_queue)
        human_like_delay(2.0, 3.5)
        dismiss_youtube_dialogs(driver, log_queue=log_queue)

    emit(log_queue, f"Performing YouTube search for: '{clean_query}'...")
    try:
        search_box = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//textarea[@name='search_query'] | //input[@name='search_query'] | "
                    "//*[contains(@class, 'yt-searchbox-input')] | //input[@id='search'] | "
                    "//textarea[@id='search'] | //ytd-searchbox//input | //ytd-searchbox//textarea | "
                    "//form[contains(@class, 'SearchForm')]//textarea | //form[contains(@class, 'SearchForm')]//input | "
                    "//input[contains(@class, 'ytSearchboxComponentInput')]",
                )
            )
        )
        try:
            search_box.click()
        except Exception:
            driver.execute_script("arguments[0].focus();", search_box)

        # Clear existing text
        try:
            search_box.clear()
        except Exception:
            pass
        try:
            search_box.send_keys(Keys.CONTROL + "a")
            search_box.send_keys(Keys.BACKSPACE)
        except Exception:
            pass

        slow_type(search_box, clean_query)
        human_like_delay(0.5, 1.0)
        search_box.send_keys(Keys.ENTER)
        human_like_delay(2.5, 3.5)

        # Fallback to search button if ENTER didn't trigger URL transition
        current_url = str(getattr(driver, "current_url", "") or "")
        if "results" not in current_url:
            search_btns = driver.find_elements(
                By.XPATH,
                "//button[@id='search-icon-legacy' or @aria-label='Search' or contains(@class, 'yt-searchbox-button')] | "
                "//button[contains(@class, 'search-icon')] | //button[contains(@class, 'ytSearchboxComponentSearchButton')]",
            )
            if search_btns:
                try:
                    search_btns[0].click()
                except Exception:
                    driver.execute_script("arguments[0].click();", search_btns[0])
                human_like_delay(2.5, 3.5)

        dismiss_youtube_dialogs(driver, log_queue=log_queue)

        # Wait for search results container
        results_selector = (
            "ytd-video-renderer, ytd-item-section-renderer a#video-title, "
            "ytd-video-renderer a#video-title, a#video-title"
        )
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, results_selector))
        )
        emit(log_queue, f"Search completed for '{clean_query}'. Results loaded.")
        return True
    except Exception as exc:
        emit(log_queue, f"Search input interaction encountered issue: {exc}. Trying direct search URL fallback...", logging.WARNING)
        try:
            safe_get(driver, f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_query)}", timeout=40, log_queue=log_queue)
            dismiss_youtube_dialogs(driver, log_queue=log_queue)
            human_like_delay(2.0, 3.5)
            return True
        except Exception as fallback_exc:
            emit(log_queue, f"Search failed completely: {fallback_exc}", logging.ERROR)
            return False


def find_and_click_search_result(
    driver: Any,
    task: Optional[VideoTask] = None,
    target_channels: Sequence[str] = (),
    max_scrolls: int = 3,
    log_queue: Optional[queue.Queue] = None,
) -> Optional[Dict[str, str]]:
    """Scan YouTube search results for a video matching task (by ID or title) or target_channels.

    If found, smooth scrolls into view, clicks the video organically to open in the current tab,
    and returns metadata {'url': ..., 'title': ..., 'channel': ...}.
    Returns None if no matching video is found after max_scrolls.
    """
    target_video_id = extract_video_id(task.url) if task and task.url else None
    candidate_titles = task.get_title_queries() if task else []
    target_channels_clean = [ch.strip().lower().lstrip("@") for ch in target_channels if ch.strip()]
    if task and task.channel:
        c_clean = task.channel.strip().lower().lstrip("@")
        if c_clean and c_clean not in target_channels_clean:
            target_channels_clean.append(c_clean)

    emit(log_queue, "Scanning search results for matching video...")

    SCAN_RESULTS_JS = """
    var videoRenderers = Array.from(document.querySelectorAll('ytd-video-renderer'));
    return videoRenderers.map(function(r) {
        var titleElem = r.querySelector('a#video-title, a#video-title-link');
        var channelElem = r.querySelector('ytd-channel-name a, #channel-name a, a.yt-formatted-string[href*="/@"], a.yt-formatted-string[href*="/channel/"]');
        return {
            title: titleElem ? (titleElem.getAttribute('title') || titleElem.textContent || '').trim() : '',
            url: titleElem ? (titleElem.href || '') : '',
            channel: channelElem ? (channelElem.textContent || '').trim() : '',
            channelHref: channelElem ? (channelElem.href || '') : ''
        };
    });
    """

    for scroll_idx in range(max_scrolls + 1):
        try:
            results_data = driver.execute_script(SCAN_RESULTS_JS) or []
        except Exception:
            results_data = []

        matched_idx = -1
        match_reason = ""

        # Priority 1: Match by exact Video ID / URL
        if target_video_id:
            for idx, item in enumerate(results_data):
                if target_video_id in item.get("url", ""):
                    matched_idx = idx
                    match_reason = f"video ID '{target_video_id}'"
                    break

        # Priority 2: Match by candidate title (scoped to target channels if specified)
        if matched_idx < 0 and candidate_titles:
            for cand in candidate_titles:
                c_lower = cand.strip().lower()
                if not c_lower:
                    continue
                for idx, item in enumerate(results_data):
                    t_lower = item.get("title", "").lower()
                    if c_lower in t_lower or t_lower in c_lower:
                        if target_channels_clean:
                            ch_name = item.get("channel", "").lower()
                            ch_href = item.get("channelHref", "").lower()
                            if not any(tc in ch_name or tc in ch_href for tc in target_channels_clean):
                                continue
                        matched_idx = idx
                        match_reason = f"title '{cand}'"
                        break
                if matched_idx >= 0:
                    break

        # Priority 3: Match by target channel name or channel handle (only when searching directly for channel)
        if matched_idx < 0 and target_channels_clean and not candidate_titles and not target_video_id:
            for idx, item in enumerate(results_data):
                ch_name = item.get("channel", "").lower()
                ch_href = item.get("channelHref", "").lower()
                for target_ch in target_channels_clean:
                    if target_ch in ch_name or target_ch in ch_href:
                        matched_idx = idx
                        match_reason = f"target channel '{target_ch}'"
                        break
                if matched_idx >= 0:
                    break

        if matched_idx >= 0:
            matched_item = results_data[matched_idx]
            emit(log_queue, f"Matched search result #{matched_idx + 1} ({match_reason}): '{matched_item.get('title')}'")

            CLICK_RESULT_JS = """
            var renderers = document.querySelectorAll('ytd-video-renderer');
            var target = renderers[arguments[0]];
            if (!target) return false;
            var link = target.querySelector('a#video-title, a#video-title-link, a#thumbnail');
            if (link) {
                link.scrollIntoView({behavior: 'smooth', block: 'center'});
                return true;
            }
            return false;
            """
            try:
                driver.execute_script(CLICK_RESULT_JS, matched_idx)
            except Exception:
                pass
            human_like_delay(1.5, 2.5)

            CLICK_EXEC_JS = """
            var renderers = document.querySelectorAll('ytd-video-renderer');
            var target = renderers[arguments[0]];
            if (!target) return false;
            var link = target.querySelector('a#video-title, a#video-title-link, a#thumbnail');
            if (link) {
                link.click();
                return true;
            }
            return false;
            """
            try:
                driver.execute_script(CLICK_EXEC_JS, matched_idx)
            except Exception:
                pass

            human_like_delay(3.0, 5.0)
            dismiss_youtube_dialogs(driver, log_queue=log_queue)
            return matched_item

        if scroll_idx < max_scrolls:
            emit(log_queue, f"Video not found in visible results (scan {scroll_idx + 1}/{max_scrolls + 1}). Scrolling down...")
            try:
                driver.execute_script("window.scrollBy({top: 700, behavior: 'smooth'});")
            except Exception:
                pass
            human_like_delay(2.0, 3.0)

    emit(log_queue, "No matching video found in search results.", logging.WARNING)
    return None


def find_channel_video_on_home(
    driver: Any,
    target_channels: Sequence[str],
    max_scrolls: int = 4,
    log_queue: Optional[queue.Queue] = None,
) -> Optional[Dict[str, str]]:
    """Scan the YouTube home page feed for videos belonging to target channels.

    If found, smooth scrolls to the video, clicks it organically, and returns metadata.
    Returns None if no matching channel video is found.
    """
    clean_targets = [ch.strip().lower().lstrip("@") for ch in target_channels if ch.strip()]
    if not clean_targets:
        emit(log_queue, "No target channels specified to find on home page.", logging.INFO)
        return None

    current_url = str(getattr(driver, "current_url", "") or "")
    if "youtube.com" not in current_url or "/watch" in current_url:
        emit(log_queue, "Navigating to YouTube home page (https://www.youtube.com)...")
        safe_get(driver, "https://www.youtube.com", timeout=45, log_queue=log_queue)
        human_like_delay(2.5, 4.0)

    dismiss_youtube_dialogs(driver, log_queue=log_queue)
    emit(log_queue, f"Scanning YouTube Home feed for videos from target channel(s): {', '.join(target_channels)}...")

    SCAN_HOME_JS = """
    var cards = Array.from(document.querySelectorAll('ytd-rich-item-renderer, ytd-video-renderer'));
    return cards.map(function(c) {
        var titleElem = c.querySelector('a#video-title-link, a#video-title');
        var channelElem = c.querySelector('ytd-channel-name a, #channel-name a, a.yt-formatted-string[href*="/@"], a.yt-formatted-string[href*="/channel/"]');
        return {
            title: titleElem ? (titleElem.getAttribute('title') || titleElem.textContent || '').trim() : '',
            url: titleElem ? (titleElem.href || '') : '',
            channel: channelElem ? (channelElem.textContent || '').trim() : '',
            channelHref: channelElem ? (channelElem.href || '') : ''
        };
    });
    """

    for scroll_idx in range(max_scrolls + 1):
        try:
            cards = driver.execute_script(SCAN_HOME_JS) or []
        except Exception:
            cards = []

        for idx, card in enumerate(cards):
            url = card.get("url", "")
            if not url or "/watch?v=" not in url:
                continue
            ch_name = card.get("channel", "").lower()
            ch_href = card.get("channelHref", "").lower()
            for target_ch in clean_targets:
                if target_ch in ch_name or target_ch in ch_href:
                    matched_title = card.get("title", "")
                    emit(log_queue, f"Found home feed video from channel '{card.get('channel')}': '{matched_title}'!")

                    CLICK_HOME_JS = """
                    var cards = document.querySelectorAll('ytd-rich-item-renderer, ytd-video-renderer');
                    var target = cards[arguments[0]];
                    if (!target) return false;
                    var link = target.querySelector('a#video-title-link, a#video-title, a#thumbnail');
                    if (link) {
                        link.scrollIntoView({behavior: 'smooth', block: 'center'});
                        return true;
                    }
                    return false;
                    """
                    try:
                        driver.execute_script(CLICK_HOME_JS, idx)
                    except Exception:
                        pass
                    human_like_delay(1.5, 2.5)

                    try:
                        driver.execute_script("""
                        var cards = document.querySelectorAll('ytd-rich-item-renderer, ytd-video-renderer');
                        var target = cards[arguments[0]];
                        if (target) {
                            var link = target.querySelector('a#video-title-link, a#video-title, a#thumbnail');
                            if (link) link.click();
                        }
                        """, idx)
                    except Exception:
                        pass

                    human_like_delay(3.0, 5.0)
                    dismiss_youtube_dialogs(driver, log_queue=log_queue)
                    return card

        if scroll_idx < max_scrolls:
            emit(log_queue, f"No target channel videos in current view (scroll {scroll_idx + 1}/{max_scrolls + 1}). Scrolling feed...")
            try:
                driver.execute_script("window.scrollBy({top: 700, behavior: 'smooth'});")
            except Exception:
                pass
            human_like_delay(2.0, 3.0)

    emit(log_queue, "No target channel videos found on YouTube Home feed.", logging.INFO)
    return None


def find_and_click_suggested_video(
    driver: Any,
    target_channels: Sequence[str] = (),
    prefer_target_channels: bool = True,
    max_scrolls: int = 2,
    log_queue: Optional[queue.Queue] = None,
) -> Optional[Dict[str, str]]:
    """Find and click a video from the YouTube suggested / up-next section.

    If prefer_target_channels is True and a video from target_channels is suggested,
    prioritizes it; otherwise picks a random suggested video among top available candidates.
    Returns metadata dict or None if no suggested video could be found/clicked.
    """
    clean_targets = [ch.strip().lower().lstrip("@") for ch in target_channels if ch.strip()]
    emit(log_queue, "Scanning suggested section for next video...")

    SCAN_SUGGESTED_JS = """
    var items = Array.from(document.querySelectorAll(
        'ytd-compact-video-renderer, ytd-watch-next-secondary-results-renderer ytd-compact-video-renderer, #related ytd-compact-video-renderer, ytd-item-section-renderer ytd-compact-video-renderer'
    ));
    return items.map(function(item) {
        var titleElem = item.querySelector('span#video-title, a#video-title, #video-title');
        var linkElem = item.querySelector('a#thumbnail, a.yt-simple-endpoint');
        var channelElem = item.querySelector('ytd-channel-name #text, #channel-name, a.yt-formatted-string');
        return {
            title: titleElem ? (titleElem.getAttribute('title') || titleElem.textContent || '').trim() : '',
            url: linkElem ? (linkElem.href || '') : '',
            channel: channelElem ? (channelElem.textContent || '').trim() : '',
            channelHref: channelElem ? (channelElem.href || '') : ''
        };
    }).filter(function(i) {
        return i.url && i.url.indexOf('/watch?v=') !== -1;
    });
    """

    for scroll_idx in range(max_scrolls + 1):
        try:
            suggested_list = driver.execute_script(SCAN_SUGGESTED_JS) or []
        except Exception:
            suggested_list = []

        if suggested_list:
            chosen_idx = -1
            chosen_reason = ""

            # Check if any suggested video matches target channels
            if prefer_target_channels and clean_targets:
                for idx, s in enumerate(suggested_list[:15]):
                    ch_name = s.get("channel", "").lower()
                    ch_href = s.get("channelHref", "").lower()
                    for target_ch in clean_targets:
                        if target_ch in ch_name or target_ch in ch_href:
                            chosen_idx = idx
                            chosen_reason = f"matched target channel '{target_ch}'"
                            break
                    if chosen_idx >= 0:
                        break

            # If no target channel video, select a random candidate among top valid suggestions
            if chosen_idx < 0:
                pool = min(len(suggested_list), 8)
                chosen_idx = random.randint(0, pool - 1)
                chosen_reason = "random selection"

            chosen_item = suggested_list[chosen_idx]
            emit(log_queue, f"Selected suggested video ({chosen_reason}): '{chosen_item.get('title')}' ({chosen_item.get('channel')})")

            CLICK_SUGGESTED_JS = """
            var items = Array.from(document.querySelectorAll(
                'ytd-compact-video-renderer, ytd-watch-next-secondary-results-renderer ytd-compact-video-renderer, #related ytd-compact-video-renderer, ytd-item-section-renderer ytd-compact-video-renderer'
            )).filter(function(item) {
                var linkElem = item.querySelector('a#thumbnail, a.yt-simple-endpoint');
                return linkElem && linkElem.href && linkElem.href.indexOf('/watch?v=') !== -1;
            });
            var target = items[arguments[0]];
            if (!target) return false;
            var link = target.querySelector('a#thumbnail, a.yt-simple-endpoint, span#video-title');
            if (link) {
                link.scrollIntoView({behavior: 'smooth', block: 'center'});
                return true;
            }
            return false;
            """
            try:
                driver.execute_script(CLICK_SUGGESTED_JS, chosen_idx)
            except Exception:
                pass
            human_like_delay(1.5, 2.5)

            try:
                driver.execute_script("""
                var items = Array.from(document.querySelectorAll(
                    'ytd-compact-video-renderer, ytd-watch-next-secondary-results-renderer ytd-compact-video-renderer, #related ytd-compact-video-renderer, ytd-item-section-renderer ytd-compact-video-renderer'
                )).filter(function(item) {
                    var linkElem = item.querySelector('a#thumbnail, a.yt-simple-endpoint');
                    return linkElem && linkElem.href && linkElem.href.indexOf('/watch?v=') !== -1;
                });
                var target = items[arguments[0]];
                if (target) {
                    var link = target.querySelector('a#thumbnail, a.yt-simple-endpoint, span#video-title');
                    if (link) link.click();
                }
                """, chosen_idx)
            except Exception:
                pass

            human_like_delay(3.0, 5.0)
            dismiss_youtube_dialogs(driver, log_queue=log_queue)
            return chosen_item

        # Scroll secondary column / page slightly down to trigger suggested load
        if scroll_idx < max_scrolls:
            try:
                driver.execute_script("window.scrollBy({top: 400, behavior: 'smooth'});")
            except Exception:
                pass
            human_like_delay(2.0, 3.0)

    emit(log_queue, "No suggested videos found or clickable in the suggested section.", logging.WARNING)
    return None
