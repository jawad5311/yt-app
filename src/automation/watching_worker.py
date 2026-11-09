from datetime import datetime, timedelta

from .email_reporter import EmailReporter, SessionReport
from .night_sleep import NightSleepScheduler
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
from .driver import (
    close_driver,
    is_driver_alive,
    is_session_disconnected_error,
    setup_driver,
)
from .login import check_account_logged_in, perform_google_login
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
        'ytd-rich-shelf-renderer[is-shorts], ytd-rich-shelf-renderer, ' +
        'ytd-rich-section-renderer ytd-rich-shelf-renderer, ytd-reel-shelf-renderer, ' +
        'grid-shelf-view-model, yt-shelf-view-model'
    );

    var cardElements = [];
    if (shelves.length > 0) {
        for (var s = 0; s < shelves.length; s++) {
            var items = shelves[s].querySelectorAll(
                'ytd-rich-item-renderer, ytd-reel-item-renderer, ' +
                'ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, ' +
                'shorts-lockup-view-model, yt-shorts-lockup-view-model, ' +
                'yt-lockup-view-model'
            );
            for (var i = 0; i < items.length; i++) {
                cardElements.push(items[i]);
            }
        }
    }

    var directLockups = document.querySelectorAll(
        'ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, ' +
        'shorts-lockup-view-model, yt-shorts-lockup-view-model, ' +
        'ytd-rich-item-renderer[is-slim-media]'
    );
    for (var j = 0; j < directLockups.length; j++) {
        cardElements.push(directLockups[j]);
    }

    for (var idx = 0; idx < cardElements.length; idx++) {
        var card = cardElements[idx];
        if (!card) continue;

        if (card.closest && card.closest('ytd-ad-slot-renderer, [layout*="ad"]')) continue;
        if (card.closest && card.closest('#guide, ytd-guide-renderer, ytd-mini-guide-renderer, #masthead')) continue;

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
            'h3.shortsLockupViewModelHostMetadataTitle a, h3 a, a.shortsLockupViewModelHostOutsideMetadataEndpoint, h3, #video-title, [class*="title"], [class*="Title"]'
        );
        var title = '';
        if (titleEl) {
            title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
        }
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }
        if (title && title.indexOf(' - play Short') !== -1) {
            title = title.replace(' - play Short', '').trim();
        }

        var viewsEl = card.querySelector(
            '.shortsLockupViewModelHostOutsideMetadataSubhead, #metadata-line, .ytContentMetadataViewModelMetadataRow, [class*="subhead"]'
        );
        var viewsText = viewsEl ? (viewsEl.textContent || '').trim() : '';

        var channelText = '';
        var channelUrl = '';
        var channelEl = card.querySelector(
            'a[href*="/@"], a[href*="/channel/"], [class*="channel"], [class*="Channel"], #channel-name, .yt-core-attributed-string'
        );
        if (channelEl) {
            channelText = (channelEl.textContent || channelEl.getAttribute('title') || '').trim();
            channelUrl = channelEl.getAttribute('href') || '';
        }
        if (!channelText) {
            var aria = (linkEl.getAttribute('aria-label') || '');
            var byMatch = aria.match(/\sby\s([^0-9]+?)(?:\s[0-9]|\sago|\s-\splay|\.|$)/i);
            if (byMatch && byMatch[1]) {
                channelText = byMatch[1].trim();
            }
        }

        results.push({
            index: results.length,
            href: href,
            video_id: vidId,
            url: href.startsWith('http') ? href : ('https://www.youtube.com' + href),
            title: title || ('Short ' + vidId),
            channel: channelText,
            channel_url: channelUrl,
            views: viewsText
        });
    }

    // Fallback: direct scan for any visible a[href*="/shorts/"] across feed
    var allShortLinks = document.querySelectorAll('a[href*="/shorts/"]');
    for (var k = 0; k < allShortLinks.length; k++) {
        var a = allShortLinks[k];
        if (!a) continue;
        if (a.closest && a.closest('ytd-ad-slot-renderer, [layout*="ad"]')) continue;
        if (a.closest && a.closest('#guide, ytd-guide-renderer, ytd-mini-guide-renderer, #masthead')) continue;

        var aHref = a.getAttribute('href') || a.href || '';
        var aVidId = extractVideoId(aHref);
        if (!aVidId || seenIds[aVidId]) continue;
        seenIds[aVidId] = true;

        var aTitle = (a.getAttribute('title') || a.getAttribute('aria-label') || a.textContent || '').trim();
        if (aTitle && aTitle.indexOf(' - play Short') !== -1) {
            aTitle = aTitle.replace(' - play Short', '').trim();
        }

        var aCard = a.closest ? a.closest(
            'ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, shorts-lockup-view-model, yt-shorts-lockup-view-model, ytd-rich-item-renderer, ytd-reel-item-renderer, grid-shelf-view-model'
        ) : null;

        var aChannel = '';
        var aChannelUrl = '';
        if (aCard) {
            var chEl = aCard.querySelector('a[href*="/@"], a[href*="/channel/"], [class*="channel"], [class*="Channel"], #channel-name');
            if (chEl) {
                aChannel = (chEl.textContent || chEl.getAttribute('title') || '').trim();
                aChannelUrl = chEl.getAttribute('href') || '';
            }
        }

        results.push({
            index: results.length,
            href: aHref,
            video_id: aVidId,
            url: aHref.startsWith('http') ? aHref : ('https://www.youtube.com' + aHref),
            title: aTitle || ('Short ' + aVidId),
            channel: aChannel,
            channel_url: aChannelUrl,
            views: ''
        });
    }

    return results;
})();
"""



SUGGESTED_SHORTS_JS = r"""
return (function() {
    var shelf = document.querySelector('ytd-reel-shelf-renderer');
    if (!shelf) return [];

    var itemsContainer = shelf.querySelector('#items-container') || shelf.querySelector('#items') || shelf;
    var containerRect = itemsContainer.getBoundingClientRect();

    function extractVideoId(href) {
        if (!href) return '';
        var m = href.match(/\/shorts\/([A-Za-z0-9_\-]{11})/);
        return m ? m[1] : '';
    }

    var rawCards = shelf.querySelectorAll(
        '#items > ytm-shorts-lockup-view-model-v2, ' +
        '#items > ytd-reel-item-renderer, ' +
        '#items > shorts-lockup-view-model, ' +
        'ytm-shorts-lockup-view-model-v2, ' +
        'ytd-reel-item-renderer, ' +
        'shorts-lockup-view-model'
    );

    var results = [];
    var seenIds = {};

    for (var i = 0; i < rawCards.length; i++) {
        var card = rawCards[i];
        if (!card) continue;

        if (card.parentElement && card.parentElement.closest &&
            card.parentElement.closest('ytm-shorts-lockup-view-model-v2, ytd-reel-item-renderer, shorts-lockup-view-model')) {
            continue;
        }

        var linkEl = card.querySelector('a.shortsLockupViewModelHostEndpoint, a.reel-item-endpoint, a[href*="/shorts/"]');
        if (!linkEl && card.tagName === 'A' && (card.getAttribute('href') || '').indexOf('/shorts/') !== -1) {
            linkEl = card;
        }
        if (!linkEl) continue;

        var href = linkEl.getAttribute('href') || '';
        var vidId = extractVideoId(href);
        if (!vidId || seenIds[vidId]) continue;
        seenIds[vidId] = true;

        var titleEl = card.querySelector('h3 a, a[title], .shortsLockupViewModelHostMetadataTitle a, #video-title, h3, [class*="title"], [class*="Title"]');
        var title = '';
        if (titleEl) {
            title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
        }
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }
        if (title && title.indexOf(' - play Short') !== -1) {
            title = title.replace(' - play Short', '').trim();
        }

        var viewsEl = card.querySelector('.shortsLockupViewModelHostMetadataSubhead, .shortsLockupViewModelHostOutsideMetadataSubhead, #metadata-line, [class*="subhead"]');
        var viewsText = viewsEl ? (viewsEl.textContent || '').trim() : '';

        var channelText = '';
        var channelEl = card.querySelector('a[href*="/@"], a[href*="/channel/"], [class*="channel"], [class*="Channel"], #channel-name');
        if (channelEl) {
            channelText = (channelEl.textContent || channelEl.getAttribute('title') || '').trim();
        }
        if (!channelText) {
            var aria = (linkEl.getAttribute('aria-label') || '');
            var byMatch = aria.match(/\sby\s([^0-9]+?)(?:\s[0-9]|\sago|\s-\splay|\.|$)/i);
            if (byMatch && byMatch[1]) {
                channelText = byMatch[1].trim();
            }
        }

        var rect = card.getBoundingClientRect();
        var isVisible = (
            rect.width > 0 && rect.height > 0 &&
            rect.right > (containerRect.left + 15) &&
            rect.left < (containerRect.right - 15)
        );

        results.push({
            index: results.length,
            href: href,
            video_id: vidId,
            url: href.startsWith('http') ? href : ('https://www.youtube.com' + href),
            title: title || ('Short ' + vidId),
            channel: channelText,
            views: viewsText,
            is_visible: isVisible,
            source: "Suggested section",
            source_type: "suggested"
        });
    }

    return results;
})();
"""

CLICK_SUGGESTED_NEXT_ARROW_JS = r"""
return (function() {
    var shelf = document.querySelector('ytd-reel-shelf-renderer');
    if (!shelf) return false;

    var nextBtn = shelf.querySelector('#right-arrow button') ||
                  shelf.querySelector('#right-arrow ytd-button-renderer button') ||
                  shelf.querySelector('#right-arrow ytd-button-renderer') ||
                  shelf.querySelector('button[aria-label="Next"]') ||
                  shelf.querySelector('button[aria-label="Next page"]') ||
                  shelf.querySelector('#right-arrow');

    if (!nextBtn) return false;

    var isDisabled = nextBtn.disabled ||
                     nextBtn.getAttribute('aria-disabled') === 'true' ||
                     nextBtn.classList.contains('yt-spec-button-shape-next--disabled') ||
                     (nextBtn.hasAttribute && nextBtn.hasAttribute('hidden')) ||
                     nextBtn.offsetParent === null;

    if (isDisabled) return false;

    try {
        nextBtn.scrollIntoView({behavior: 'smooth', block: 'center'});
    } catch(e) {}

    try {
        nextBtn.click();
        return true;
    } catch(e) {
        return false;
    }
})();
"""


GET_ACTIVE_SHORT_INFO_JS = r"""
return (function() {
    var renderers = document.querySelectorAll('ytd-reel-video-renderer');
    var activeReel = null;

    // 1. Identify active renderer by URL video ID
    var currentUrl = window.location.href || '';
    var m = currentUrl.match(/\/shorts\/([a-zA-Z0-9_-]+)/);
    var activeVidId = m ? m[1] : '';

    if (activeVidId && renderers.length > 0) {
        for (var i = 0; i < renderers.length; i++) {
            var r = renderers[i];
            if (r.querySelector('a[href*="' + activeVidId + '"]') ||
                (r.getAttribute('id') && r.getAttribute('id').indexOf(activeVidId) !== -1)) {
                activeReel = r;
                break;
            }
        }
    }

    // 2. Identify active renderer by viewport center (currently displayed on screen)
    if (!activeReel && renderers.length > 0) {
        var vCenter = window.innerHeight / 2;
        for (var i = 0; i < renderers.length; i++) {
            var rect = renderers[i].getBoundingClientRect();
            if (rect.top <= vCenter && rect.bottom >= vCenter) {
                activeReel = renderers[i];
                break;
            }
        }
    }

    // 3. Fallback: is-active attribute
    if (!activeReel) {
        activeReel = document.querySelector('ytd-reel-video-renderer[is-active], ytd-reel-video-renderer.active');
    }

    // 4. Fallback: renderer containing the playing video element
    if (!activeReel) {
        var videos = document.querySelectorAll('video');
        for (var i = 0; i < videos.length; i++) {
            var v = videos[i];
            if (!v.paused || v.currentTime > 0) {
                var pr = v.closest('ytd-reel-video-renderer');
                if (pr) {
                    activeReel = pr;
                    break;
                }
            }
        }
    }

    // CRITICAL: If no active reel is found, return found: false and empty metadata.
    // NEVER fall back to searching the entire document as that grabs the previous short's channel!
    if (!activeReel) {
        return {
            found: false,
            channel_name: '',
            channel_url: '',
            title: '',
            url: window.location.href
        };
    }

    var channelName = '';
    var channelUrl = '';

    // Query channel link strictly within activeReel
    var chLink = activeReel.querySelector(
        'yt-reel-channel-bar-view-model .ytReelChannelBarViewModelChannelName a, ' +
        'yt-reel-channel-bar-view-model a[href*="/@"], ' +
        'yt-reel-channel-bar-view-model a[href*="/channel/"], ' +
        'ytd-channel-name a, #channel-name a, ' +
        '.ytReelMetapanelViewModelMetapanelItem a[href*="/@"], ' +
        '.ytReelMetapanelViewModelMetapanelItem a[href*="/channel/"], ' +
        'ytd-reel-player-header-renderer ytd-channel-name a, ' +
        'ytd-reel-player-header-renderer a[href*="/@"], ' +
        'ytd-reel-player-header-renderer a[href*="/channel/"], ' +
        'a.ytAttributedStringLink[href*="/@"], ' +
        'a[href*="/@"], a[href*="/channel/"]'
    );

    if (chLink) {
        channelName = (chLink.textContent || '').trim();
        channelUrl = chLink.getAttribute('href') || '';
    }

    // Check avatar or button with aria-label strictly within activeReel
    if (!channelName) {
        var avatarEl = activeReel.querySelector('.ytSpecAvatarShapeHost[aria-label*="Go to channel" i], [aria-label*="Go to channel" i]');
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

    // Fallback channel text container strictly within activeReel
    if (!channelName) {
        var chEl = activeReel.querySelector(
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

    // Title strictly within activeReel
    var titleEl = activeReel.querySelector(
        'ytd-reel-player-header-renderer h2, h2.title, #video-title, .title.ytd-reel-player-header-renderer, .ytReelMetapanelViewModelMetapanelItem h2, yt-reel-metapanel-view-model h2, h2'
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


class ReelLoopResult(tuple):
    """Result returned by _run_shorts_reel_loop.
    Unpacks as (target_shorts_watched, non_targets_in_this_reel, exit_reason),
    while also comparing equal to target_shorts_watched when compared with int.
    """
    def __new__(cls, target_shorts_watched: int, non_targets_in_this_reel: int, exit_reason: str):
        return super().__new__(cls, (target_shorts_watched, non_targets_in_this_reel, exit_reason))

    @property
    def target_shorts_watched(self) -> int:
        return self[0]

    @property
    def non_targets(self) -> int:
        return self[1]

    @property
    def exit_reason(self) -> str:
        return self[2]

    def __eq__(self, other):
        if isinstance(other, int):
            return self[0] == other
        return super().__eq__(other)

    def __int__(self):
        return self[0]


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
        self.max_ad_seconds = 9
        self.channel_tracker = SessionChannelTracker(
            config_mgr=self.config_mgr,
            stats_store=self.stats_store,
            channels=self.channels,
            emit_event=self._emit_event,
            log_queue=self.log_queue,
        )
        self.session_start_time = datetime.now()
        stamp = self.session_start_time.strftime("%d %b %Y - %I:%M %p")
        system_name = (self.config_mgr.get_setting("SystemName", "") or "").strip() if self.config_mgr else ""
        if system_name:
            self.session_thread_subject = f"{system_name} - Entertainment Hub Session [{stamp}] - {getattr(self.account, 'email', '')}"
        else:
            self.session_thread_subject = f"Entertainment Hub Session [{stamp}] - {getattr(self.account, 'email', '')}"
        self.session_thread_url: Optional[str] = None
        self.session_thread_message_id: Optional[str] = None
        self.session_thread_info: Dict[str, Any] = {}
        EmailReporter.reset_session_thread()
        self.reporting_driver: Optional[Any] = None
        self.reporting_thread: Optional[threading.Thread] = None
        self.has_started_browsing = False
        self.is_sleeping: bool = False
        self.cycle_report_dispatched: bool = False
        self.sleep_cycle_count: int = 0
        self.night_sleep_count: int = 0
        self.night_scheduler = NightSleepScheduler(config_mgr=self.config_mgr)

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
        skip_ads = self.config_mgr.get_bool("SkipAds", True) or self.config_mgr.get_bool("BlockAds", True)
        max_ad_seconds = self.config_mgr.get_int("MaxAdWatchSeconds", 9)
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
            "fullscreen_theater_mode": self.config_mgr.get_bool("FullscreenTheaterMode", True),
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

        cfg = getattr(self, "config_mgr", None)
        if cfg is not None:
            try:
                stored_dicts = cfg.get_channels()
                if stored_dicts is not None:
                    stored_objs = [Channel.from_dict(c) for c in stored_dicts]
                    return [c for c in stored_objs if _is_active(c)]
            except Exception as exc:
                emit(self.log_queue, f"Failed to read channels from configuration: {exc}", logging.ERROR)
                return []
        raw_channels = getattr(self, "channels", []) or []
        return [c for c in raw_channels if _is_active(c)]

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
            if is_session_disconnected_error(exc):
                emit(self.log_queue, f"Browser session disconnected during home page videos fetch: {exc}", logging.ERROR)
                raise
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
            if is_session_disconnected_error(exc):
                emit(self.log_queue, f"Browser session disconnected during recommended videos fetch: {exc}", logging.ERROR)
                raise
            emit(self.log_queue, f"Error fetching recommended videos: {exc}", logging.WARNING)
            return []

    def _attempt_element_click_and_verify(
        self,
        element: Any,
        vid_id: str,
        is_short: bool = False,
        is_home: bool = True,
    ) -> bool:
        """Attempt authentic, organic in-page OS-level pointer clicks on a DOM element and verify navigation.

        Uses strictly real human-like OS pointer interactions:
        1. Native element.click() (OS-level driver event, isTrusted: true)
        2. Selenium ActionChains physical mouse movement and coordinate click (isTrusted: true)

        Synthetic JavaScript dispatchEvent and direct script clicks are removed to ensure
        all clicks are genuine, organic, and pass YouTube's isTrusted telemetry.
        Never navigates via the browser address bar.
        """
        if not element:
            return False

        def _is_navigated() -> bool:
            try:
                cur = getattr(self.driver, "current_url", "") or ""
                if vid_id and vid_id in cur:
                    return True
                if is_short:
                    if "/shorts/" in cur:
                        return True
                elif is_home:
                    if "/watch" in cur:
                        return True
            except Exception:
                pass
            return False

        if _is_navigated():
            return True

        # Center element in viewport immediately so it is in view for physical OS click
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'instant', block: 'center', inline: 'center'});",
                element,
            )
            time.sleep(0.15)
        except Exception:
            pass

        # Strategy 1: Native element click (OS-level browser event with isTrusted: true)
        try:
            element.click()
            for _ in range(5):
                time.sleep(0.2)
                if _is_navigated():
                    return True
        except Exception as exc:
            err_line = (getattr(exc, "msg", None) or str(exc)).split("\n")[0].strip()
            emit(self.log_queue, f"Native element click attempt bypassed ({err_line})", logging.DEBUG)

        if _is_navigated():
            return True

        # Strategy 2: Selenium ActionChains physical mouse movement and coordinate click (isTrusted: true)
        try:
            ActionChains(self.driver).move_to_element(element).click().perform()
            for _ in range(5):
                time.sleep(0.2)
                if _is_navigated():
                    return True
        except Exception as exc:
            err_line = (getattr(exc, "msg", None) or str(exc)).split("\n")[0].strip()
            emit(self.log_queue, f"ActionChains click attempt bypassed ({err_line})", logging.DEBUG)

        return _is_navigated()

    def click_home_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on the target video card on the home page and verify navigation."""
        href = card_info.get("href", "")
        vid_id = extract_video_id(href) or (href.split("v=")[-1].split("&")[0] if "v=" in href else "")
        title = card_info.get("title", "")
        try:
            # 1. Candidate-based in-page discovery and cascading click (prioritizing title link over thumbnail)
            if vid_id or title:
                try:
                    candidates = self.driver.execute_script(r"""
                        var vidId = arguments[0];
                        var targetTitle = arguments[1] || '';
                        var candidates = [];
                        if (!vidId && !targetTitle) return candidates;

                        var cleanLinks = [];
                        if (vidId) {
                            var allLinks = Array.from(document.querySelectorAll('a[href*="' + vidId + '"]'));
                            cleanLinks = allLinks.filter(function(l) {
                                if (!l) return false;
                                if (l.closest && l.closest(
                                    'ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, [layout*="ad"], ' +
                                    'square-image-layout-view-model, ad-image-view-model, feed-ad-metadata-view-model'
                                )) return false;
                                if (l.closest && l.closest('#guide, ytd-guide-renderer, ytd-mini-guide-renderer, #masthead')) return false;
                                return true;
                            });
                        }

                        // Sub-priority 1: Video title links (NEVER triggers hover preview player)
                        for (var i = 0; i < cleanLinks.length; i++) {
                            var l = cleanLinks[i];
                            if (
                                l.id === 'video-title-link' ||
                                l.id === 'video-title' ||
                                (l.className && typeof l.className === 'string' && l.className.indexOf('title') !== -1) ||
                                l.closest('h3') ||
                                l.closest('#meta') ||
                                l.closest('#details') ||
                                l.querySelector('#video-title')
                            ) {
                                candidates.push(l);
                                var innerTitle = l.querySelector('#video-title, yt-formatted-string, span');
                                if (innerTitle && innerTitle !== l) {
                                    candidates.push(innerTitle);
                                }
                                break;
                            }
                        }

                        // Sub-priority 2: Any clean non-thumbnail link
                        for (var i = 0; i < cleanLinks.length; i++) {
                            var l = cleanLinks[i];
                            var isThumb = l.id === 'thumbnail' || (l.closest && l.closest('ytd-thumbnail')) ||
                                          (l.className && typeof l.className === 'string' && l.className.indexOf('content-image') !== -1);
                            if (!isThumb && candidates.indexOf(l) === -1) {
                                candidates.push(l);
                                break;
                            }
                        }

                        // Sub-priority 3: Thumbnail links (if title links are not available or fail)
                        for (var i = 0; i < cleanLinks.length; i++) {
                            var l = cleanLinks[i];
                            var isThumb = l.id === 'thumbnail' || (l.closest && l.closest('ytd-thumbnail')) ||
                                          (l.className && typeof l.className === 'string' && l.className.indexOf('content-image') !== -1);
                            if (isThumb && candidates.indexOf(l) === -1) {
                                candidates.push(l);
                                break;
                            }
                        }

                        // Sub-priority 4: Enclosing card container
                        if (cleanLinks.length > 0) {
                            var card = cleanLinks[0].closest('ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, yt-lockup-view-model');
                            if (card && candidates.indexOf(card) === -1) {
                                candidates.push(card);
                            }
                        }

                        // Fallback search by title text if provided and no candidates yet
                        if (targetTitle && candidates.length === 0) {
                            var q = targetTitle.toLowerCase().trim();
                            var allTitles = Array.from(document.querySelectorAll('#video-title, a#video-title-link, h3 a, h3'));
                            for (var j = 0; j < allTitles.length; j++) {
                                var tEl = allTitles[j];
                                if (tEl.closest && tEl.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, [layout*="ad"]')) continue;
                                var text = (tEl.getAttribute('title') || tEl.getAttribute('aria-label') || tEl.textContent || '').trim().toLowerCase();
                                if (text && text.indexOf(q) !== -1) {
                                    candidates.push(tEl);
                                    break;
                                }
                            }
                        }

                        return candidates;
                    """, vid_id, title)
                    if isinstance(candidates, list):
                        for el in candidates:
                            if not el:
                                continue
                            if self._attempt_element_click_and_verify(el, vid_id=vid_id, is_short=False, is_home=True):
                                emit(self.log_queue, f"Successfully navigated to Home video via in-page element (vid_id: '{vid_id}').", logging.DEBUG)
                                return True
                except Exception as exc:
                    emit(self.log_queue, f"Direct home video click attempt error: {exc}", logging.DEBUG)

            # 2. Index-based element click fallback (with strict ad check)
            idx = int(card_info.get("index", 0)) if str(card_info.get("index", "")).isdigit() else -1
            if idx >= 0:
                try:
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
                            clickable = None
                            for selector in (
                                "a#video-title-link",
                                "h3 a",
                                "#video-title",
                                "a.ytLockupMetadataViewModelTitle",
                                "a#thumbnail",
                                "a.yt-simple-endpoint",
                                "a[href*='/watch']",
                            ):
                                els = item.find_elements(By.CSS_SELECTOR, selector)
                                if els and els[0].is_displayed():
                                    clickable = els[0]
                                    break
                            target = clickable or item
                            if self._attempt_element_click_and_verify(target, vid_id=vid_id, is_short=False, is_home=True):
                                return True
                        else:
                            emit(self.log_queue, "Home video item at index is an ad element; skipping index click to avoid ad.", logging.WARNING)
                except Exception as exc:
                    emit(self.log_queue, f"Index-based home video click attempt error: {exc}", logging.DEBUG)

            # 3. XPath fallback matching vid_id (with strict ad check, prioritizing title links)
            if href and vid_id:
                try:
                    for xpath_expr in (
                        f"//a[contains(@href, '{vid_id}') and (@id='video-title-link' or contains(@class, 'title') or ancestor::h3)]",
                        f"//a[contains(@href, '{vid_id}')]",
                    ):
                        match_links = self.driver.find_elements(By.XPATH, xpath_expr)
                        for lk in match_links:
                            if lk.is_displayed():
                                is_ad_lk = self.driver.execute_script(r"""
                                    var el = arguments[0];
                                    if (!el) return true;
                                    return !!el.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, square-image-layout-view-model');
                                """, lk)
                                if is_ad_lk:
                                    continue
                                if self._attempt_element_click_and_verify(lk, vid_id=vid_id, is_short=False, is_home=True):
                                    return True
                except Exception as exc:
                    emit(self.log_queue, f"XPath home video click attempt error: {exc}", logging.DEBUG)

        except Exception as exc:
            emit(self.log_queue, f"Home video click error: {exc}", logging.WARNING)

        # Do NOT fallback to direct navigation in address bar
        emit(
            self.log_queue,
            f"In-page click on Home video failed to navigate (vid_id: '{vid_id or href}'). Direct address bar navigation is disabled.",
            logging.WARNING,
        )
        return False

    def click_recommended_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on a recommended video item from the sidebar/recommendations and verify navigation."""
        href = card_info.get("href", "")
        vid_id = extract_video_id(href) or (href.split("v=")[-1].split("&")[0] if "v=" in href else "")
        title = card_info.get("title", "")
        try:
            # 1. Candidate-based in-page discovery and cascading click (prioritizing title link over thumbnail)
            if vid_id or title:
                try:
                    candidates = self.driver.execute_script(r"""
                        var vidId = arguments[0];
                        var targetTitle = arguments[1] || '';
                        var candidates = [];
                        if (!vidId && !targetTitle) return candidates;

                        var cleanLinks = [];
                        if (vidId) {
                            var allLinks = Array.from(document.querySelectorAll(
                                '#related a[href*="' + vidId + '"], ' +
                                'ytd-watch-next-secondary-results-renderer a[href*="' + vidId + '"], ' +
                                'a[href*="' + vidId + '"]'
                            ));
                            cleanLinks = allLinks.filter(function(l) {
                                if (!l) return false;
                                if (l.closest && l.closest(
                                    'ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, [layout*="ad"], ' +
                                    'square-image-layout-view-model, ad-image-view-model, feed-ad-metadata-view-model'
                                )) return false;
                                if (l.closest && l.closest('#guide, ytd-guide-renderer, ytd-mini-guide-renderer, #masthead')) return false;
                                return true;
                            });
                        }

                        // Sub-priority 1: Title links
                        for (var i = 0; i < cleanLinks.length; i++) {
                            var l = cleanLinks[i];
                            if (
                                l.id === 'video-title-link' ||
                                l.id === 'video-title' ||
                                (l.className && typeof l.className === 'string' && l.className.indexOf('Title') !== -1) ||
                                l.closest('h3') ||
                                l.closest('#meta') ||
                                l.querySelector('#video-title')
                            ) {
                                candidates.push(l);
                                var innerTitle = l.querySelector('#video-title, yt-formatted-string, span');
                                if (innerTitle && innerTitle !== l) {
                                    candidates.push(innerTitle);
                                }
                                break;
                            }
                        }

                        // Sub-priority 2: Any clean non-thumbnail link
                        for (var i = 0; i < cleanLinks.length; i++) {
                            var l = cleanLinks[i];
                            var isThumb = l.id === 'thumbnail' || (l.closest && l.closest('ytd-thumbnail')) ||
                                          (l.className && typeof l.className === 'string' && l.className.indexOf('Image') !== -1);
                            if (!isThumb && candidates.indexOf(l) === -1) {
                                candidates.push(l);
                                break;
                            }
                        }

                        // Sub-priority 3: Thumbnail links
                        for (var i = 0; i < cleanLinks.length; i++) {
                            var l = cleanLinks[i];
                            var isThumb = l.id === 'thumbnail' || (l.closest && l.closest('ytd-thumbnail')) ||
                                          (l.className && typeof l.className === 'string' && l.className.indexOf('Image') !== -1);
                            if (isThumb && candidates.indexOf(l) === -1) {
                                candidates.push(l);
                                break;
                            }
                        }

                        // Sub-priority 4: Enclosing card container
                        if (cleanLinks.length > 0) {
                            var card = cleanLinks[0].closest('ytd-compact-video-renderer, yt-lockup-view-model');
                            if (card && candidates.indexOf(card) === -1) {
                                candidates.push(card);
                            }
                        }

                        // Fallback search by title text if provided
                        if (targetTitle && candidates.length === 0) {
                            var q = targetTitle.toLowerCase().trim();
                            var allTitles = Array.from(document.querySelectorAll('#related #video-title, #related a, ytd-compact-video-renderer #video-title'));
                            for (var j = 0; j < allTitles.length; j++) {
                                var tEl = allTitles[j];
                                if (tEl.closest && tEl.closest('ytd-ad-slot-renderer, [layout*="ad"]')) continue;
                                var text = (tEl.getAttribute('title') || tEl.getAttribute('aria-label') || tEl.textContent || '').trim().toLowerCase();
                                if (text && text.indexOf(q) !== -1) {
                                    candidates.push(tEl);
                                    break;
                                }
                            }
                        }

                        return candidates;
                    """, vid_id, title)
                    if isinstance(candidates, list):
                        for el in candidates:
                            if not el:
                                continue
                            if self._attempt_element_click_and_verify(el, vid_id=vid_id, is_short=False, is_home=False):
                                emit(self.log_queue, f"Successfully navigated to Suggested video via in-page element (vid_id: '{vid_id}').", logging.DEBUG)
                                return True
                except Exception as exc:
                    emit(self.log_queue, f"Direct recommended video click attempt error: {exc}", logging.DEBUG)

            # 2. Index-based element click fallback (with strict ad check)
            idx = int(card_info.get("index", 0)) if str(card_info.get("index", "")).isdigit() else -1
            if idx >= 0:
                try:
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
                            clickable = None
                            for selector in (
                                "a.ytLockupMetadataViewModelTitle",
                                "a#video-title-link",
                                "h3 a",
                                "#video-title",
                                "span#video-title",
                                "a.ytLockupViewModelContentImage",
                                "a#thumbnail",
                                "a.yt-simple-endpoint",
                                "a[href*='/watch']",
                            ):
                                els = item.find_elements(By.CSS_SELECTOR, selector)
                                if els and els[0].is_displayed():
                                    clickable = els[0]
                                    break
                            target = clickable or item
                            if self._attempt_element_click_and_verify(target, vid_id=vid_id, is_short=False, is_home=False):
                                return True
                        else:
                            emit(self.log_queue, "Recommended item at index is an ad element; skipping index click to avoid ad.", logging.WARNING)
                except Exception as exc:
                    emit(self.log_queue, f"Index-based recommended video click attempt error: {exc}", logging.DEBUG)

            # 3. XPath fallback matching vid_id (with strict ad check, prioritizing title links)
            if href and vid_id:
                try:
                    for xpath_expr in (
                        f"//a[contains(@href, '{vid_id}') and (@id='video-title-link' or contains(@class, 'Title') or contains(@class, 'title') or ancestor::h3)]",
                        f"//a[contains(@href, '{vid_id}')]",
                    ):
                        match_links = self.driver.find_elements(By.XPATH, xpath_expr)
                        for lk in match_links:
                            if lk.is_displayed():
                                is_ad_lk = self.driver.execute_script(r"""
                                    var el = arguments[0];
                                    if (!el) return true;
                                    return !!el.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ad-slot-renderer, square-image-layout-view-model');
                                """, lk)
                                if is_ad_lk:
                                    continue
                                if self._attempt_element_click_and_verify(lk, vid_id=vid_id, is_short=False, is_home=False):
                                    return True
                except Exception as exc:
                    emit(self.log_queue, f"XPath recommended video click attempt error: {exc}", logging.DEBUG)

        except Exception as exc:
            emit(self.log_queue, f"Recommended video click error: {exc}", logging.WARNING)

        # Do NOT fallback to direct navigation in address bar
        emit(
            self.log_queue,
            f"In-page click on Suggested video failed to navigate (vid_id: '{vid_id or href}'). Direct address bar navigation is disabled.",
            logging.WARNING,
        )
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
            self._emit_event(("channel_watch_time_updated",))
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

    def _get_cooldown_hours(self) -> float:
        mgr = getattr(self, "config_mgr", None)
        if mgr is None:
            return 18.0
        try:
            val = mgr.get_float("VideoCooldownHours", 18.0)
            if isinstance(val, (int, float)):
                return float(val)
            return 18.0
        except Exception:
            return 18.0

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

        cooldown_hours = self._get_cooldown_hours()

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

        cooldown_hours = self._get_cooldown_hours()
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
        if (target_rec_id and target_rec_id not in cur_url) or not clicked:
            emit(
                self.log_queue,
                f"In-page click on suggested video for random buffer failed to navigate. Direct address bar navigation is disabled.",
                logging.WARNING,
            )
            return False

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

        if not self._check_account_login_before_play(content_type="random video", content_title=title):
            return False
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
            if self.stats_store:
                self._record_watch(
                    title=title,
                    channel_name="",
                    watch_seconds=watch_res.watched_seconds,
                    ad_seconds=watch_res.ad_seconds,
                    duration=watch_res.video_duration,
                    completed=watch_res.completed,
                    playback_finished=watch_res.playback_finished,
                    interrupted=watch_res.interrupted,
                )

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

        inherited_mult = (
            self.config_mgr.get_float("DefaultVideoTargetViews", DEFAULT_TARGET_MULTIPLIER)
            if self.config_mgr
            else DEFAULT_TARGET_MULTIPLIER
        )
        inherited_max_runs = (
            self.config_mgr.get_int("DefaultVideoMaxViews", DEFAULT_MAX_RUNS)
            if self.config_mgr
            else DEFAULT_MAX_RUNS
        )
        if type(inherited_max_runs) is not int:
            inherited_max_runs = DEFAULT_MAX_RUNS
        raw_q = self.config_mgr.get_video_queue()
        for q_item in raw_q:
            if (q_item.get("channel") or "").strip().lower() == channel_display.strip().lower():
                if q_item.get("target_multiplier", 0.0) > 0:
                    inherited_mult = float(q_item["target_multiplier"])
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
        elif source == "search":
            clicked = True
        else:
            clicked = self.click_recommended_video(card_info)

        cur_url = getattr(self.driver, "current_url", "") or ""
        cur_id = extract_video_id(cur_url)
        if (target_vid_id and cur_id != target_vid_id) or not clicked:
            try:
                if target_vid_id:
                    WebDriverWait(self.driver, 5).until(lambda d: extract_video_id(getattr(d, "current_url", "") or "") == target_vid_id)
                else:
                    WebDriverWait(self.driver, 5).until(lambda d: "/watch" in (getattr(d, "current_url", "") or ""))
            except Exception:
                pass

        final_cur_url = getattr(self.driver, "current_url", "") or ""
        final_cur_id = extract_video_id(final_cur_url)
        if target_vid_id and final_cur_id != target_vid_id:
            emit(
                self.log_queue,
                f"Playback identity verification failed: expected video ID '{target_vid_id}', observed '{final_cur_id or final_cur_url}'. Direct address bar navigation is disabled.",
                logging.ERROR,
            )
            return WatchResult(completed=False, reason="navigation_identity_mismatch", interrupted=True)
        if not clicked:
            emit(
                self.log_queue,
                f"In-page click did not trigger watch navigation to video '{title}'. Direct address bar navigation is disabled.",
                logging.WARNING,
            )
            return WatchResult(completed=False, reason="in_page_click_failed", interrupted=True)

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

        if not self._check_account_login_before_play(content_type="video", content_title=title):
            return
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
            if is_session_disconnected_error(exc):
                emit(self.log_queue, f"Browser session disconnected during home shorts fetch: {exc}", logging.ERROR)
                raise
            emit(self.log_queue, f"Error fetching home shorts: {exc}", logging.DEBUG)
            return []

    def get_target_shorts(self, filter_cooldown: bool = False) -> List[VideoTask]:
        """Retrieve all enabled target shorts configured in the app."""
        target_shorts: List[VideoTask] = []
        cooldown_hours = self._get_cooldown_hours()
        cfg = getattr(self, "config_mgr", None)
        if cfg:
            try:
                raw_queue = cfg.get_video_queue()
                for item in raw_queue:
                    if not isinstance(item, dict):
                        continue
                    task = VideoTask.from_dict(item)
                    if task.enabled and (task.is_short or "/shorts/" in (task.url or "") or bool(item.get("is_short", False))):
                        if filter_cooldown and cooldown_hours > 0 and getattr(self, "stats_store", None):
                            rec = self.stats_store.find_for_task(task)
                            if rec and hasattr(rec, "is_on_cooldown"):
                                try:
                                    res = rec.is_on_cooldown(cooldown_hours)
                                    is_cool = res[0] if (isinstance(res, tuple) and len(res) == 2) else False
                                except Exception:
                                    is_cool = False
                                if is_cool:
                                    continue
                        target_shorts.append(task)
            except Exception as exc:
                emit(self.log_queue, f"Error loading target shorts from queue: {exc}", logging.DEBUG)
        return target_shorts

    def match_home_short(
        self,
        candidate_shorts: List[Dict[str, Any]],
        target_shorts: List[VideoTask],
        session_watched_keys: Optional[Set[str]] = None,
        target_channels: Optional[Sequence[Channel]] = None,
    ) -> Optional[Tuple[Dict[str, Any], VideoTask]]:
        """
        Match only one Short at a time by inspecting the first Short in the list of candidate shorts
        and matching its URL / video ID / title / channel against our Shorts Queue and targeted channels.
        Returns (first_short, matched_task) if there is a match, otherwise None.
        """
        if not candidate_shorts:
            return None

        first_short = candidate_shorts[0]
        cand_id = first_short.get("video_id") or extract_video_id(first_short.get("href", ""))
        cand_url = first_short.get("url") or first_short.get("href", "")
        cand_title = (first_short.get("title") or "").strip().lower()
        cand_channel = (first_short.get("channel") or "").strip()
        cand_ch_url = (first_short.get("channel_url") or "").strip()

        if not cand_id and not cand_url and not cand_title and not cand_channel:
            return None

        # 1. Match against target shorts queue
        if target_shorts:
            for t in target_shorts:
                if not getattr(t, "enabled", True):
                    continue

                # 1a. Match by Video ID
                task_id = extract_video_id(t.url)
                if task_id and cand_id and task_id.lower() == cand_id.lower():
                    return (first_short, t)

                # 1b. Match by Clean URL
                clean_cand_url = cand_url.split("?")[0].rstrip("/") if cand_url else ""
                clean_task_url = t.url.split("?")[0].rstrip("/") if t.url else ""
                if clean_cand_url and clean_task_url and clean_cand_url == clean_task_url:
                    return (first_short, t)

                # 1c. Match by Title / Search Queries
                queries = t.get_title_queries() if hasattr(t, "get_title_queries") else [t.title]
                if cand_title and queries:
                    for q in queries:
                        q_clean = q.strip().lower()
                        if not q_clean:
                            continue
                        if q_clean == cand_title:
                            return (first_short, t)
                        if len(q_clean) >= 4 and (q_clean in cand_title or cand_title in q_clean):
                            return (first_short, t)

                # 1d. Match by Channel (normalized to handle @handle vs Display Name)
                if cand_channel and getattr(t, "channel", ""):
                    norm_task_ch = normalize_channel_text(t.channel)
                    norm_cand_ch = normalize_channel_text(cand_channel)
                    if norm_task_ch and norm_cand_ch:
                        if norm_task_ch == norm_cand_ch or norm_task_ch in norm_cand_ch or norm_cand_ch in norm_task_ch:
                            return (first_short, t)

        # 2. Match against targeted channels
        ch_list = target_channels if target_channels is not None else (self.get_target_channels() if hasattr(self, "get_target_channels") else [])
        if ch_list and (cand_channel or cand_ch_url):
            ch_lookup = cand_ch_url or (f"/{cand_channel}" if cand_channel.startswith("@") else "")
            matched_ch = match_channel(cand_channel, ch_lookup, ch_list)
            if not matched_ch and cand_channel:
                norm_cand = normalize_channel_text(cand_channel)
                for ch in ch_list:
                    if ch.name and norm_cand:
                        norm_ch = normalize_channel_text(ch.name)
                        if norm_ch and (norm_ch == norm_cand or norm_ch in norm_cand or norm_cand in norm_ch):
                            matched_ch = ch
                            break

            if matched_ch:
                found_task = None
                if cand_id and getattr(self, "config_mgr", None):
                    try:
                        for item in self.config_mgr.get_video_queue():
                            if isinstance(item, dict):
                                vt = VideoTask.from_dict(item)
                                if cand_id in (vt.url or "") or extract_video_id(vt.url) == cand_id:
                                    found_task = vt
                                    break
                    except Exception:
                        pass
                if not found_task and target_shorts:
                    for t in target_shorts:
                        if cand_id and (cand_id in (t.url or "") or extract_video_id(t.url) == cand_id):
                            found_task = t
                            break
                if not found_task:
                    def_shorts_max = (
                        self.config_mgr.get_int("DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS)
                        if getattr(self, "config_mgr", None)
                        else DEFAULT_SHORTS_MAX_RUNS
                    )
                    if type(def_shorts_max) is not int:
                        def_shorts_max = DEFAULT_SHORTS_MAX_RUNS
                    found_task = VideoTask(
                        url=first_short.get("url") or (f"https://www.youtube.com/shorts/{cand_id}" if cand_id else ""),
                        title=first_short.get("title") or "Short",
                        channel=matched_ch.name,
                        enabled=True,
                        max_runs=def_shorts_max,
                        target_multiplier=0.0,
                    )
                    if hasattr(self, "_ensure_video_in_queue"):
                        found_task = self._ensure_video_in_queue(found_task)
                return (first_short, found_task)

        return None


    def _has_suggested_shorts_shelf(self) -> bool:
        """Check if a suggested Shorts shelf (ytd-reel-shelf-renderer) exists on current page."""
        try:
            return bool(self.driver.execute_script(
                "return !!document.querySelector('ytd-reel-shelf-renderer');"
            ))
        except Exception:
            return False

    def fetch_suggested_shelf_shorts(self) -> List[Dict[str, Any]]:
        """Fetch all shorts items from the suggested shorts shelf on watch page, including visibility flags."""
        try:
            items = self.driver.execute_script(SUGGESTED_SHORTS_JS)
            if isinstance(items, list):
                return items
        except Exception as exc:
            emit(self.log_queue, f"Error fetching suggested shelf shorts: {exc}", logging.DEBUG)
        return []

    def click_suggested_shelf_next_arrow(self) -> bool:
        """Click the Next arrow button on the suggested shorts shelf carousel to reveal the next 3 shorts."""
        try:
            return bool(self.driver.execute_script(CLICK_SUGGESTED_NEXT_ARROW_JS))
        except Exception as exc:
            emit(self.log_queue, f"Error clicking suggested shorts next arrow: {exc}", logging.DEBUG)
            return False

    def click_suggested_short(self, short_info: Dict[str, Any]) -> bool:
        """Click on the target short card in the suggested shorts shelf."""
        return self.click_home_short(short_info)

    def find_target_short_on_suggested_shelf(
        self,
        session_watched_keys: Optional[Set[str]] = None,
        max_arrow_clicks: int = 5,
    ) -> Optional[Tuple[Dict[str, Any], VideoTask]]:
        """
        Scan the suggested Shorts shelf (<ytd-reel-shelf-renderer>) on the current watch page.
        Displays 3 shorts per view. Checks visible shorts against the Shorts Queue;
        if none match, clicks the Next arrow button to reveal the next 3 shorts (up to max_arrow_clicks times).
        Returns (cand_item, task_item) if a match is found, or None.
        """
        cooldown_hours = self._get_cooldown_hours()
        target_shorts = self.get_target_shorts(filter_cooldown=False)
        target_channels = self.get_target_channels() if hasattr(self, "get_target_channels") else []

        if not target_shorts and not target_channels:
            emit(self.log_queue, "No enabled Shorts or targeted Channels configured. Skipping suggested Shorts search.", logging.INFO)
            return None

        shelf_found = self._has_suggested_shorts_shelf()
        if not shelf_found:
            for _ in range(2):
                if self.stop_event.is_set():
                    return None
                try:
                    self.driver.execute_script(r"""
                        var sec = document.querySelector('#secondary') || document.querySelector('#related');
                        if (sec) sec.scrollIntoView({behavior: 'smooth', block: 'start'});
                        window.scrollBy({top: 600, behavior: 'smooth'});
                    """)
                except Exception:
                    pass
                human_like_delay(1.0, 1.5)
                shelf_found = self._has_suggested_shorts_shelf()
                if shelf_found:
                    break

        if not shelf_found:
            emit(self.log_queue, "No suggested Shorts shelf (<ytd-reel-shelf-renderer>) found on this watch page.", logging.INFO)
            return None

        emit(self.log_queue, "Found Shorts shelf in suggested section. Inspecting visible Shorts...", logging.INFO)
        try:
            self.driver.execute_script(r"""
                var shelf = document.querySelector('ytd-reel-shelf-renderer');
                if (shelf) shelf.scrollIntoView({behavior: 'smooth', block: 'center'});
            """)
            human_like_delay(0.8, 1.2)
        except Exception:
            pass

        seen_cand_ids: Set[str] = set()
        total_candidates_checked = 0

        for slide_idx in range(max_arrow_clicks + 1):
            if self.stop_event.is_set():
                return None
            self.check_pause()
            if self.stop_event.is_set():
                return None

            shelf_items = self.fetch_suggested_shelf_shorts()
            visible_items = [item for item in shelf_items if item.get("is_visible")]
            if not visible_items and shelf_items:
                start_i = slide_idx * 3
                visible_items = shelf_items[start_i : start_i + 3] if start_i < len(shelf_items) else shelf_items[:3]

            for cand in visible_items:
                cid = cand.get("video_id") or extract_video_id(cand.get("href", ""))
                if cid and cid not in seen_cand_ids:
                    seen_cand_ids.add(cid)
                    total_candidates_checked += 1

            for cand in visible_items:
                cid = cand.get("video_id") or extract_video_id(cand.get("href", ""))
                curl = cand.get("url") or cand.get("href", "")
                if session_watched_keys and (cid in session_watched_keys or curl in session_watched_keys):
                    continue

                if cid and getattr(self, "stats_store", None) and cooldown_hours > 0:
                    rec_stat = self.stats_store.get(cid)
                    if rec_stat and hasattr(rec_stat, "is_on_cooldown"):
                        try:
                            res = rec_stat.is_on_cooldown(cooldown_hours)
                            is_cool = res[0] if (isinstance(res, tuple) and len(res) == 2) else False
                        except Exception:
                            is_cool = False
                        if is_cool:
                            continue

                m = self.match_home_short([cand], target_shorts, session_watched_keys, target_channels=target_channels)
                if m:
                    cand_item, task_item = m
                    if getattr(self, "stats_store", None) and cooldown_hours > 0:
                        task_rec = self.stats_store.find_for_task(task_item)
                        if task_rec and hasattr(task_rec, "is_on_cooldown"):
                            try:
                                res = task_rec.is_on_cooldown(cooldown_hours)
                                is_cool, rem_hours = res if (isinstance(res, tuple) and len(res) == 2) else (False, 0.0)
                            except Exception:
                                is_cool, rem_hours = False, 0.0
                            if is_cool:
                                rem_str = f"{rem_hours:.1f}h" if rem_hours >= 1.0 else f"{int(rem_hours * 60)}m"
                                emit(
                                    self.log_queue,
                                    f"Skipping matched suggested Short '{cand_item.get('title')}' for task '{task_item.title}': cooldown ({rem_str} remaining).",
                                    logging.INFO,
                                )
                                continue

                    cand_item["source"] = "Suggested section"
                    cand_item["source_type"] = "suggested"
                    emit(
                        self.log_queue,
                        f"Found matching Short on suggested shelf (slide {slide_idx + 1}): '{cand_item.get('title')}' matching Shorts Queue task '{task_item.title or task_item.url}'.",
                        logging.INFO,
                    )
                    return m

            if slide_idx < max_arrow_clicks and not self.stop_event.is_set():
                emit(
                    self.log_queue,
                    f"None of the visible Shorts on suggested shelf matched Shorts Queue (slide {slide_idx + 1}/{max_arrow_clicks + 1}). Clicking Next arrow to show next 3 Shorts...",
                    logging.INFO,
                )
                clicked = self.click_suggested_shelf_next_arrow()
                if not clicked:
                    emit(
                        self.log_queue,
                        "Reached end of suggested Shorts carousel or Next arrow button is disabled/hidden.",
                        logging.INFO,
                    )
                    break
                human_like_delay(1.5, 2.2)

        emit(
            self.log_queue,
            f"No matching Short found on suggested shelf ({total_candidates_checked} candidate(s) checked across {slide_idx + 1} slide(s)).",
            logging.INFO,
        )
        return None

    def find_target_short_on_home(
        self,
        session_watched_keys: Optional[Set[str]] = None,
        max_scrolls: int = 3,
    ) -> Optional[Tuple[Dict[str, Any], VideoTask]]:
        """Scan YouTube Home page shelves for targeted shorts, scrolling down up to max_scrolls times if needed."""
        cooldown_hours = self._get_cooldown_hours()
        target_shorts = self.get_target_shorts(filter_cooldown=False)
        target_channels = self.get_target_channels() if hasattr(self, "get_target_channels") else []

        if not target_shorts and not target_channels:
            emit(self.log_queue, "No enabled Shorts or targeted Channels configured. Skipping Home Shorts search.", logging.INFO)
            return None

        total_candidates_seen = 0
        seen_cand_ids: Set[str] = set()

        for scroll_idx in range(max_scrolls + 1):
            if self.stop_event.is_set():
                return None
            self.check_pause()
            if self.stop_event.is_set():
                return None

            candidate_shorts = self.fetch_home_shorts()
            if candidate_shorts:
                self._last_home_shorts = candidate_shorts
            for cand in candidate_shorts:
                cid = cand.get("video_id") or extract_video_id(cand.get("href", ""))
                if cid and cid not in seen_cand_ids:
                    seen_cand_ids.add(cid)
                    total_candidates_seen += 1

            for cand in candidate_shorts:
                cid = cand.get("video_id") or extract_video_id(cand.get("href", ""))
                curl = cand.get("url") or cand.get("href", "")
                if session_watched_keys and (cid in session_watched_keys or curl in session_watched_keys):
                    continue

                m = self.match_home_short([cand], target_shorts, session_watched_keys, target_channels=target_channels)
                if m:
                    cand_item, task_item = m
                    # Check cooldown
                    is_cool = False
                    rem_hours = 0.0
                    if getattr(self, "stats_store", None) and cooldown_hours > 0:
                        rec = None
                        if cid:
                            rec = self.stats_store.get(cid)
                        if not rec and task_item:
                            rec = self.stats_store.find_for_task(task_item)
                        if rec and hasattr(rec, "is_on_cooldown"):
                            try:
                                res = rec.is_on_cooldown(cooldown_hours)
                                if isinstance(res, tuple) and len(res) == 2:
                                    is_cool, rem_hours = res
                                else:
                                    is_cool, rem_hours = False, 0.0
                            except Exception:
                                is_cool, rem_hours = False, 0.0

                    if is_cool:
                        rem_str = f"{rem_hours:.1f}h" if rem_hours >= 1.0 else f"{int(rem_hours * 60)}m"
                        ch_label = getattr(task_item, "channel", "") or cand_item.get("channel") or "Target Channel"
                        emit(
                            self.log_queue,
                            f"Skipping matched Short '{cand_item.get('title')}' from channel '{ch_label}': within {cooldown_hours:g}h cooldown period ({rem_str} remaining).",
                            logging.INFO,
                        )
                        continue

                    ch_display = getattr(task_item, "channel", "") or cand_item.get("channel") or "Target Channel"
                    emit(
                        self.log_queue,
                        f"Found matching Short on Home feed: '{cand_item.get('title')}' from targeted channel '{ch_display}'.",
                        logging.INFO,
                    )
                    return m

            if scroll_idx < max_scrolls and not self.stop_event.is_set():
                if len(candidate_shorts) > 0:
                    cand_details = []
                    for c in candidate_shorts[:3]:
                        c_title = c.get("title") or "Short"
                        c_ch = c.get("channel") or ""
                        cand_details.append(f"'{c_title}'" + (f" by {c_ch}" if c_ch else ""))
                    detail_str = ", ".join(cand_details)
                    if len(candidate_shorts) > 3:
                        detail_str += f" (+{len(candidate_shorts) - 3} more)"
                    emit(
                        self.log_queue,
                        f"Found {len(candidate_shorts)} untargeted Short(s) on Home feed ({detail_str}), but none matched any enabled task in your Shorts Queue or targeted channels (attempt {scroll_idx + 1}/{max_scrolls + 1}). Scrolling feed down...",
                        logging.INFO,
                    )
                else:
                    emit(
                        self.log_queue,
                        f"Shorts shelf/items not yet visible in Home feed (attempt {scroll_idx + 1}/{max_scrolls + 1}). Scrolling feed down...",
                        logging.INFO,
                    )
                try:
                    self.driver.execute_script("window.scrollBy({top: 1200, behavior: 'smooth'});")
                except Exception:
                    try:
                        self.driver.execute_script("window.scrollBy(0, 1200);")
                    except Exception:
                        pass
                human_like_delay(1.5, 2.5)

        if total_candidates_seen > 0:
            cand_summary = ""
            last_cands = getattr(self, "_last_home_shorts", None) or candidate_shorts
            if last_cands:
                details = []
                for c in last_cands[:3]:
                    c_title = c.get("title") or "Short"
                    c_ch = c.get("channel") or ""
                    details.append(f"'{c_title}'" + (f" by {c_ch}" if c_ch else ""))
                cand_summary = f" (including {', '.join(details)})"
            emit(
                self.log_queue,
                f"No targeted Short appeared on Home feed ({total_candidates_seen} untargeted recommendation(s) checked across {max_scrolls + 1} scroll attempts{cand_summary}).",
                logging.INFO,
            )
        else:
            emit(
                self.log_queue,
                f"No Shorts shelf or items were detected on the Home feed after {max_scrolls + 1} scroll attempts.",
                logging.INFO,
            )
        return None

    def click_shorts_tab(self) -> bool:
        """Click on the Shorts navigation entry in YouTube's sidebar, or navigate directly."""
        try:
            clicked = self.driver.execute_script(r"""
                var selectors = [
                    'ytd-guide-entry-renderer a[title="Shorts"]',
                    'ytd-guide-entry-renderer a[href*="/shorts"]',
                    'ytd-mini-guide-entry-renderer a[title="Shorts"]',
                    'ytd-mini-guide-entry-renderer a[href*="/shorts"]',
                    'a#endpoint[title="Shorts"]',
                    'a[title="Shorts"]',
                    'a[href="/shorts"]'
                ];
                for (var i = 0; i < selectors.length; i++) {
                    var el = document.querySelector(selectors[i]);
                    if (el && el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                }
                return false;
            """)
            if clicked:
                human_like_delay(2.0, 3.5)
                return True
        except Exception:
            pass

        try:
            safe_get(self.driver, "https://www.youtube.com/shorts", timeout=30, log_queue=self.log_queue)
            human_like_delay(2.0, 3.5)
            return True
        except Exception as exc:
            emit(self.log_queue, f"Failed to navigate to YouTube Shorts: {exc}", logging.WARNING)
            return False

    def click_home_short(self, short_info: Dict[str, Any]) -> bool:
        """Click on the target short card on the home page and verify navigation."""
        href = short_info.get("href", "")
        vid_id = short_info.get("video_id") or extract_video_id(href)
        try:
            if vid_id:
                candidates = self.driver.execute_script(r"""
                    var vidId = arguments[0];
                    var links = Array.from(document.querySelectorAll(
                        'ytd-reel-shelf-renderer a[href*="' + vidId + '"], ' +
                        'ytd-rich-shelf-renderer a[href*="' + vidId + '"], ' +
                        'grid-shelf-view-model a[href*="' + vidId + '"], ' +
                        'ytd-rich-section-renderer a[href*="' + vidId + '"], ' +
                        'ytm-shorts-lockup-view-model-v2 a[href*="' + vidId + '"], ' +
                        'ytm-shorts-lockup-view-model a[href*="' + vidId + '"], ' +
                        'shorts-lockup-view-model a[href*="' + vidId + '"], ' +
                        'a[href*="/shorts/' + vidId + '"]'
                    ));
                    var cleanLinks = links.filter(function(l) {
                        if (!l) return false;
                        if (l.closest && l.closest('ytd-ad-slot-renderer, [layout*="ad"]')) return false;
                        if (l.closest && l.closest('#guide, ytd-guide-renderer, ytd-mini-guide-renderer, #masthead')) return false;
                        return true;
                    });
                    var result = [];
                    cleanLinks.forEach(function(l) {
                        result.push(l);
                        var card = l.closest ? l.closest(
                            'ytm-shorts-lockup-view-model-v2, ytm-shorts-lockup-view-model, shorts-lockup-view-model, ytd-rich-item-renderer, ytd-reel-item-renderer'
                        ) : null;
                        if (card && result.indexOf(card) === -1) {
                            result.push(card);
                        }
                    });
                    return result;
                """, vid_id)

                if isinstance(candidates, list):
                    for el in candidates:
                        if not el:
                            continue
                        if self._attempt_element_click_and_verify(el, vid_id=vid_id, is_short=True, is_home=True):
                            return True
        except Exception as exc:
            emit(self.log_queue, f"Error clicking home short: {exc}", logging.DEBUG)

        # Do NOT fallback to direct navigation in address bar
        emit(
            self.log_queue,
            f"In-page click on Home Short failed to navigate (vid_id: '{vid_id or href}'). Direct address bar navigation is disabled.",
            logging.WARNING,
        )
        return False

    def _play_target_short(
        self,
        short_info: Dict[str, Any],
        task: VideoTask,
        session_watched_keys: Set[str],
        enter_reel: bool = True,
        source: str = "home",
    ) -> bool:
        """Watch a matched short for 1-3 loops randomly in a single go and record stats."""
        title = short_info.get("title") or task.title or "Short"
        vid_id = short_info.get("video_id") or extract_video_id(task.url)
        full_url = short_info.get("url") or task.url or (f"https://www.youtube.com/shorts/{vid_id}" if vid_id else "")

        source_label = "Suggested Section" if source == "suggested" else "Home Feed"
        emit(self.log_queue, f"=== Opening Target Short from {source_label}: '{title}' ({full_url}) ===")
        nav_ok = self.click_home_short(short_info)
        if not nav_ok:
            emit(self.log_queue, f"Failed to navigate to short '{title}'.", logging.WARNING)
            return False

        human_like_delay(1.5, 2.5)
        dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

        target_loops = random.randint(1, 2)
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
                self._emit_event(("channel_watch_time_updated",))
                emit(
                    self.log_queue,
                    f"Credited 1 view to Target Short '{registered_task.title}' (Loop {loop_num}, reached {cur_t:.1f}s / {dur:.1f}s).",
                    logging.INFO,
                )

        if not self._check_account_login_before_play(content_type="short", content_title=title):
            return
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
        if enter_reel:
            prev_url = full_url or getattr(self.driver, "current_url", "")
            self.scroll_to_next_short(prev_url=prev_url)
            human_like_delay(1.0, 1.8)
            # Continue into the Shorts reel loop:
            self._run_shorts_reel_loop(session_watched_keys=session_watched_keys, initial_prev_url=prev_url)

        return watch_res.completed or watch_res.playback_finished


    @staticmethod
    def _is_unknown_channel_short(ch_name: Optional[str], title: Optional[str]) -> bool:
        """Return True if channel name or video title is unknown/unresolved."""
        clean_ch = (ch_name or "").strip().lower()
        clean_title = (title or "").strip().lower()
        if not clean_ch or clean_ch in ("unknown", "unknown channel", "none"):
            return True
        if not clean_title or clean_title in ("short", "untargeted short", "unknown"):
            return True
        return False

    def get_active_short_info(self) -> Dict[str, Any]:
        """Extract metadata of the currently active Short in the reel."""
        for attempt in range(4):
            try:
                info = self.driver.execute_script(GET_ACTIVE_SHORT_INFO_JS)
                if isinstance(info, dict) and info.get("found") and info.get("channel_name"):
                    curr_url = getattr(self.driver, "current_url", "") or info.get("url", "")
                    info["url"] = curr_url
                    info["video_id"] = extract_video_id(curr_url)
                    return info
            except Exception as exc:
                emit(self.log_queue, f"Error getting active short info: {exc}", logging.DEBUG)
            if attempt < 3:
                time.sleep(0.3)

        # Fallback if channel_name wasn't populated within retry attempts
        try:
            info = self.driver.execute_script(GET_ACTIVE_SHORT_INFO_JS)
            if isinstance(info, dict):
                curr_url = getattr(self.driver, "current_url", "") or info.get("url", "")
                info["url"] = curr_url
                info["video_id"] = extract_video_id(curr_url)
                return info
        except Exception:
            pass

        raw_url = getattr(self.driver, "current_url", "")
        curr_url = raw_url if isinstance(raw_url, str) else ""
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
        if not getattr(self, "driver", None):
            return False
        prev = prev_url or getattr(self.driver, "current_url", "")
        prev_vid = extract_video_id(prev) if prev else ""
        clicked = False
        try:
            res = self.driver.execute_script(r"""
                return (function() {
                    function isVisible(el) {
                        if (!el) return false;
                        try {
                            var rect = el.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) return false;
                            var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                            if (style && (style.visibility === 'hidden' || style.display === 'none')) return false;
                            return true;
                        } catch(e) {
                            return false;
                        }
                    }

                    function triggerClick(el) {
                        if (!el) return false;
                        try { el.focus(); } catch(e) {}
                        try {
                            var mousedown = new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window });
                            var mouseup = new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window });
                            var click = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                            el.dispatchEvent(mousedown);
                            el.dispatchEvent(mouseup);
                            el.dispatchEvent(click);
                            return true;
                        } catch(e) {
                            try {
                                el.click();
                                return true;
                            } catch(e2) {
                                return false;
                            }
                        }
                    }

                    var selectors = [
                        '#navigation-button-down button[aria-label="Next video"]',
                        '#navigation-button-down button[aria-label="Next"]',
                        '#navigation-button-down button',
                        'ytd-shorts #navigation-button-down button',
                        'ytd-shorts [id="navigation-button-down"] button',
                        '#navigation-button-down tp-yt-paper-icon-button',
                        'button[aria-label="Next video"]',
                        'button[aria-label="Next Short"]',
                        'button[aria-label="Next"]',
                        '.navigation-button#navigation-button-down button',
                        'button.navigation-button-down',
                        '[id="navigation-button-down"]'
                    ];

                    for (var i = 0; i < selectors.length; i++) {
                        var btn = document.querySelector(selectors[i]);
                        if (btn && isVisible(btn)) {
                            if (triggerClick(btn)) {
                                return true;
                            }
                        }
                    }

                    var active = document.querySelector('ytd-reel-video-renderer[is-active]');
                    if (active && active.nextElementSibling) {
                        try {
                            active.nextElementSibling.scrollIntoView({ behavior: 'smooth', block: 'start' });
                            return true;
                        } catch(e) {}
                    }

                    var container = document.querySelector('#shorts-container') ||
                                    document.querySelector('ytd-shorts') ||
                                    document.querySelector('#shorts-inner-container');
                    if (container && container.scrollTop !== undefined) {
                        container.scrollTop += (window.innerHeight || 800);
                    }

                    ['keydown', 'keyup'].forEach(function(evtType) {
                        try {
                            var e = new KeyboardEvent(evtType, {
                                key: 'PageDown',
                                code: 'PageDown',
                                keyCode: 34,
                                which: 34,
                                bubbles: true,
                                cancelable: true
                            });
                            (document.activeElement || document.body || document).dispatchEvent(e);
                        } catch(e) {}
                    });

                    return false;
                })();
            """)
            clicked = bool(res)
        except Exception:
            clicked = False

        if not clicked:
            try:
                nav_btns = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "#navigation-button-down button, button[aria-label='Next video'], button[aria-label='Next'], #navigation-button-down"
                )
                for nb in nav_btns:
                    if nb.is_displayed():
                        ActionChains(self.driver).move_to_element(nb).pause(0.05).click().perform()
                        clicked = True
                        break
            except Exception:
                pass

        if not clicked:
            try:
                active_el = self.driver.switch_to.active_element
                if active_el:
                    active_el.send_keys(Keys.PAGE_DOWN)
            except Exception:
                try:
                    ActionChains(self.driver).send_keys(Keys.PAGE_DOWN).perform()
                except Exception:
                    try:
                        self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.PAGE_DOWN)
                    except Exception:
                        pass

        scrolled = False
        for attempt in range(12):
            time.sleep(0.2)
            curr = getattr(self.driver, "current_url", "")
            curr_vid = extract_video_id(curr) if curr else ""
            if (curr_vid and prev_vid and curr_vid != prev_vid) or (curr and prev and curr != prev):
                scrolled = True
                break

            if attempt == 4:
                try:
                    ActionChains(self.driver).send_keys(Keys.PAGE_DOWN).perform()
                except Exception:
                    try:
                        self.driver.execute_script("var c = document.querySelector('#shorts-container') || document.querySelector('ytd-shorts'); if (c) c.scrollTop += 800;")
                    except Exception:
                        pass
            elif attempt == 8:
                try:
                    ActionChains(self.driver).send_keys("j").perform()
                except Exception:
                    pass

        return scrolled

    def _skip_non_target_reel(self, prev_url: Optional[str] = None) -> bool:
        """Advance past the current Short in the YouTube Shorts reel to the next one."""
        prev = prev_url or getattr(getattr(self, "driver", None), "current_url", "")
        res = self.scroll_to_next_short(prev_url=prev)
        human_like_delay(0.5, 1.0)
        return res

    def _run_shorts_cycle(
        self,
        session_watched_keys: Set[str],
    ) -> int:
        """
        Execute a full Shorts cycle:
        1. Desired Target Shorts: TargetShortsPerCycle (±20%, e.g. 10 -> 8-12).
        2. Max Total Non-Target Shorts Limit: TotalNonTargetShortsLimit (±20%, e.g. 33 -> 26-40).
        3. Max Non-Target Reels on scrolling page: NonTargetReelsLimit (±20%, e.g. 12 -> 10-14).
        Flow:
        - Scans Home feed for a matching Short from targeted channels.
        - If found: plays it (full short), enters the Shorts reel.
        - In the reel: skips non-target reels immediately to the next one, incrementing the reel counter.
          If a targeted short is found: watches full short, credits view, RESETS reel counter back to 0!
        - If reel counter reaches limit: returns to Home feed to find next target short.
        - If desired targeted shorts reached OR max total non-target shorts limit reached: moves to watching long videos.
        - If no matching short found on Home: moves to watching long videos.
        """
        base_target_shorts = self.config_mgr.get_int("TargetShortsPerCycle", 10) if self.config_mgr else 10
        min_target_shorts = max(1, round(base_target_shorts * 0.8))
        max_target_shorts = max(min_target_shorts, round(base_target_shorts * 1.2))
        cycle_target_shorts = random.randint(min_target_shorts, max_target_shorts)

        base_total_non_target = self.config_mgr.get_int("TotalNonTargetShortsLimit", 33) if self.config_mgr else 33
        min_total_non_target = max(1, round(base_total_non_target * 0.8))
        max_total_non_target = max(min_total_non_target, round(base_total_non_target * 1.2))
        cycle_max_total_non_target = random.randint(min_total_non_target, max_total_non_target)

        total_target_shorts_watched = 0
        total_non_target_shorts_seen = 0

        emit(
            self.log_queue,
            f"=== Starting Shorts Cycle ===\n"
            f"Desired Target Shorts for this cycle: {cycle_target_shorts} (configured base {base_target_shorts} ±20% [range {min_target_shorts}-{max_target_shorts}]).\n"
            f"Max Total Non-Target Shorts Limit: {cycle_max_total_non_target} (configured base {base_total_non_target} ±20% [range {min_total_non_target}-{max_total_non_target}]).",
            logging.INFO,
        )

        while not self.stop_event.is_set():
            self.check_pause()
            if self.stop_event.is_set():
                break

            if total_target_shorts_watched >= cycle_target_shorts:
                emit(
                    self.log_queue,
                    f"Achieved desired target of {total_target_shorts_watched}/{cycle_target_shorts} targeted Short(s)! Exiting Shorts cycle and moving to watching long-form videos.",
                    logging.INFO,
                )
                break

            if total_non_target_shorts_seen >= cycle_max_total_non_target:
                emit(
                    self.log_queue,
                    f"Reached max total non-target limit of {total_non_target_shorts_seen}/{cycle_max_total_non_target} Short(s). Exiting Shorts cycle and moving to watching long-form videos.",
                    logging.INFO,
                )
                break

            # 1. First, search suggested section on watch page if on a watch page or shelf exists
            matched_short = None
            cur_url = getattr(self.driver, "current_url", "") or ""
            if "/watch" in cur_url or self._has_suggested_shorts_shelf():
                emit(
                    self.log_queue,
                    "Checking suggested section on watch page for Shorts matching your Shorts Queue...",
                    logging.INFO,
                )
                matched_short = self.find_target_short_on_suggested_shelf(
                    session_watched_keys=session_watched_keys,
                    max_arrow_clicks=5,
                )

            # 2. If no matching Short on suggested shelf (or not on a watch page), navigate to Home feed
            if not matched_short:
                cur_url = getattr(self.driver, "current_url", "") or ""
                if "/watch" in cur_url or "/shorts" in cur_url:
                    emit(
                        self.log_queue,
                        "No target Short found on suggested shelf. Navigating to YouTube Home feed to find Shorts...",
                        logging.INFO,
                    )
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)
                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                emit(
                    self.log_queue,
                    f"Searching YouTube Home feed for Shorts matching your Shorts Queue (progress: {total_target_shorts_watched}/{cycle_target_shorts} target Shorts, {total_non_target_shorts_seen}/{cycle_max_total_non_target} non-target Shorts)...",
                    logging.INFO,
                )
                matched_short = self.find_target_short_on_home(
                    session_watched_keys=session_watched_keys,
                    max_scrolls=3,
                )

            if not matched_short:
                candidates = getattr(self, "_last_home_shorts", None) or self.fetch_home_shorts()
                if not candidates:
                    try:
                        self.driver.execute_script("window.scrollBy({top: 800, behavior: 'smooth'});")
                    except Exception:
                        pass
                    human_like_delay(1.5, 2.5)
                    candidates = self.fetch_home_shorts()

                r_title = "Untargeted Short"
                r_url = ""
                random_short = None
                first_info = None
                nav_ok = False

                if candidates:
                    random_short = random.choice(candidates)
                    r_title = random_short.get("title") or "Untargeted Short"
                    r_url = random_short.get("url") or (f"https://www.youtube.com/shorts/{random_short.get('video_id')}" if random_short.get('video_id') else "")
                    emit(
                        self.log_queue,
                        f"No targeted Short matched on Home feed. Clicking random Short from list as untargeted: '{r_title}' ({r_url})...",
                        logging.INFO,
                    )
                    nav_ok = self.click_home_short(random_short)

                # Fallback: If no candidate Short was found on Home feed, or clicking failed, enter YouTube Shorts via navigation
                if not nav_ok:
                    emit(
                        self.log_queue,
                        "No Shorts found on Home feed. Entering YouTube Shorts via navigation to discover targeted channels in reel...",
                        logging.INFO,
                    )
                    nav_ok = self.click_shorts_tab()
                    if nav_ok:
                        first_info = self.get_active_short_info()
                        r_title = first_info.get("title") or "Untargeted Short"
                        r_url = getattr(self.driver, "current_url", "") or "https://www.youtube.com/shorts"

                if not nav_ok:
                    emit(
                        self.log_queue,
                        "Could not open YouTube Shorts. Moving back to watching long-form videos.",
                        logging.WARNING,
                    )
                    break

                human_like_delay(0.5, 1.0)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                first_ch = ""
                if first_info and isinstance(first_info, dict):
                    first_ch = first_info.get("channel_name", "") or ""
                elif random_short and isinstance(random_short, dict):
                    first_ch = random_short.get("channel") or random_short.get("channel_name") or ""

                if self._is_unknown_channel_short(first_ch, r_title):
                    untargeted_watch_time = random.uniform(1.0, 2.0)
                    emit(
                        self.log_queue,
                        f"Short from unknown channel '{r_title}' found. Scrolling immediately within {untargeted_watch_time:.1f}s before entering Shorts reel...",
                        logging.INFO,
                    )
                else:
                    untargeted_watch_time = random.uniform(2.0, 6.0)
                    emit(
                        self.log_queue,
                        f"Short from '{first_ch}' is not in targeted channels list. Watching for {untargeted_watch_time:.1f}s randomly before entering Shorts reel...",
                        logging.INFO,
                    )
                elapsed = 0.0
                step = 0.2
                while elapsed < untargeted_watch_time and not self.stop_event.is_set():
                    self.check_pause()
                    if self.stop_event.is_set():
                        break
                    time.sleep(min(step, max(0.01, untargeted_watch_time - elapsed)))
                    elapsed += step

                total_non_target_shorts_seen += 1

                if total_non_target_shorts_seen >= cycle_max_total_non_target:
                    emit(
                        self.log_queue,
                        f"Reached max total non-target limit of {total_non_target_shorts_seen}/{cycle_max_total_non_target} Short(s). Exiting Shorts cycle and moving to watching long-form videos.",
                        logging.INFO,
                    )
                    break

                prev_url = r_url or getattr(self.driver, "current_url", "")
                self.scroll_to_next_short(prev_url=prev_url)
                human_like_delay(1.0, 1.8)

                watched_in_reel, non_targets_in_reel, exit_reason = self._run_shorts_reel_loop(
                    session_watched_keys=session_watched_keys,
                    current_target_watched=total_target_shorts_watched,
                    cycle_target_shorts=cycle_target_shorts,
                    current_total_non_target=total_non_target_shorts_seen,
                    cycle_max_total_non_target=cycle_max_total_non_target,
                    initial_prev_url=prev_url,
                )
                total_target_shorts_watched = watched_in_reel
                total_non_target_shorts_seen += non_targets_in_reel

                if exit_reason == "target_achieved" or total_target_shorts_watched >= cycle_target_shorts:
                    emit(
                        self.log_queue,
                        f"Achieved desired target of {total_target_shorts_watched}/{cycle_target_shorts} targeted Short(s)! Moving to watching long-form videos.",
                        logging.INFO,
                    )
                    break

                if exit_reason == "total_non_target_limit" or total_non_target_shorts_seen >= cycle_max_total_non_target:
                    emit(
                        self.log_queue,
                        f"Reached max total non-target limit of {total_non_target_shorts_seen}/{cycle_max_total_non_target} Short(s). Moving to watching long-form videos.",
                        logging.INFO,
                    )
                    break

                emit(
                    self.log_queue,
                    "Navigating back to YouTube Home page to find the next Short...",
                    logging.INFO,
                )
                safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                human_like_delay(2.0, 3.5)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                continue

            short_item, short_task = matched_short
            source_desc = short_item.get("source", "Home feed")
            emit(
                self.log_queue,
                f"Selected matching Short from {source_desc}: '{short_task.title or short_item.get('title')}' ({short_item.get('url') or short_task.url}). Clicking to play full short...",
                logging.INFO,
            )
            played_ok = self._play_target_short(
                short_info=short_item,
                task=short_task,
                session_watched_keys=session_watched_keys,
                enter_reel=False,
                source=short_item.get("source_type", "home"),
            )
            if not played_ok:
                emit(self.log_queue, f"Target short '{short_task.title}' could not be navigated via click. Continuing Shorts cycle...", logging.WARNING)
                continue

            self.target_attempts += 1
            self.watched_count = self.target_attempts
            prog_total = self.target_limit if not self.is_unlimited else 0
            self._emit_event(("progress", 0, self.target_attempts, prog_total))
            total_target_shorts_watched += 1

            if total_target_shorts_watched >= cycle_target_shorts:
                emit(
                    self.log_queue,
                    f"Achieved desired target of {total_target_shorts_watched}/{cycle_target_shorts} targeted Short(s)! Exiting Shorts cycle and moving to watching long-form videos.",
                    logging.INFO,
                )
                break

            # 2. Advance to next short before continuing the reel loop so the bot
            # does not re-watch the short that was just opened from Home
            prev_url = short_item.get("url") or short_task.url or getattr(self.driver, "current_url", "")
            self.scroll_to_next_short(prev_url=prev_url)
            human_like_delay(1.0, 1.8)

            watched_in_reel, non_targets_in_reel, exit_reason = self._run_shorts_reel_loop(
                session_watched_keys=session_watched_keys,
                current_target_watched=total_target_shorts_watched,
                cycle_target_shorts=cycle_target_shorts,
                current_total_non_target=total_non_target_shorts_seen,
                cycle_max_total_non_target=cycle_max_total_non_target,
                initial_prev_url=prev_url,
            )
            total_target_shorts_watched = watched_in_reel
            total_non_target_shorts_seen += non_targets_in_reel

            if exit_reason == "target_achieved" or total_target_shorts_watched >= cycle_target_shorts:
                emit(
                    self.log_queue,
                    f"Achieved desired target of {total_target_shorts_watched}/{cycle_target_shorts} targeted Short(s)! Moving to watching long-form videos.",
                    logging.INFO,
                )
                break

            if exit_reason == "total_non_target_limit" or total_non_target_shorts_seen >= cycle_max_total_non_target:
                emit(
                    self.log_queue,
                    f"Reached max total non-target limit of {total_non_target_shorts_seen}/{cycle_max_total_non_target} Short(s). Moving to watching long-form videos.",
                    logging.INFO,
                )
                break

            # If exit_reason == "reel_limit_reached", it returns to Home page to find the next short
            emit(
                self.log_queue,
                "Navigating back to YouTube Home page to find the next target Short...",
                logging.INFO,
            )
            safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
            human_like_delay(2.0, 3.5)
            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

        return total_target_shorts_watched

    def _run_shorts_reel_loop(
        self,
        session_watched_keys: Set[str],
        current_target_watched: int = 0,
        cycle_target_shorts: Optional[int] = None,
        current_total_non_target: int = 0,
        cycle_max_total_non_target: Optional[int] = None,
        initial_prev_url: Optional[str] = None,
    ) -> Tuple[int, int, str]:
        """
        Continuously navigate and watch Shorts on the Shorts reel:
        - If active Short is from a targeted channel: watch full short (1-3 loops), sync stats, reset reel non-target counter.
        - If active Short is NOT from a targeted channel: skip that short and move to next reel.
        - Exits with 'target_achieved' once cycle_target_shorts is reached.
        - Exits with 'reel_limit_reached' once NonTargetReelsLimit (±20% randomized) is reached on this scrolling page.
        - Exits with 'total_non_target_limit' if total non-target shorts reaches cycle_max_total_non_target.
        Returns (target_shorts_watched, non_targets_in_this_reel, exit_reason).
        """
        if cycle_target_shorts is None:
            base_target_shorts = self.config_mgr.get_int("TargetShortsPerCycle", 10) if self.config_mgr else 10
            min_target_shorts = max(1, round(base_target_shorts * 0.8))
            max_target_shorts = max(min_target_shorts, round(base_target_shorts * 1.2))
            cycle_target_shorts = random.randint(min_target_shorts, max_target_shorts)

        base_reel_non_target = self.config_mgr.get_int("NonTargetReelsLimit", self.config_mgr.get_int("NonTargetShortsLimit", 12)) if self.config_mgr else 12
        min_reel_non_target = max(1, round(base_reel_non_target * 0.8))
        max_reel_non_target = max(min_reel_non_target, round(base_reel_non_target * 1.2))
        cycle_reel_max_non_target = random.randint(min_reel_non_target, max_reel_non_target)

        reel_non_target_count = 0
        non_targets_in_this_reel = 0
        target_shorts_watched = current_target_watched
        last_seen_video_key: Optional[str] = extract_video_id(initial_prev_url) if initial_prev_url else None
        consecutive_same_video_count = 0

        emit(
            self.log_queue,
            f"=== Entering YouTube Shorts Reel Loop ===\n"
            f"Target Shorts for cycle: {cycle_target_shorts}. Current progress: {target_shorts_watched}/{cycle_target_shorts}.\n"
            f"Max Non-Target Reels on scrolling page: {cycle_reel_max_non_target} (configured base {base_reel_non_target} ±20% [range {min_reel_non_target}-{max_reel_non_target}]).",
            logging.INFO,
        )

        if target_shorts_watched >= cycle_target_shorts:
            return ReelLoopResult(target_shorts_watched, 0, "target_achieved")

        exit_reason = "stopped"
        while not self.stop_event.is_set():
            self.check_pause()
            if self.stop_event.is_set():
                break

            target_channels = self.get_target_channels()
            target_shorts_queue = self.get_target_shorts()

            short_info = self.get_active_short_info()
            if self.stop_event.is_set():
                break

            ch_name = short_info.get("channel_name", "")
            ch_url = short_info.get("channel_url", "")
            title = short_info.get("title", "") or "Short"
            vid_id = short_info.get("video_id") or extract_video_id(short_info.get("url", ""))
            full_url = short_info.get("url") or (f"https://www.youtube.com/shorts/{vid_id}" if vid_id else "")

            if not short_info.get("found") and not vid_id and not ch_name:
                human_like_delay(0.5, 1.0)
                continue

            current_video_key = vid_id or full_url or (f"{ch_name}_{title}" if ch_name else None)

            # Prevent double-counting or re-dwelling if the reel has not advanced yet
            if current_video_key and current_video_key == last_seen_video_key:
                consecutive_same_video_count += 1
                if consecutive_same_video_count <= 3:
                    emit(
                        self.log_queue,
                        f"Still on same Short '{title}' ({current_video_key}) from '{ch_name or 'Unknown'}'. Advancing immediately to next Short (attempt {consecutive_same_video_count})...",
                        logging.INFO,
                    )
                    self.scroll_to_next_short(prev_url=full_url or getattr(self.driver, "current_url", ""))
                    human_like_delay(0.5, 1.0)
                    continue
                else:
                    emit(
                        self.log_queue,
                        f"Reel scrolling stuck on '{title}' ({current_video_key}) after {consecutive_same_video_count} attempts. Exiting reel loop...",
                        logging.WARNING,
                    )
                    exit_reason = "reel_stuck"
                    break

            last_seen_video_key = current_video_key
            consecutive_same_video_count = 0

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
                # Check cooldown before accepting as a target watch
                cooldown_hours = self._get_cooldown_hours()
                rec = None
                store = getattr(self, "stats_store", None)
                if store:
                    if vid_id:
                        rec = store.get(vid_id)
                    if not rec and matched_task:
                        rec = store.find_for_task(matched_task)

                if rec and cooldown_hours > 0 and hasattr(rec, "is_on_cooldown"):
                    try:
                        res = rec.is_on_cooldown(cooldown_hours)
                        if isinstance(res, tuple) and len(res) == 2:
                            is_cool, remaining_hours = res
                        else:
                            is_cool, remaining_hours = False, 0.0
                    except Exception:
                        is_cool, remaining_hours = False, 0.0
                    if is_cool:
                        rem_str = f"{remaining_hours:.1f}h" if remaining_hours >= 1.0 else f"{int(remaining_hours * 60)}m"
                        emit(
                            self.log_queue,
                            f"Skipping Target Short '{title}' from channel '{ch_display}': within {cooldown_hours:g}h cooldown period ({rem_str} remaining).",
                            logging.INFO,
                        )
                        self._skip_non_target_reel()
                        continue

                # Reset scrolling page non-target counter back to 0!
                reel_non_target_count = 0
                emit(
                    self.log_queue,
                    f"Target Short found! Reset non-target reels counter back to 0/{cycle_reel_max_non_target}.",
                    logging.INFO,
                )

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
                    def_shorts_max = (
                        self.config_mgr.get_int("DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS)
                        if self.config_mgr
                        else DEFAULT_SHORTS_MAX_RUNS
                    )
                    if type(def_shorts_max) is not int:
                        def_shorts_max = DEFAULT_SHORTS_MAX_RUNS
                    new_task = VideoTask(
                        url=full_url,
                        title=title,
                        channel=ch_display,
                        enabled=True,
                        max_runs=def_shorts_max,
                        target_multiplier=0.0,
                    )
                    registered_task = self._ensure_video_in_queue(new_task)

                target_loops = random.randint(1, 2)
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
                        self._emit_event(("channel_watch_time_updated",))
                        emit(
                            self.log_queue,
                            f"Credited 1 view to Target Short '{registered_task.title}' (Loop {loop_num}, reached {cur_t:.1f}s / {dur:.1f}s).",
                            logging.INFO,
                        )

                if not self._check_account_login_before_play(content_type="discovered short", content_title=ch_display):
                    return
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

                emit(
                    self.log_queue,
                    f"Updated stats for Short '{title}' ({full_url}): {actual_watched:.0f}s watched ({target_loops} loop(s)). "
                    f"Targeted Shorts progress: {target_shorts_watched}/{cycle_target_shorts} in this cycle.",
                    logging.INFO,
                )

                if target_shorts_watched >= cycle_target_shorts:
                    emit(
                        self.log_queue,
                        f"Achieved desired target of {target_shorts_watched}/{cycle_target_shorts} targeted Short(s)! Exiting Shorts reel.",
                        logging.INFO,
                    )
                    exit_reason = "target_achieved"
                    break
            else:
                reel_non_target_count += 1
                non_targets_in_this_reel += 1
                ch_label = ch_name or "Unknown Channel"

                if self._is_unknown_channel_short(ch_name, title):
                    non_target_watch_time = random.uniform(1.0, 2.0)
                    emit(
                        self.log_queue,
                        f"Short from unknown channel '{ch_label}' found. Scrolling immediately within {non_target_watch_time:.1f}s ({reel_non_target_count}/{cycle_reel_max_non_target})...",
                        logging.INFO,
                    )
                else:
                    non_target_watch_time = random.uniform(2.0, 6.0)
                    emit(
                        self.log_queue,
                        f"Short from '{ch_label}' is not in targeted channels list. Watching for {non_target_watch_time:.1f}s randomly before skipping ({reel_non_target_count}/{cycle_reel_max_non_target})...",
                        logging.INFO,
                    )
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

                if cycle_max_total_non_target and (current_total_non_target + non_targets_in_this_reel) >= cycle_max_total_non_target:
                    emit(
                        self.log_queue,
                        f"Reached cycle ceiling of {current_total_non_target + non_targets_in_this_reel}/{cycle_max_total_non_target} total non-targeted Short(s). Exiting Shorts reel...",
                        logging.INFO,
                    )
                    exit_reason = "total_non_target_limit"
                    break

                if reel_non_target_count >= cycle_reel_max_non_target:
                    emit(
                        self.log_queue,
                        f"Reached limit of {reel_non_target_count}/{cycle_reel_max_non_target} non-targeted reels on scrolling page (base {base_reel_non_target} ±20%). Exiting reel to find next Short on Home feed...",
                        logging.INFO,
                    )
                    exit_reason = "reel_limit_reached"
                    break

            prev_url = full_url or getattr(self.driver, "current_url", "")
            self.scroll_to_next_short(prev_url=prev_url)
            human_like_delay(1.0, 1.8)

        return ReelLoopResult(target_shorts_watched, non_targets_in_this_reel, exit_reason)

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

    def _check_and_play_target_shorts_before_sleep(self, session_watched_keys: Set[str]) -> int:
        """Search for and play any available targeted Shorts before concluding no content was found."""
        try:
            target_shorts = self.get_target_shorts(filter_cooldown=True)
            if not target_shorts:
                return 0

            # 1. Check suggested shelf if on a watch page or shelf exists
            matched_short = None
            cur_url = getattr(self.driver, "current_url", "") or ""
            if "/watch" in cur_url or self._has_suggested_shorts_shelf():
                matched_short = self.find_target_short_on_suggested_shelf(
                    session_watched_keys=session_watched_keys,
                    max_arrow_clicks=3,
                )

            # 2. Check YouTube Home feed shorts shelf
            if not matched_short:
                if not is_clean_youtube_home_url(cur_url):
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(1.5, 2.5)
                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                matched_short = self.find_target_short_on_home(
                    session_watched_keys=session_watched_keys,
                    max_scrolls=3,
                )

            if matched_short:
                short_item, short_task = matched_short
                played_ok = self._play_target_short(
                    short_info=short_item,
                    task=short_task,
                    session_watched_keys=session_watched_keys,
                    enter_reel=False,
                    source=short_item.get("source_type", "home"),
                )
                if played_ok:
                    self.target_attempts += 1
                    self.watched_count = self.target_attempts
                    prog_total = self.target_limit if not self.is_unlimited else 0
                    self._emit_event(("progress", 0, self.target_attempts, prog_total))
                    return 1

            # 3. If no targeted short directly visible on shelf, execute shorts cycle
            shorts_watched = self._run_shorts_cycle(session_watched_keys=session_watched_keys)
            return int(shorts_watched or 0)
        except Exception as exc:
            emit(self.log_queue, f"Notice during shorts search: {exc}", logging.DEBUG)
            return 0

    def _check_and_enter_night_sleep(self, session_watched_keys: Set[str]) -> bool:
        """Evaluate local system clock against night sleep settings and enter Long Sleep if triggered."""
        if not hasattr(self, "night_scheduler") or not self.night_scheduler or not self.night_scheduler.is_enabled():
            return False

        sched = self.night_scheduler.evaluate()
        if not sched.get("is_sleep_time"):
            return False

        return self._execute_night_sleep_cycle(sched, session_watched_keys)

    def _execute_night_sleep_cycle(self, sched: Dict[str, Any], session_watched_keys: Set[str]) -> bool:
        """Execute Long Sleep / Night Mode: closes browser, sends night notification, sleeps until morning, resets stats."""
        if self.stop_event.is_set():
            return False

        self.night_sleep_count += 1
        current_night_count = self.night_sleep_count
        wake_up_time = sched["scheduled_sleep_end"]
        sleep_duration_hours = sched.get("sleep_duration_hours", 8.0)
        window_id = sched.get("window_id", "")

        wake_str = wake_up_time.strftime("%I:%M %p")
        emit(
            self.log_queue,
            f"🌙 [Night Mode] Scheduled nighttime sleep window reached! Entering Long Sleep for {sleep_duration_hours:.1f} hours (until {wake_str})...",
            logging.INFO,
        )
        self._report_status(f"🌙 Night Mode: Sleeping until {wake_str}", "info")

        # 1. Close main visible YouTube browser tab
        if self.driver:
            try:
                emit(self.log_queue, "Closing YouTube browser session before Long Sleep...", logging.INFO)
                close_driver(self.driver, log_queue=self.log_queue)
            except Exception as e:
                emit(self.log_queue, f"Notice while closing browser before night sleep: {e}", logging.DEBUG)
            self.driver = None

        # 2. Deliver session sleep notification report to email under the exact same session thread
        try:
            emit(
                self.log_queue,
                f"Sending Long Sleep / Night Mode notification email under thread '{self.session_thread_subject}'...",
                logging.INFO,
            )
            self._send_session_report(
                status="Long Sleep (Night Mode)",
                is_sleep_notification=True,
                sleep_mode_type="night",
                sleep_cycle_number=current_night_count,
                sleep_duration_hours=sleep_duration_hours,
                wake_up_time=wake_up_time,
            )
            self.cycle_report_dispatched = True
        except Exception as e:
            emit(self.log_queue, f"Error sending night sleep notification email: {e}", logging.WARNING)

        if getattr(self, "reporting_driver", None):
            try:
                close_driver(self.reporting_driver, log_queue=self.log_queue)
            except Exception:
                pass
            self.reporting_driver = None

        # 3. Enter interruptible sleep loop until wake_up_time
        self.is_sleeping = True
        emit(
            self.log_queue,
            f"Bot is now in Long Sleep / Night Mode until {wake_str} ({sleep_duration_hours:.1f} hours). YouTube tab is closed. The bot will automatically wake up and start daytime browsing with fresh stats.",
            logging.INFO,
        )

        while not self.stop_event.is_set():
            if datetime.now() >= wake_up_time:
                break
            time.sleep(0.5)

        self.is_sleeping = False
        if self.stop_event.is_set():
            emit(self.log_queue, "Long Sleep / Night Mode interrupted by user stop.", logging.INFO)
            self._report_status("Stopped by user", "info")
            return False

        # 4. Mark night window completed
        if window_id:
            self.night_scheduler.mark_completed(window_id)

        # 5. Wake up in the morning! Reset cycle counters and start with fresh stats
        emit(
            self.log_queue,
            f"=== ☀️ Good Morning! Long Sleep / Night Mode completed! Waking up now to start daytime browsing with fresh stats... ===",
            logging.INFO,
        )
        self._report_status("☀️ Morning wake-up: Relaunching browser with fresh stats", "info")

        session_watched_keys.clear()
        self.cycle_report_dispatched = False
        self.target_attempts = 0
        self.watched_count = 0

        if hasattr(self, "channel_tracker") and self.channel_tracker:
            self.channel_tracker = SessionChannelTracker(
                config_mgr=self.config_mgr,
                stats_store=self.stats_store,
                channels=self.channels,
                emit_event=self._emit_event,
                log_queue=self.log_queue,
            )

        known_raw = self.config_mgr.get_accounts() if self.config_mgr else None
        known = [Account.from_dict(a) if isinstance(a, dict) else a for a in known_raw] if known_raw else None
        self.driver = setup_driver(self.account, log_queue=self.log_queue, known_accounts=known)
        self.has_started_browsing = True
        perform_google_login(self.driver, self.account, log_queue=self.log_queue, stop_event=self.stop_event)
        return True

    def _enter_no_target_sleep_cycle(self, session_watched_keys: Set[str]) -> bool:
        """Handle sleep & wake-up cycle when no target videos or shorts are found.

        If sleep is disabled (<= 0), stops the watching session immediately.
        Otherwise:
        1. Closes the YouTube browser session.
        2. Delivers the session watch report to email.
        3. Enters an interruptible sleep loop for the configured duration.
        4. Updates status with live countdown on the dashboard.
        5. Wakes up, relaunches browser, and resumes searching for new videos.
        Returns True if successfully woke up and ready to continue, False if stopped.
        """
        # If Long Sleep / Night Mode is triggered, prioritize night sleep
        if hasattr(self, "night_scheduler") and self.night_scheduler and self.night_scheduler.is_enabled():
            sched = self.night_scheduler.evaluate()
            if sched.get("is_sleep_time"):
                return self._execute_night_sleep_cycle(sched, session_watched_keys)

        sleep_hours = self.config_mgr.get_short_sleep_hours(default=2.0) if self.config_mgr else 2.0

        if sleep_hours <= 0:
            stop_msg = "No video of targeted channel found on YouTube Home or via YouTube Search. Stopping watching session."
            emit(self.log_queue, stop_msg, logging.WARNING)
            self._report_status("Stopped: No target videos found", "warning")
            self.stop_event.set()
            return False

        self.sleep_cycle_count += 1
        current_sleep_cycle = self.sleep_cycle_count

        stop_msg = "No video of targeted channel found on YouTube Home or via YouTube Search."
        emit(self.log_queue, stop_msg, logging.WARNING)
        hours_str = f"{int(sleep_hours)} hour{'s' if int(sleep_hours) != 1 else ''}" if sleep_hours.is_integer() else f"{sleep_hours:.1f} hours"
        emit(
            self.log_queue,
            f"No targeted channel content found in current pass. Entering Short Sleep / Nap mode for {hours_str} (Nap Cycle #{current_sleep_cycle})...",
            logging.INFO,
        )
        self._report_status(f"No target content. Sending nap notification (Cycle #{current_sleep_cycle})...", "info")

        # 1. Close main visible YouTube browser tab
        if self.driver:
            try:
                emit(self.log_queue, "Closing YouTube browser session before sleep mode...", logging.INFO)
                close_driver(self.driver, log_queue=self.log_queue)
            except Exception as e:
                emit(self.log_queue, f"Notice while closing browser before sleep: {e}", logging.DEBUG)
            self.driver = None

        # 2. Deliver session sleep notification report to email under the exact same session thread
        sleep_seconds = sleep_hours * 3600.0
        wake_up_time = datetime.now() + timedelta(seconds=sleep_seconds)
        try:
            emit(
                self.log_queue,
                f"Sending sleep notification email under thread '{self.session_thread_subject}' (Sleep Cycle #{current_sleep_cycle})...",
                logging.INFO,
            )
            self._send_session_report(
                status="Going to Sleep",
                is_sleep_notification=True,
                sleep_mode_type="nap",
                sleep_cycle_number=current_sleep_cycle,
                sleep_duration_hours=sleep_hours,
                wake_up_time=wake_up_time,
            )
            self.cycle_report_dispatched = True
        except Exception as e:
            emit(self.log_queue, f"Error sending sleep notification email: {e}", logging.WARNING)

        # Close reporting driver as well so no background Chrome processes linger during sleep
        if getattr(self, "reporting_driver", None):
            try:
                close_driver(self.reporting_driver, log_queue=self.log_queue)
            except Exception:
                pass
            self.reporting_driver = None

        # 3. Enter interruptible sleep loop
        sleep_start = time.monotonic()
        self.is_sleeping = True

        emit(
            self.log_queue,
            f"Bot is now sleeping for {hours_str} (Sleep Cycle #{current_sleep_cycle}). YouTube tab is closed. The bot will automatically wake up and search for new videos with fresh stats.",
            logging.INFO,
        )

        last_logged_m = -1
        while not self.stop_event.is_set():
            elapsed = time.monotonic() - sleep_start
            remaining = sleep_seconds - elapsed
            if remaining <= 0:
                break

            rem_m, rem_s = divmod(int(remaining), 60)
            rem_h, rem_m = divmod(rem_m, 60)

            if rem_h == 0 and rem_m < 5:
                countdown = f"Sleeping (no target videos) · Wakes in {rem_m}m {rem_s:02d}s"
            else:
                countdown = f"Sleeping (no target videos) · Wakes in {rem_h}h {rem_m:02d}m"

            self._report_status(countdown, "info")

            total_rem_m = int(remaining // 60)
            if total_rem_m % 30 == 0 and total_rem_m != last_logged_m:
                emit(self.log_queue, f"[Sleep Mode] {countdown}", logging.INFO)
                last_logged_m = total_rem_m

            self.stop_event.wait(timeout=min(5.0, max(0.2, remaining)))
            if getattr(self, "pause_event", None) and self.pause_event.is_set():
                self.check_pause()

        self.is_sleeping = False

        # 4. Check if interrupted by user stop
        if self.stop_event.is_set():
            emit(self.log_queue, "Watching session stopped by user during sleep mode.", logging.INFO)
            self._report_status("Stopped by user", "info")
            return False

        # 5. Wake-up! Relaunch browser and reset cycle counters to fresh stats
        emit(
            self.log_queue,
            f"=== Sleep Cycle #{current_sleep_cycle} completed ({hours_str})! Waking up now to look for new videos with fresh stats... ===",
            logging.INFO,
        )
        self._report_status("Waking up... Relaunching browser with fresh stats", "info")

        # Start from FRESH stats!
        session_watched_keys.clear()
        self.cycle_report_dispatched = False
        self.target_attempts = 0
        self.watched_count = 0

        # Fresh channel tracker so session watch time, videos watched, and discovery counts reset to 0
        if hasattr(self, "channel_tracker") and self.channel_tracker:
            self.channel_tracker = SessionChannelTracker(
                config_mgr=self.config_mgr,
                stats_store=self.stats_store,
                channels=self.channels,
                emit_event=self._emit_event,
                log_queue=self.log_queue,
            )

        known_raw = self.config_mgr.get_accounts() if self.config_mgr else None
        known = [Account.from_dict(a) if isinstance(a, dict) else a for a in known_raw] if known_raw else None
        self.driver = setup_driver(self.account, log_queue=self.log_queue, known_accounts=known)
        self.has_started_browsing = True

        if self.stop_event.is_set():
            return False

        login_ok = perform_google_login(
            self.driver,
            self.account,
            log_queue=self.log_queue,
            stop_event=self.stop_event,
            config_mgr=self.config_mgr,
            alert_callback=self._send_critical_alert,
        )
        if not login_ok and not self.stop_event.is_set():
            emit(self.log_queue, f"Notice: Google sign-in verification check after wake-up for {self.account.email}.", logging.WARNING)

        emit(self.log_queue, "Browser relaunched successfully. Starting search for new videos and shorts...", logging.INFO)
        self._report_status("Browsing YouTube Home feed...", "info")
        return True

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
            self.has_started_browsing = True

            if self.stop_event.is_set():
                return

            # Sign-in step
            login_ok = perform_google_login(
                self.driver,
                self.account,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
                config_mgr=self.config_mgr,
                alert_callback=self._send_critical_alert,
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

            # Cycle tracking: watch 3 to 5 targeted long-form videos across target channels before switching to Shorts
            cycle_long_vids_watched: int = 0
            target_long_vids_cycle: int = random.randint(3, 5)
            emit(
                self.log_queue,
                f"Starting long-form video cycle: will watch {target_long_vids_cycle} targeted long-form video(s) before switching to Shorts.",
                logging.INFO,
            )

            while not self.stop_event.is_set():
                if self._is_target_limit_reached(target_attempts):
                    emit(self.log_queue, f"Reached configured limit of {self.target_limit} target video attempt(s).")
                    break

                self.check_pause()
                if self.stop_event.is_set():
                    break

                # Check if local system time has reached scheduled Long Sleep / Night Mode
                if self._check_and_enter_night_sleep(session_watched_keys=session_watched_keys):
                    cycle_long_vids_watched = 0
                    suggested_miss_count = 0
                    consecutive_target_watched = 0
                    channel_buffer_counts.clear()
                    target_attempts = 0
                    continue

                if not is_driver_alive(self.driver):
                    emit(self.log_queue, "Browser session closed or disconnected. Exiting watch loop.", logging.WARNING)
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
                    cycle_long_vids_watched = 0
                    target_long_vids_cycle = random.randint(3, 5)
                    emit(
                        self.log_queue,
                        f"Exited Shorts reel. Starting new long-form cycle: will watch {target_long_vids_cycle} targeted long video(s).",
                        logging.INFO,
                    )
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

                    suggested_played = False
                    failed_rec_ids = set()

                    while not self.stop_event.is_set():
                        valid_candidates = [
                            c for c in rec_candidates
                            if (extract_video_id(c.get("href", "")) or c.get("href", "")) not in failed_rec_ids
                        ]
                        target_match = self._find_preferred_target_video(
                            candidates=valid_candidates,
                            target_channels=target_channels,
                            last_channel_name=last_watched_channel,
                            session_watched_keys=session_watched_keys,
                            channel_watch_counts=channel_buffer_counts,
                            channel_consecutive_counts=channel_buffer_counts,
                        )
                        if not target_match:
                            break

                        item, match_obj, full_url, title, ch_display = target_match
                        cand_id = extract_video_id(full_url) or full_url
                        emit(self.log_queue, "Found video in suggested section")

                        try:
                            watch_res = self._play_target_video(
                                item, full_url, title, ch_display, session_watched_keys, is_home=False, source="suggested"
                            )
                        except Exception:
                            raise

                        if not watch_res.completed and watch_res.reason in ("in_page_click_failed", "navigation_identity_mismatch"):
                            emit(
                                self.log_queue,
                                f"In-page click on suggested video '{title}' failed to navigate. Trying next video on page...",
                                logging.WARNING,
                            )
                            failed_rec_ids.add(cand_id)
                            continue

                        suggested_played = True
                        target_attempts += 1
                        self.target_attempts = target_attempts
                        self.watched_count = target_attempts
                        prog_total = self.target_limit if not self.is_unlimited else 0
                        self._emit_event(("progress", 0, target_attempts, prog_total))

                        ch_canon = SessionChannelTracker._canon_name(ch_display)
                        channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                        last_watched_channel = ch_display
                        consecutive_target_watched += 1
                        suggested_miss_count = 0
                        cycle_long_vids_watched += 1

                        emit(
                            self.log_queue,
                            f"Target channel '{ch_display}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) "
                            f"(total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}). "
                            f"[Long-form cycle: {cycle_long_vids_watched}/{target_long_vids_cycle}]",
                        )

                        if cycle_long_vids_watched >= target_long_vids_cycle:
                            emit(
                                self.log_queue,
                                f"Completed {cycle_long_vids_watched}/{target_long_vids_cycle} targeted long-form videos in this cycle! Switching to Shorts (checking suggested section on watch page first)...",
                                logging.INFO,
                            )
                        break

                    if suggested_played:
                        if cycle_long_vids_watched >= target_long_vids_cycle:
                            self._run_shorts_cycle(session_watched_keys=session_watched_keys)
                            cycle_long_vids_watched = 0
                            target_long_vids_cycle = random.randint(3, 5)
                            suggested_miss_count = 0
                            emit(
                                self.log_queue,
                                f"Exited Shorts cycle. Starting next long-form cycle: target is {target_long_vids_cycle} video(s).",
                                logging.INFO,
                            )
                            safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                            human_like_delay(2.0, 3.5)
                            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                        continue

                    if failed_rec_ids and not suggested_played:
                        emit(
                            self.log_queue,
                            "Could not navigate to suggested video(s) via in-page click and no more target candidates on page. Clicking browser Back button to return to previous page...",
                            logging.WARNING,
                        )
                        try:
                            self.driver.back()
                            human_like_delay(2.5, 4.0)
                            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                        except Exception as back_err:
                            emit(self.log_queue, f"Back button navigation error: {back_err}", logging.WARNING)
                        suggested_miss_count += 1
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
                            cycle_long_vids_watched += 1
                            suggested_miss_count = 0

                            emit(
                                self.log_queue,
                                f"Target channel '{played_ch}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) "
                                f"(total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}). "
                                f"[Long-form cycle: {cycle_long_vids_watched}/{target_long_vids_cycle}]",
                            )

                            if cycle_long_vids_watched >= target_long_vids_cycle:
                                emit(
                                    self.log_queue,
                                    f"Completed {cycle_long_vids_watched}/{target_long_vids_cycle} targeted long-form videos in this cycle! Switching to Shorts (checking suggested section on watch page first)...",
                                    logging.INFO,
                                )
                                self._run_shorts_cycle(session_watched_keys=session_watched_keys)
                                cycle_long_vids_watched = 0
                                target_long_vids_cycle = random.randint(3, 5)
                                suggested_miss_count = 0
                                emit(
                                    self.log_queue,
                                    f"Exited Shorts cycle. Starting next long-form cycle: target is {target_long_vids_cycle} video(s).",
                                    logging.INFO,
                                )
                                safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                                human_like_delay(2.0, 3.5)
                                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
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
                # If we completed the long-form video cycle target (3 to 5 videos), prioritize finding and playing Shorts!
                if cycle_long_vids_watched >= target_long_vids_cycle:
                    emit(
                        self.log_queue,
                        f"Completed {cycle_long_vids_watched}/{target_long_vids_cycle} targeted long-form videos in this cycle! Switching to Shorts...",
                        logging.INFO,
                    )
                    self._run_shorts_cycle(session_watched_keys=session_watched_keys)

                    # Cycle completed! Reset counter and pick new random target of 3 to 5 long videos
                    cycle_long_vids_watched = 0
                    target_long_vids_cycle = random.randint(3, 5)
                    suggested_miss_count = 0
                    emit(
                        self.log_queue,
                        f"Exited Shorts cycle. Starting next long-form cycle: target is {target_long_vids_cycle} video(s).",
                        logging.INFO,
                    )

                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)
                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                    continue

                # Otherwise (or if no Short found on Home), look for long-form video on Home feed
                matched_home = self._scan_home_feed_for_target(
                    session_watched_keys=session_watched_keys,
                    last_watched_channel=last_watched_channel,
                    channel_watch_counts=channel_buffer_counts,
                    channel_consecutive_counts=channel_buffer_counts,
                    max_attempts=3,
                )

                if matched_home:
                    item, match_obj, full_url, title, ch_display = matched_home
                    emit(self.log_queue, f"Found long-form video on Home page: '{title}' ({full_url})")

                    try:
                        watch_res = self._play_target_video(
                            item, full_url, title, ch_display, session_watched_keys, is_home=True, source="home"
                        )
                    except Exception:
                        raise

                    if not watch_res.completed and watch_res.reason in ("in_page_click_failed", "navigation_identity_mismatch"):
                        emit(
                            self.log_queue,
                            f"In-page click on Home video '{title}' failed to navigate. Refreshing Home feed...",
                            logging.WARNING,
                        )
                        safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                        human_like_delay(2.0, 3.5)
                        dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                        continue

                    target_attempts += 1
                    self.target_attempts = target_attempts
                    self.watched_count = target_attempts
                    prog_total = self.target_limit if not self.is_unlimited else 0
                    self._emit_event(("progress", 0, target_attempts, prog_total))

                    ch_canon = SessionChannelTracker._canon_name(ch_display)
                    channel_buffer_counts[ch_canon] = channel_buffer_counts.get(ch_canon, 0) + 1
                    last_watched_channel = ch_display
                    consecutive_target_watched += 1
                    suggested_miss_count = 0
                    cycle_long_vids_watched += 1

                    emit(
                        self.log_queue,
                        f"Target channel '{ch_display}' watch countdown: {channel_buffer_counts[ch_canon]}/3 video(s) "
                        f"(total target videos since buffer: {consecutive_target_watched}/{random_buffer_threshold}). "
                        f"[Long-form cycle: {cycle_long_vids_watched}/{target_long_vids_cycle}]",
                    )

                    if cycle_long_vids_watched >= target_long_vids_cycle:
                        emit(
                            self.log_queue,
                            f"Completed {cycle_long_vids_watched}/{target_long_vids_cycle} targeted long-form videos in this cycle! Switching to Shorts (checking suggested section on watch page first)...",
                            logging.INFO,
                        )
                        self._run_shorts_cycle(session_watched_keys=session_watched_keys)
                        cycle_long_vids_watched = 0
                        target_long_vids_cycle = random.randint(3, 5)
                        suggested_miss_count = 0
                        emit(
                            self.log_queue,
                            f"Exited Shorts cycle. Starting next long-form cycle: target is {target_long_vids_cycle} video(s).",
                            logging.INFO,
                        )
                        safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                        human_like_delay(2.0, 3.5)
                        dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
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
                        emit(
                            self.log_queue,
                            "No long-form video found on Home feed or via Search. Checking YouTube Shorts for targeted channels...",
                            logging.INFO,
                        )
                        target_shorts_found = self._check_and_play_target_shorts_before_sleep(
                            session_watched_keys=session_watched_keys
                        )
                        if target_shorts_found > 0:
                            cycle_long_vids_watched = 0
                            suggested_miss_count = 0
                            continue

                        # Both long-form videos and shorts checks yielded no target content
                        woke_up = self._enter_no_target_sleep_cycle(session_watched_keys=session_watched_keys)
                        if woke_up:
                            cycle_long_vids_watched = 0
                            suggested_miss_count = 0
                            consecutive_target_watched = 0
                            channel_buffer_counts.clear()
                            continue
                        else:
                            break

            summary = f"Browsing session finished: attempted {target_attempts} target video(s)."
            emit(self.log_queue, summary)
            self._report_status(summary, "info")

        except Exception as exc:
            err_str = str(exc)
            if is_session_disconnected_error(exc):
                emit(self.log_queue, f"Browser session disconnected or closed externally: {err_str}", logging.WARNING)
                self._report_status("Browser session closed", "warning")
                alert_type = "Browser Disconnected"
                session_status = "Browser Closed / Disconnected"
                session_err = f"Browser session was disconnected or closed externally: {err_str}"
            else:
                emit(self.log_queue, f"Critical error during watching session: {exc}", logging.ERROR)
                self._report_status(f"Watching error: {exc}", "error")
                alert_type = "Critical Failure / Worker Crash"
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
            elif "exc" in locals() and locals()["exc"] is not None and not session_status:
                session_status = "Error"
                session_err = str(locals()["exc"])

            if hasattr(self, "channel_tracker"):
                self.channel_tracker.on_stop()

            # Close main visible watching driver
            if self.driver:
                close_driver(self.driver, log_queue=self.log_queue)
                self.driver = None

            # Deliver session report via the persistent headless browser parked on the email thread
            # ONLY if browsing successfully started and was not aborted during setup/driver launch
            should_send_final_report = getattr(self, "has_started_browsing", False) and (
                not getattr(self, "cycle_report_dispatched", False)
                or self.stop_event.is_set()
                or (session_status and session_status not in ("Completed", "Long Sleep (Night Mode)", "Short Sleep (Nap Mode)"))
            )
            if should_send_final_report:
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
                session_mode="Continuous Browsing",
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

    def _check_account_login_before_play(self, content_type: str = "video", content_title: str = "") -> bool:
        """Verify account login status before playing every video or short."""
        if not self.account or not getattr(self.account, "email", None):
            return True
        if self.stop_event.is_set():
            return False
        emit(self.log_queue, f"[Account Check] Verifying login state for {self.account.email} before playing {content_type}...", logging.DEBUG)
        ok = check_account_logged_in(
            driver=self.driver,
            account=self.account,
            config_mgr=self.config_mgr,
            log_queue=self.log_queue,
            stop_event=self.stop_event,
            alert_callback=self._send_critical_alert,
            content_type=content_type,
            content_title=content_title,
        )
        if not ok and not self.stop_event.is_set():
            self._report_status(f"Manual action needed on this PC for {self.account.email}.", "error")
        return ok

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
            if getattr(self, "session_thread_info", None) and isinstance(self.session_thread_info, dict):
                if self.session_thread_info.get("thread_url"):
                    self.session_thread_url = self.session_thread_info["thread_url"]
                if self.session_thread_info.get("message_id"):
                    self.session_thread_message_id = self.session_thread_info["message_id"]
        except Exception as exc:
            logging.debug(f"Failed dispatching critical alert email: {exc}")

    def _send_session_report(
        self,
        status: str = "Completed",
        error: Optional[str] = None,
        is_sleep_notification: bool = False,
        sleep_mode_type: Optional[str] = None,
        sleep_cycle_number: Optional[int] = None,
        sleep_duration_hours: Optional[float] = None,
        wake_up_time: Optional[datetime] = None,
    ) -> None:
        """Deliver session watch time report or sleep notification email under the same thread."""
        if sleep_mode_type == "night":
            modal_msg = "Sending Long Sleep / Night notification... Please do not close the app."
        elif is_sleep_notification:
            c_str = f" (Cycle #{sleep_cycle_number})" if sleep_cycle_number else ""
            modal_msg = f"Sending Short Sleep / Nap notification{c_str}... Please do not close the app."
        else:
            modal_msg = "Generating session report... Please do not close the app."
        self._emit_event(("report_generating", modal_msg))
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
                session_mode="Continuous Browsing",
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
                thread_url=getattr(self, "session_thread_url", None) or (self.session_thread_info.get("thread_url") if getattr(self, "session_thread_info", None) else None),
                thread_message_id=getattr(self, "session_thread_message_id", None) or (self.session_thread_info.get("message_id") if getattr(self, "session_thread_info", None) else None),
                thread_references=list(self.session_thread_info.get("references", [])) if getattr(self, "session_thread_info", None) else None,
                root_message_id=self.session_thread_info.get("root_message_id") if getattr(self, "session_thread_info", None) else None,
                suggested_videos_count=report_data.get("suggested_videos_count", 0),
                search_videos_count=report_data.get("search_videos_count", 0),
                home_videos_count=report_data.get("home_videos_count", 0),
                random_videos_count=report_data.get("random_videos_count", 0),
                search_queries_count=report_data.get("search_queries_count", 0),
                system_name=(self.config_mgr.get_setting("SystemName", "") or "").strip() if self.config_mgr else "",
                sleep_cycle_number=sleep_cycle_number,
                sleep_duration_hours=sleep_duration_hours,
                wake_up_time=wake_up_time,
                sleep_mode_type=sleep_mode_type,
            )
            rep_driver = getattr(self, "reporting_driver", None)
            if rep_driver and not is_driver_alive(rep_driver):
                try:
                    close_driver(rep_driver, log_queue=self.log_queue)
                except Exception:
                    pass
                rep_driver = None
                self.reporting_driver = None

            watch_driver = getattr(self, "driver", None)
            if watch_driver and not is_driver_alive(watch_driver):
                watch_driver = None

            chosen_driver = rep_driver or watch_driver
            ok = EmailReporter.dispatch_report(
                report=report,
                config_mgr=self.config_mgr,
                driver=chosen_driver,
                account=self.account,
                log_queue=self.log_queue,
                thread_info=getattr(self, "session_thread_info", None),
            )
            if not ok:
                emit(self.log_queue, "Session report email delivery could not be completed.", logging.WARNING)
                self._emit_event(("report_sent", False, "Session report email delivery could not be completed."))
            else:
                if getattr(self, "session_thread_info", None) and isinstance(self.session_thread_info, dict):
                    if self.session_thread_info.get("thread_url"):
                        self.session_thread_url = self.session_thread_info["thread_url"]
                    if self.session_thread_info.get("message_id"):
                        self.session_thread_message_id = self.session_thread_info["message_id"]
                if sleep_mode_type == "night":
                    msg = "Long Sleep / Night notification sent successfully!"
                elif is_sleep_notification:
                    c_str = f" (Cycle #{sleep_cycle_number})" if sleep_cycle_number else ""
                    msg = f"Short Sleep / Nap notification{c_str} sent successfully!"
                else:
                    msg = "Session report sent successfully!"
                self._emit_event(("report_sent", True, msg))
        except Exception as exc:
            emit(self.log_queue, f"Failed dispatching session email report: {exc}", logging.WARNING)
            self._emit_event(("report_sent", False, f"Failed dispatching session email report: {exc}"))

    def stop(self) -> None:
        """Stop the continuous watching loop and all running tasks."""
        self.stop_event.set()
        if hasattr(self, "channel_tracker"):
            self.channel_tracker.on_stop()
