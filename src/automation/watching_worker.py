from .session_tracker import SessionChannelTracker
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
from ..models.watch_stats import TaskOutcome, WatchResult, resolve_video_key
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

    var AD_CONTAINERS = [
        'ytd-ad-slot-renderer',
        'ytd-in-feed-ad-layout-renderer',
        'ad-slot-renderer',
        'yt-ad-slot-renderer',
        'ytd-display-ad-renderer',
        'ytd-banner-promo-renderer',
        'ytd-promoted-sparkles-web-renderer',
        'ytd-compact-promoted-video-renderer',
        'ytd-promoted-video-renderer',
        'square-image-layout-view-model',
        'ad-image-view-model',
        'feed-ad-metadata-view-model'
    ].join(', ');

    function isAdElement(el) {
        if (!el) return false;
        if (el.closest && el.closest(AD_CONTAINERS)) return true;
        if (el.querySelector && el.querySelector(
            'ad-badge-view-model, ad-image-view-model, feed-ad-metadata-view-model, ' +
            '.ytBadgeShapeAd, .ytwAdImageViewModelHostIsClickableAdComponent, ' +
            '.badge-style-type-ad, .badge-style-type-sponsored, ' +
            '[aria-label="Sponsored" i], [aria-label="Ad" i], [aria-label="Advertisement" i], ' +
            '[aria-label*="Sponsored" i]'
        )) {
            return true;
        }
        var badges = el.querySelectorAll ? el.querySelectorAll('badge-shape, yt-badge-shape, ytd-badge-supported-renderer, .ytBadgeShapeText, span.badge-style-type-simple') : [];
        for (var b = 0; b < badges.length; b++) {
            var txt = (badges[b].textContent || '').trim().toLowerCase();
            if (txt === 'ad' || txt === 'sponsored' || txt === 'promoted') {
                return true;
            }
        }
        return false;
    }

    function isAdUrl(href) {
        if (!href) return true;
        var lower = href.toLowerCase();
        if (
            lower.indexOf('googleadservices.com') !== -1 ||
            lower.indexOf('/pagead/') !== -1 ||
            lower.indexOf('doubleclick.net') !== -1 ||
            lower.indexOf('googleads') !== -1 ||
            lower.indexOf('ad_type') !== -1 ||
            lower.indexOf('aclk') !== -1
        ) {
            return true;
        }
        if (lower.indexOf('/watch') === -1 && lower.indexOf('watch?') === -1 && lower.indexOf('v=') === -1) {
            return true;
        }
        return false;
    }

    var cards = document.querySelectorAll(
        'ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-grid-media'
    );

    function parseCard(card, idx) {
        if (!card) return null;
        if (isAdElement(card)) {
            return null;
        }
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
        if (!href || isAdUrl(href)) {
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
            if (isAdElement(a)) continue;
            var hrefVal = a.getAttribute('href') || '';
            if (isAdUrl(hrefVal)) continue;
            var cUrl = hrefVal.split('&')[0];
            if (seenUrls[cUrl]) continue;

            var parentCard = a.closest('ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-grid-media, div#dismissible');
            if (parentCard && isAdElement(parentCard)) continue;
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

    var AD_CONTAINERS = [
        'ytd-ad-slot-renderer',
        'ytd-in-feed-ad-layout-renderer',
        'ad-slot-renderer',
        'yt-ad-slot-renderer',
        'ytd-display-ad-renderer',
        'ytd-banner-promo-renderer',
        'ytd-promoted-sparkles-web-renderer',
        'ytd-compact-promoted-video-renderer',
        'ytd-promoted-video-renderer',
        'square-image-layout-view-model',
        'ad-image-view-model',
        'feed-ad-metadata-view-model'
    ].join(', ');

    function isAdElement(el) {
        if (!el) return false;
        if (el.closest && el.closest(AD_CONTAINERS)) return true;
        if (el.querySelector && el.querySelector(
            'ad-badge-view-model, ad-image-view-model, feed-ad-metadata-view-model, ' +
            '.ytBadgeShapeAd, .ytwAdImageViewModelHostIsClickableAdComponent, ' +
            '.badge-style-type-ad, .badge-style-type-sponsored, ' +
            '[aria-label="Sponsored" i], [aria-label="Ad" i], [aria-label="Advertisement" i], ' +
            '[aria-label*="Sponsored" i]'
        )) {
            return true;
        }
        var badges = el.querySelectorAll ? el.querySelectorAll('badge-shape, yt-badge-shape, ytd-badge-supported-renderer, .ytBadgeShapeText, span.badge-style-type-simple') : [];
        for (var b = 0; b < badges.length; b++) {
            var txt = (badges[b].textContent || '').trim().toLowerCase();
            if (txt === 'ad' || txt === 'sponsored' || txt === 'promoted') {
                return true;
            }
        }
        return false;
    }

    function isAdUrl(href) {
        if (!href) return true;
        var lower = href.toLowerCase();
        if (
            lower.indexOf('googleadservices.com') !== -1 ||
            lower.indexOf('/pagead/') !== -1 ||
            lower.indexOf('doubleclick.net') !== -1 ||
            lower.indexOf('googleads') !== -1 ||
            lower.indexOf('ad_type') !== -1 ||
            lower.indexOf('aclk') !== -1
        ) {
            return true;
        }
        if (lower.indexOf('/watch') === -1 && lower.indexOf('watch?') === -1 && lower.indexOf('v=') === -1) {
            return true;
        }
        return false;
    }

    var cards = document.querySelectorAll(
        'yt-lockup-view-model, ytd-compact-video-renderer, #related ytd-video-renderer, ytd-watch-next-secondary-results-renderer ytd-compact-video-renderer, ytd-compact-radio-renderer'
    );

    function parseRecCard(card, idx) {
        if (!card) return null;
        if (isAdElement(card)) {
            return null;
        }
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
        if (!href || isAdUrl(href)) {
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
            var match = aria.match(/\\bby\\s+([^0-9\\n\\r]+?)(?:\\s+\\d+|\\s+ago|\\s+views|\\s+Streamed|$)/i);
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
            if (isAdElement(a)) continue;
            var h = a.getAttribute('href') || '';
            if (isAdUrl(h)) continue;
            var cUrl = h.split('&')[0];
            if (seenUrls[cUrl]) continue;
            var parent = a.closest('yt-lockup-view-model, ytd-compact-video-renderer, ytd-video-renderer, div#dismissible');
            if (parent && isAdElement(parent)) continue;
            var p = parseRecCard(parent, j);
            if (p) {
                results.push(p);
            }
        }
    }

    return results;
})();
"""



def is_candidate_ad(item: Dict[str, Any]) -> bool:
    """Check if candidate video dictionary represents an advertisement or sponsored card."""
    if not isinstance(item, dict):
        return True
    href = (item.get("href") or "").lower()
    if not href:
        return True
    if any(ad_kw in href for ad_kw in ["googleadservices", "doubleclick", "/pagead/", "aclk", "ad_type", "googleads"]):
        return True
    if not ("/watch" in href or "v=" in href):
        return True
    title = (item.get("title") or "").strip().lower()
    if title.startswith("sponsored") or title.startswith("ad ") or title == "ad":
        return True
    ch_name = (item.get("channel_name") or "").strip().lower()
    if ch_name.startswith("sponsored") or ch_name == "ad":
        return True
    return False

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
        self.channel_tracker = SessionChannelTracker(
            config_mgr=self.config_mgr,
            stats_store=self.stats_store,
            channels=self.channels,
            emit_event=self._emit_event,
            log_queue=self.log_queue,
        )

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
        """Get fresh enabled target channels list from settings.json. Only returns channels that are checked/enabled, not archived, and haven't achieved target watch time."""
        def _is_active(c: Channel) -> bool:
            if not (c.name and c.enabled and not c.archived):
                return False
            if hasattr(self, "channel_tracker") and self.channel_tracker.is_channel_target_achieved(c.name):
                return False
            if c.target_achieved:
                return False
            return True

        try:
            stored_dicts = self.config_mgr.get_channels()
            if stored_dicts:
                stored_objs = [Channel.from_dict(c) for c in stored_dicts]
                return [c for c in stored_objs if _is_active(c)]
        except Exception:
            pass
        return [c for c in self.channels if _is_active(c)]

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

                    filtered_items = [it for it in raw_items if not is_candidate_ad(it)]
                    if len(filtered_items) >= target_count and not target_channels:
                        return filtered_items[:target_count]

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
            items_list = raw_items if isinstance(raw_items, list) else []
            return [it for it in items_list if not is_candidate_ad(it)]
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
                if isinstance(raw_items, list):
                    filtered_rec = [it for it in raw_items if not is_candidate_ad(it)]
                    if len(filtered_rec) >= 10:
                        return filtered_rec

                # Smoothly scroll down the page slightly to populate recommended items
                self.driver.execute_script("window.scrollBy({top: 600, behavior: 'smooth'});")
                time.sleep(1.8)

            raw_items = self.driver.execute_script(RECOMMENDED_VIDEOS_JS)
            items_list = raw_items if isinstance(raw_items, list) else []
            return [it for it in items_list if not is_candidate_ad(it)]
        except Exception as exc:
            emit(self.log_queue, f"Error fetching recommended videos: {exc}", logging.WARNING)
            return []

    def click_home_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on the target video card on the home page and verify navigation."""
        href = card_info.get("href", "")
        vid_id = extract_video_id(href) or (href.split("v=")[-1].split("&")[0] if "v=" in href else "")
        try:
            # 1. Direct search by vid_id: find clean non-ad link on Home page
            if vid_id:
                try:
                    matched_el = self.driver.execute_script(r"""
                        var vidId = arguments[0];
                        var links = document.querySelectorAll(
                            'ytd-rich-item-renderer a[href*="' + vidId + '"], ' +
                            'ytd-video-renderer a[href*="' + vidId + '"], ' +
                            'a[href*="' + vidId + '"]'
                        );
                        for (var i = 0; i < links.length; i++) {
                            var l = links[i];
                            if (l.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, [layout*="ad"]')) {
                                continue;
                            }
                            return l;
                        }
                        return null;
                    """, vid_id)
                    if matched_el:
                        self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", matched_el)
                        human_like_delay(0.5, 1.0)
                        try:
                            ActionChains(self.driver).move_to_element(matched_el).pause(0.2).click().perform()
                        except Exception:
                            try:
                                matched_el.click()
                            except Exception:
                                click_element(self.driver, matched_el)

                        for _ in range(8):
                            cur = getattr(self.driver, "current_url", "") or ""
                            if (vid_id and vid_id in cur) or ("/watch" in cur):
                                return True
                            time.sleep(0.3)
                except Exception as exc:
                    emit(self.log_queue, f"Direct home video click attempt error: {exc}", logging.DEBUG)

            # 2. Index-based element click (with strict ad check)
            idx = int(card_info.get("index", 0))
            items = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ytd-rich-item-renderer, ytd-rich-grid-media, ytd-video-renderer, ytd-grid-video-renderer",
            )
            if 0 <= idx < len(items):
                item = items[idx]
                is_ad = self.driver.execute_script(r"""
                    var el = arguments[0];
                    if (!el) return true;
                    return !!(el.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, square-image-layout-view-model') ||
                              el.querySelector('ad-badge-view-model, feed-ad-metadata-view-model, .ytBadgeShapeAd'));
                """, item)
                if not is_ad:
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
                else:
                    emit(self.log_queue, "Home video item at index is an ad element; skipping index click to avoid ad.", logging.WARNING)

            # 3. XPath fallback matching vid_id (with strict ad check)
            if href and vid_id:
                match_links = self.driver.find_elements(
                    By.XPATH, f"//a[contains(@href, '{vid_id}')]"
                )
                for lk in match_links:
                    if lk.is_displayed():
                        is_ad_lk = self.driver.execute_script(r"""
                            var el = arguments[0];
                            if (!el) return true;
                            return !!el.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, square-image-layout-view-model');
                        """, lk)
                        if is_ad_lk:
                            continue
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
            # 1. Direct search by vid_id: find clean non-ad link
            if vid_id:
                try:
                    matched_el = self.driver.execute_script(r"""
                        var vidId = arguments[0];
                        var links = document.querySelectorAll(
                            '#related a[href*="' + vidId + '"], ' +
                            'ytd-watch-next-secondary-results-renderer a[href*="' + vidId + '"], ' +
                            'a[href*="' + vidId + '"]'
                        );
                        for (var i = 0; i < links.length; i++) {
                            var l = links[i];
                            if (l.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, [layout*="ad"]')) {
                                continue;
                            }
                            return l;
                        }
                        return null;
                    """, vid_id)
                    if matched_el:
                        self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", matched_el)
                        human_like_delay(0.5, 1.0)
                        try:
                            ActionChains(self.driver).move_to_element(matched_el).pause(0.2).click().perform()
                        except Exception:
                            try:
                                matched_el.click()
                            except Exception:
                                click_element(self.driver, matched_el)

                        for _ in range(8):
                            cur = getattr(self.driver, "current_url", "") or ""
                            if vid_id and vid_id in cur:
                                return True
                            time.sleep(0.3)
                except Exception as exc:
                    emit(self.log_queue, f"Direct recommended video click attempt error: {exc}", logging.DEBUG)

            # 2. Index-based element click (with strict ad check)
            idx = int(card_info.get("index", 0))
            items = self.driver.find_elements(By.CSS_SELECTOR, "yt-lockup-view-model, ytd-compact-video-renderer")
            if 0 <= idx < len(items):
                item = items[idx]
                is_ad = self.driver.execute_script(r"""
                    var el = arguments[0];
                    if (!el) return true;
                    return !!(el.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, square-image-layout-view-model') ||
                              el.querySelector('ad-badge-view-model, feed-ad-metadata-view-model, .ytBadgeShapeAd'));
                """, item)
                if not is_ad:
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
                else:
                    emit(self.log_queue, "Recommended item at index is an ad element; skipping index click to avoid ad.", logging.WARNING)

            # 3. XPath fallback matching vid_id (with strict ad check)
            if href and vid_id:
                match_links = self.driver.find_elements(
                    By.XPATH, f"//a[contains(@href, '{vid_id}')]"
                )
                for lk in match_links:
                    if lk.is_displayed():
                        is_ad_lk = self.driver.execute_script(r"""
                            var el = arguments[0];
                            if (!el) return true;
                            return !!el.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, square-image-layout-view-model');
                        """, lk)
                        if is_ad_lk:
                            continue
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
        playback_finished: bool = False,
        interrupted: bool = False,
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
                interrupted=interrupted,
                watched_seconds=watch_seconds,
                ad_seconds=ad_seconds,
                video_duration=duration,
                playback_finished=playback_finished,
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
                    if getattr(existing_t, "target_multiplier", 0.0) == 0.0 and getattr(task, "target_multiplier", 0.0) > 0.0:
                        existing_t.target_multiplier = task.target_multiplier
                        item["target_multiplier"] = task.target_multiplier
                        changed = True
                    if changed:
                        self.config_mgr.set_video_queue(raw_queue)
                    self._emit_event(("add_video_task", existing_t.to_dict()))
                    return existing_t

            raw_queue.append(task.to_dict())
            self.config_mgr.set_video_queue(raw_queue)
            self._emit_event(("add_video_task", task.to_dict()))
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
        watch_seconds = 0.0
        video_duration = 0.0
        rec = None
        if self.stats_store:
            rec = self.stats_store.find_for_task(existing_task or temp_task) or self.stats_store.get(vid_key)
            if rec:
                current_runs = rec.runs
                watch_seconds = rec.watch_seconds
                video_duration = rec.video_duration

        cooldown_hours = self.config_mgr.get_float("VideoCooldownHours", 12.0)

        if existing_task is not None:
            # If manually disabled in queue
            if not existing_task.enabled:
                return False, existing_task, "disabled in video queue"
            if existing_task.channel:
                matching_ch = [c for c in self.get_target_channels() if c.name.lower() == existing_task.channel.lower()]
                if not matching_ch or not matching_ch[0].enabled or matching_ch[0].archived:
                    return False, existing_task, f"channel '{existing_task.channel}' is disabled or not active"

            eff_dur = video_duration if video_duration > 0 else getattr(existing_task, "duration_seconds", 0.0)
            limit_disp = existing_task.max_runs if existing_task.max_runs > 0 else 3
            if existing_task.is_target_reached(current_runs, watch_seconds, eff_dur):
                reason = "target multiplier reached" if (existing_task.target_multiplier > 0 and eff_dur > 0 and watch_seconds / eff_dur >= existing_task.target_multiplier) else f"run limit ({current_runs}/{limit_disp} runs completed)"
                return False, existing_task, f"already reached {reason}"

            # Check cooldown before confirming eligibility
            if rec and cooldown_hours > 0 and hasattr(rec, "is_on_cooldown"):
                is_cool, remaining_hours = rec.is_on_cooldown(cooldown_hours)
                if is_cool:
                    rem_str = f"{remaining_hours:.1f}h" if remaining_hours >= 1.0 else f"{int(remaining_hours * 60)}m"
                    return False, existing_task, f"within {cooldown_hours:g}h cooldown period ({rem_str} remaining)"

            limit_desc = f"max_views={existing_task.max_runs}" if existing_task.max_runs > 0 else "unlimited views"
            mult_desc = f"target={existing_task.target_multiplier:.1f}x" if existing_task.target_multiplier > 0 else "unlimited target"
            return True, existing_task, f"eligible ({current_runs}/{limit_disp} runs completed)"

        # 3. Not in queue, but check if stats_store has already recorded >= 3 runs
        if current_runs >= 3:
            return False, None, f"already reached run limit ({current_runs}/3 runs completed in stats)"

        # Check cooldown for non-queue videos as well
        if rec and cooldown_hours > 0 and hasattr(rec, "is_on_cooldown"):
            is_cool, remaining_hours = rec.is_on_cooldown(cooldown_hours)
            if is_cool:
                rem_str = f"{remaining_hours:.1f}h" if remaining_hours >= 1.0 else f"{int(remaining_hours * 60)}m"
                return False, None, f"within {cooldown_hours:g}h cooldown period ({rem_str} remaining)"

        return True, None, "new video (0/3 runs)"

    def _get_allowed_target_channels(
        self,
        target_channels: Sequence[Channel],
        last_channel_name: Optional[str],
        allow_same_channel: bool = False,
    ) -> List[Channel]:
        """Return the target channels allowed to be watched next.

        Enforces: Don't watch two videos of the same channel in a go.
        If last_channel_name was Channel A, tries to find Channel B.
        """
        if not target_channels:
            return []
        active = [c for c in target_channels if c.enabled and not c.archived]
        if not active:
            return []
        if not last_channel_name:
            return active

        last_canon = SessionChannelTracker._canon_name(last_channel_name)
        if len(active) > 1:
            # When multiple target channels exist, never allow the same channel back-to-back
            other_channels = [c for c in active if SessionChannelTracker._canon_name(c.name) != last_canon]
            return other_channels
        else:
            # Only 1 target channel exists: disallowed immediately back-to-back,
            # but allowed once a random filler video intervened.
            return active if allow_same_channel else []

    def _find_target_video(
        self,
        candidates: List[Dict[str, Any]],
        allowed_channels: Sequence[Channel],
        session_watched_keys: Set[str],
    ) -> Optional[Tuple[Dict[str, Any], Channel, str, str, str]]:
        """Search a list of candidate video cards for an eligible target channel video.

        Returns (item, match_channel_obj, full_url, title, channel_display) or None.
        """
        if not allowed_channels:
            return None

        for item in candidates:
            if is_candidate_ad(item):
                continue
            href = item.get("href", "")
            if not href:
                continue
            clean_href = href.split("&")[0]
            full_url = clean_href if clean_href.startswith("http") else f"https://www.youtube.com{clean_href}"
            title = item.get("title") or "Untitled Video"
            ch_name = item.get("channel_name") or ""
            ch_url = item.get("channel_url") or ""

            match = match_channel(ch_name, ch_url, allowed_channels)
            if not match:
                continue

            channel_display = (match.name if match else "") or ch_name or "Unknown Channel"

            eligible, existing_task, reason = self.is_candidate_eligible(
                full_url=full_url,
                title=title,
                channel_name=channel_display,
            )
            if not eligible:
                emit(
                    self.log_queue,
                    f"Skipping video '{title}' from channel '{channel_display}': {reason}.",
                )
                continue

            vid_id = extract_video_id(clean_href) or clean_href
            if vid_id in session_watched_keys and len(candidates) > 1:
                continue

            return (item, match, full_url, title, channel_display)
        return None

    def _find_random_suggested_video(
        self,
        candidates: List[Dict[str, Any]],
        current_url: str,
        session_watched_keys: Set[str],
        exclude_target_channels: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Find a random video from the suggested list (excluding currently playing video, shorts, and optionally target channels)."""
        cur_vid_id = extract_video_id(current_url) or ""
        target_channels = self.get_target_channels() if exclude_target_channels else []
        valid_candidates = []
        for item in candidates:
            if is_candidate_ad(item):
                continue
            href = item.get("href", "")
            if not href:
                continue
            clean_href = href.split("&")[0]
            vid_id = extract_video_id(clean_href)
            if not vid_id:
                continue
            if cur_vid_id and vid_id == cur_vid_id:
                continue
            if "/shorts/" in clean_href:
                continue

            if exclude_target_channels and target_channels:
                ch_name = item.get("channel_name") or ""
                ch_url = item.get("channel_url") or ""
                if match_channel(ch_name, ch_url, target_channels):
                    continue

            valid_candidates.append(item)

        if not valid_candidates:
            return None

        cooldown_hours = self.config_mgr.get_float("VideoCooldownHours", 12.0)
        fresh = []
        for c in valid_candidates:
            v_id = extract_video_id(c.get("href", "")) or ""
            if v_id in session_watched_keys:
                continue
            if self.stats_store and cooldown_hours > 0 and v_id:
                rec_stat = self.stats_store.get(v_id)
                if rec_stat and hasattr(rec_stat, "is_on_cooldown"):
                    is_cool, _ = rec_stat.is_on_cooldown(cooldown_hours)
                    if is_cool:
                        continue
            fresh.append(c)

        pool = fresh if fresh else valid_candidates
        return random.choice(pool)

    def _watch_interleaving_buffer_video(self, session_watched_keys: Set[str]) -> bool:
        """Watch a random video that is NOT from any target channel for 30-90s (Option 5)."""
        if self.stop_event.is_set():
            return False

        rec_candidates = self.fetch_recommended_videos()
        random_card = self._find_random_suggested_video(
            rec_candidates,
            getattr(self.driver, "current_url", "") or "",
            session_watched_keys,
            exclude_target_channels=True,
        )

        if not random_card:
            current_url = getattr(self.driver, "current_url", "") or ""
            if not is_clean_youtube_home_url(current_url):
                safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                human_like_delay(2.0, 3.5)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
            home_candidates = self.fetch_home_videos(target_count=20)
            random_card = self._find_random_suggested_video(
                home_candidates,
                getattr(self.driver, "current_url", "") or "",
                session_watched_keys,
                exclude_target_channels=True,
            )

        if not random_card:
            emit(self.log_queue, "Could not find a non-target video for 30-90s buffer. Proceeding directly.", logging.WARNING)
            return False

        random_dur = round(random.uniform(30.0, 90.0), 1)
        random_title = random_card.get("title") or "Non-Target Video"
        emit(
            self.log_queue,
            f"\n=== Watching Non-Target Buffer Video ({random_dur:.0f}s): '{random_title}' (No interactions) ===",
        )

        watched_ok = self._watch_random_video(random_card, random_dur, session_watched_keys)
        if watched_ok:
            emit(
                self.log_queue,
                "Finished non-target buffer video. Searching for next target channel video...\n",
            )
        return watched_ok

    def _after_target_video_watched(
        self,
        session_watched_keys: Set[str],
        watched_count: int,
    ) -> bool:
        """Execute Option 5: watch a non-target suggested video (30-90s) before seeking the next target video."""
        if self.stop_event.is_set():
            return False
        if self.total_loops > 1 and watched_count >= self.total_loops:
            return False

        active_channels = self.get_target_channels()
        if not active_channels or all(c.target_achieved for c in active_channels):
            return False

        return self._watch_interleaving_buffer_video(session_watched_keys)

    def _watch_random_video(
        self,
        card_info: Dict[str, Any],
        duration_seconds: float,
        session_watched_keys: Set[str],
    ) -> bool:
        """Watch a random video for 30 - 90 seconds without liking, commenting, or subscribing."""
        title = card_info.get("title") or "Suggested Video"
        href = card_info.get("href", "")
        clean_href = href.split("&")[0]
        full_url = clean_href if clean_href.startswith("http") else f"https://www.youtube.com{clean_href}"
        target_rec_id = extract_video_id(clean_href) or ""

        clicked = self.click_recommended_video(card_info)
        cur_url = getattr(self.driver, "current_url", "") or ""
        if target_rec_id and target_rec_id not in cur_url:
            emit(self.log_queue, f"Navigation not detected after click, navigating directly to: {full_url}", logging.INFO)
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)

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

        self._report_status(f"Watching random video ({duration_seconds:.0f}s): {title}", "info")

        # Disable all interactivity: no comments, no likes, no subscribe
        # Active channel is None so channel tracker minutes are NOT incremented
        self.channel_tracker.set_active_channel(None)
        watch_res = watch_video(
            self.driver,
            duration_seconds=duration_seconds,
            stop_event=self.stop_event,
            log_queue=self.log_queue,
            full_timeout_seconds=self._full_video_timeout_seconds(),
            skip_ads=self.skip_ads,
            max_ad_seconds=self.max_ad_seconds,
            end_buffer_seconds=self._end_buffer_seconds(),
            enable_interactivity=False,
            pause_event=self.pause_event,
        )

        if target_rec_id:
            session_watched_keys.add(target_rec_id)

        return not self.stop_event.is_set()

    def _play_target_video(
        self,
        card_info: Dict[str, Any],
        full_url: str,
        title: str,
        channel_display: str,
        session_watched_keys: Set[str],
        is_home: bool = False,
    ) -> WatchResult:
        """Play and track a target channel video."""
        emit(
            self.log_queue,
            f"Channel: {channel_display}\nURL: {full_url}\nTitle: {title}",
        )

        inherited_mult = 0.0
        inherited_max_runs = 3
        raw_q = self.config_mgr.get_video_queue()
        for q_item in raw_q:
            if (q_item.get("channel") or "").strip().lower() == channel_display.strip().lower():
                if q_item.get("target_multiplier", 0.0) > 0:
                    inherited_mult = float(q_item["target_multiplier"])
                if q_item.get("max_runs", 0) > 0:
                    inherited_max_runs = int(q_item["max_runs"])
                break

        video_task = VideoTask(
            url=full_url,
            title=title,
            channel=channel_display,
            watchtime_duration="Full Video",
            enabled=True,
            max_runs=inherited_max_runs,
            target_multiplier=inherited_mult,
        )
        registered_task = self._ensure_video_in_queue(video_task)

        target_vid_id = extract_video_id(full_url) or ""

        if is_home:
            clicked = self.click_home_video(card_info)
        else:
            clicked = self.click_recommended_video(card_info)

        cur_url = getattr(self.driver, "current_url", "") or ""
        if target_vid_id and target_vid_id not in cur_url:
            emit(self.log_queue, f"Navigation not detected after click, navigating directly to: {full_url}", logging.INFO)
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)

        try:
            if target_vid_id:
                WebDriverWait(self.driver, 15).until(lambda d: target_vid_id in (getattr(d, "current_url", "") or ""))
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
        except Exception:
            pass

        enable_interactivity, interactivity_cfg = self._interactivity_settings()
        self._report_status(f"Watching: {title}", "info")

        def _should_stop(ch_name=channel_display):
            if not ch_name:
                return False
            if hasattr(self, "channel_tracker") and self.channel_tracker.is_channel_target_achieved(ch_name):
                return "channel_target_achieved"
            active_names = {c.name.lower() for c in self.get_target_channels()}
            if ch_name.lower() not in active_names:
                return "channel_disabled"
            if registered_task and getattr(registered_task, "target_multiplier", 0.0) > 0:
                rec = self.stats_store.find_for_task(registered_task) if self.stats_store else None
                prev_watched = rec.watch_seconds if rec else 0.0
                curr_watch = self.channel_tracker.get_active_watch_delta() if hasattr(self, "channel_tracker") else 0.0
                total_w = prev_watched + curr_watch
                eff_d = (rec.video_duration if rec and rec.video_duration > 0 else 0.0) or getattr(registered_task, "duration_seconds", 0.0)
                if eff_d > 0 and (total_w / eff_d) >= registered_task.target_multiplier:
                    emit(self.log_queue, f"Video '{registered_task.title}' reached target multiplier ({registered_task.target_multiplier:.2f}x). Stopping video playback.")
                    return "video_target_achieved"
            return False

        target_watch_duration = registered_task.parse_duration_seconds()
        self.channel_tracker.set_active_channel(channel_display)
        watch_res = watch_video(
            self.driver,
            duration_seconds=target_watch_duration,
            stop_event=self.stop_event,
            log_queue=self.log_queue,
            full_timeout_seconds=self._full_video_timeout_seconds(),
            skip_ads=self.skip_ads,
            max_ad_seconds=self.max_ad_seconds,
            end_buffer_seconds=self._end_buffer_seconds(),
            enable_interactivity=enable_interactivity,
            interactivity_config=interactivity_cfg,
            pause_event=self.pause_event,
            should_stop_callback=_should_stop,
            on_watch_tick=lambda d: self.channel_tracker.add_watch_delta(channel_display, d),
        )
        self.channel_tracker.set_active_channel(None)

        if target_vid_id:
            session_watched_keys.add(target_vid_id)

        actual_watched = float(watch_res.watched_seconds or 0.0)
        actual_ad = float(watch_res.ad_seconds or 0.0)
        actual_dur = float(watch_res.video_duration or 0.0)
        self._record_watch(
            title=title,
            channel_name=channel_display,
            watch_seconds=actual_watched,
            ad_seconds=actual_ad,
            duration=actual_dur,
            completed=watch_res.completed,
            playback_finished=watch_res.playback_finished,
            interrupted=watch_res.interrupted,
            task=registered_task,
        )
        self._update_task_duration(registered_task, actual_dur)
        emit(
            self.log_queue,
            f"Updated video time for '{title}': watched {actual_watched:.1f}s (duration: {actual_dur:.1f}s, completed={watch_res.completed}).",
        )
        return watch_res

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
            last_watched_channel: Optional[str] = None

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

                if all(c.target_achieved for c in target_channels):
                    emit(self.log_queue, "All active target channels have achieved their target watch time! Watching session complete.")
                    break

                current_url = getattr(self.driver, "current_url", "") or ""

                # -------------------------------------------------------------
                # Step 1: If on a video watch page, inspect suggested videos first!
                # -------------------------------------------------------------
                if "/watch" in current_url:
                    emit(self.log_queue, "Inspecting suggested/recommended videos on watch page for target channels...")
                    rec_candidates = self.fetch_recommended_videos()

                    # Find target channel video respecting alternation (don't play two of the same channel in a go)
                    allowed_channels = self._get_allowed_target_channels(
                        target_channels, last_watched_channel, allow_same_channel=False
                    )
                    target_match = self._find_target_video(rec_candidates, allowed_channels, session_watched_keys)

                    if target_match:
                        item, match_obj, full_url, title, ch_display = target_match
                        watch_res = self._play_target_video(
                            item, full_url, title, ch_display, session_watched_keys, is_home=False
                        )
                        last_watched_channel = ch_display
                        watched_count += 1
                        self._emit_event(("progress", 0, watched_count, max(watched_count, self.total_loops)))

                        if self.stop_event.is_set():
                            break

                        if watch_res.interrupted:
                            remaining_channels = self.get_target_channels()
                            if not remaining_channels:
                                emit(self.log_queue, "All target channels have reached their targets or are disabled. Stopping browsing session.")
                                break
                            reason_desc = "target watch time was achieved" if watch_res.reason == "channel_target_achieved" else "channel was disabled/unchecked"
                            emit(self.log_queue, f"Channel '{ch_display}' {reason_desc}. Continuing browsing for remaining {len(remaining_channels)} target channel(s)...")
                            continue

                        # Option 5: Interleave a 30-90s non-target video before next target video
                        self._after_target_video_watched(session_watched_keys, watched_count)
                        continue

                    # If no target video found in suggestions:
                    # Open a random video from suggested list, watch for 30-90s without interaction,
                    # and try to find the video of the targeted channel. Repeat up to 3 times.
                    emit(
                        self.log_queue,
                        "Target channel video not found in suggestions. Starting random suggested video fallback (up to 3 attempts)...",
                    )
                    found_target_via_random = False

                    for random_attempt in range(1, 4):
                        if self.stop_event.is_set():
                            break
                        self.check_pause()
                        if self.stop_event.is_set():
                            break

                        random_card = self._find_random_suggested_video(
                            rec_candidates, getattr(self.driver, "current_url", "") or "", session_watched_keys
                        )
                        if not random_card:
                            emit(
                                self.log_queue,
                                f"No suggested videos found to pick random video (attempt {random_attempt}/3).",
                                logging.WARNING,
                            )
                            break

                        random_dur = round(random.uniform(30.0, 90.0), 1)
                        random_title = random_card.get("title") or "Suggested Video"
                        emit(
                            self.log_queue,
                            f"Attempt {random_attempt}/3: Watching random suggested video '{random_title}' for {random_dur:.0f}s...",
                        )

                        watched_ok = self._watch_random_video(random_card, random_dur, session_watched_keys)
                        if not watched_ok or self.stop_event.is_set():
                            break

                        # We are now on the random video's watch page!
                        # "and then try to find the video of the targeted channel"
                        emit(
                            self.log_queue,
                            f"Inspecting suggestions on random video page for target channel (attempt {random_attempt}/3)...",
                        )
                        rec_candidates = self.fetch_recommended_videos()

                        # If multiple channels exist, we still want a channel other than last_watched_channel (e.g. Channel B)
                        # If only 1 target channel exists, since a random video intervened, it is allowed now
                        allowed_now = self._get_allowed_target_channels(
                            self.get_target_channels(), last_watched_channel, allow_same_channel=True
                        )
                        sub_target = self._find_target_video(rec_candidates, allowed_now, session_watched_keys)
                        if sub_target:
                            item, match_obj, full_url, title, ch_display = sub_target
                            emit(
                                self.log_queue,
                                f"Found target channel video after random video attempt {random_attempt}/3:",
                            )
                            watch_res = self._play_target_video(
                                item, full_url, title, ch_display, session_watched_keys, is_home=False
                            )
                            last_watched_channel = ch_display
                            watched_count += 1
                            self._emit_event(("progress", 0, watched_count, max(watched_count, self.total_loops)))
                            found_target_via_random = True

                            if self.stop_event.is_set():
                                break

                            if watch_res.interrupted:
                                remaining_channels = self.get_target_channels()
                                if not remaining_channels:
                                    emit(self.log_queue, "All target channels have reached their targets or are disabled. Stopping browsing session.")
                                    break
                                reason_desc = "target watch time was achieved" if watch_res.reason == "channel_target_achieved" else "channel was disabled/unchecked"
                                emit(self.log_queue, f"Channel '{ch_display}' {reason_desc}. Continuing browsing for remaining {len(remaining_channels)} target channel(s)...")
                                break

                            # Option 5: Interleave a 30-90s non-target video before next target video
                            self._after_target_video_watched(session_watched_keys, watched_count)
                            break
                        else:
                            emit(
                                self.log_queue,
                                f"Target channel video not found in suggestions after random video attempt {random_attempt}/3.",
                            )

                    if self.stop_event.is_set():
                        break

                    if found_target_via_random:
                        continue

                    # If still no video found after 3 random attempts:
                    emit(
                        self.log_queue,
                        "No target channel video found in suggestions after 3 random video attempts. Going to YouTube Home page...",
                    )

                # -------------------------------------------------------------
                # Step 2: YouTube Home Feed Discovery (with 3 refresh attempts)
                # -------------------------------------------------------------
                found_home_target = False

                for home_attempt in range(1, 4):
                    if self.stop_event.is_set():
                        break
                    self.check_pause()
                    if self.stop_event.is_set():
                        break

                    current_channels = self.get_target_channels()
                    if not current_channels:
                        emit(self.log_queue, "No active target channels found. Stopping watching session.", logging.WARNING)
                        break

                    # Enforce channel alternation: if multiple channels exist, do not pick last_watched_channel
                    allowed_home = self._get_allowed_target_channels(
                        current_channels, last_watched_channel, allow_same_channel=True
                    )

                    current_url = getattr(self.driver, "current_url", "") or ""
                    if home_attempt == 1:
                        if not is_clean_youtube_home_url(current_url):
                            emit(self.log_queue, "Navigating to clean YouTube Home (https://www.youtube.com/)...")
                            safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                            human_like_delay(2.0, 3.5)
                    else:
                        emit(self.log_queue, f"Refreshing YouTube Home page (attempt {home_attempt}/3)...")
                        safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                        human_like_delay(2.0, 3.5)

                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                    emit(self.log_queue, f"Waiting for YouTube Home videos to load (attempt {home_attempt}/3)...")
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
                    human_like_delay(1.0, 1.5)

                    emit(self.log_queue, f"Scanning YouTube Home feed for target channel (attempt {home_attempt}/3)...")
                    matched_home_item: Optional[Tuple[Dict[str, Any], Channel, str, str, str]] = None
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
                            batch = []
                            for item in raw_items:
                                href = item.get("href", "")
                                if not href:
                                    continue
                                clean_href = href.split("&")[0]
                                if clean_href in seen_clean_urls:
                                    continue
                                seen_clean_urls.add(clean_href)
                                batch.append(item)

                            matched_home_item = self._find_target_video(batch, allowed_home, session_watched_keys)
                            if matched_home_item:
                                break

                        scroll_step = random.randint(500, 850)
                        try:
                            self.driver.execute_script(f"window.scrollBy({{top: {scroll_step}, behavior: 'smooth'}});")
                        except Exception:
                            self.driver.execute_script(f"window.scrollBy(0, {scroll_step});")

                        human_like_delay(1.3, 2.2)

                    if self.stop_event.is_set():
                        break

                    if matched_home_item:
                        item, match_obj, full_url, title, ch_display = matched_home_item
                        emit(self.log_queue, "Found target channel video on Home page:")
                        watch_res = self._play_target_video(
                            item, full_url, title, ch_display, session_watched_keys, is_home=True
                        )
                        last_watched_channel = ch_display
                        watched_count += 1
                        self._emit_event(("progress", 0, watched_count, max(watched_count, self.total_loops)))
                        found_home_target = True

                        if self.stop_event.is_set():
                            break

                        if watch_res.interrupted:
                            remaining_channels = self.get_target_channels()
                            if not remaining_channels:
                                emit(self.log_queue, "All target channels have reached their targets or are disabled. Stopping browsing session.")
                                break
                            reason_desc = "target watch time was achieved" if watch_res.reason == "channel_target_achieved" else "channel was disabled/unchecked"
                            emit(self.log_queue, f"Channel '{ch_display}' {reason_desc}. Continuing browsing for remaining {len(remaining_channels)} target channel(s)...")
                            continue

                        # Option 5: Interleave a 30-90s non-target video before next target video
                        self._after_target_video_watched(session_watched_keys, watched_count)
                        break
                    else:
                        emit(
                            self.log_queue,
                            f"No video matching target channels found on YouTube Home (attempt {home_attempt}/3).",
                            logging.WARNING,
                        )

                if self.stop_event.is_set():
                    break

                if not found_home_target:
                    # "and still no video is found then stop the script and update the log"
                    stop_msg = "No video of targeted channel found on YouTube Home after 3 refresh attempts. Stopping watching session."
                    emit(self.log_queue, stop_msg, logging.WARNING)
                    self._report_status("Stopped: No target videos found on Home page", "warning")
                    self.stop_event.set()
                    break

                continue

            summary = f"Watching session finished: watched {watched_count} video(s)."
            emit(self.log_queue, summary)
            self._report_status(summary, "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during watching session: {exc}", logging.ERROR)
            self._report_status(f"Watching error: {exc}", "error")
        finally:
            if hasattr(self, "channel_tracker"):
                self.channel_tracker.on_stop()
            close_driver(self.driver, log_queue=self.log_queue)

    def stop(self) -> None:
        """Stop the continuous watching loop and all running tasks."""
        self.stop_event.set()
        if hasattr(self, "channel_tracker"):
            self.channel_tracker.on_stop()
