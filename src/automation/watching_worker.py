from datetime import datetime

from .email_reporter import EmailReporter, SessionReport
from .session_tracker import SessionChannelTracker

"""Watching Worker.

Manages autonomous video discovery and continuous chained playback:
1. Google sign-in.
2. Home page feed inspection: scans ~20 videos and matches against stored channels.
3. Clicks the first matching video and watches it naturally with ad skipping and human interactivity.
4. When the video ends / approaches end, inspects recommended videos for stored channels.
5. Clicks the matching recommended video and continues watching until stopped.
"""

import logging
import queue
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, parse_channel_identity
from ..models.video_task import (
    DEFAULT_MAX_RUNS,
    DEFAULT_SHORTS_MAX_RUNS,
    DEFAULT_TARGET_MULTIPLIER,
    VideoTask,
)
from ..models.watch_stats import TaskOutcome, WatchResult, resolve_video_key
from ..utils.helpers import extract_video_id, human_like_delay
from ..utils.logger import emit
from .driver import close_driver, setup_driver
from .login import perform_google_login
from .navigation import (
    dismiss_youtube_dialogs,
    find_and_click_search_result,
    perform_youtube_search,
    safe_get,
)
from .youtube_actions import (
    DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS,
    click_element,
    ensure_video_playing,
    watch_short,
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



HOME_SHORTS_JS = r"""
return (function() {
    var results = [];
    var seenIds = {};

    function extractVideoId(href) {
        if (!href) return '';
        var m = href.match(/\/shorts\/([A-Za-z0-9_\-]{11})/);
        return m ? m[1] : '';
    }

    var shelves = document.querySelectorAll(
        'ytd-rich-shelf-renderer[is-shorts], ytd-rich-section-renderer ytd-rich-shelf-renderer, ytd-reel-shelf-renderer'
    );

    var cardElements = [];
    if (shelves.length > 0) {
        for (var s = 0; s < shelves.length; s++) {
            var items = shelves[s].querySelectorAll(
                'ytd-rich-item-renderer, ytd-reel-item-renderer, ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model'
            );
            for (var i = 0; i < items.length; i++) {
                cardElements.push(items[i]);
            }
        }
    }

    var directLockups = document.querySelectorAll(
        'ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, ytd-rich-item-renderer[is-slim-media]'
    );
    for (var j = 0; j < directLockups.length; j++) {
        cardElements.push(directLockups[j]);
    }

    for (var idx = 0; idx < cardElements.length; idx++) {
        var card = cardElements[idx];
        if (!card) continue;

        if (card.closest && card.closest('ytd-ad-slot-renderer, [layout*="ad"]')) continue;

        var linkEl = card.querySelector(
            'a.shortsLockupViewModelHostEndpoint, a.reel-item-endpoint, a[href*="/shorts/"]'
        );
        if (!linkEl && card.tagName === 'A' && (card.getAttribute('href') || '').indexOf('/shorts/') !== -1) {
            linkEl = card;
        }
        if (!linkEl) continue;

        var href = linkEl.getAttribute('href') || '';
        var vidId = extractVideoId(href);
        if (!vidId || seenIds[vidId]) continue;
        seenIds[vidId] = true;

        var titleEl = card.querySelector(
            'h3.shortsLockupViewModelHostMetadataTitle a, h3 a, a.shortsLockupViewModelHostOutsideMetadataEndpoint, h3, #video-title'
        );
        var title = '';
        if (titleEl) {
            title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
        }
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }

        var viewsEl = card.querySelector(
            '.shortsLockupViewModelHostOutsideMetadataSubhead, #metadata-line, .ytContentMetadataViewModelMetadataRow'
        );
        var viewsText = viewsEl ? (viewsEl.textContent || '').trim() : '';

        results.push({
            index: idx,
            href: href,
            video_id: vidId,
            url: href.startsWith('http') ? href : ('https://www.youtube.com' + href),
            title: title || ('Short ' + vidId),
            views: viewsText
        });
    }

    return results;
})();
"""


GET_ACTIVE_SHORT_INFO_JS = r"""
return (function() {
    var activeReel = document.querySelector('ytd-reel-video-renderer[is-active], ytd-reel-video-renderer.active');
    var scope = activeReel || document;

    var channelName = '';
    var channelUrl = '';

    // 1. Channel link in modern reel channel bar or header
    var chLink = scope.querySelector(
        'yt-reel-channel-bar-view-model .ytReelChannelBarViewModelChannelName a, ' +
        'yt-reel-channel-bar-view-model a[href*="/@"], ' +
        'yt-reel-channel-bar-view-model a[href*="/channel/"], ' +
        '.ytReelMetapanelViewModelMetapanelItem a[href*="/@"], ' +
        '.ytReelMetapanelViewModelMetapanelItem a[href*="/channel/"], ' +
        'a.ytAttributedStringLink[href*="/@"], ' +
        'ytd-reel-player-header-renderer ytd-channel-name a, ' +
        'ytd-reel-player-header-renderer a[href*="/@"], ' +
        'ytd-reel-player-header-renderer a[href*="/channel/"], ' +
        'ytd-channel-name a, #channel-name a, a[href*="/@"], a[href*="/channel/"]'
    );

    if (chLink) {
        channelName = (chLink.textContent || '').trim();
        channelUrl = chLink.getAttribute('href') || '';
    }

    // 2. Avatar or button with "Go to channel @handle" aria-label
    if (!channelName) {
        var avatarEl = scope.querySelector('.ytSpecAvatarShapeHost[aria-label*="Go to channel" i], [aria-label*="Go to channel" i]');
        if (avatarEl) {
            var aLabel = avatarEl.getAttribute('aria-label') || '';
            var match = aLabel.match(/Go to channel\s+(@?[^\s]+)/i);
            if (match && match[1]) {
                channelName = match[1].trim();
                if (!channelUrl) {
                    channelUrl = channelName.startsWith('/') ? channelName : ('/' + channelName);
                }
            }
        }
    }

    // 3. Fallback text element
    if (!channelName) {
        var chEl = scope.querySelector(
            'yt-reel-channel-bar-view-model .ytReelChannelBarViewModelChannelName, ' +
            'ytd-channel-name #text, ytd-channel-name, #channel-name'
        );
        if (chEl) {
            channelName = (chEl.textContent || '').trim();
        }
    }

    if (!channelUrl && channelName && channelName.indexOf('@') !== -1) {
        channelUrl = channelName.startsWith('/') ? channelName : ('/' + channelName);
    }

    var titleEl = scope.querySelector(
        'ytd-reel-player-header-renderer h2, h2.title, #video-title, .title.ytd-reel-player-header-renderer, .ytReelMetapanelViewModelMetapanelItem h2'
    );
    var title = titleEl ? (titleEl.textContent || '').trim() : '';

    return {
        found: true,
        channel_name: channelName,
        channel_url: channelUrl,
        title: title,
        url: window.location.href
    };
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
    """Normalize text for channel matching (lowercase, unicode alphanumeric characters).
    Preserves unicode word characters so non-Latin names are never collapsed to empty strings.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    return re.sub(r"[^\w]", "", raw.lower())


def match_channel(
    scanned_name: str,
    scanned_url: str,
    channels: Sequence[Channel],
    fuzzy_threshold: float = 0.80,
) -> Optional[Channel]:
    """Check if scanned video's channel matches any channel stored in the app.

    Enforces strict, unambiguous identity matching:
    1. Rejects disabled and archived channels.
    2. Verified explicit channel identity (channel ID, handle, custom, user) takes precedence.
    3. Compares complete identities: no substring or fuzzy matching.
    4. When comparable identities conflict, returns None even if display names match.
    5. Different identity forms (handle vs channel ID) remain unmatched without verified equivalence.
    6. If usable identity is unavailable, permits only conservative, unambiguous exact-name fallback.
    7. Ambiguous display-name matches (>1 stored channel matching) return None.
    """
    clean_scanned_name = (scanned_name or "").strip()
    norm_scanned_name = normalize_channel_text(clean_scanned_name)
    scanned_type, scanned_val = parse_channel_identity(scanned_url)

    eligible = [
        ch for ch in channels
        if ch.enabled and not ch.archived and ((ch.name and ch.name.strip()) or (ch.url and ch.url.strip()))
    ]
    if not eligible:
        return None

    # Step 1: If scanned has a parsed identity, look for explicit identity match
    if scanned_type and scanned_val:
        identity_matches = []
        for ch in eligible:
            ch_type, ch_val = parse_channel_identity(ch.url)
            if ch_type and ch_val:
                if ch_type == scanned_type:
                    # Comparable identities: exact complete match required
                    if ch_type == "id":
                        matched = (ch_val == scanned_val)
                    else:
                        matched = (ch_val.lower() == scanned_val.lower())
                    if matched:
                        identity_matches.append(ch)
        if identity_matches:
            return identity_matches[0]

        # Scanned video has a specific identity, but none of the stored channels matched that identity.
        # Requirement 5: Conflicting comparable identities CANNOT match by name.
        # Requirement 6: Unverified different identity forms (e.g. handle vs ID) CANNOT match by name.
        # Only stored channels with NO URL (usable identity unavailable) can qualify for name fallback:
        if norm_scanned_name:
            name_candidates = []
            for ch in eligible:
                ch_type, ch_val = parse_channel_identity(ch.url)
                if not ch_type and not ch_val:
                    norm_ch = normalize_channel_text(ch.name)
                    if norm_ch and norm_ch == norm_scanned_name:
                        name_candidates.append(ch)
            if len(name_candidates) == 1:
                return name_candidates[0]
        return None

    # Step 2: Scanned video has NO usable identity (scanned_url has no parsed identity).
    # Conservative, unambiguous exact display-name fallback.
    if not norm_scanned_name:
        return None

    name_matches = []
    for ch in eligible:
        norm_ch = normalize_channel_text(ch.name)
        if norm_ch and norm_ch == norm_scanned_name:
            name_matches.append(ch)

    # Requirement 11: If more than one stored channel could match a name-only candidate, return no match.
    if len(name_matches) == 1:
        return name_matches[0]

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
        total_loops: Optional[int] = None,
        target_limit: Optional[int] = None,
        is_unlimited: bool = False,
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

        if is_unlimited:
            self.is_unlimited = True
            self.target_limit = None
        elif target_limit is not None:
            self.is_unlimited = False
            self.target_limit = max(1, target_limit)
        elif total_loops is not None:
            self.is_unlimited = False
            self.target_limit = max(1, total_loops)
        else:
            unl = self.config_mgr.get_bool("BrowsingUnlimited", True) if self.config_mgr else True
            if unl:
                self.is_unlimited = True
                self.target_limit = None
            else:
                lim = self.config_mgr.get_int("BrowsingTargetLimit", 1) if self.config_mgr else 1
                self.is_unlimited = False
                self.target_limit = max(1, lim)

        self.total_loops = self.target_limit if not self.is_unlimited else 1
        self.target_attempts = 0
        self.watched_count = 0
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
        self.session_start_time = datetime.now()
        stamp = self.session_start_time.strftime("%d %b %Y - %I:%M %p")
        self.session_thread_subject = f"Entertainment Hub Session [{stamp}] - {getattr(self.account, 'email', '')}"
        self.session_thread_url: Optional[str] = None
        self.session_thread_message_id: Optional[str] = None
        self.session_thread_info: Dict[str, Any] = {}
        self.reporting_driver: Optional[Any] = None
        self.reporting_thread: Optional[threading.Thread] = None

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

        if self.config_mgr is not None:
            try:
                stored_dicts = self.config_mgr.get_channels()
                if stored_dicts is not None:
                    stored_objs = [Channel.from_dict(c) for c in stored_dicts]
                    return [c for c in stored_objs if _is_active(c)]
            except Exception as exc:
                emit(self.log_queue, f"Failed to read channels from configuration: {exc}", logging.ERROR)
                return []
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
        runs: Optional[int] = None,
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
                runs=runs,
            )
            self.stats_store.record(outcome)
        except Exception as exc:
            emit(self.log_queue, f"Could not record watching stats: {exc}", logging.WARNING)

    def _ensure_video_in_queue(self, task: VideoTask) -> VideoTask:
        """Register the video in settings.json video queue atomically and emit UI event."""
        try:
            vid_key = resolve_video_key(task, task.url)
            matched_existing: List[VideoTask] = []

            def _updater(raw_queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                for item in raw_queue:
                    existing_t = VideoTask.from_dict(item)
                    if resolve_video_key(existing_t, existing_t.url) == vid_key:
                        if not existing_t.channel and task.channel:
                            existing_t.channel = task.channel
                            item["channel"] = task.channel
                        if existing_t.max_runs == 0 and task.max_runs > 0:
                            existing_t.max_runs = task.max_runs
                            item["max_runs"] = task.max_runs
                        if getattr(existing_t, "target_multiplier", 0.0) == 0.0 and getattr(task, "target_multiplier", 0.0) > 0.0:
                            existing_t.target_multiplier = task.target_multiplier
                            item["target_multiplier"] = task.target_multiplier
                        matched_existing.append(existing_t)
                        return raw_queue

                raw_queue.append(task.to_dict())
                return raw_queue

            self.config_mgr.update_video_queue(_updater)
            if matched_existing:
                res_task = matched_existing[0]
                self._emit_event(("add_video_task", res_task.to_dict()))
                return res_task

            self._emit_event(("add_video_task", task.to_dict()))
            return task
        except Exception as exc:
            emit(self.log_queue, f"Could not add video to queue: {exc}", logging.WARNING)
            return task

    def _update_task_duration(self, task: VideoTask, duration_seconds: float) -> None:
        """Update video duration text and seconds in settings.json video queue atomically."""
        if duration_seconds <= 0:
            return
        try:
            vid_key = resolve_video_key(task, task.url)

            def _updater(raw_queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                for v in raw_queue:
                    if resolve_video_key(VideoTask.from_dict(v), v.get("url", "")) == vid_key:
                        v["duration_seconds"] = int(duration_seconds)
                        m, s = divmod(int(duration_seconds), 60)
                        h, m = divmod(m, 60)
                        v["duration_text"] = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                        break
                return raw_queue

            self.config_mgr.update_video_queue(_updater)
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
                try:
                    current_runs = int(getattr(rec, "runs", 0) or 0)
                except (ValueError, TypeError):
                    current_runs = 0
                try:
                    watch_seconds = float(getattr(rec, "watch_seconds", 0.0) or 0.0)
                except (ValueError, TypeError):
                    watch_seconds = 0.0
                try:
                    video_duration = float(getattr(rec, "video_duration", 0.0) or 0.0)
                except (ValueError, TypeError):
                    video_duration = 0.0

        cooldown_hours = self.config_mgr.get_float("VideoCooldownHours", 12.0)

        if existing_task is not None:
            # If manually disabled in queue
            if not existing_task.enabled:
                return False, existing_task, "disabled in video queue"
            if existing_task.channel:
                matching_ch = [c for c in self.get_target_channels() if c.name.lower() == existing_task.channel.lower()]
                if not matching_ch or not matching_ch[0].enabled or matching_ch[0].archived:
                    return False, existing_task, f"channel '{existing_task.channel}' is disabled or not active"

            raw_task_dur = getattr(existing_task, "duration_seconds", 0.0)
            try:
                task_dur = float(raw_task_dur or 0.0)
            except (ValueError, TypeError):
                task_dur = 0.0
            eff_dur = video_duration if video_duration > 0 else task_dur
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

    def _find_preferred_target_video(
        self,
        candidates: List[Dict[str, Any]],
        target_channels: Sequence[Channel],
        last_channel_name: Optional[str],
        session_watched_keys: Set[str],
        channel_watch_counts: Optional[Dict[str, int]] = None,
        channel_consecutive_counts: Optional[Dict[str, int]] = None,
        **kwargs,
    ) -> Optional[Tuple[Dict[str, Any], Channel, str, str, str]]:
        """Search candidate video cards for an eligible target channel video,
        prioritizing alternating between different target channels.

        1. If multiple target channels exist and last_channel_name is set, first search for
           videos from any OTHER target channel.
        2. If no other channel video is found, fall back to the current channel provided its
           watch countdown hasn't reached 3.
        3. If only one channel exists, allow it provided its countdown hasn't reached 3.
        """
        active = [c for c in target_channels if c.enabled and not c.archived]
        if not active:
            return None

        counts = channel_watch_counts if channel_watch_counts is not None else (channel_consecutive_counts or {})

        if last_channel_name:
            last_canon = SessionChannelTracker._canon_name(last_channel_name)
            last_count = counts.get(last_canon, 0)

            if len(active) > 1:
                # Priority A: Check other target channels first
                other_channels = [c for c in active if SessionChannelTracker._canon_name(c.name) != last_canon]
                match_other = self._find_target_video(candidates, other_channels, session_watched_keys)
                if match_other:
                    return match_other

                # Priority B: Fall back to current channel if countdown < 3
                if last_count < 3:
                    same_channels = [c for c in active if SessionChannelTracker._canon_name(c.name) == last_canon]
                    return self._find_target_video(candidates, same_channels, session_watched_keys)
                else:
                    return None
            else:
                # Single target channel
                if last_count < 3:
                    return self._find_target_video(candidates, active, session_watched_keys)
                else:
                    return None

        return self._find_target_video(candidates, active, session_watched_keys)

    def _check_and_play_random_buffer_video(
        self,
        session_watched_keys: Set[str],
        consecutive_target_watched: int = 0,
        random_buffer_threshold: int = 3,
        channel_watch_counts: Optional[Dict[str, int]] = None,
        channel_consecutive_counts: Optional[Dict[str, int]] = None,
        **kwargs,
    ) -> bool:
        """Check if random non-target buffer video condition is met and watch it if so.

        Triggers when:
        1. consecutive_target_watched >= random_buffer_threshold (randomly 1-3 target videos watched), OR
        2. Any individual channel countdown reaches 3 videos (non-consecutive, accumulated since last buffer).
        """
        if self.stop_event.is_set():
            return False

        counts = channel_watch_counts if channel_watch_counts is not None else (channel_consecutive_counts or {})
        exceeded_channel = None
        for ch_canon, count in counts.items():
            if count >= 3:
                exceeded_channel = ch_canon
                break

        should_buffer = (consecutive_target_watched >= random_buffer_threshold) or (exceeded_channel is not None)
        if not should_buffer:
            return False

        if exceeded_channel:
            emit(
                self.log_queue,
                f"Channel countdown reached 3 video(s) for '{exceeded_channel}'. Triggering non-target random buffer video...",
            )
        else:
            emit(
                self.log_queue,
                f"Reached target watch threshold ({consecutive_target_watched}/{random_buffer_threshold} target video(s)). Triggering non-target random buffer video...",
            )

        active_channels = self.get_target_channels()
        num_channels = len(active_channels)
        if num_channels >= 3:
            random_dur = round(random.uniform(60.0, 120.0), 1)
        elif num_channels == 2:
            random_dur = round(random.uniform(45.0, 100.0), 1)
        else:
            random_dur = round(random.uniform(30.0, 90.0), 1)

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
            emit(
                self.log_queue,
                "Could not find a non-target video for random buffer. Proceeding directly.",
                logging.WARNING,
            )
            return False

        random_title = random_card.get("title") or "Non-Target Video"
        emit(
            self.log_queue,
            f"\n=== Watching Non-Target Buffer Video ({random_dur:.0f}s): '{random_title}' (No interactions) ===",
        )
        watched_ok = self._watch_random_video(random_card, random_dur, session_watched_keys)
        if watched_ok:
            emit(
                self.log_queue,
                "Finished non-target buffer video. Resuming targeted browsing cycle...\n",
            )
        return watched_ok

    def _scan_home_feed_for_target(
        self,
        session_watched_keys: Set[str],
        last_watched_channel: Optional[str] = None,
        channel_watch_counts: Optional[Dict[str, int]] = None,
        channel_consecutive_counts: Optional[Dict[str, int]] = None,
        max_attempts: int = 3,
        **kwargs,
    ) -> Optional[Tuple[Dict[str, Any], Channel, str, str, str]]:
        """Scan YouTube Home feed for an eligible target channel video up to max_attempts."""
        for home_attempt in range(1, max_attempts + 1):
            if self.stop_event.is_set():
                return None
            self.check_pause()
            if self.stop_event.is_set():
                return None

            current_channels = self.get_target_channels()
            if not current_channels:
                emit(self.log_queue, "No active target channels found. Stopping watching session.", logging.WARNING)
                return None

            current_url = getattr(self.driver, "current_url", "") or ""
            if not is_clean_youtube_home_url(current_url):
                emit(self.log_queue, "Navigating to clean YouTube Home (https://www.youtube.com/)...")
                safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                human_like_delay(2.0, 3.5)

            emit(self.log_queue, f"Scanning YouTube Home feed (attempt {home_attempt}/{max_attempts})...")
            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

            matched_home_item: Optional[Tuple[Dict[str, Any], Channel, str, str, str]] = None
            seen_clean_urls: Set[str] = set()

            max_scrolls = 15
            for scroll_idx in range(max_scrolls):
                if self.stop_event.is_set():
                    return None
                self.check_pause()
                if self.stop_event.is_set():
                    return None

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

                    counts = channel_watch_counts if channel_watch_counts is not None else (channel_consecutive_counts or {})
                    matched_home_item = self._find_preferred_target_video(
                        candidates=batch,
                        target_channels=current_channels,
                        last_channel_name=last_watched_channel,
                        session_watched_keys=session_watched_keys,
                        channel_watch_counts=counts,
                        channel_consecutive_counts=counts,
                    )
                    if matched_home_item:
                        return matched_home_item

                scroll_step = random.randint(500, 850)
                try:
                    self.driver.execute_script(f"window.scrollBy({{top: {scroll_step}, behavior: 'smooth'}});")
                except Exception:
                    self.driver.execute_script(f"window.scrollBy(0, {scroll_step});")

                human_like_delay(1.3, 2.0)

            emit(
                self.log_queue,
                f"No video matching target channels found on YouTube Home (attempt {home_attempt}/{max_attempts}).",
                logging.INFO,
            )

        return None

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

        if not fresh:
            return None
        return random.choice(fresh)

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

    def _is_target_limit_reached(self, target_attempts: int) -> bool:
        if self.is_unlimited:
            return False
        if self.target_limit is None:
            return False
        return target_attempts >= self.target_limit

    def _after_target_video_watched(
        self,
        session_watched_keys: Set[str],
        target_attempts: int,
    ) -> bool:
        """Execute Option 5: watch a non-target suggested video (30-90s) before seeking the next target video."""
        if self.stop_event.is_set():
            return False
        if self._is_target_limit_reached(target_attempts):
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

        if hasattr(self, "channel_tracker") and self.channel_tracker and (watch_res.watched_seconds > 0 or watch_res.completed):
            self.channel_tracker.record_video_watched(is_short=False, channel_name=None, source="suggested", is_random=True)

        return not self.stop_event.is_set()

    def _play_target_video(
        self,
        card_info: Dict[str, Any],
        full_url: str,
        title: str,
        channel_display: str,
        session_watched_keys: Set[str],
        is_home: bool = False,
        source: Optional[str] = None,
        is_random: bool = False,
    ) -> WatchResult:
        """Play and track a target channel video."""
        emit(
            self.log_queue,
            f"URL: {full_url}\nTitle: {title}\nChannel: {channel_display}",
        )

        inherited_mult = DEFAULT_TARGET_MULTIPLIER
        inherited_max_runs = DEFAULT_MAX_RUNS
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
        cur_id = extract_video_id(cur_url)
        if target_vid_id and cur_id != target_vid_id:
            emit(self.log_queue, f"Navigation not detected after click, navigating directly to: {full_url}", logging.INFO)
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)

        try:
            if target_vid_id:
                WebDriverWait(self.driver, 15).until(lambda d: extract_video_id(getattr(d, "current_url", "") or "") == target_vid_id)
            else:
                WebDriverWait(self.driver, 15).until(lambda d: "/watch" in (getattr(d, "current_url", "") or ""))
        except Exception:
            pass

        final_cur_url = getattr(self.driver, "current_url", "") or ""
        final_cur_id = extract_video_id(final_cur_url)
        if target_vid_id and final_cur_id != target_vid_id:
            emit(
                self.log_queue,
                f"Playback identity verification failed: expected video ID '{target_vid_id}', observed '{final_cur_id or final_cur_url}'. Aborting playback.",
                logging.ERROR,
            )
            return WatchResult(completed=False, reason="navigation_identity_mismatch", interrupted=True)

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

        task_url = (registered_task.url or full_url or "").strip()
        task_vid_id = target_vid_id or (extract_video_id(task_url) if task_url else "")
        task_key = resolve_video_key(registered_task, full_url)
        task_label = (registered_task.title or title or "").strip()

        def _should_stop(ch_name=channel_display):
            # 1. Check video task controls from config_mgr queue
            latest_task: Optional[VideoTask] = None
            if self.config_mgr is not None:
                try:
                    current_queue = self.config_mgr.get_video_queue()
                except Exception as exc:
                    emit(self.log_queue, f"Control state read error (video queue): {exc}", logging.ERROR)
                    return "control_read_failed"

                matched_item = None
                for item in current_queue:
                    if not isinstance(item, dict):
                        continue
                    item_url = (item.get("url") or "").strip()
                    if task_vid_id:
                        if extract_video_id(item_url) == task_vid_id:
                            matched_item = item
                            break
                    elif task_url:
                        if item_url == task_url:
                            matched_item = item
                            break
                    else:
                        t_temp = VideoTask.from_dict(item)
                        if resolve_video_key(t_temp) == task_key:
                            matched_item = item
                            break
                        t_label = (t_temp.title or t_temp.keywords or "").strip()
                        if task_label and t_label.lower() == task_label.lower():
                            matched_item = item
                            break

                if matched_item is None:
                    emit(self.log_queue, f"Video '{task_label or task_url}' was removed from queue. Stopping playback.")
                    return "task_removed"

                latest_task = VideoTask.from_dict(matched_item)
                if not latest_task.enabled:
                    emit(self.log_queue, f"Video '{latest_task.title or task_label}' was unchecked/disabled. Stopping playback.")
                    return "task_disabled"

                latest_max_runs = latest_task.max_runs
                latest_multiplier = latest_task.target_multiplier

                if self.stats_store is not None:
                    rec = self.stats_store.find_for_task(latest_task)
                    if rec is None and registered_task is not None:
                        rec = self.stats_store.find_for_task(registered_task)
                    current_runs = rec.runs if rec is not None else 0
                    prev_watched = rec.watch_seconds if rec else 0.0
                    curr_watch = self.channel_tracker.get_active_watch_delta() if hasattr(self, "channel_tracker") else 0.0
                    total_watched = prev_watched + curr_watch
                    eff_dur = (rec.video_duration if rec and rec.video_duration > 0 else 0.0) or getattr(latest_task, "duration_seconds", 0.0)

                    if latest_max_runs > 0 and current_runs >= latest_max_runs:
                        emit(self.log_queue, f"Video '{latest_task.title or task_label}' reached max runs limit ({current_runs}/{latest_max_runs}). Stopping playback.")
                        return "task_limit_reached"

                    if latest_multiplier > 0.0 and eff_dur > 0.0:
                        if total_watched >= latest_multiplier * eff_dur:
                            emit(self.log_queue, f"Video '{latest_task.title or task_label}' reached target multiplier ({latest_multiplier:.2f}x). Stopping video playback.")
                            return "video_target_achieved"

            # 2. Check channel status
            current_ch_name = (latest_task.channel if latest_task else ch_name) or ch_name
            if current_ch_name:
                if hasattr(self, "channel_tracker") and self.channel_tracker.is_channel_target_achieved(current_ch_name):
                    return "channel_target_achieved"
                active_names = {c.name.lower() for c in self.get_target_channels()}
                if current_ch_name.lower() not in active_names:
                    return "channel_disabled"

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
        if hasattr(self, "channel_tracker") and self.channel_tracker and (actual_watched > 0 or watch_res.completed):
            is_s = getattr(registered_task, "is_short", False) or ("/shorts/" in (getattr(registered_task, "url", "") or str(getattr(self.driver, "current_url", "")) or ""))
            resolved_source = source or ("home" if is_home else "suggested")
            target_names = {c.name.strip().lower() for c in self.get_target_channels() if c.name}
            is_rand = is_random or (channel_display.strip().lower() not in target_names if channel_display else True)
            self.channel_tracker.record_video_watched(
                is_short=is_s,
                channel_name=channel_display,
                source=resolved_source,
                is_random=is_rand,
            )
        emit(
            self.log_queue,
            f"Updated video time for '{title}': watched {actual_watched:.1f}s (duration: {actual_dur:.1f}s, completed={watch_res.completed}).",
        )
        return watch_res


    def fetch_home_shorts(self) -> List[Dict[str, Any]]:
        """Extract candidate shorts from YouTube Home page shelves."""
        try:
            items = self.driver.execute_script(HOME_SHORTS_JS)
            return items if isinstance(items, list) else []
        except Exception as exc:
            emit(self.log_queue, f"Error fetching home shorts: {exc}", logging.DEBUG)
            return []

    def get_target_shorts(self) -> List[VideoTask]:
        """Retrieve all enabled target shorts configured in the app."""
        target_shorts: List[VideoTask] = []
        if self.config_mgr:
            try:
                raw_queue = self.config_mgr.get_video_queue()
                for item in raw_queue:
                    if not isinstance(item, dict):
                        continue
                    task = VideoTask.from_dict(item)
                    if task.enabled and (task.is_short or "/shorts/" in (task.url or "")):
                        target_shorts.append(task)
            except Exception as exc:
                emit(self.log_queue, f"Error loading target shorts from queue: {exc}", logging.DEBUG)
        return target_shorts

    def match_home_short(
        self,
        candidate_shorts: List[Dict[str, Any]],
        target_shorts: List[VideoTask],
        session_watched_keys: Optional[Set[str]] = None,
    ) -> Optional[Tuple[Dict[str, Any], VideoTask]]:
        """
        Match only one Short at a time by inspecting the first Short in the list of candidate shorts
        and matching its URL / video ID against the Shorts in our Shorts queue.
        Returns (first_short, matched_task) if there is a match, otherwise None.
        """
        if not candidate_shorts or not target_shorts:
            return None

        first_short = candidate_shorts[0]
        cand_id = first_short.get("video_id") or extract_video_id(first_short.get("href", ""))
        cand_url = first_short.get("url") or first_short.get("href", "")

        if not cand_id and not cand_url:
            return None

        for t in target_shorts:
            task_id = extract_video_id(t.url)
            if task_id and cand_id and task_id == cand_id:
                return (first_short, t)
            if cand_url and t.url and cand_url.split("?")[0] == t.url.split("?")[0]:
                return (first_short, t)

        return None

    def click_home_short(self, short_info: Dict[str, Any]) -> bool:
        """Click on the target short card on the home page and verify navigation."""
        href = short_info.get("href", "")
        vid_id = short_info.get("video_id") or extract_video_id(href)
        try:
            if vid_id:
                matched_el = self.driver.execute_script(r"""
                    var vidId = arguments[0];
                    var links = document.querySelectorAll(
                        'ytd-rich-shelf-renderer[is-shorts] a[href*="' + vidId + '"], ' +
                        'ytd-rich-section-renderer a[href*="' + vidId + '"], ' +
                        'ytm-shorts-lockup-view-model-v2 a[href*="' + vidId + '"], ' +
                        'ytm-shorts-lockup-view-model a[href*="' + vidId + '"], ' +
                        'a[href*="/shorts/' + vidId + '"]'
                    );
                    for (var i = 0; i < links.length; i++) {
                        var l = links[i];
                        if (l.closest && l.closest('ytd-ad-slot-renderer, [layout*="ad"]')) continue;
                        return l;
                    }
                    return null;
                """, vid_id)

                if matched_el:
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", matched_el)
                        human_like_delay(0.5, 1.0)
                    except Exception:
                        pass
                    try:
                        ActionChains(self.driver).move_to_element(matched_el).pause(0.2).click().perform()
                    except Exception:
                        try:
                            matched_el.click()
                        except Exception:
                            click_element(self.driver, matched_el)

                    for _ in range(8):
                        cur = getattr(self.driver, "current_url", "") or ""
                        if ("/shorts/" in cur) or (vid_id and vid_id in cur):
                            return True
                        time.sleep(0.3)
        except Exception as exc:
            emit(self.log_queue, f"Error clicking home short: {exc}", logging.DEBUG)

        full_url = short_info.get("url") or (f"https://www.youtube.com/shorts/{vid_id}" if vid_id else "")
        if full_url:
            emit(self.log_queue, f"Navigating directly to short: {full_url}")
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)
            human_like_delay(1.5, 2.5)
            return True
        return False

    def _play_target_short(
        self,
        short_info: Dict[str, Any],
        task: VideoTask,
        session_watched_keys: Set[str],
    ) -> bool:
        """Watch a matched short for 1-3 loops randomly in a single go and record stats."""
        title = short_info.get("title") or task.title or "Short"
        vid_id = short_info.get("video_id") or extract_video_id(task.url)
        full_url = short_info.get("url") or task.url or (f"https://www.youtube.com/shorts/{vid_id}" if vid_id else "")

        emit(self.log_queue, f"=== Opening Target Short from Home Feed: '{title}' ({full_url}) ===")
        nav_ok = self.click_home_short(short_info)
        if not nav_ok:
            emit(self.log_queue, f"Failed to navigate to short '{title}'.", logging.WARNING)
            return False

        human_like_delay(1.5, 2.5)
        dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

        target_loops = random.randint(1, 3)
        ch_name = task.channel or "Shorts Channel"
        self.channel_tracker.set_active_channel(ch_name)

        registered_task = self._ensure_video_in_queue(task)

        def _is_short_channel_disabled() -> Optional[str]:
            target_channels = self.get_target_channels()
            ch_names = {c.name.strip().lower() for c in target_channels if c.name}
            if ch_name.strip().lower() not in ch_names and ch_name != "Shorts Channel":
                return "channel_disabled"
            return None

        def _on_short_view_counted(loop_num: int, cur_t: float, dur: float):
            if registered_task and self.stats_store:
                self.stats_store.record_view(registered_task, views=1)
                emit(
                    self.log_queue,
                    f"Credited 1 view to Target Short '{registered_task.title}' (Loop {loop_num}, reached {cur_t:.1f}s / {dur:.1f}s).",
                    logging.INFO,
                )

        watch_res = watch_short(
            self.driver,
            target_loops=target_loops,
            stop_event=self.stop_event,
            log_queue=self.log_queue,
            pause_event=self.pause_event,
            should_stop_callback=_is_short_channel_disabled,
            on_watch_tick=lambda d: self.channel_tracker.add_watch_delta(ch_name, d),
            like_short=True,
            on_view_counted=_on_short_view_counted,
        )
        self.channel_tracker.set_active_channel(None)

        if vid_id:
            session_watched_keys.add(vid_id)
        if full_url:
            session_watched_keys.add(full_url)

        actual_watched = float(watch_res.watched_seconds or 0.0)
        actual_ad = float(watch_res.ad_seconds or 0.0)
        actual_dur = float(watch_res.video_duration or 0.0)

        views_done = getattr(watch_res, "views_counted", 0)
        self._record_watch(
            title=title,
            channel_name=ch_name,
            watch_seconds=actual_watched,
            ad_seconds=actual_ad,
            duration=actual_dur,
            completed=watch_res.completed,
            playback_finished=watch_res.playback_finished,
            interrupted=watch_res.interrupted,
            task=registered_task,
            runs=0 if (views_done > 0 or getattr(registered_task, "is_short", False)) else None,
        )
        self._update_task_duration(registered_task, actual_dur)

        if hasattr(self, "channel_tracker") and self.channel_tracker and (actual_watched > 0 or watch_res.completed):
            self.channel_tracker.record_video_watched(
                is_short=True,
                channel_name=ch_name,
                source="home",
                is_random=False,
            )

        emit(
            self.log_queue,
            f"Logged Target Short view ({actual_watched:.0f}s watched, {target_loops} loop(s)).",
        )
        # Continue into the Shorts reel loop:
        self._run_shorts_reel_loop(session_watched_keys=session_watched_keys)

        return watch_res.completed or watch_res.playback_finished


    def get_active_short_info(self) -> Dict[str, Any]:
        """Extract metadata of the currently active Short in the reel."""
        try:
            info = self.driver.execute_script(GET_ACTIVE_SHORT_INFO_JS)
            if isinstance(info, dict):
                curr_url = getattr(self.driver, "current_url", "") or info.get("url", "")
                info["url"] = curr_url
                info["video_id"] = extract_video_id(curr_url)
                return info
        except Exception as exc:
            emit(self.log_queue, f"Error getting active short info: {exc}", logging.DEBUG)
        curr_url = getattr(self.driver, "current_url", "")
        return {
            "found": False,
            "channel_name": "",
            "channel_url": "",
            "title": "",
            "url": curr_url,
            "video_id": extract_video_id(curr_url),
        }

    def scroll_to_next_short(self, prev_url: Optional[str] = None) -> bool:
        """Scroll down to the next Short in the YouTube Shorts reel and verify progression."""
        prev = prev_url or getattr(self.driver, "current_url", "")
        clicked = False
        try:
            clicked = self.driver.execute_script(r"""
                var btn = document.querySelector(
                    '#navigation-button-down button[aria-label="Next video"], ' +
                    '#navigation-button-down button, ' +
                    '.navigation-button#navigation-button-down button, ' +
                    'ytd-shorts #navigation-button-down button, ' +
                    'button[aria-label="Next video"], ' +
                    'button.navigation-button-down'
                );
                if (btn && btn.offsetParent !== null) {
                    btn.click();
                    return true;
                }
                return false;
            """)
        except Exception:
            clicked = False

        if not clicked:
            try:
                nav_btns = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "#navigation-button-down button, button[aria-label='Next video']"
                )
                for nb in nav_btns:
                    if nb.is_displayed():
                        ActionChains(self.driver).move_to_element(nb).pause(0.1).click().perform()
                        clicked = True
                        break
            except Exception:
                pass

        if not clicked:
            try:
                ActionChains(self.driver).send_keys(Keys.ARROW_DOWN).perform()
            except Exception:
                try:
                    self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_DOWN)
                except Exception:
                    try:
                        self.driver.execute_script("window.scrollBy(0, window.innerHeight);")
                    except Exception:
                        pass

        for _ in range(10):
            time.sleep(0.2)
            curr = getattr(self.driver, "current_url", "")
            if curr and curr != prev:
                break
        return True

    def _run_shorts_reel_loop(
        self,
        session_watched_keys: Set[str],
    ) -> int:
        """
        Continuously navigate and watch Shorts on the Shorts reel:
        - If active Short is from a targeted channel: watch for 1-3 loops randomly, sync stats, scroll down.
        - If active Short is NOT from a targeted channel: skip immediately and scroll down.
        - Exits once non-target shorts limit (NonTargetShortsLimit) is reached or stopped.
        Returns the number of target shorts watched.
        """
        max_non_target = self.config_mgr.get_int("NonTargetShortsLimit", 5) if self.config_mgr else 5
        non_target_count = 0
        target_shorts_watched = 0

        emit(self.log_queue, f"=== Entering YouTube Shorts Reel Loop (Max non-target skips: {max_non_target}) ===")

        while not self.stop_event.is_set():
            self.check_pause()
            if self.stop_event.is_set():
                break

            target_channels = self.get_target_channels()
            target_shorts_queue = self.get_target_shorts()

            short_info = self.get_active_short_info()
            ch_name = short_info.get("channel_name", "")
            ch_url = short_info.get("channel_url", "")
            title = short_info.get("title", "") or "Short"
            vid_id = short_info.get("video_id") or extract_video_id(short_info.get("url", ""))
            full_url = short_info.get("url") or (f"https://www.youtube.com/shorts/{vid_id}" if vid_id else "")

            # Match against targeted channels
            ch_lookup_url = ch_url or (f"/{ch_name}" if ch_name.startswith("@") else "")
            matched_channel = match_channel(ch_name, ch_lookup_url, target_channels) if (ch_name or ch_lookup_url) else None

            # Check if this Short is already in the Shorts queue
            matched_task = None
            if vid_id and self.config_mgr:
                try:
                    for item in self.config_mgr.get_video_queue():
                        if isinstance(item, dict):
                            t = VideoTask.from_dict(item)
                            if vid_id in (t.url or "") or extract_video_id(t.url) == vid_id or resolve_video_key(t, t.url) == vid_id:
                                matched_task = t
                                break
                except Exception:
                    pass
            if matched_task is None and vid_id:
                for t in target_shorts_queue:
                    if vid_id in (t.url or "") or extract_video_id(t.url) == vid_id or resolve_video_key(t, t.url) == vid_id:
                        matched_task = t
                        break

            is_target = bool(matched_channel or matched_task)

            if is_target:
                ch_display = (matched_channel.name if matched_channel else (matched_task.channel if matched_task else ch_name)) or "Target Channel"

                if matched_task is not None:
                    registered_task = matched_task
                    emit(
                        self.log_queue,
                        f"Short '{title}' ({full_url}) from targeted channel '{ch_display}' is already in Shorts queue. Playing and updating stats...",
                        logging.INFO,
                    )
                else:
                    emit(
                        self.log_queue,
                        f"Short '{title}' ({full_url}) from targeted channel '{ch_display}' is not in Shorts queue. Adding to Shorts queue...",
                        logging.INFO,
                    )
                    new_task = VideoTask(
                        url=full_url,
                        title=title,
                        channel=ch_display,
                        enabled=True,
                        max_runs=DEFAULT_SHORTS_MAX_RUNS,
                        target_multiplier=0.0,
                    )
                    registered_task = self._ensure_video_in_queue(new_task)

                target_loops = random.randint(1, 3)
                self.channel_tracker.set_active_channel(ch_display)

                def _is_short_channel_disabled() -> Optional[str]:
                    target_ch_list = self.get_target_channels()
                    ch_names = {c.name.strip().lower() for c in target_ch_list if c.name}
                    if ch_display.strip().lower() not in ch_names and ch_display != "Target Channel":
                        return "channel_disabled"
                    return None

                def _on_short_view_counted(loop_num: int, cur_t: float, dur: float):
                    if registered_task and self.stats_store:
                        self.stats_store.record_view(registered_task, views=1)
                        emit(
                            self.log_queue,
                            f"Credited 1 view to Target Short '{registered_task.title}' (Loop {loop_num}, reached {cur_t:.1f}s / {dur:.1f}s).",
                            logging.INFO,
                        )

                watch_res = watch_short(
                    self.driver,
                    target_loops=target_loops,
                    stop_event=self.stop_event,
                    log_queue=self.log_queue,
                    pause_event=self.pause_event,
                    should_stop_callback=_is_short_channel_disabled,
                    on_watch_tick=lambda d: self.channel_tracker.add_watch_delta(ch_display, d),
                    like_short=True,
                    on_view_counted=_on_short_view_counted,
                )
                self.channel_tracker.set_active_channel(None)

                if vid_id:
                    session_watched_keys.add(vid_id)
                if full_url:
                    session_watched_keys.add(full_url)

                actual_watched = float(watch_res.watched_seconds or 0.0)
                actual_ad = float(watch_res.ad_seconds or 0.0)
                actual_dur = float(watch_res.video_duration or 0.0)

                views_done = getattr(watch_res, "views_counted", 0)
                self._record_watch(
                    title=title,
                    channel_name=ch_display,
                    watch_seconds=actual_watched,
                    ad_seconds=actual_ad,
                    duration=actual_dur,
                    completed=watch_res.completed,
                    playback_finished=watch_res.playback_finished,
                    interrupted=watch_res.interrupted,
                    task=registered_task,
                    runs=0 if (views_done > 0 or getattr(registered_task, "is_short", False)) else None,
                )
                self._update_task_duration(registered_task, actual_dur)

                if hasattr(self, "channel_tracker") and self.channel_tracker and (actual_watched > 0 or watch_res.completed):
                    self.channel_tracker.record_video_watched(
                        is_short=True,
                        channel_name=ch_display,
                        source="shorts_reel",
                        is_random=False,
                    )

                target_shorts_watched += 1
                self.target_attempts += 1
                self.watched_count = self.target_attempts
                prog_total = self.target_limit if not self.is_unlimited else 0
                self._emit_event(("progress", 0, self.target_attempts, prog_total))

                emit(self.log_queue, f"Updated stats for Short '{title}' ({full_url}): {actual_watched:.0f}s watched ({target_loops} loop(s)). Moving to next Short...")
            else:
                non_target_count += 1
                ch_label = ch_name or "Unknown Channel"
                non_target_watch_time = random.uniform(2.0, 6.0)
                emit(
                    self.log_queue,
                    f"Short from '{ch_label}' is not in targeted channels list. Watching for {non_target_watch_time:.1f}s randomly before skipping ({non_target_count}/{max_non_target})...",
                    logging.INFO,
                )
                try:
                    ensure_video_playing(self.driver, log_queue=self.log_queue)
                except Exception:
                    pass

                elapsed = 0.0
                step = 0.2
                while elapsed < non_target_watch_time and not self.stop_event.is_set():
                    self.check_pause()
                    if self.stop_event.is_set():
                        break
                    time.sleep(min(step, max(0.01, non_target_watch_time - elapsed)))
                    elapsed += step

                if self.stop_event.is_set():
                    break

                if non_target_count >= max_non_target:
                    emit(
                        self.log_queue,
                        f"Reached limit of {max_non_target} non-target Short(s) from outside targeted list. Exiting Shorts reel.",
                        logging.INFO,
                    )
                    break

            prev_url = full_url or getattr(self.driver, "current_url", "")
            self.scroll_to_next_short(prev_url=prev_url)
            human_like_delay(1.2, 2.0)

        return target_shorts_watched

    def search_youtube_query(self, query: str) -> bool:
        """Type and execute a YouTube search for the given query."""
        return perform_youtube_search(self.driver, query, log_queue=None)

    def search_and_play_target_video(
        self,
        session_watched_keys: Set[str],
        preferred_channel_to_avoid: Optional[str] = None,
    ) -> Optional[str]:
        """Search YouTube for a video from the video queue or targeted channels.

        1. Selects candidate tasks from the video queue (prioritizing active channels).
        2. Randomly searches for a video using its keywords/title.
        3. Scans search results to find the matching video (by URL/ID, title, or channel).
        4. If found, clicks and plays the video in the current tab.
        5. If not found, tries another candidate video from the queue/channels until found.
        """
        if self.stop_event.is_set():
            return None

        target_channels = self.get_target_channels()
        target_channel_names = [c.name for c in target_channels if c.name]

        # 1. Gather runnable candidate video tasks from queue
        raw_queue = self.config_mgr.get_video_queue() if self.config_mgr else []
        queue_tasks: List[VideoTask] = []
        for raw in raw_queue:
            if isinstance(raw, dict):
                t = VideoTask.from_dict(raw)
                if t.enabled:
                    if t.channel and target_channels:
                        if not any(c.name.strip().lower() == t.channel.strip().lower() for c in target_channels):
                            continue
                    queue_tasks.append(t)

        candidate_tasks = list(queue_tasks)
        if preferred_channel_to_avoid and len(target_channels) > 1:
            avoid_canon = SessionChannelTracker._canon_name(preferred_channel_to_avoid)
            other_tasks = [t for t in candidate_tasks if t.channel and SessionChannelTracker._canon_name(t.channel) != avoid_canon]
            same_tasks = [t for t in candidate_tasks if not t.channel or SessionChannelTracker._canon_name(t.channel) == avoid_canon]
            random.shuffle(other_tasks)
            random.shuffle(same_tasks)
            candidate_tasks = other_tasks + same_tasks
        else:
            random.shuffle(candidate_tasks)

        emit(
            self.log_queue,
            f"\n=== [YouTube Search] Searching for targeted video across {len(candidate_tasks)} queued candidate(s) and {len(target_channels)} channel(s) ===",
        )

        for cand_idx, task in enumerate(candidate_tasks, start=1):
            if self.stop_event.is_set():
                return None
            self.check_pause()
            if self.stop_event.is_set():
                return None

            queries = task.get_title_queries()
            if not queries:
                queries = [task.title or task.keywords or task.channel]

            for query in queries:
                if self.stop_event.is_set():
                    return None
                if not query:
                    continue

                emit(
                    self.log_queue,
                    "Scanning YouTube search...",
                )

                search_ok = self.search_youtube_query(query)
                if hasattr(self, "channel_tracker") and self.channel_tracker:
                    self.channel_tracker.record_search_query()
                if not search_ok:
                    continue

                matched_result = find_and_click_search_result(
                    self.driver,
                    task=task,
                    target_channels=target_channel_names,
                    max_scrolls=3,
                    log_queue=None,
                )

                if matched_result:
                    title = matched_result.get("title") or task.title or "Target Video"
                    url = matched_result.get("url") or task.url
                    ch_display = matched_result.get("channel") or task.channel or (target_channels[0].name if target_channels else "Target Channel")
                    clean_href = url.split("&")[0] if url else ""
                    vid_id = extract_video_id(clean_href) or clean_href

                    emit(
                        self.log_queue,
                        "Found video in YouTube search result",
                    )

                    try:
                        watch_res = self._play_target_video(
                            matched_result, url, title, ch_display, session_watched_keys, is_home=False, source="search"
                        )
                    except Exception as exc:
                        emit(self.log_queue, f"Error watching search result video: {exc}", logging.ERROR)
                        return None

                    if vid_id:
                        session_watched_keys.add(vid_id)
                    self.last_search_played_channel = ch_display
                    return ch_display
                else:
                    emit(
                        self.log_queue,
                        f"[YouTube Search] Candidate '{query}' not found in search results. Trying next candidate...",
                        logging.INFO,
                    )

        if target_channels and not self.stop_event.is_set():
            search_channels = list(target_channels)
            if preferred_channel_to_avoid and len(target_channels) > 1:
                avoid_canon = SessionChannelTracker._canon_name(preferred_channel_to_avoid)
                other_chs = [c for c in search_channels if SessionChannelTracker._canon_name(c.name) != avoid_canon]
                same_chs = [c for c in search_channels if SessionChannelTracker._canon_name(c.name) == avoid_canon]
                random.shuffle(other_chs)
                random.shuffle(same_chs)
                search_channels = other_chs + same_chs
            else:
                random.shuffle(search_channels)

            for ch in search_channels:
                if self.stop_event.is_set():
                    return None
                self.check_pause()
                if self.stop_event.is_set():
                    return None

                ch_query = ch.name.strip()
                if not ch_query:
                    continue

                emit(self.log_queue, "Scanning YouTube search...")
                search_ok = self.search_youtube_query(ch_query)
                if hasattr(self, "channel_tracker") and self.channel_tracker:
                    self.channel_tracker.record_search_query()
                if not search_ok:
                    continue

                matched_result = find_and_click_search_result(
                    self.driver,
                    target_channels=[ch_query],
                    max_scrolls=3,
                    log_queue=None,
                )

                if matched_result:
                    title = matched_result.get("title") or f"{ch_query} Video"
                    url = matched_result.get("url") or ""
                    clean_href = url.split("&")[0] if url else ""
                    vid_id = extract_video_id(clean_href) or clean_href

                    emit(
                        self.log_queue,
                        "Found video in YouTube search result",
                    )

                    try:
                        watch_res = self._play_target_video(
                            matched_result, url, title, ch_query, session_watched_keys, is_home=False, source="search"
                        )
                    except Exception as exc:
                        emit(self.log_queue, f"Error watching channel search video: {exc}", logging.ERROR)
                        return None

                    if vid_id:
                        session_watched_keys.add(vid_id)
                    self.last_search_played_channel = ch_query
                    return ch_query

        emit(self.log_queue, "[YouTube Search] No video found in search results across candidates and channels.", logging.WARNING)
        return None

    def run(self) -> None:
        """Main watching loop: home discovery -> watch -> repeat."""
        emit(self.log_queue, f"=== Starting Watch Session for {self.account.email} ===")
        active_channels_msg = ", ".join(f"'{c.name}'" for c in self.channels if c.name)
        emit(self.log_queue, f"Target stored channel(s): {active_channels_msg}")

        # Dispatch start notification in separate background headless window without blocking automation launch
        self.reporting_thread = threading.Thread(
            target=self._async_init_and_send_start_notification,
            name="ReportingStartNotificationThread",
            daemon=True,
        )
        self.reporting_thread.start()

        try:
            self.skip_ads, self.max_ad_seconds = self._ad_settings()
            known_raw = self.config_mgr.get_accounts() if self.config_mgr else None
            known = [Account.from_dict(a) if isinstance(a, dict) else a for a in known_raw] if known_raw else None
            self.driver = setup_driver(self.account, log_queue=self.log_queue, known_accounts=known)

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
                alert_type = "Account Disconnected / Sign-In Failure"
                details = f"Google sign-in could not be completed for {self.account.email}."
                curr_url = (getattr(self.driver, "current_url", "") or "").lower()
                page_src = ""
                try:
                    page_src = (getattr(self.driver, "page_source", "") or "").lower()
                except Exception:
                    pass

                if any(c in curr_url or c in page_src for c in ("captcha", "challenge", "2step", "speedbump", "sorry/index")):
                    alert_type = "Bot Challenge / CAPTCHA Alert"
                    details = f"Google presented a CAPTCHA or 2-Step challenge for account {self.account.email}."
                elif any(c in curr_url or c in page_src for c in ("err_internet_disconnected", "err_proxy_connection_failed", "err_name_not_resolved", "neterror")):
                    alert_type = "Proxy / Network Failure"
                    details = f"Network or proxy connection failed during sign-in for {self.account.email}."

                emit(self.log_queue, f"Sign-in failed for {self.account.email}: {details}", logging.ERROR)
                self._report_status(f"Sign-in failed for {self.account.email}.", "error")
                self._send_critical_alert(alert_type, details)
                session_status = alert_type
                session_err = details
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
                                    f"URL: {full_url}\nTitle: {title}\nChannel: {channel_display}",
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
            target_attempts = 0
            self.target_attempts = 0
            self.watched_count = 0
            last_watched_channel: Optional[str] = None
            channel_buffer_counts: Dict[str, int] = {}
            consecutive_target_watched: int = 0
            random_buffer_threshold: int = random.randint(1, 3)
            suggested_miss_count: int = 0
            max_suggested_misses: int = random.randint(2, 3)

            while not self.stop_event.is_set():
                if self._is_target_limit_reached(target_attempts):
                    emit(self.log_queue, f"Reached configured limit of {self.target_limit} target video attempt(s).")
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
                # Case 1: Watch page flow
                # -------------------------------------------------------------
                if "/shorts" in current_url:
                    emit(self.log_queue, "Currently on Shorts page. Entering Shorts reel loop...")
                    self._run_shorts_reel_loop(session_watched_keys=session_watched_keys)
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)
                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                    continue

                if "/watch" in current_url:
                    # 1. Check if random buffer video condition met
                    buffered = self._check_and_play_random_buffer_video(
                        session_watched_keys=session_watched_keys,
                        consecutive_target_watched=consecutive_target_watched,
                        random_buffer_threshold=random_buffer_threshold,
                        channel_watch_counts=channel_buffer_counts,
                        channel_consecutive_counts=channel_buffer_counts,
                    )
                    if buffered:
                        consecutive_target_watched = 0
                        channel_buffer_counts.clear()
                        random_buffer_threshold = random.randint(1, 3)
                        emit(
                            self.log_queue,
                            f"Target countdown reset. Next random buffer after {random_buffer_threshold} target video(s).",
                        )
                        if self.stop_event.is_set() or self._is_target_limit_reached(target_attempts):
                            break

                    # 2. Check if consecutive suggested misses threshold reached -> navigate to Home
                    if suggested_miss_count >= max_suggested_misses:
                        emit(
                            self.log_queue,
                            f"Target channel videos not found in suggested section after {suggested_miss_count} attempts. Navigating back to YouTube Home feed...",
                        )
                        suggested_miss_count = 0
                        max_suggested_misses = random.randint(2, 3)
                        safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                        human_like_delay(2.0, 3.5)
                        dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                        continue

                    # 3. Look in suggested section for target channel video
                    emit(self.log_queue, "Scanning suggested section...")
                    rec_candidates = self.fetch_recommended_videos()

                    target_match = self._find_preferred_target_video(
                        candidates=rec_candidates,
                        target_channels=target_channels,
                        last_channel_name=last_watched_channel,
                        session_watched_keys=session_watched_keys,
                        channel_watch_counts=channel_buffer_counts,
                        channel_consecutive_counts=channel_buffer_counts,
                    )

                    if target_match:
                        item, match_obj, full_url, title, ch_display = target_match
                        emit(self.log_queue, "Found video in suggested section")
                        target_attempts += 1
                        self.target_attempts = target_attempts
                        self.watched_count = target_attempts
                        prog_total = self.target_limit if not self.is_unlimited else 0
                        self._emit_event(("progress", 0, target_attempts, prog_total))

                        try:
                            watch_res = self._play_target_video(
                                item, full_url, title, ch_display, session_watched_keys, is_home=False, source="suggested"
                            )
                        except Exception:
                            raise

                        ch_canon = SessionChannelTracker._canon_name(ch_display)
                        channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                        last_watched_channel = ch_display
                        consecutive_target_watched += 1
                        suggested_miss_count = 0

                        emit(
                            self.log_queue,
                            f"Target channel '{ch_display}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) (total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}).",
                        )
                        continue
                    else:
                        # NOT found in suggestions: do NOT watch random video; do YouTube search!
                        suggested_miss_count += 1
                        emit(
                            self.log_queue,
                            f"No target channel video found in suggested section (miss attempt {suggested_miss_count}/{max_suggested_misses}). Switching immediately to YouTube search...",
                        )
                        played_ch = self.search_and_play_target_video(
                            session_watched_keys=session_watched_keys,
                            preferred_channel_to_avoid=last_watched_channel,
                        )
                        if played_ch:
                            target_attempts += 1
                            self.target_attempts = target_attempts
                            self.watched_count = target_attempts
                            prog_total = self.target_limit if not self.is_unlimited else 0
                            self._emit_event(("progress", 0, target_attempts, prog_total))

                            ch_canon = SessionChannelTracker._canon_name(played_ch)
                            channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                            last_watched_channel = played_ch
                            consecutive_target_watched += 1

                            emit(
                                self.log_queue,
                                f"Target channel '{played_ch}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) (total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}).",
                            )
                        else:
                            emit(
                                self.log_queue,
                                "[Browsing Loop] YouTube search was unable to find candidate video. Navigating to YouTube Home feed...",
                                logging.INFO,
                            )
                            safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                            human_like_delay(2.0, 3.5)

                        continue

                # -------------------------------------------------------------
                # Case 2: Home feed discovery (startup or Home navigation)
                # -------------------------------------------------------------
                target_shorts = self.get_target_shorts()
                candidate_shorts = self.fetch_home_shorts() if target_shorts else []
                matched_short = self.match_home_short(candidate_shorts, target_shorts, session_watched_keys) if candidate_shorts else None

                matched_home = self._scan_home_feed_for_target(
                    session_watched_keys=session_watched_keys,
                    last_watched_channel=last_watched_channel,
                    channel_watch_counts=channel_buffer_counts,
                    channel_consecutive_counts=channel_buffer_counts,
                    max_attempts=3,
                )

                if not matched_short and target_shorts:
                    candidate_shorts = self.fetch_home_shorts()
                    matched_short = self.match_home_short(candidate_shorts, target_shorts, session_watched_keys)

                chosen_action = None
                if matched_home and matched_short:
                    chosen_action = random.choice(["video", "short"])
                elif matched_home:
                    chosen_action = "video"
                elif matched_short:
                    chosen_action = "short"

                if chosen_action == "short" and matched_short:
                    short_item, short_task = matched_short
                    emit(self.log_queue, f"Selected matching Short from Home feed: '{short_task.title or short_item.get('title')}'")
                    target_attempts += 1
                    self.target_attempts = target_attempts
                    self.watched_count = target_attempts
                    prog_total = self.target_limit if not self.is_unlimited else 0
                    self._emit_event(("progress", 0, target_attempts, prog_total))

                    played_ok = self._play_target_short(
                        short_info=short_item,
                        task=short_task,
                        session_watched_keys=session_watched_keys,
                    )

                    ch_display = short_task.channel or short_item.get("channel") or "Shorts Channel"
                    ch_canon = SessionChannelTracker._canon_name(ch_display)
                    channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                    last_watched_channel = ch_display
                    consecutive_target_watched += 1
                    suggested_miss_count = 0

                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)
                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                    continue

                if matched_home:
                    item, match_obj, full_url, title, ch_display = matched_home
                    emit(self.log_queue, "Found video on Home page")
                    target_attempts += 1
                    self.target_attempts = target_attempts
                    self.watched_count = target_attempts
                    prog_total = self.target_limit if not self.is_unlimited else 0
                    self._emit_event(("progress", 0, target_attempts, prog_total))

                    try:
                        watch_res = self._play_target_video(
                            item, full_url, title, ch_display, session_watched_keys, is_home=True, source="home"
                        )
                    except Exception:
                        raise

                    ch_canon = SessionChannelTracker._canon_name(ch_display)
                    channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                    last_watched_channel = ch_display
                    consecutive_target_watched += 1
                    suggested_miss_count = 0

                    emit(
                        self.log_queue,
                        f"Target channel '{ch_display}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) (total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}).",
                    )
                    continue
                else:
                    # Not found on Home feed after 3 attempts: fall back to YouTube search!
                    emit(
                        self.log_queue,
                        "No video matching target channels found on YouTube Home. Falling back to YouTube search...",
                        logging.INFO,
                    )
                    played_ch = self.search_and_play_target_video(
                        session_watched_keys=session_watched_keys,
                        preferred_channel_to_avoid=last_watched_channel,
                    )
                    if played_ch:
                        target_attempts += 1
                        self.target_attempts = target_attempts
                        self.watched_count = target_attempts
                        prog_total = self.target_limit if not self.is_unlimited else 0
                        self._emit_event(("progress", 0, target_attempts, prog_total))

                        ch_canon = SessionChannelTracker._canon_name(played_ch)
                        channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                        last_watched_channel = played_ch
                        consecutive_target_watched += 1
                        suggested_miss_count = 0

                        emit(
                            self.log_queue,
                            f"Target channel '{played_ch}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) (total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}).",
                        )
                        continue
                    else:
                        stop_msg = "No video of targeted channel found on YouTube Home or via YouTube Search. Stopping watching session."
                        emit(self.log_queue, stop_msg, logging.WARNING)
                        self._report_status("Stopped: No target videos found", "warning")
                        self.stop_event.set()
                        break

            summary = f"Browsing session finished: attempted {target_attempts} target video(s)."
            emit(self.log_queue, summary)
            self._report_status(summary, "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during watching session: {exc}", logging.ERROR)
            self._report_status(f"Watching error: {exc}", "error")
            alert_type = "Critical Failure / Worker Crash"
            err_str = str(exc)
            if any(c in err_str.lower() for c in ("proxy", "connection", "err_internet")):
                alert_type = "Proxy / Network Failure"
            self._send_critical_alert(alert_type, f"Worker crashed with exception: {err_str}")
            session_status = alert_type
            session_err = err_str
        finally:
            if "session_status" not in locals() or not session_status:
                session_status = "Completed"
            if "session_err" not in locals():
                session_err = None
            if self.stop_event.is_set():
                session_status = "Stopped by user"
            if "exc" in locals() and locals()["exc"] is not None:
                session_status = "Error"
                session_err = str(locals()["exc"])

            if hasattr(self, "channel_tracker"):
                self.channel_tracker.on_stop()

            # Close main visible watching driver
            if self.driver:
                close_driver(self.driver, log_queue=self.log_queue)
                self.driver = None

            # Deliver session report via the persistent headless browser parked on the email thread
            self._send_session_report(status=session_status, error=session_err)

            # Wait briefly if background start notification thread is still finishing
            rep_thread = getattr(self, "reporting_thread", None)
            if rep_thread and rep_thread.is_alive():
                rep_thread.join(timeout=5)

            # Close persistent headless reporting browser
            if self.reporting_driver:
                close_driver(self.reporting_driver, log_queue=self.log_queue)
                self.reporting_driver = None

    def _async_init_and_send_start_notification(self) -> None:
        """Initialize headless reporting browser and send start notification asynchronously in background."""
        try:
            method = self.config_mgr.get_setting("ReportEmailMethod", "Auto") if self.config_mgr else "Auto"
            if "headless" in method.lower() and not getattr(self, "reporting_driver", None):
                try:
                    self.reporting_driver = EmailReporter.create_headless_reporting_driver(
                        account=self.account,
                        log_queue=self.log_queue,
                    )
                except Exception as exc:
                    logging.debug(f"Could not create persistent reporting driver: {exc}")
                    self.reporting_driver = None

            self._send_start_notification()
        except Exception as exc:
            logging.debug(f"Failed in async start notification dispatch: {exc}")

    def _send_start_notification(self) -> None:
        """Deliver start notification email via headless browser."""
        if not self.config_mgr:
            return
        try:
            total_sec = 0.0
            if hasattr(self, "channel_tracker") and self.channel_tracker:
                total_sec = sum(self.channel_tracker.initial_channel_watch_seconds.values())
            elif self.config_mgr:
                total_sec = sum(float(c.get("watch_seconds", 0.0) or 0.0) for c in self.config_mgr.get_channels())

            EmailReporter.send_start_notification(
                account=self.account,
                session_mode="Continuous Watching / Browsing",
                config_mgr=self.config_mgr,
                start_time=getattr(self, "session_start_time", datetime.now()),
                custom_subject=getattr(self, "session_thread_subject", None),
                total_app_watch_seconds=total_sec,
                log_queue=self.log_queue,
                thread_info=self.session_thread_info,
                driver=self.reporting_driver,
            )
            if self.session_thread_info.get("thread_url"):
                self.session_thread_url = self.session_thread_info["thread_url"]
            if self.session_thread_info.get("message_id"):
                self.session_thread_message_id = self.session_thread_info["message_id"]
        except Exception as exc:
            logging.debug(f"Failed dispatching start notification email: {exc}")

    def _send_critical_alert(self, alert_type: str, details: str) -> None:
        """Send urgent alert email for bot challenges, disconnections, or crashes."""
        if not self.config_mgr:
            return
        try:
            EmailReporter.send_alert_notification(
                account=self.account,
                alert_type=alert_type,
                message_details=details,
                config_mgr=self.config_mgr,
                start_time=getattr(self, "session_start_time", datetime.now()),
                custom_subject=getattr(self, "session_thread_subject", None),
                log_queue=None,
                thread_url=getattr(self, "session_thread_url", None),
                thread_message_id=getattr(self, "session_thread_message_id", None),
                thread_info=getattr(self, "session_thread_info", None),
                driver=self.reporting_driver or self.driver,
            )
        except Exception as exc:
            logging.debug(f"Failed dispatching critical alert email: {exc}")

    def _send_session_report(self, status: str = "Completed", error: Optional[str] = None) -> None:
        """Deliver session watch time report."""
        self._emit_event(("report_generating", "Generating session report... Please do not close the app."))
        if not self.config_mgr:
            self._emit_event(("report_sent", False, "No configuration manager available."))
            return
        if not hasattr(self, "channel_tracker") or not self.channel_tracker:
            self._emit_event(("report_sent", False, "No session tracking data."))
            return

        # If background start notification thread is still active, wait briefly for it to complete
        rep_thread = getattr(self, "reporting_thread", None)
        if rep_thread and rep_thread.is_alive():
            rep_thread.join(timeout=15)

        try:
            report_data = self.channel_tracker.get_session_report_data()
            interruption = error or (status if status != "Completed" else "Completed normally")
            report = SessionReport(
                account_email=getattr(self.account, "email", "") if self.account else "",
                session_mode="Continuous Watching / Browsing",
                start_time=report_data["start_time"],
                end_time=report_data["end_time"],
                duration_seconds=report_data["duration_seconds"],
                total_session_watch_seconds=report_data["total_session_watch_seconds"],
                channel_reports=report_data["channels"],
                status=status,
                interruption_reason=interruption,
                error_message=error,
                total_videos_watched=report_data.get("total_videos_watched", 0),
                regular_videos_count=report_data.get("regular_videos_count", 0),
                shorts_count=report_data.get("shorts_count", 0),
                custom_subject=getattr(self, "session_thread_subject", None),
                thread_url=getattr(self, "session_thread_url", None),
                thread_message_id=getattr(self, "session_thread_message_id", None),
                suggested_videos_count=report_data.get("suggested_videos_count", 0),
                search_videos_count=report_data.get("search_videos_count", 0),
                home_videos_count=report_data.get("home_videos_count", 0),
                random_videos_count=report_data.get("random_videos_count", 0),
                search_queries_count=report_data.get("search_queries_count", 0),
            )
            ok = EmailReporter.dispatch_report(
                report=report,
                config_mgr=self.config_mgr,
                driver=self.reporting_driver or self.driver,
                account=self.account,
                log_queue=self.log_queue,
            )
            if not ok:
                emit(self.log_queue, "Session report email delivery could not be completed.", logging.WARNING)
                self._emit_event(("report_sent", False, "Session report email delivery could not be completed."))
            else:
                self._emit_event(("report_sent", True, "Session report sent successfully!"))
        except Exception as exc:
            emit(self.log_queue, f"Failed dispatching session email report: {exc}", logging.WARNING)
            self._emit_event(("report_sent", False, f"Failed dispatching session email report: {exc}"))

    def stop(self) -> None:
        """Stop the continuous watching loop and all running tasks."""
        self.stop_event.set()
        if hasattr(self, "channel_tracker"):
            self.channel_tracker.on_stop()
