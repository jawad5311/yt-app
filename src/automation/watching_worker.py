"""Watching Worker.

Manages autonomous video discovery and continuous chained playback:
1. Google sign-in.
2. Home page feed inspection: scans ~20 videos and matches against stored channels.
3. Clicks the first matching video and watches it naturally with ad skipping and human interactivity.
4. When the video ends / approaches end, inspects recommended videos for stored channels.
5. Clicks the matching recommended video and continues watching until stopped.
"""

import difflib
import logging
import queue
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, canonical_channel_url
from ..models.video_task import VideoTask
from ..models.watch_stats import TaskOutcome, resolve_video_key
from ..utils.helpers import extract_video_id, human_like_delay
from ..utils.logger import emit
from .driver import close_driver, setup_driver
from .login import perform_google_login
from .navigation import dismiss_youtube_dialogs, safe_get
from .youtube_actions import (
    DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS,
    click_element,
    ensure_video_playing,
    watch_video,
)

HOME_VIDEOS_JS = r"""
return (function() {
    var results = [];
    var seenUrls = {};

    var cards = document.querySelectorAll(
        'ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-grid-media'
    );

    function parseCard(card, idx) {
        if (!card) return null;
        // Explicitly skip Shorts shelves, reels, and slim-media shorts lockups
        if (
            card.closest('ytd-rich-shelf-renderer[is-shorts], ytd-rich-shelf-renderer, ytd-reel-shelf-renderer, ytd-rich-section-renderer') ||
            card.querySelector('ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, a[href*="/shorts/"]')
        ) {
            return null;
        }

        var linkEl = card.querySelector('a#video-title-link, a#thumbnail, a[href*="/watch?v="], a[href*="watch?"]');
        if (!linkEl && card.tagName === 'A' && card.getAttribute('href')) {
            linkEl = card;
        }
        var href = linkEl ? (linkEl.getAttribute('href') || '') : '';
        if (!href || (!href.includes('/watch') && !href.includes('v='))) {
            return null;
        }

        var cleanUrl = href.split('&')[0];
        if (seenUrls[cleanUrl]) return null;

        var titleEl = card.querySelector('#video-title, a#video-title-link, h3, #title');
        var title = '';
        if (titleEl) {
            title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
        }
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }

        // 1. Channel link & name discovery (supports modern yt-lockup-view-model and legacy ytd-channel-name)
        var chLink = card.querySelector(
            'a.ytAttributedStringLink[href*="/@"], a.ytAttributedStringLink[href*="/channel/"], yt-content-metadata-view-model a[href*="/@"], yt-content-metadata-view-model a[href*="/channel/"], ytd-channel-name a, #channel-name a, a[href*="/@"], a[href*="/channel/"], a[href*="/c/"], a[href*="/user/"]'
        );
        var channelUrl = chLink ? (chLink.getAttribute('href') || '') : '';
        var channelName = chLink ? (chLink.textContent || '').trim() : '';

        if (!channelName) {
            var channelEl = card.querySelector(
                'ytd-channel-name #text a, ytd-channel-name a, #channel-name a, ytd-channel-name yt-formatted-string, ytd-channel-name, #channel-name, ytd-video-meta-block ytd-channel-name, yt-content-metadata-view-model .ytContentMetadataViewModelMetadataRow'
            );
            if (channelEl) channelName = (channelEl.textContent || '').trim();
        }

        if (!channelName && linkEl) {
            var aria = linkEl.getAttribute('aria-label') || '';
            var match = aria.match(/\\bby\\s+([^0-9\\n\\r]+?)(?:\\s+\\d+|\\s+ago|\\s+views|\\s+Streamed|$)/i);
            if (match && match[1]) {
                channelName = match[1].trim();
            }
        }

        seenUrls[cleanUrl] = true;
        return {
            index: idx,
            title: title,
            href: href,
            channel_name: channelName,
            channel_url: channelUrl
        };
    }

    for (var i = 0; i < cards.length; i++) {
        var parsed = parseCard(cards[i], i);
        if (parsed) {
            results.push(parsed);
        }
    }

    if (results.length === 0) {
        var links = document.querySelectorAll('a[href*="/watch?v="]');
        for (var j = 0; j < links.length; j++) {
            var a = links[j];
            var hrefVal = a.getAttribute('href') || '';
            var cUrl = hrefVal.split('&')[0];
            if (seenUrls[cUrl]) continue;

            var parentCard = a.closest('ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-grid-media, div#dismissible');
            var parsedFallback = parseCard(parentCard, j);
            if (parsedFallback) {
                results.push(parsedFallback);
            } else {
                var t = (a.getAttribute('title') || a.textContent || '').trim();
                var chName = '';
                var ariaText = a.getAttribute('aria-label') || '';
                var m = ariaText.match(/\\bby\\s+([^0-9\\n\\r]+?)(?:\\s+\\d+|\\s+ago|\\s+views|$)/i);
                if (m && m[1]) chName = m[1].trim();
                seenUrls[cUrl] = true;
                results.push({
                    index: j,
                    title: t,
                    href: hrefVal,
                    channel_name: chName,
                    channel_url: ''
                });
            }
        }
    }

    return results;
})();
"""

RECOMMENDED_VIDEOS_JS = r"""
return (function() {
    var results = [];
    var seenUrls = {};

    var cards = document.querySelectorAll(
        'yt-lockup-view-model, ytd-compact-video-renderer, #related ytd-video-renderer, ytd-watch-next-secondary-results-renderer ytd-compact-video-renderer, ytd-compact-radio-renderer'
    );

    function parseRecCard(card, idx) {
        if (!card) return null;
        // Explicitly skip Shorts shelves and lockups
        if (
            card.closest('ytd-reel-shelf-renderer, ytd-rich-shelf-renderer[is-shorts], ytd-rich-section-renderer') ||
            card.querySelector('ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, a[href*="/shorts/"]')
        ) {
            return null;
        }

        var linkEl = card.querySelector('a.ytLockupViewModelContentImage, a.ytLockupMetadataViewModelTitle, a#thumbnail, a.yt-simple-endpoint, a#video-title-link, a[href*="/watch?v="], a[href*="watch?"]');
        if (!linkEl && card.tagName === 'A') linkEl = card;
        var href = linkEl ? (linkEl.getAttribute('href') || '') : '';
        if (!href || (!href.includes('/watch') && !href.includes('v='))) {
            return null;
        }

        var cleanUrl = href.split('&')[0];
        if (seenUrls[cleanUrl]) return null;

        var titleEl = card.querySelector('h3.ytLockupMetadataViewModelHeadingReset, a.ytLockupMetadataViewModelTitle, #video-title, span#video-title, #title');
        var title = titleEl ? (titleEl.getAttribute('title') || titleEl.getAttribute('aria-label') || titleEl.textContent || '').trim() : '';
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }

        // Channel name discovery: supports modern yt-lockup-view-model metadata rows and avatar labels
        var channelName = '';
        var rowSpan = card.querySelector('yt-content-metadata-view-model .ytContentMetadataViewModelMetadataRow span.ytAttributedStringHost, yt-content-metadata-view-model .ytContentMetadataViewModelMetadataRow');
        if (rowSpan) {
            channelName = (rowSpan.textContent || '').trim();
        }

        if (!channelName) {
            var avatarEl = card.querySelector('yt-avatar-shape [aria-label*="Go to channel"], [aria-label*="Go to channel"]');
            if (avatarEl) {
                var aria = avatarEl.getAttribute('aria-label') || '';
                if (aria.indexOf('Go to channel ') !== -1) {
                    channelName = aria.replace('Go to channel ', '').trim();
                }
            }
        }

        if (!channelName) {
            var channelEl = card.querySelector(
                'ytd-channel-name #text, ytd-channel-name yt-formatted-string, ytd-channel-name a, #channel-name, ytd-video-meta-block ytd-channel-name'
            );
            if (channelEl) channelName = (channelEl.textContent || '').trim();
        }

        if (!channelName && linkEl) {
            var aria = linkEl.getAttribute('aria-label') || '';
            var match = aria.match(/\bby\s+([^0-9\n\r]+?)(?:\s+\d+|\s+ago|\s+views|\s+Streamed|$)/i);
            if (match && match[1]) {
                channelName = match[1].trim();
            }
        }

        // Channel URL extraction
        var chLink = card.querySelector(
            'a.ytAttributedStringLink[href*="/@"], a.ytAttributedStringLink[href*="/channel/"], yt-content-metadata-view-model a[href*="/@"], yt-content-metadata-view-model a[href*="/channel/"], ytd-channel-name a, #channel-name a, a[href*="/@"], a[href*="/channel/"]'
        );
        var channelUrl = chLink ? (chLink.getAttribute('href') || '') : '';

        // Duration text extraction
        var durationBadge = card.querySelector('badge-shape .ytBadgeShapeText, yt-thumbnail-badge-view-model .ytBadgeShapeText, #overlays ytd-thumbnail-overlay-time-status-renderer, span.ytd-thumbnail-overlay-time-status-renderer');
        var durationText = durationBadge ? (durationBadge.textContent || '').trim() : '';

        seenUrls[cleanUrl] = true;
        return {
            index: idx,
            title: title,
            href: href,
            channel_name: channelName,
            channel_url: channelUrl,
            duration_text: durationText
        };
    }

    for (var i = 0; i < cards.length; i++) {
        var parsed = parseRecCard(cards[i], i);
        if (parsed) results.push(parsed);
    }

    if (results.length === 0) {
        var relContainer = document.querySelector('#related, ytd-watch-next-secondary-results-renderer, #contents.ytd-item-section-renderer');
        var links = (relContainer || document).querySelectorAll('a[href*="/watch?v="]');
        for (var j = 0; j < links.length; j++) {
            var a = links[j];
            var h = a.getAttribute('href') || '';
            var cUrl = h.split('&')[0];
            if (seenUrls[cUrl]) continue;
            var parent = a.closest('yt-lockup-view-model, ytd-compact-video-renderer, ytd-video-renderer, div#dismissible');
            var p = parseRecCard(parent, j);
            if (p) {
                results.push(p);
            }
        }
    }

    return results;
})();
"""


def is_clean_youtube_home_url(url: str) -> bool:
    """Check if URL is strictly the clean YouTube Home page (no extra path, channel, or query parameters)."""
    if not url:
        return False
    u = url.strip().rstrip("/")
    return u.lower() in (
        "https://www.youtube.com",
        "http://www.youtube.com",
        "https://youtube.com",
        "http://youtube.com",
    )


def normalize_channel_text(text: str) -> str:
    """Normalize text for channel matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def match_channel(
    scanned_name: str,
    scanned_url: str,
    channels: Sequence[Channel],
    fuzzy_threshold: float = 0.80,
) -> Optional[Channel]:
    """Check if scanned video's channel matches any channel stored in the app.

    Supports:
    - Exact case-insensitive matching.
    - Normalized alphanumeric matching.
    - Substring / handle containment.
    - Canonical URL & handle matching.
    - Fuzzy similarity matching for minor misspellings/badges.
    """
    clean_scanned_name = (scanned_name or "").strip()
    norm_scanned_name = normalize_channel_text(clean_scanned_name)
    canon_scanned_url = canonical_channel_url(scanned_url)

    for ch in channels:
        if ch.archived or not ch.enabled:
            continue

        clean_ch_name = (ch.name or "").strip()
        if not clean_ch_name and not ch.url:
            continue

        # 1. Exact case-insensitive match
        if clean_scanned_name and clean_ch_name:
            if clean_scanned_name.lower() == clean_ch_name.lower():
                return ch

        # 2. Canonical URL / handle match
        if canon_scanned_url and ch.url:
            canon_ch_url = canonical_channel_url(ch.url)
            if canon_ch_url and (
                canon_ch_url == canon_scanned_url
                or canon_ch_url in canon_scanned_url
                or canon_scanned_url in canon_ch_url
            ):
                return ch

        # 3. Normalized alphanumeric match
        norm_ch_name = normalize_channel_text(clean_ch_name)
        if norm_scanned_name and norm_ch_name:
            if norm_scanned_name == norm_ch_name:
                return ch
            # If one is entirely contained in the other and has meaningful length (>= 4 chars)
            if len(norm_ch_name) >= 4 and norm_ch_name in norm_scanned_name:
                return ch
            if len(norm_scanned_name) >= 4 and norm_scanned_name in norm_ch_name:
                return ch

            # 4. Fuzzy similarity matching
            if len(norm_scanned_name) >= 4 and len(norm_ch_name) >= 4:
                ratio = difflib.SequenceMatcher(None, norm_scanned_name, norm_ch_name).ratio()
                if ratio >= fuzzy_threshold:
                    return ch

    return None


class WatchingWorker:
    """Worker managing the 'Start Watching' autonomous recommendation loop."""

    def __init__(
        self,
        account: Account,
        channels: List[Channel],
        config_mgr: ConfigManager,
        log_queue: Optional[queue.Queue] = None,
        stop_event: Optional[threading.Event] = None,
        event_queue: Optional[queue.Queue] = None,
        stats_store: Optional[StatsStore] = None,
        pause_event: Optional[threading.Event] = None,
        total_loops: int = 1,
        open_videos: bool = True,
    ):
        self.account = account
        self.channels = channels
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self.event_queue = event_queue
        self.stats_store = stats_store
        self.total_loops = max(1, total_loops)
        self.open_videos = open_videos
        self.driver: Any = None
        self.skip_ads = True
        self.max_ad_seconds = 32

    def check_pause(self) -> None:
        """Pause worker execution if pause_event is set until resumed or stopped."""
        if self.pause_event.is_set():
            emit(self.log_queue, "Browsing session paused by user. Pausing player and waiting to resume...")
            try:
                if self.driver:
                    self.driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
            except Exception:
                pass
            self._report_status("Paused", "info")
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)
            if not self.stop_event.is_set():
                emit(self.log_queue, "Browsing session resumed.")
                self._report_status("Watching", "info")
                try:
                    if self.driver:
                        ensure_video_playing(self.driver, log_queue=self.log_queue)
                except Exception:
                    pass

    def _emit_event(self, event: Tuple[Any, ...]) -> None:
        if self.event_queue is None:
            return
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

    def _report_status(self, message: str, level: str = "info") -> None:
        self._emit_event(("status", message, level))

    def _full_video_timeout_seconds(self) -> int:
        minutes = self.config_mgr.get_int("FullVideoTimeoutMinutes", 5)
        if minutes < 0:
            return DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS
        return minutes * 60

    def _ad_settings(self) -> Tuple[bool, int]:
        skip_ads = self.config_mgr.get_bool("SkipAds", True) or self.config_mgr.get_bool("BlockAds", False)
        max_ad_seconds = self.config_mgr.get_int("MaxAdWatchSeconds", 32)
        if max_ad_seconds < 0:
            max_ad_seconds = 0
        return skip_ads, max_ad_seconds

    def _end_buffer_seconds(self) -> float:
        buffer_val = self.config_mgr.get_int("AutoplayEndBufferSeconds", 10)
        return float(max(0, buffer_val))

    def _interactivity_settings(self) -> Tuple[bool, Dict[str, Any]]:
        enabled = self.config_mgr.get_bool("EnableHumanInteractivity", True)
        config: Dict[str, Any] = {
            "scroll_comments": self.config_mgr.get_bool("RandomScrolling", True),
            "variable_speed": False,
            "toggle_captions": self.config_mgr.get_bool("ToggleCaptions", True),
            "expand_description": self.config_mgr.get_bool("ExpandDescription", True),
            "like_comments": self.config_mgr.get_bool("LikeComments", True),
            "like_video": self.config_mgr.get_bool("LikeVideo", True),
            "adjust_quality": self.config_mgr.get_bool("AdjustQuality", True),
            "quality_level": self.config_mgr.get_setting("QualityLevel", "360p"),
            "micro_pauses": self.config_mgr.get_bool("RandomMicroPauses", True),
        }
        return enabled, config

    def get_target_channels(self) -> List[Channel]:
        """Get fresh enabled target channels list from settings.json. Only returns channels that are checked/enabled and not archived."""
        try:
            stored_dicts = self.config_mgr.get_channels()
            if stored_dicts:
                stored_objs = [Channel.from_dict(c) for c in stored_dicts]
                return [c for c in stored_objs if c.name and c.enabled and not c.archived]
        except Exception:
            pass
        return [c for c in self.channels if c.name and c.enabled and not c.archived]

    def _watchtime_duration_seconds(self) -> Optional[int]:
        """Read watch duration budget from settings. Returns None for Full Video."""
        setting = self.config_mgr.get_setting("WatchtimeDuration", "Full Video")
        task = VideoTask(watchtime_duration=setting)
        return task.parse_duration_seconds()

    def fetch_home_videos(self, target_count: int = 20, target_channels: Optional[Sequence[Channel]] = None) -> List[Dict[str, Any]]:
        """Wait for YouTube Home videos to load, then smoothly scroll down and extract video cards."""
        try:
            current_url = getattr(self.driver, "current_url", "")
            if not is_clean_youtube_home_url(current_url):
                safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                human_like_delay(2.0, 3.5)

            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

            # Wait for YouTube Home video items to be present in DOM
            emit(self.log_queue, "Waiting for YouTube Home videos to load...")
            try:
                WebDriverWait(self.driver, 15).until(
                    lambda d: len(
                        d.find_elements(
                            By.CSS_SELECTOR,
                            "ytd-rich-item-renderer, ytd-rich-grid-media, ytd-video-renderer, a[href*='/watch?v=']",
                        )
                    )
                    > 0
                )
            except Exception:
                pass

            # Initial pause to let thumbnail and metadata render
            time.sleep(1.5)

            # Smoothly scroll down the feed until a matching channel video is found (or target count reached)
            emit(self.log_queue, "Smooth scrolling down YouTube Home feed to find a matching target video...")
            max_scrolls = 20 if target_channels else 5
            for scroll_attempt in range(max_scrolls):
                if self.stop_event.is_set():
                    return []
                self.check_pause()
                if self.stop_event.is_set():
                    return []

                raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
                if isinstance(raw_items, list) and raw_items:
                    # If target channels are provided, check if we found a match right now
                    if target_channels:
                        for itm in raw_items:
                            m = match_channel(itm.get("channel_name", ""), itm.get("channel_url", ""), target_channels)
                            if m:
                                return raw_items

                    if len(raw_items) >= target_count and not target_channels:
                        return raw_items[:target_count]

                # Perform a smooth scroll down
                scroll_step = random.randint(500, 800)
                try:
                    self.driver.execute_script(f"window.scrollBy({{top: {scroll_step}, behavior: 'smooth'}});")
                except Exception:
                    self.driver.execute_script(f"window.scrollBy(0, {scroll_step});")

                # Natural delay between smooth scrolls to let next batch load
                sleep_time = random.uniform(1.3, 2.2)
                end_t = time.time() + sleep_time
                while time.time() < end_t:
                    if self.stop_event.is_set():
                        return []
                    time.sleep(0.1)

            raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
            return raw_items if isinstance(raw_items, list) else []
        except Exception as exc:
            emit(self.log_queue, f"Error fetching home page videos: {exc}", logging.WARNING)
            return []

    def fetch_recommended_videos(self) -> List[Dict[str, Any]]:
        """Fetch recommended videos on the watch page sidebar/feed."""
        try:
            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

            for scroll_attempt in range(3):
                if self.stop_event.is_set():
                    return []
                raw_items = self.driver.execute_script(RECOMMENDED_VIDEOS_JS)
                if isinstance(raw_items, list) and len(raw_items) >= 10:
                    return raw_items

                # Smoothly scroll down the page slightly to populate recommended items
                self.driver.execute_script("window.scrollBy({top: 600, behavior: 'smooth'});")
                time.sleep(1.8)

            raw_items = self.driver.execute_script(RECOMMENDED_VIDEOS_JS)
            return raw_items if isinstance(raw_items, list) else []
        except Exception as exc:
            emit(self.log_queue, f"Error fetching recommended videos: {exc}", logging.WARNING)
            return []

    def click_home_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on the target video card on the home page and verify navigation."""
        href = card_info.get("href", "")
        vid_id = extract_video_id(href) or (href.split("v=")[-1].split("&")[0] if "v=" in href else "")
        try:
            idx = int(card_info.get("index", 0))
            items = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ytd-rich-item-renderer, ytd-rich-grid-media, ytd-video-renderer, ytd-grid-video-renderer",
            )
            if 0 <= idx < len(items):
                item = items[idx]
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", item
                    )
                    human_like_delay(0.8, 1.5)
                except Exception:
                    pass

                clickable = None
                for selector in (
                    "a#video-title-link",
                    "a#thumbnail",
                    "#video-title",
                    "a.yt-simple-endpoint",
                    "a[href*='/watch']",
                ):
                    els = item.find_elements(By.CSS_SELECTOR, selector)
                    if els and els[0].is_displayed():
                        clickable = els[0]
                        break

                target = clickable or item
                try:
                    ActionChains(self.driver).move_to_element(target).pause(0.2).click().perform()
                except Exception:
                    try:
                        target.click()
                    except Exception:
                        click_element(self.driver, target)

                for _ in range(8):
                    cur = getattr(self.driver, "current_url", "") or ""
                    if (vid_id and vid_id in cur) or ("/watch" in cur):
                        return True
                    time.sleep(0.3)

            # If index click did not trigger navigation, match by URL
            if href and vid_id:
                match_links = self.driver.find_elements(
                    By.XPATH, f"//a[contains(@href, '{vid_id}')]"
                )
                for lk in match_links:
                    if lk.is_displayed():
                        try:
                            self.driver.execute_script(
                                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", lk
                            )
                            human_like_delay(0.5, 1.0)
                        except Exception:
                            pass
                        try:
                            ActionChains(self.driver).move_to_element(lk).pause(0.2).click().perform()
                        except Exception:
                            try:
                                lk.click()
                            except Exception:
                                click_element(self.driver, lk)

                        for _ in range(8):
                            cur = getattr(self.driver, "current_url", "") or ""
                            if (vid_id and vid_id in cur) or ("/watch" in cur):
                                return True
                            time.sleep(0.3)
        except Exception as exc:
            emit(self.log_queue, f"Home video click error: {exc}", logging.WARNING)

        # Fallback to direct navigation if click fails or navigation was not detected
        if href:
            full_url = href if href.startswith("http") else f"https://www.youtube.com{href}"
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)
            return True
        return False

    def click_recommended_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on a recommended video item from the sidebar/recommendations and verify navigation."""
        href = card_info.get("href", "")
        vid_id = extract_video_id(href) or (href.split("v=")[-1].split("&")[0] if "v=" in href else "")
        try:
            idx = int(card_info.get("index", 0))
            items = self.driver.find_elements(By.CSS_SELECTOR, "yt-lockup-view-model, ytd-compact-video-renderer")
            if 0 <= idx < len(items):
                item = items[idx]
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", item
                    )
                    human_like_delay(0.8, 1.5)
                except Exception:
                    pass

                clickable = None
                for selector in (
                    "a.ytLockupViewModelContentImage",
                    "a.ytLockupMetadataViewModelTitle",
                    "h3 a",
                    "a#thumbnail",
                    "#video-title",
                    "span#video-title",
                    "a.yt-simple-endpoint",
                    "a[href*='/watch']",
                ):
                    els = item.find_elements(By.CSS_SELECTOR, selector)
                    if els and els[0].is_displayed():
                        clickable = els[0]
                        break

                target = clickable or item
                try:
                    ActionChains(self.driver).move_to_element(target).pause(0.2).click().perform()
                except Exception:
                    try:
                        target.click()
                    except Exception:
                        click_element(self.driver, target)

                # Poll to verify navigation actually started (must contain target vid_id, not just old /watch)
                for _ in range(8):
                    cur = getattr(self.driver, "current_url", "") or ""
                    if vid_id and vid_id in cur:
                        return True
                    time.sleep(0.3)

            if href and vid_id:
                match_links = self.driver.find_elements(
                    By.XPATH, f"//a[contains(@href, '{vid_id}')]"
                )
                for lk in match_links:
                    if lk.is_displayed():
                        try:
                            self.driver.execute_script(
                                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", lk
                            )
                            human_like_delay(0.5, 1.0)
                        except Exception:
                            pass
                        try:
                            ActionChains(self.driver).move_to_element(lk).pause(0.2).click().perform()
                        except Exception:
                            try:
                                lk.click()
                            except Exception:
                                click_element(self.driver, lk)

                        for _ in range(8):
                            cur = getattr(self.driver, "current_url", "") or ""
                            if vid_id and vid_id in cur:
                                return True
                            time.sleep(0.3)
        except Exception as exc:
            emit(self.log_queue, f"Recommended video click error: {exc}", logging.WARNING)

        if href:
            full_url = href if href.startswith("http") else f"https://www.youtube.com{href}"
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)
            return True
        return False

    def _record_watch(
        self,
        title: str = "",
        channel_name: str = "",
        watch_seconds: float = 0.0,
        ad_seconds: float = 0.0,
        duration: float = 0.0,
        completed: bool = False,
        task: Optional[VideoTask] = None,
    ) -> None:
        if not self.stats_store:
            return
        try:
            current_url = str(getattr(self.driver, "current_url", "") or "")
            if task is None:
                task = VideoTask(
                    url=current_url,
                    title=title,
                    channel=channel_name,
                )
            key = resolve_video_key(task, current_url)
            rec_label = (task.title or "").strip() or title or key
            rec_channel = (task.channel or "").strip() or channel_name
            outcome = TaskOutcome(
                video_key=key,
                task_key=key,
                label=rec_label,
                channel=rec_channel,
                attempts=1,
                success=completed,
                watched_seconds=watch_seconds,
                ad_seconds=ad_seconds,
                video_duration=duration,
                playback_finished=completed,
            )
            self.stats_store.record(outcome)
        except Exception as exc:
            emit(self.log_queue, f"Could not record watching stats: {exc}", logging.WARNING)

    def _ensure_video_in_queue(self, task: VideoTask) -> VideoTask:
        """Register the video in settings.json video queue and emit UI event."""
        try:
            raw_queue = self.config_mgr.get_video_queue()
            vid_key = resolve_video_key(task, task.url)
            for item in raw_queue:
                existing_t = VideoTask.from_dict(item)
                if resolve_video_key(existing_t, existing_t.url) == vid_key:
                    changed = False
                    if not existing_t.channel and task.channel:
                        existing_t.channel = task.channel
                        item["channel"] = task.channel
                        changed = True
                    if existing_t.max_runs == 0 and task.max_runs > 0:
                        existing_t.max_runs = task.max_runs
                        item["max_runs"] = task.max_runs
                        changed = True
                    if changed:
                        self.config_mgr.set_video_queue(raw_queue)
                    self._emit_event(("add_video_task", existing_t.to_dict()))
                    return existing_t

            raw_queue.append(task.to_dict())
            self.config_mgr.set_video_queue(raw_queue)
            self._emit_event(("add_video_task", task.to_dict()))
            emit(self.log_queue, f"Added video to queue under channel '{task.channel}': '{task.title}'")
            return task
        except Exception as exc:
            emit(self.log_queue, f"Could not add video to queue: {exc}", logging.WARNING)
            return task

    def _update_task_duration(self, task: VideoTask, duration_seconds: float) -> None:
        """Update video duration text and seconds in settings.json video queue."""
        if duration_seconds <= 0:
            return
        try:
            raw_queue = self.config_mgr.get_video_queue()
            vid_key = resolve_video_key(task, task.url)
            for v in raw_queue:
                if resolve_video_key(VideoTask.from_dict(v), v.get("url", "")) == vid_key:
                    v["duration_seconds"] = int(duration_seconds)
                    m, s = divmod(int(duration_seconds), 60)
                    h, m = divmod(m, 60)
                    v["duration_text"] = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                    break
            self.config_mgr.set_video_queue(raw_queue)
            self._emit_event(("update_video_duration", task.to_dict(), duration_seconds))
        except Exception:
            pass

    def is_candidate_eligible(
        self,
        full_url: str,
        title: str,
        channel_name: str,
    ) -> Tuple[bool, Optional[VideoTask], str]:
        """Check if candidate video is stored in database and whether it is eligible to run.

        Returns:
            (is_eligible, existing_task_or_none, reason_msg)
        """
        temp_task = VideoTask(url=full_url, title=title, channel=channel_name)
        vid_key = resolve_video_key(temp_task, full_url)

        # 1. Check if stored in video queue (settings.json)
        raw_queue = self.config_mgr.get_video_queue()
        existing_task: Optional[VideoTask] = None
        for item in raw_queue:
            t = VideoTask.from_dict(item)
            if resolve_video_key(t, t.url) == vid_key:
                existing_task = t
                break

        # 2. Check recorded run count from stats_store
        current_runs = 0
        if self.stats_store:
            rec = self.stats_store.find_for_task(existing_task or temp_task) or self.stats_store.get(vid_key)
            if rec:
                current_runs = rec.runs

        if existing_task is not None:
            # If manually disabled in queue
            if not existing_task.enabled:
                return False, existing_task, "disabled in video queue"
            if existing_task.channel:
                matching_ch = [c for c in self.get_target_channels() if c.name.lower() == existing_task.channel.lower()]
                if not matching_ch or not matching_ch[0].enabled or matching_ch[0].archived:
                    return False, existing_task, f"channel '{existing_task.channel}' is disabled or not active"

            limit = existing_task.max_runs if existing_task.max_runs > 0 else 3
            if current_runs >= limit:
                return False, existing_task, f"already reached run limit ({current_runs}/{limit} runs completed)"

            return True, existing_task, f"eligible ({current_runs}/{limit} runs completed)"

        # 3. Not in queue, but check if stats_store has already recorded >= 3 runs
        if current_runs >= 3:
            return False, None, f"already reached run limit ({current_runs}/3 runs completed in stats)"

        return True, None, "new video (0/3 runs)"

    def run(self) -> None:
        """Main watching loop: home discovery -> watch -> repeat."""
        emit(self.log_queue, f"=== Starting Watch Session for {self.account.email} ===")
        active_channels_msg = ", ".join(f"'{c.name}'" for c in self.channels if c.name)
        emit(self.log_queue, f"Target stored channel(s): {active_channels_msg}")

        try:
            self.skip_ads, self.max_ad_seconds = self._ad_settings()
            self.driver = setup_driver(self.account, log_queue=self.log_queue)

            if self.stop_event.is_set():
                return

            # Sign-in step
            login_ok = perform_google_login(
                self.driver,
                self.account,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
            )

            if not login_ok:
                if self.stop_event.is_set():
                    emit(self.log_queue, "Watching session stopped during sign-in.", logging.WARNING)
                    return
                emit(self.log_queue, f"Sign-in failed for {self.account.email}. Aborting watching session.", logging.ERROR)
                self._report_status(f"Sign-in failed for {self.account.email}.", "error")
                return

            if not self.open_videos:
                emit(self.log_queue, "=== Video opening paused. Scanning and grabbing links from YouTube Home feed ===")
                seen_video_urls: Set[str] = set()
                total_links_found = 0
                matched_count = 0

                current_url = getattr(self.driver, "current_url", "")
                if not is_clean_youtube_home_url(current_url):
                    emit(self.log_queue, "Navigating to clean YouTube Home (https://www.youtube.com/)...")
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                emit(self.log_queue, "Waiting for YouTube Home videos to load...")
                try:
                    WebDriverWait(self.driver, 15).until(
                        lambda d: len(
                            d.find_elements(
                                By.CSS_SELECTOR,
                                "ytd-rich-item-renderer, ytd-rich-grid-media, ytd-video-renderer, a[href*='/watch?v=']",
                            )
                        )
                        > 0
                    )
                except Exception:
                    pass
                time.sleep(1.5)

                while not self.stop_event.is_set():
                    self.check_pause()
                    if self.stop_event.is_set():
                        break

                    target_channels = self.get_target_channels()
                    raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
                    if isinstance(raw_items, list):
                        for item in raw_items:
                            href = item.get("href", "")
                            if not href:
                                continue
                            clean_href = href.split("&")[0]
                            if clean_href in seen_video_urls:
                                continue
                            seen_video_urls.add(clean_href)
                            total_links_found += 1

                            full_url = clean_href if clean_href.startswith("http") else f"https://www.youtube.com{clean_href}"
                            title = item.get("title") or "Untitled Video"
                            ch_name = item.get("channel_name") or "Unknown Channel"
                            ch_url = item.get("channel_url") or ""

                            match = match_channel(ch_name, ch_url, target_channels)
                            if match:
                                matched_count += 1
                                channel_display = match.name or ch_name
                                emit(
                                    self.log_queue,
                                    f"Channel: {channel_display}\nURL: {full_url}\nTitle: {title}",
                                )
                                self._emit_event(("progress", 0, matched_count, matched_count))

                    scroll_step = random.randint(500, 850)
                    try:
                        self.driver.execute_script(f"window.scrollBy({{top: {scroll_step}, behavior: 'smooth'}});")
                    except Exception:
                        self.driver.execute_script(f"window.scrollBy(0, {scroll_step});")

                    sleep_time = random.uniform(1.5, 2.5)
                    end_t = time.time() + sleep_time
                    while time.time() < end_t:
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.1)

                summary = f"Browsing session stopped: logged {total_links_found} video(s), {matched_count} matched target channel(s)."
                emit(self.log_queue, summary)
                self._report_status(summary, "info")
                return

            session_watched_keys: Set[str] = set()
            watched_count = 0

            while not self.stop_event.is_set():
                if self.total_loops > 1 and watched_count >= self.total_loops:
                    emit(self.log_queue, f"Reached configured limit of {self.total_loops} video(s).")
                    break

                self.check_pause()
                if self.stop_event.is_set():
                    break

                target_channels = self.get_target_channels()
                if not target_channels:
                    emit(self.log_queue, "No active target channels found in settings. Aborting watching session.", logging.WARNING)
                    break

                current_url = getattr(self.driver, "current_url", "") or ""

                # -------------------------------------------------------------
                # Step 1: If on a video watch page, inspect suggested videos first!
                # -------------------------------------------------------------
                if "/watch" in current_url:
                    emit(self.log_queue, "Inspecting suggested/recommended videos on watch page for target channels...")
                    rec_candidates = self.fetch_recommended_videos()
                    matched_rec_item: Optional[Dict[str, Any]] = None
                    matched_rec_channel: Optional[Channel] = None
                    matched_rec_url: str = ""
                    matched_rec_title: str = ""
                    matched_rec_channel_name: str = ""

                    for item in rec_candidates:
                        href = item.get("href", "")
                        if not href:
                            continue
                        clean_href = href.split("&")[0]
                        full_url = clean_href if clean_href.startswith("http") else f"https://www.youtube.com{clean_href}"
                        title = item.get("title") or "Untitled Video"
                        ch_name = item.get("channel_name") or ""
                        ch_url = item.get("channel_url") or ""

                        match = match_channel(ch_name, ch_url, target_channels)
                        if not match:
                            continue

                        channel_display = (match.name if match else "") or ch_name or "Unknown Channel"

                        # Check eligibility against database & run counts
                        eligible, existing_task, reason = self.is_candidate_eligible(
                            full_url=full_url,
                            title=title,
                            channel_name=channel_display,
                        )
                        if not eligible:
                            emit(
                                self.log_queue,
                                f"Skipping suggested video '{title}' from channel '{channel_display}': {reason}.",
                            )
                            continue

                        vid_id = extract_video_id(clean_href) or clean_href
                        if vid_id in session_watched_keys and len(rec_candidates) > 1:
                            continue

                        matched_rec_item = item
                        matched_rec_channel = match
                        matched_rec_url = full_url
                        matched_rec_title = title
                        matched_rec_channel_name = channel_display
                        break

                    if matched_rec_item:
                        emit(
                            self.log_queue,
                            f"Channel: {matched_rec_channel_name}\nURL: {matched_rec_url}\nTitle: {matched_rec_title}",
                        )

                        video_task = VideoTask(
                            url=matched_rec_url,
                            title=matched_rec_title,
                            channel=matched_rec_channel_name,
                            watchtime_duration="Full Video",
                            enabled=True,
                            max_runs=3,
                        )
                        registered_task = self._ensure_video_in_queue(video_task)

                        emit(self.log_queue, f"Opening suggested video by clicking: '{matched_rec_title}'...")
                        target_rec_id = extract_video_id(matched_rec_url) or ""
                        clicked = self.click_recommended_video(matched_rec_item)
                        cur_url = getattr(self.driver, "current_url", "") or ""
                        if target_rec_id and target_rec_id not in cur_url:
                            emit(self.log_queue, f"Navigation not detected after click, navigating directly to: {matched_rec_url}", logging.INFO)
                            safe_get(self.driver, matched_rec_url, timeout=30, log_queue=self.log_queue)

                        try:
                            if target_rec_id:
                                WebDriverWait(self.driver, 15).until(lambda d: target_rec_id in (getattr(d, "current_url", "") or ""))
                            else:
                                WebDriverWait(self.driver, 15).until(lambda d: "/watch" in (getattr(d, "current_url", "") or ""))
                        except Exception:
                            pass

                        human_like_delay(2.0, 3.5)
                        dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                        ensure_video_playing(self.driver, log_queue=self.log_queue)

                        try:
                            init_dur = self.driver.execute_script("""
                                var player = document.querySelector('.html5-video-player');
                                var isAd = player && (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting') || player.classList.contains('ytp-showing-ad'));
                                if (isAd) return 0;
                                var v = document.querySelector('video');
                                return v && v.duration && !isNaN(v.duration) ? v.duration : 0;
                            """)
                            if init_dur and float(init_dur) > 0:
                                self._update_task_duration(registered_task, float(init_dur))
                                emit(self.log_queue, f"Noted video duration: {int(float(init_dur)//60)}m {int(float(init_dur)%60):02d}s")
                        except Exception:
                            pass

                        enable_interactivity, interactivity_cfg = self._interactivity_settings()
                        emit(self.log_queue, f"\n--- Watching Suggested Video (1-Minute Test): '{matched_rec_title}' ({matched_rec_channel_name}) ---")
                        self._report_status(f"Watching: {matched_rec_title}", "info")

                        def _should_stop_rec(ch_name=matched_rec_channel_name) -> bool:
                            if not ch_name:
                                return False
                            active_names = {c.name.lower() for c in self.get_target_channels()}
                            return ch_name.lower() not in active_names

                        watch_res = watch_video(
                            self.driver,
                            duration_seconds=60,
                            stop_event=self.stop_event,
                            log_queue=self.log_queue,
                            full_timeout_seconds=self._full_video_timeout_seconds(),
                            skip_ads=self.skip_ads,
                            max_ad_seconds=self.max_ad_seconds,
                            end_buffer_seconds=self._end_buffer_seconds(),
                            enable_interactivity=enable_interactivity,
                            interactivity_config=interactivity_cfg,
                            pause_event=self.pause_event,
                            should_stop_callback=_should_stop_rec,
                        )

                        watched_count += 1
                        vid_id = extract_video_id(matched_rec_url) or matched_rec_url
                        session_watched_keys.add(vid_id)

                        self._record_watch(
                            title=matched_rec_title,
                            channel_name=matched_rec_channel_name,
                            watch_seconds=watch_res.watched_seconds,
                            ad_seconds=watch_res.ad_seconds,
                            duration=watch_res.video_duration,
                            completed=watch_res.completed,
                            task=registered_task,
                        )
                        self._update_task_duration(registered_task, watch_res.video_duration)
                        self._emit_event(("progress", 0, watched_count, max(watched_count, self.total_loops)))
                        emit(
                            self.log_queue,
                            f"Updated video time for '{matched_rec_title}': watched {watch_res.watched_seconds:.1f}s (duration: {watch_res.video_duration:.1f}s, completed={watch_res.completed}).",
                        )

                        if self.stop_event.is_set() or watch_res.interrupted:
                            if watch_res.interrupted and not self.stop_event.is_set():
                                emit(self.log_queue, f"Channel '{matched_rec_channel_name}' was disabled. Stopped video playback.")
                            break
                        # Seamlessly chain to inspect recommended videos on this watch page
                        continue

                    # If no eligible suggested video found, log and proceed to clean Home feed
                    emit(
                        self.log_queue,
                        "No eligible suggested videos from target channels found on this watch page. Returning to YouTube Home feed...",
                    )

                # -------------------------------------------------------------
                # Step 2: Ensure strictly clean YouTube Home URL (no channel, parameters, or extras)
                # -------------------------------------------------------------
                current_url = getattr(self.driver, "current_url", "") or ""
                if not is_clean_youtube_home_url(current_url):
                    emit(self.log_queue, "Navigating to clean YouTube Home (https://www.youtube.com/)...")
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                emit(self.log_queue, "Waiting for YouTube Home videos to load...")
                try:
                    WebDriverWait(self.driver, 15).until(
                        lambda d: len(
                            d.find_elements(
                                By.CSS_SELECTOR,
                                "ytd-rich-item-renderer, ytd-rich-grid-media, ytd-video-renderer, a[href*='/watch?v=']",
                            )
                        )
                        > 0
                    )
                except Exception:
                    pass
                time.sleep(1.5)

                emit(self.log_queue, "Scanning YouTube Home feed to find a target channel video...")
                matched_item: Optional[Dict[str, Any]] = None
                matched_channel_obj: Optional[Channel] = None
                seen_clean_urls: Set[str] = set()

                max_scrolls = 25
                for scroll_idx in range(max_scrolls):
                    if self.stop_event.is_set():
                        break
                    self.check_pause()
                    if self.stop_event.is_set():
                        break

                    raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
                    if isinstance(raw_items, list):
                        for item in raw_items:
                            href = item.get("href", "")
                            if not href:
                                continue
                            clean_href = href.split("&")[0]
                            if clean_href in seen_clean_urls:
                                continue
                            seen_clean_urls.add(clean_href)

                            ch_name = item.get("channel_name") or ""
                            ch_url = item.get("channel_url") or ""
                            match = match_channel(ch_name, ch_url, target_channels)
                            if match:
                                full_url = clean_href if clean_href.startswith("http") else f"https://www.youtube.com{clean_href}"
                                item_title = item.get("title") or "Untitled Video"
                                channel_display = (match.name if match else "") or ch_name or "Unknown Channel"

                                # Check database and run count eligibility
                                eligible, existing_task, reason = self.is_candidate_eligible(
                                    full_url=full_url,
                                    title=item_title,
                                    channel_name=channel_display,
                                )
                                if not eligible:
                                    emit(
                                        self.log_queue,
                                        f"Skipping video '{item_title}' from channel '{channel_display}': {reason}.",
                                    )
                                    continue

                                vid_id = extract_video_id(clean_href) or clean_href
                                if vid_id in session_watched_keys and scroll_idx < max_scrolls - 1:
                                    continue

                                matched_item = item
                                matched_channel_obj = match
                                break

                    if matched_item:
                        break

                    scroll_step = random.randint(500, 850)
                    try:
                        self.driver.execute_script(f"window.scrollBy({{top: {scroll_step}, behavior: 'smooth'}});")
                    except Exception:
                        self.driver.execute_script(f"window.scrollBy(0, {scroll_step});")

                    sleep_time = random.uniform(1.3, 2.2)
                    end_t = time.time() + sleep_time
                    while time.time() < end_t:
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.1)

                if self.stop_event.is_set():
                    break

                if not matched_item:
                    emit(
                        self.log_queue,
                        "No videos matching target channels found on YouTube Home after scrolling. Waiting 10 seconds before retrying...",
                        logging.WARNING,
                    )
                    for _ in range(10):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                href = matched_item.get("href", "")
                clean_href = href.split("&")[0]
                full_url = clean_href if clean_href.startswith("http") else f"https://www.youtube.com{clean_href}"
                title = matched_item.get("title") or "Untitled Video"
                channel_display = (matched_channel_obj.name if matched_channel_obj else "") or matched_item.get("channel_name") or "Unknown Channel"

                emit(
                    self.log_queue,
                    f"Channel: {channel_display}\nURL: {full_url}\nTitle: {title}",
                )

                # Register video under target channel in the app's video queue with run limit 3
                video_task = VideoTask(
                    url=full_url,
                    title=title,
                    channel=channel_display,
                    watchtime_duration="Full Video",
                    enabled=True,
                    max_runs=3,
                )
                registered_task = self._ensure_video_in_queue(video_task)

                target_home_id = extract_video_id(full_url) or ""
                emit(self.log_queue, f"Opening video by clicking: '{title}'...")
                clicked = self.click_home_video(matched_item)
                cur_url = getattr(self.driver, "current_url", "") or ""
                if target_home_id and target_home_id not in cur_url:
                    emit(self.log_queue, f"Navigation not detected after click, loading directly: {full_url}", logging.INFO)
                    safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)

                try:
                    if target_home_id:
                        WebDriverWait(self.driver, 15).until(lambda d: target_home_id in (getattr(d, "current_url", "") or ""))
                    else:
                        WebDriverWait(self.driver, 15).until(lambda d: "/watch" in (getattr(d, "current_url", "") or ""))
                except Exception:
                    pass

                human_like_delay(2.0, 3.5)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                ensure_video_playing(self.driver, log_queue=self.log_queue)

                try:
                    init_dur = self.driver.execute_script("""
                        var player = document.querySelector('.html5-video-player');
                        var isAd = player && (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting') || player.classList.contains('ytp-showing-ad'));
                        if (isAd) return 0;
                        var v = document.querySelector('video');
                        return v && v.duration && !isNaN(v.duration) ? v.duration : 0;
                    """)
                    if init_dur and float(init_dur) > 0:
                        self._update_task_duration(registered_task, float(init_dur))
                        emit(self.log_queue, f"Noted video duration: {int(float(init_dur)//60)}m {int(float(init_dur)%60):02d}s")
                except Exception:
                    pass

                enable_interactivity, interactivity_cfg = self._interactivity_settings()
                emit(self.log_queue, f"\n--- Watching Video (1-Minute Test): '{title}' ({channel_display}) ---")
                self._report_status(f"Watching (1m test): {title}", "info")

                def _should_stop_home(ch_name=channel_display) -> bool:
                    if not ch_name:
                        return False
                    active_names = {c.name.lower() for c in self.get_target_channels()}
                    return ch_name.lower() not in active_names

                watch_res = watch_video(
                    self.driver,
                    duration_seconds=60,
                    stop_event=self.stop_event,
                    log_queue=self.log_queue,
                    full_timeout_seconds=self._full_video_timeout_seconds(),
                    skip_ads=self.skip_ads,
                    max_ad_seconds=self.max_ad_seconds,
                    end_buffer_seconds=self._end_buffer_seconds(),
                    enable_interactivity=enable_interactivity,
                    interactivity_config=interactivity_cfg,
                    pause_event=self.pause_event,
                    should_stop_callback=_should_stop_home,
                )

                watched_count += 1
                vid_id = extract_video_id(clean_href) or clean_href
                session_watched_keys.add(vid_id)

                self._record_watch(
                    title=title,
                    channel_name=channel_display,
                    watch_seconds=watch_res.watched_seconds,
                    ad_seconds=watch_res.ad_seconds,
                    duration=watch_res.video_duration,
                    completed=watch_res.completed,
                    task=registered_task,
                )
                self._update_task_duration(registered_task, watch_res.video_duration)
                self._emit_event(("progress", 0, watched_count, max(watched_count, self.total_loops)))

                emit(
                    self.log_queue,
                    f"Updated video time for '{title}': watched {watch_res.watched_seconds:.1f}s (duration: {watch_res.video_duration:.1f}s, completed={watch_res.completed}).",
                )

                if self.stop_event.is_set() or watch_res.interrupted:
                    if watch_res.interrupted and not self.stop_event.is_set():
                        emit(self.log_queue, f"Channel '{channel_display}' was disabled. Stopped video playback.")
                        continue
                    emit(self.log_queue, "Watching session stopped by user.")
                    break

            summary = f"Watching session finished: watched {watched_count} video(s)."
            emit(self.log_queue, summary)
            self._report_status(summary, "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during watching session: {exc}", logging.ERROR)
            self._report_status(f"Watching error: {exc}", "error")
        finally:
            close_driver(self.driver, log_queue=self.log_queue)
