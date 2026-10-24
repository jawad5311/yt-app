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
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, canonical_channel_url
from ..models.video_task import VideoTask
from ..models.watch_stats import TaskOutcome, resolve_video_key
from ..utils.helpers import human_like_delay
from ..utils.logger import emit
from .adblock import ensure_adblock
from .driver import close_driver, setup_driver
from .login import perform_google_login
from .navigation import dismiss_youtube_dialogs, safe_get
from .youtube_actions import (
    DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS,
    click_element,
    watch_video,
)

HOME_VIDEOS_JS = """
return (function() {
    var items = document.querySelectorAll('ytd-rich-item-renderer');
    var list = [];
    for (var i = 0; i < items.length; i++) {
        var item = items[i];
        if (item.closest('ytd-rich-shelf-renderer') || item.closest('ytd-reel-shelf-renderer')) {
            continue;
        }
        var titleEl = item.querySelector('#video-title-link, #video-title');
        if (!titleEl) continue;
        var title = (titleEl.textContent || titleEl.getAttribute('title') || '').trim();
        var href = titleEl.getAttribute('href') || '';
        if (!href || (!href.includes('/watch') && !href.includes('v='))) {
            continue;
        }
        var channelEl = item.querySelector(
            'ytd-channel-name #text a, ytd-channel-name a, #channel-name a, ytd-channel-name yt-formatted-string, ytd-channel-name'
        );
        var channelName = channelEl ? (channelEl.textContent || '').trim() : '';
        var channelUrl = (channelEl && channelEl.getAttribute('href')) ? channelEl.getAttribute('href') : '';
        if (!channelUrl) {
            var chLink = item.querySelector('a[href*="/@"], a[href*="/channel/"], a[href*="/c/"]');
            if (chLink) channelUrl = chLink.getAttribute('href') || '';
        }
        list.push({
            index: i,
            title: title,
            href: href,
            channel_name: channelName,
            channel_url: channelUrl
        });
    }
    return list;
})();
"""

RECOMMENDED_VIDEOS_JS = """
return (function() {
    var items = document.querySelectorAll('ytd-compact-video-renderer');
    var list = [];
    for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var titleEl = item.querySelector('#video-title, span#video-title');
        if (!titleEl) continue;
        var title = (titleEl.textContent || titleEl.getAttribute('title') || '').trim();
        var linkEl = item.querySelector('a#thumbnail, a.yt-simple-endpoint, a[href*="/watch"]');
        var href = linkEl ? (linkEl.getAttribute('href') || '') : '';
        if (!href || (!href.includes('/watch') && !href.includes('v='))) {
            continue;
        }
        var channelEl = item.querySelector(
            'ytd-channel-name #text, ytd-channel-name yt-formatted-string, ytd-channel-name a, #channel-name, ytd-video-meta-block ytd-channel-name'
        );
        var channelName = channelEl ? (channelEl.textContent || '').trim() : '';
        var channelLink = item.querySelector('ytd-channel-name a, #channel-name a, a[href*="/@"], a[href*="/channel/"]');
        var channelUrl = channelLink ? (channelLink.getAttribute('href') || '') : '';
        list.push({
            index: i,
            title: title,
            href: href,
            channel_name: channelName,
            channel_url: channelUrl
        });
    }
    return list;
})();
"""


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
    ):
        self.account = account
        self.channels = channels
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event or threading.Event()
        self.event_queue = event_queue
        self.stats_store = stats_store
        self.driver: Any = None
        self.skip_ads = True
        self.max_ad_seconds = 32

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
        skip_ads = self.config_mgr.get_bool("BlockAds", self.config_mgr.get_bool("SkipAds", False))
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
            "variable_speed": self.config_mgr.get_bool("VariableSpeed", True),
            "adjust_quality": self.config_mgr.get_bool("AdjustQuality", True),
            "quality_level": self.config_mgr.get_setting("QualityLevel", "360p"),
            "micro_pauses": self.config_mgr.get_bool("RandomMicroPauses", True),
        }
        return enabled, config

    def _watchtime_duration_seconds(self) -> Optional[int]:
        """Read watch duration budget from settings. Returns None for Full Video."""
        setting = self.config_mgr.get_setting("WatchtimeDuration", "Full Video")
        task = VideoTask(watchtime_duration=setting)
        return task.parse_duration_seconds()

    def fetch_home_videos(self, target_count: int = 20) -> List[Dict[str, Any]]:
        """Fetch ~20 videos from YouTube home page, scrolling if needed."""
        try:
            current_url = getattr(self.driver, "current_url", "")
            if not current_url or "youtube.com" not in current_url:
                safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                human_like_delay(2.0, 3.0)

            dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

            for scroll_attempt in range(4):
                if self.stop_event.is_set():
                    return []
                raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
                if isinstance(raw_items, list) and len(raw_items) >= target_count:
                    return raw_items[:target_count]

                # Scroll down to trigger infinite loading
                self.driver.execute_script("window.scrollBy({top: 900, behavior: 'smooth'});")
                time.sleep(2.0)

            raw_items = self.driver.execute_script(HOME_VIDEOS_JS)
            return raw_items[:target_count] if isinstance(raw_items, list) else []
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
        """Click on the target video card on the home page."""
        try:
            idx = int(card_info.get("index", 0))
            items = self.driver.find_elements(By.CSS_SELECTOR, "ytd-rich-item-renderer")
            if 0 <= idx < len(items):
                item = items[idx]
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", item
                )
                human_like_delay(1.0, 2.0)

                clickable = None
                for selector in (
                    "a#video-title-link",
                    "a#thumbnail",
                    "#video-title",
                    "a.yt-simple-endpoint",
                ):
                    els = item.find_elements(By.CSS_SELECTOR, selector)
                    if els and els[0].is_displayed():
                        clickable = els[0]
                        break

                target = clickable or item
                if click_element(self.driver, target):
                    return True
        except Exception as exc:
            emit(self.log_queue, f"Home video click error: {exc}", logging.WARNING)

        # Fallback to direct navigation if click fails
        href = card_info.get("href", "")
        if href:
            full_url = href if href.startswith("http") else f"https://www.youtube.com{href}"
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)
            return True
        return False

    def click_recommended_video(self, card_info: Dict[str, Any]) -> bool:
        """Click on a recommended video item from the sidebar/recommendations."""
        try:
            idx = int(card_info.get("index", 0))
            items = self.driver.find_elements(By.CSS_SELECTOR, "ytd-compact-video-renderer")
            if 0 <= idx < len(items):
                item = items[idx]
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", item
                )
                human_like_delay(1.0, 2.0)

                clickable = None
                for selector in ("a#thumbnail", "#video-title", "span#video-title", "a.yt-simple-endpoint"):
                    els = item.find_elements(By.CSS_SELECTOR, selector)
                    if els and els[0].is_displayed():
                        clickable = els[0]
                        break

                target = clickable or item
                if click_element(self.driver, target):
                    return True
        except Exception as exc:
            emit(self.log_queue, f"Recommended video click error: {exc}", logging.WARNING)

        href = card_info.get("href", "")
        if href:
            full_url = href if href.startswith("http") else f"https://www.youtube.com{href}"
            safe_get(self.driver, full_url, timeout=30, log_queue=self.log_queue)
            return True
        return False

    def _record_watch(
        self,
        title: str,
        channel_name: str,
        watch_seconds: float,
        ad_seconds: float,
        duration: float,
        completed: bool,
    ) -> None:
        if not self.stats_store:
            return
        try:
            current_url = str(getattr(self.driver, "current_url", "") or "")
            task = VideoTask(
                url=current_url,
                title=title,
                channel=channel_name,
            )
            key = resolve_video_key(task, current_url)
            outcome = TaskOutcome(
                video_key=key,
                task_key=key,
                label=title or key,
                channel=channel_name,
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

    def run(self) -> None:
        """Main watching loop: home discovery -> watch -> recommended chaining -> repeat."""
        emit(self.log_queue, f"=== Starting Watch Session for {self.account.email} ===")
        active_channels_msg = ", ".join(f"'{c.name}'" for c in self.channels if c.name)
        emit(self.log_queue, f"Target stored channel(s): {active_channels_msg}")

        try:
            self.skip_ads, self.max_ad_seconds = self._ad_settings()
            adblock_path = ensure_adblock(log_queue=self.log_queue) if self.skip_ads else None
            self.driver = setup_driver(self.account, log_queue=self.log_queue, adblock_path=adblock_path)

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

            emit(self.log_queue, "Navigating to YouTube Home to inspect videos...")
            safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
            human_like_delay(2.0, 3.5)

            current_video_title = ""
            current_channel_name = ""
            watched_count = 0

            while not self.stop_event.is_set():
                # -------------------------------------------------------------
                # 1. Video Discovery Phase
                # -------------------------------------------------------------
                matched_video: Optional[Dict[str, Any]] = None
                matched_channel_obj: Optional[Channel] = None

                # If on watch page with a finished video, first inspect recommended videos
                curr_url = getattr(self.driver, "current_url", "")
                if "/watch" in curr_url:
                    emit(self.log_queue, "Checking recommended videos for stored channels...")
                    rec_items = self.fetch_recommended_videos()
                    emit(self.log_queue, f"Scanned {len(rec_items)} recommended videos.")

                    for rec in rec_items:
                        match = match_channel(rec.get("channel_name", ""), rec.get("channel_url", ""), self.channels)
                        if match:
                            matched_video = rec
                            matched_channel_obj = match
                            emit(
                                self.log_queue,
                                f"Found matching recommended video: '{rec.get('title')}' "
                                f"from channel '{rec.get('channel_name')}' (matches stored channel '{match.name}')",
                            )
                            break

                # If no matching recommended video found, fall back to home feed
                if not matched_video:
                    if "/watch" in curr_url:
                        emit(
                            self.log_queue,
                            "No stored channels found in recommendations. Navigating to YouTube Home feed...",
                        )
                    safe_get(self.driver, "https://www.youtube.com/", timeout=30, log_queue=self.log_queue)
                    human_like_delay(2.0, 3.5)

                    emit(self.log_queue, "Fetching ~20 videos from YouTube Home feed...")
                    home_items = self.fetch_home_videos(target_count=20)
                    emit(self.log_queue, f"Fetched {len(home_items)} videos from Home feed. Checking channels...")

                    for item in home_items:
                        match = match_channel(
                            item.get("channel_name", ""), item.get("channel_url", ""), self.channels
                        )
                        if match:
                            matched_video = item
                            matched_channel_obj = match
                            emit(
                                self.log_queue,
                                f"Found matching Home video: '{item.get('title')}' "
                                f"from channel '{item.get('channel_name')}' (matches stored channel '{match.name}')",
                            )
                            break

                if not matched_video:
                    emit(
                        self.log_queue,
                        "No videos matching stored channels found on Home page or recommendations. "
                        "Waiting 15 seconds before re-scanning...",
                        logging.WARNING,
                    )
                    for _ in range(15):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                # -------------------------------------------------------------
                # 2. Open Video by Clicking
                # -------------------------------------------------------------
                current_video_title = matched_video.get("title", "")
                current_channel_name = matched_channel_obj.name if matched_channel_obj else ""

                emit(self.log_queue, f"Opening video by clicking: '{current_video_title}'...")
                clicked = False
                if "/watch" in curr_url and "index" in matched_video:
                    clicked = self.click_recommended_video(matched_video)
                else:
                    clicked = self.click_home_video(matched_video)

                if not clicked:
                    emit(self.log_queue, f"Could not click video '{current_video_title}'. Re-trying...", logging.WARNING)
                    time.sleep(2)
                    continue

                # Wait for watch page to load
                try:
                    WebDriverWait(self.driver, 15).until(lambda d: "/watch" in (d.current_url or ""))
                except Exception:
                    pass

                human_like_delay(2.5, 4.0)
                dismiss_youtube_dialogs(self.driver, log_queue=self.log_queue)

                # -------------------------------------------------------------
                # 3. Watch Video Normally
                # -------------------------------------------------------------
                enable_interactivity, interactivity_cfg = self._interactivity_settings()
                duration_budget = self._watchtime_duration_seconds()

                emit(self.log_queue, f"\n--- Watching: '{current_video_title}' ({current_channel_name}) ---")
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
                    title=current_video_title,
                    channel_name=current_channel_name,
                    watch_seconds=watch_res.watched_seconds,
                    ad_seconds=watch_res.ad_seconds,
                    duration=watch_res.video_duration,
                    completed=watch_res.completed,
                )

                # Send progress update
                self._emit_event(("progress", 0, watched_count, watched_count))

                if self.stop_event.is_set():
                    emit(self.log_queue, "Watching session stopped by user.")
                    break

                emit(
                    self.log_queue,
                    f"Completed watch for '{current_video_title}'. Approached end of video.",
                )

            summary = f"Watching session finished: watched {watched_count} video(s)."
            emit(self.log_queue, summary)
            self._report_status(summary, "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during watching session: {exc}", logging.ERROR)
            self._report_status(f"Watching error: {exc}", "error")
        finally:
            close_driver(self.driver, log_queue=self.log_queue)
