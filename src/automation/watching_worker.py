"""Watching Worker.

Manages autonomous video discovery and continuous chained playback:
1. Google sign-in (like normal script).
2. Visit YouTube Homepage and wait 5 seconds for the page to completely load.
3. Scroll down and fetch data for ~100 videos (inspecting each video one by one).
4. Match channel URLs against channels stored in the app.
5. If a matching video is found, open it by clicking it.
6. Watch it like the normal script (ads, human interactivity simulation, stats).
7. Where the normal script closes the browser window, stop the video there (keep browser open).
8. Inspect recommended videos on the watch page for stored channels.
9. If one is found, start watching that recommended video.
10. If not found, return to the YouTube Homepage and repeat.
"""

import difflib
import logging
import queue
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel
from ..models.video_task import VideoTask
from ..models.watch_stats import TaskOutcome, resolve_video_key
from ..utils.helpers import human_like_delay
from ..utils.logger import emit
from .adblock import ensure_adblock
from .driver import close_driver, setup_driver
from .login import perform_google_login
from .navigation import dismiss_youtube_dialogs, safe_get
from .youtube_actions import (
    click_element,
    ensure_video_playing,
    watch_video,
)

HOME_VIDEOS_JS = """
return (function() {
    var items = [];
    var seenVideoIds = {};

    var cards = document.querySelectorAll(
        'ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-grid-media'
    );

    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        if (!card) continue;

        if (card.closest('ytd-rich-shelf-renderer, ytd-reel-shelf-renderer, ytd-ad-slot-renderer, ytd-banner-promo-renderer')) {
            continue;
        }

        var linkEl = card.querySelector(
            'a#video-title-link, a#thumbnail[href*="/watch"], a.yt-simple-endpoint[href*="/watch?v="], a[href*="/watch?v="]'
        );
        if (!linkEl) continue;

        var href = linkEl.getAttribute('href') || '';
        if (!href || href.indexOf('/watch') === -1) continue;

        var matchVid = href.match(/[?&]v=([^&]+)/);
        var videoId = matchVid ? matchVid[1] : '';
        if (!videoId || seenVideoIds[videoId]) continue;

        var titleEl = card.querySelector('#video-title, #video-title-link, h3, #title');
        var title = '';
        if (titleEl) {
            title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
        }
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }

        var chLink = card.querySelector(
            '#avatar-link, ytd-channel-name a, #channel-name a, a[href*="/@"], a[href*="/channel/"], a[href*="/c/"], a[href*="/user/"]'
        );
        var channelUrl = chLink ? (chLink.getAttribute('href') || '') : '';

        var channelNameEl = card.querySelector('ytd-channel-name, #channel-name, ytd-video-meta-block ytd-channel-name');
        var channelName = channelNameEl ? (channelNameEl.textContent || '').trim() : '';
        if (!channelName && chLink) {
            channelName = (chLink.textContent || '').trim();
        }

        if (!channelName && linkEl) {
            var aria = linkEl.getAttribute('aria-label') || '';
            var m = aria.match(/\\bby\\s+([^0-9\\n\\r]+?)(?:\\s+\\d+|\\s+ago|\\s+views|\\s+Streamed|$)/i);
            if (m && m[1]) channelName = m[1].trim();
        }

        seenVideoIds[videoId] = true;
        items.push({
            index: i,
            video_id: videoId,
            title: title,
            href: href,
            channel_name: channelName,
            channel_url: channelUrl
        });
    }

    return items;
})();
"""

RECOMMENDED_VIDEOS_JS = """
return (function() {
    var items = [];
    var seenVideoIds = {};

    var cards = document.querySelectorAll(
        'ytd-compact-video-renderer, #related ytd-video-renderer, ytd-watch-next-secondary-results-renderer ytd-compact-video-renderer, ytd-compact-radio-renderer'
    );

    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        if (!card) continue;

        if (card.closest('ytd-reel-shelf-renderer, ytd-ad-slot-renderer')) {
            continue;
        }

        var linkEl = card.querySelector(
            'a#thumbnail[href*="/watch"], a#video-title-link, a.yt-simple-endpoint[href*="/watch"], a[href*="/watch?v="]'
        );
        if (!linkEl) continue;

        var href = linkEl.getAttribute('href') || '';
        if (!href || href.indexOf('/watch') === -1) continue;

        var matchVid = href.match(/[?&]v=([^&]+)/);
        var videoId = matchVid ? matchVid[1] : '';
        if (!videoId || seenVideoIds[videoId]) continue;

        var titleEl = card.querySelector('#video-title, span#video-title, #title');
        var title = titleEl ? (titleEl.getAttribute('title') || titleEl.textContent || '').trim() : '';
        if (!title && linkEl) {
            title = (linkEl.getAttribute('title') || linkEl.getAttribute('aria-label') || '').trim();
        }

        var chLink = card.querySelector(
            '#avatar-link, ytd-channel-name a, #channel-name a, a[href*="/@"], a[href*="/channel/"], a[href*="/c/"], a[href*="/user/"]'
        );
        var channelUrl = chLink ? (chLink.getAttribute('href') || '') : '';

        var channelNameEl = card.querySelector('ytd-channel-name, #channel-name');
        var channelName = channelNameEl ? (channelNameEl.textContent || '').trim() : '';
        if (!channelName && chLink) {
            channelName = (chLink.textContent || '').trim();
        }

        if (!channelName && linkEl) {
            var aria = linkEl.getAttribute('aria-label') || '';
            var m = aria.match(/\\bby\\s+([^0-9\\n\\r]+?)(?:\\s+\\d+|\\s+ago|\\s+views|$)/i);
            if (m && m[1]) channelName = m[1].trim();
        }

        seenVideoIds[videoId] = true;
        items.push({
            index: i,
            video_id: videoId,
            title: title,
            href: href,
            channel_name: channelName,
            channel_url: channelUrl
        });
    }

    return items;
})();
"""


def extract_channel_identifier(url: str) -> str:
    """Extract a canonical channel identifier (e.g. '@nextuniverz' or 'channel/uc123')."""
    clean = (url or "").strip().lower()
    if not clean:
        return ""
    if "://" in clean:
        clean = clean.split("://", 1)[1]
    for prefix in ("www.youtube.com/", "youtube.com/", "m.youtube.com/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    clean = clean.lstrip("/")
    for suffix in ("/videos", "/shorts", "/featured", "/streams", "/community"):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
    return clean.rstrip("/")


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

    Prioritizes canonical channel URL / handle matching, followed by exact and
    normalized channel name matching.
    """
    scanned_id = extract_channel_identifier(scanned_url)
    clean_scanned_name = (scanned_name or "").strip()
    norm_scanned_name = normalize_channel_text(clean_scanned_name)

    for ch in channels:
        if ch.archived or not ch.enabled:
            continue

        # 1. Match by channel URL / handle
        if scanned_id and ch.url:
            stored_id = extract_channel_identifier(ch.url)
            if stored_id:
                if (
                    scanned_id == stored_id
                    or (len(stored_id) >= 4 and stored_id in scanned_id)
                    or (len(scanned_id) >= 4 and scanned_id in stored_id)
                ):
                    return ch

        # 2. Match by exact channel name
        clean_ch_name = (ch.name or "").strip()
        if clean_scanned_name and clean_ch_name:
            if clean_scanned_name.lower() == clean_ch_name.lower():
                return ch

        # 3. Match by normalized channel name
        norm_ch_name = normalize_channel_text(clean_ch_name)
        if norm_scanned_name and norm_ch_name:
            if norm_scanned_name == norm_ch_name:
                return ch
            if len(norm_ch_name) >= 4 and len(norm_scanned_name) >= 4:
                if norm_ch_name in norm_scanned_name or norm_scanned_name in norm_ch_name:
                    return ch
                ratio = difflib.SequenceMatcher(None, norm_scanned_name, norm_ch_name).ratio()
                if ratio >= fuzzy_threshold:
                    return ch

    return None


class WatchingWorker:
    """Worker managing the 'Start Watching' autonomous recommendation loop."""

    def __init__(
        self,
        account: Account,
        channels: Sequence[Channel],
        config_mgr: ConfigManager,
        log_queue: queue.Queue,
        stop_event: threading.Event,
        event_queue: queue.Queue,
        stats_store: StatsStore,
    ) -> None:
        self.account = account
        self.channels = [c for c in channels if c.enabled and not c.archived]
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event
        self.event_queue = event_queue
        self.stats_store = stats_store
        self.driver: Any = None
        self.skip_ads: bool = True
        self.max_ad_seconds: int = 32

    def _emit_event(self, event: Tuple[Any, ...]) -> None:
        try:
            self.event_queue.put_nowait(event)
        except Exception:
            pass

    def _report_status(self, message: str, level: str = "info") -> None:
        self._emit_event(("status", message, level))

    def _ad_settings(self) -> Tuple[bool, int]:
        """Read ad policy settings from config."""
        skip = self.config_mgr.get_bool("SkipAds", True)
        if self.config_mgr.get_setting("BlockAds") is not None:
            skip = self.config_mgr.get_bool("BlockAds", skip)
        max_ad = self.config_mgr.get_int("MaxAdWatchSeconds", 32)
        return skip, max_ad

    def _interactivity_settings(self) -> Tuple[bool, Dict[str, Any]]:
        """Read human interactivity simulation settings from config."""
        enabled = self.config_mgr.get_bool("EnableHumanInteractivity", True)
        cfg = {
            "scroll_comments": self.config_mgr.get_bool("RandomScrolling", True),
            "variable_speed": self.config_mgr.get_bool("VariableSpeed", True),
            "adjust_quality": self.config_mgr.get_bool("AdjustQuality", True),
            "quality_level": self.config_mgr.get_setting("QualityLevel", "360p"),
            "micro_pauses": self.config_mgr.get_bool("RandomMicroPauses", True),
            "speed_chance": 0.35,
            "quality_chance": 0.60,
        }
        return enabled, cfg

    def _full_video_timeout_seconds(self) -> int:
        """Full Video safety timeout in seconds (0 = disabled)."""
        minutes = self.config_mgr.get_int("FullVideoTimeoutMinutes", 360)
        return max(0, minutes) * 60

    def _end_buffer_seconds(self) -> float:
        """Safety end buffer in seconds to prevent autoplay countdown."""
        return float(self.config_mgr.get_int("AutoplayEndBufferSeconds", 10))

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

    def fetch_home_videos(self, target_count: int = 100) -> List[Dict[str, Any]]:
        """Fetch ~100 videos from YouTube Homepage by scrolling and inspecting cards."""
        try:
            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

            last_count = 0
            stagnant_count = 0

            # Progressively scroll down to trigger loading of about 100 videos
            for _ in range(18):
                if self.stop_event.is_set():
                    return []

                raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
                count = len(raw_items) if isinstance(raw_items, list) else 0
                if count >= target_count:
                    return raw_items[:target_count]

                if count == last_count and count > 0:
                    stagnant_count += 1
                    if stagnant_count >= 3:
                        break
                else:
                    stagnant_count = 0
                last_count = count

                self.driver.execute_script("window.scrollBy({top: 1300, behavior: 'smooth'});")
                time.sleep(1.6)

            raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
            return raw_items[:target_count] if isinstance(raw_items, list) else []
        except Exception as exc:
            emit(self.log_queue, f"Error fetching home page videos: {exc}", logging.WARNING)
            return []

    def fetch_recommended_videos(self) -> List[Dict[str, Any]]:
        """Fetch recommended videos on the watch page sidebar/feed."""
        try:
            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

            for _ in range(4):
                if self.stop_event.is_set():
                    return []
                raw_items = self.driver.execute_script(RECOMMENDED_VIDEOS_JS)
                if isinstance(raw_items, list) and len(raw_items) >= 15:
                    return raw_items

                self.driver.execute_script("window.scrollBy({top: 600, behavior: 'smooth'});")
                time.sleep(1.6)

            raw_items = self.driver.execute_script(RECOMMENDED_VIDEOS_JS)
            return raw_items if isinstance(raw_items, list) else []
        except Exception as exc:
            emit(self.log_queue, f"Error fetching recommended videos: {exc}", logging.WARNING)
            return []

    def click_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on the target video card using its video_id or href."""
        video_id = card_info.get("video_id", "")
        href = card_info.get("href", "")
        clean_id = video_id or (href.split("v=")[-1].split("&")[0] if "v=" in href else "")
        if not clean_id:
            return False

        selectors = [
            f"a#video-title-link[href*='{clean_id}']",
            f"a#thumbnail[href*='{clean_id}']",
            f"ytd-rich-item-renderer a[href*='{clean_id}']",
            f"ytd-compact-video-renderer a[href*='{clean_id}']",
            f"a[href*='{clean_id}']",
        ]

        target_el = None
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    try:
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", el
                        )
                        time.sleep(0.6)
                        target_el = el
                        break
                    except Exception:
                        continue
                if target_el:
                    break
            except Exception:
                continue

        if not target_el:
            try:
                xpath_els = self.driver.find_elements(By.XPATH, f"//a[contains(@href, '{clean_id}')]")
                if xpath_els:
                    target_el = xpath_els[0]
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", target_el
                    )
                    time.sleep(0.6)
            except Exception:
                pass

        if not target_el:
            return False

        human_like_delay(1.0, 1.8)

        # 1. Selenium click
        try:
            if click_element(self.driver, target_el):
                return True
        except Exception:
            pass

        # 2. JavaScript click fallback
        try:
            self.driver.execute_script("arguments[0].click();", target_el)
            return True
        except Exception:
            pass

        return False

    def click_home_video(self, card_info: Dict[str, Any]) -> bool:
        """Alias for clicking a homepage video card."""
        return self.click_video(card_info)

    def click_recommended_video(self, card_info: Dict[str, Any]) -> bool:
        """Alias for clicking a recommended video card."""
        return self.click_video(card_info)

    def _record_watch(
        self,
        title: str,
        channel_name: str,
        watch_seconds: float,
        ad_seconds: float,
        duration: float,
        completed: bool,
    ) -> None:
        """Record watch time and run statistics in stats.json."""
        try:
            curr_url = getattr(self.driver, "current_url", "")
            task = VideoTask(title=title, url=curr_url, channel=channel_name)
            resolved_key = resolve_video_key(task=task, resolved_url=curr_url)
            outcome = TaskOutcome(
                video_key=resolved_key,
                channel=channel_name,
                success=completed,
                interrupted=False,
                watched_seconds=watch_seconds,
                ad_seconds=ad_seconds,
                video_duration=duration,
                playback_finished=completed,
                attempts=1,
            )
            self.stats_store.record(outcome)
        except Exception as exc:
            emit(self.log_queue, f"Could not record watching stats: {exc}", logging.WARNING)

    def run(self) -> None:
        """Main watching loop:
        1. Google sign-in.
        2. Visit YouTube Homepage, wait 5 seconds for page to completely load.
        3. Scroll down and fetch data for ~100 videos (inspecting each video one-by-one).
        4. Match video channel URLs against stored channels in app.
        5. If found, open video by clicking it.
        6. Watch video like normal script (ads, human interactivity, watch stats).
        7. Where normal script closes window, stop video there (keep browser open).
        8. Inspect recommended videos on watch page for stored channels.
        9. If found, start watching that recommended video.
        10. If not found, go back to Homepage and repeat.
        """
        emit(self.log_queue, f"=== Starting Watch Session for {self.account.email} ===")
        active_channels_msg = ", ".join(f"'{c.name}' ({c.url})" for c in self.channels if c.name or c.url)
        emit(self.log_queue, f"Target stored channel(s): {active_channels_msg}")

        try:
            self.skip_ads, self.max_ad_seconds = self._ad_settings()
            adblock_path = ensure_adblock(log_queue=self.log_queue) if self.skip_ads else None
            self.driver = setup_driver(self.account, log_queue=self.log_queue, adblock_path=adblock_path)

            if self.stop_event.is_set():
                return

            # Step 1: Sign-in like normal script
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

            watched_count = 0
            # Target video to watch next (populated either from Homepage or Recommendations)
            next_video_to_watch: Optional[Dict[str, Any]] = None
            next_video_channel: Optional[Channel] = None
            is_recommended: bool = False

            while not self.stop_event.is_set():
                # -------------------------------------------------------------
                # Steps 2-4: If no video is queued from recommendations, visit Homepage
                # -------------------------------------------------------------
                if not next_video_to_watch:
                    emit(self.log_queue, "Navigating to YouTube Homepage (https://www.youtube.com/)...")
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)

                    # Wait 5 seconds for page to completely load
                    emit(self.log_queue, "Waiting 5 seconds for YouTube Homepage to completely load...")
                    for _ in range(5):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1.0)

                    if self.stop_event.is_set():
                        break

                    dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                    # Scroll down and fetch data for ~100 videos
                    emit(self.log_queue, "Scrolling down YouTube feed to load ~100 videos...")
                    home_items = self.fetch_home_videos(target_count=100)
                    emit(self.log_queue, f"Fetched data of {len(home_items)} videos from Homepage feed.")

                    # Refresh target channels dynamically from settings.json
                    target_channels = self.get_target_channels()

                    # Inspect videos one by one and match channel URLs against stored channels
                    emit(self.log_queue, "Inspecting videos one-by-one to match stored channel URLs...")
                    for idx, item in enumerate(home_items, 1):
                        ch = match_channel(item.get("channel_name", ""), item.get("channel_url", ""), target_channels)
                        if ch:
                            next_video_to_watch = item
                            next_video_channel = ch
                            emit(
                                self.log_queue,
                                f"Match found [#{idx}/{len(home_items)}]! "
                                f"Video: '{item.get('title')}' (ID: {item.get('video_id')}) "
                                f"from channel '{item.get('channel_name')}' ({item.get('channel_url')}) "
                                f"matches stored channel '{ch.name}' ({ch.url})",
                            )
                            break

                    if not next_video_to_watch:
                        emit(
                            self.log_queue,
                            "No videos from stored channels found on Homepage feed. Waiting 15 seconds before re-scanning...",
                            logging.WARNING,
                        )
                        for _ in range(15):
                            if self.stop_event.is_set():
                                break
                            time.sleep(1.0)
                        continue

                # -------------------------------------------------------------
                # Step 5: Open the video by clicking it
                # -------------------------------------------------------------
                video_title = next_video_to_watch.get("title", "")
                channel_name = next_video_channel.name if next_video_channel else next_video_to_watch.get("channel_name", "")
                video_id = next_video_to_watch.get("video_id", "")
                video_href = next_video_to_watch.get("href", "")

                emit(self.log_queue, f"Opening video by clicking: '{video_title}' (ID: {video_id})...")
                if is_recommended:
                    clicked = self.click_recommended_video(next_video_to_watch)
                else:
                    clicked = self.click_home_video(next_video_to_watch)
                if not clicked:
                    emit(self.log_queue, f"Click failed on '{video_title}'. Navigating via direct link fallback...", logging.WARNING)
                    full_link = f"https://www.youtube.com{video_href}" if video_href.startswith("/") else video_href
                    safe_get(self.driver, full_link, timeout=30, log_queue=self.log_queue)

                # Reset next_video_to_watch so the loop doesn't re-use it
                next_video_to_watch = None
                next_video_channel = None
                is_recommended = False

                # Wait for watch page to load
                try:
                    WebDriverWait(self.driver, 15).until(lambda d: "/watch" in (d.current_url or ""))
                except Exception:
                    pass

                time.sleep(2.5)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)
                ensure_video_playing(self.driver, log_queue=self.log_queue)

                # -------------------------------------------------------------
                # Step 6: Watch it like normal script
                # -------------------------------------------------------------
                enable_interactivity, interactivity_cfg = self._interactivity_settings()
                duration_budget = self._watchtime_duration_seconds()

                emit(self.log_queue, f"\n--- Watching: '{video_title}' ({channel_name}) ---")
                watch_res = watch_video(
                    self.driver,
                    duration_seconds=duration_budget,
                    stop_event=self.stop_event,
                    log_queue=self.log_queue,
                    full_timeout_seconds=self._full_video_timeout_seconds(),
                    skip_ads=self.skip_ads,
                    max_ad_seconds=self.max_ad_seconds,
                    end_buffer_seconds=self._end_buffer_seconds(),
                    enable_interactivity=enable_interactivity,
                    interactivity_config=interactivity_cfg,
                )

                watched_count += 1
                self._record_watch(
                    title=video_title,
                    channel_name=channel_name,
                    watch_seconds=watch_res.watched_seconds,
                    ad_seconds=watch_res.ad_seconds,
                    duration=watch_res.video_duration,
                    completed=watch_res.completed,
                )
                self._emit_event(("progress", 0, watched_count, watched_count))

                if self.stop_event.is_set():
                    emit(self.log_queue, "Watching session stopped by user.")
                    break

                # -------------------------------------------------------------
                # Step 7: Where normal script closes window, stop video there (keep browser open)
                # -------------------------------------------------------------
                emit(self.log_queue, f"Finished watch for '{video_title}'. Stopping video playback (browser stays open)...")
                try:
                    self.driver.execute_script("var v = document.querySelector('video'); if (v) v.pause();")
                except Exception:
                    pass

                time.sleep(1.5)

                # -------------------------------------------------------------
                # Steps 8-10: Inspect recommended videos on watch page
                # -------------------------------------------------------------
                emit(self.log_queue, "Looking for videos from stored channels in Recommended videos...")
                rec_items = self.fetch_recommended_videos()
                emit(self.log_queue, f"Scanned {len(rec_items)} recommended videos on watch page.")

                # Refresh target channels dynamically from settings.json
                target_channels = self.get_target_channels()

                for r_idx, rec in enumerate(rec_items, 1):
                    rec_ch = match_channel(rec.get("channel_name", ""), rec.get("channel_url", ""), target_channels)
                    if rec_ch:
                        next_video_to_watch = rec
                        next_video_channel = rec_ch
                        is_recommended = True
                        emit(
                            self.log_queue,
                            f"Found matching Recommended video [#{r_idx}]! "
                            f"'{rec.get('title')}' (ID: {rec.get('video_id')}) "
                            f"from channel '{rec.get('channel_name')}' ({rec.get('channel_url')}) "
                            f"matches stored channel '{rec_ch.name}' ({rec_ch.url})",
                        )
                        break

                if next_video_to_watch:
                    # Step 9: If one is found, start watching that recommended video
                    emit(self.log_queue, f"Chaining directly to recommended video: '{next_video_to_watch.get('title')}'...")
                else:
                    # Step 10: If not found, go back to Homepage
                    emit(self.log_queue, "No stored channels found in recommendations. Returning to YouTube Homepage...")

            summary = f"Watching session finished: watched {watched_count} video(s)."
            emit(self.log_queue, summary)
            self._report_status(summary, "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during watching session: {exc}", logging.ERROR)
            self._report_status(f"Watching error: {exc}", "error")
        finally:
            close_driver(self.driver, log_queue=self.log_queue)
