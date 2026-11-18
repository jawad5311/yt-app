import logging
import queue
import random
import threading
from typing import Any, List, Optional, Tuple

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, is_video_task_runnable
from ..models.video_task import VideoTask
from ..models.watch_stats import TaskOutcome, resolve_video_key
from ..utils.helpers import extract_video_id
from ..utils.logger import emit
from .adblock import ensure_adblock
from .driver import close_driver, setup_driver
from .login import check_account_logged_in, perform_google_login
from .navigation import (
    find_and_click_search_result,
    find_and_click_suggested_video,
    find_channel_video_on_home,
    perform_youtube_search,
)
from .youtube_actions import DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS, watch_video


class BrowsingWorker:
    """Worker thread manager that executes an organic YouTube browsing and search loop:

    1. Checks the YouTube Home feed for videos belonging to targeted channels.
    2. Watches discovered videos using watch_video() and records stats.
    3. Traverses the suggested / up-next section, watching 2 to 3 suggested videos randomly.
    4. If the suggested section runs out, falls back immediately to keyword research / search.
    5. Performs YouTube searches using queued tasks and targeted channel keywords.
    6. Finds and clicks the target video in search results; if not found, tries another video until found.
    7. Cycles between searching and watching suggested videos in a continuous organic loop until stopped.
    """

    def __init__(
        self,
        account: Account,
        video_tasks: List[VideoTask],
        channels: List[Channel],
        config_mgr: ConfigManager,
        log_queue: Optional[queue.Queue] = None,
        stop_event: Optional[threading.Event] = None,
        event_queue: Optional[queue.Queue] = None,
        worker_id: int = 0,
        stats_store: Optional[StatsStore] = None,
    ):
        self.account = account
        self.video_tasks = video_tasks
        self.channels = channels
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event or threading.Event()
        self.event_queue = event_queue
        self.worker_id = worker_id
        self.stats_store = stats_store
        self.driver: Any = None
        self.skip_ads = True
        self.max_ad_seconds = 9

    # ------------------------------------------------------------------ events

    def _emit_event(self, event: Tuple[Any, ...]) -> None:
        """Push a structured UI event safely."""
        if self.event_queue is None:
            return
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

    def _report_progress(self, processed: int, total: int) -> None:
        self._emit_event(("progress", self.worker_id, processed, total))

    def _send_critical_alert(self, alert_type: str, details: str, force: bool = False) -> None:
        """Send urgent alert email for bot challenges, disconnections, or manual action needed."""
        if not self.config_mgr:
            return
        if getattr(self, "_login_verified", None) is False:
            emit(
                self.log_queue,
                f"Skipping alert email ({alert_type}): account {getattr(self.account, 'email', '')} is not logged in yet. Waiting for login to complete.",
                logging.INFO,
            )
            return
        try:
            from .email_reporter import EmailReporter
            EmailReporter.send_alert_notification(
                account=self.account,
                alert_type=alert_type,
                message_details=details,
                config_mgr=self.config_mgr,
                log_queue=self.log_queue,
                driver=self.driver,
                force=force,
            )
        except Exception as exc:
            logging.debug(f"Failed dispatching critical alert email in BrowsingWorker: {exc}")

    def _check_account_login_before_play(self, content_type: str = "video", content_title: str = "") -> bool:
        """Verify that account is logged in before playing any video; alert if manual action needed."""
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

    def _report_status(self, message: str, level: str = "info") -> None:
        self._emit_event(("status", message, level))

    def _full_video_timeout_seconds(self) -> int:
        minutes = self.config_mgr.get_int("FullVideoTimeoutMinutes", 360)
        if minutes < 0:
            return DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS
        return minutes * 60

    def _ad_settings(self) -> Tuple[bool, int]:
        skip_ads = self.config_mgr.get_bool("SkipAds", True)
        max_ad_seconds = self.config_mgr.get_int("MaxAdWatchSeconds", 9)
        return skip_ads, max(0, max_ad_seconds)

    def _end_buffer_seconds(self) -> float:
        buffer_val = self.config_mgr.get_int("AutoplayEndBufferSeconds", 10)
        return float(max(0, buffer_val))

    def _default_browsing_duration(self) -> Optional[int]:
        """Default watch duration in seconds for discovered/suggested videos."""
        raw = self.config_mgr.get_setting("WatchtimeDuration", "5 Minutes")
        task_temp = VideoTask(watchtime_duration=str(raw))
        parsed = task_temp.parse_duration_seconds()
        # For general browsing, if Full Video is selected, cap suggested videos to 3-5 minutes
        if parsed is None or parsed <= 0:
            return random.randint(180, 300)
        return parsed

    def _extract_target_channels(self) -> List[str]:
        """Return names and handles of all enabled targeted channels."""
        targets: List[str] = []
        for ch in self.channels:
            if not ch.enabled:
                continue
            name = ch.name.strip()
            if name and name not in targets:
                targets.append(name)
            # Check if URL has a handle like /@handle
            if ch.url and "/@" in ch.url:
                handle = ch.url.split("/@")[-1].split("/")[0].split("?")[0].strip()
                if handle and handle not in targets:
                    targets.append(handle)
        return targets

    def _record(self, outcome: TaskOutcome) -> None:
        """Persist finished task outcome to stats store."""
        if self.stats_store is None:
            return
        try:
            self.stats_store.record(outcome)
        except Exception as exc:
            emit(self.log_queue, f"Could not record watch statistics: {exc}", logging.WARNING)

    # -------------------------------------------------------------------- run

    def run(self) -> None:
        """Main execution flow for continuous browsing & search automation loop."""
        emit(self.log_queue, f"Starting YouTube Browsing Loop session for account: {self.account.email}")

        target_channels = self._extract_target_channels()
        if target_channels:
            emit(self.log_queue, f"Targeted Channel(s): {', '.join(target_channels)}")
        else:
            emit(self.log_queue, "No explicit target channels configured; using queued video metadata for targeted browsing.")

        videos_watched_count = 0

        try:
            self._login_verified = False
            self.skip_ads, self.max_ad_seconds = self._ad_settings()
            adblock_path = ensure_adblock(log_queue=self.log_queue) if self.skip_ads else None
            self.driver = setup_driver(self.account, log_queue=self.log_queue, adblock_path=adblock_path)

            if self.stop_event.is_set():
                return

            # Sign-in phase
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
                    emit(self.log_queue, "Session stopped during sign-in.", logging.WARNING)
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
                self._report_status(f"Sign-in failed for {self.account.email} — {details}", "error")
                return

            self._login_verified = True
            self._report_status(f"Browsing session running on {self.account.email}...", "info")

            # ----------------------------------------------------------
            # Phase 1: Check YouTube Home Feed for Targeted Channels
            # ----------------------------------------------------------
            on_watch_page = False

            if not self.stop_event.is_set() and target_channels:
                emit(self.log_queue, "\n=== [Browsing Loop] Checking YouTube Home Feed for Targeted Channel Videos ===")
                home_video = find_channel_video_on_home(
                    self.driver,
                    target_channels=target_channels,
                    max_scrolls=4,
                    log_queue=self.log_queue,
                )

                if home_video:
                    if not self._check_account_login_before_play(content_type="video", content_title=home_video.get("title", "")):
                        return
                    emit(self.log_queue, f"[Browsing Loop] Watching target channel video found on Home: '{home_video.get('title')}'")
                    watch_dur = self._default_browsing_duration()
                    result = watch_video(
                        self.driver,
                        watch_dur,
                        log_queue=self.log_queue,
                        stop_event=self.stop_event,
                        full_timeout_seconds=self._full_video_timeout_seconds(),
                        skip_ads=self.skip_ads,
                        max_ad_seconds=self.max_ad_seconds,
                        end_buffer_seconds=self._end_buffer_seconds(),
                    )
                    videos_watched_count += 1
                    on_watch_page = True

                    # Record stats
                    cur_url = str(getattr(self.driver, "current_url", "") or home_video.get("url", ""))
                    vid_key = extract_video_id(cur_url) or home_video.get("title", "")
                    self._record(
                        TaskOutcome(
                            video_key=vid_key,
                            task_key=vid_key,
                            label=home_video.get("title", vid_key),
                            channel=home_video.get("channel", ""),
                            attempts=1,
                            watched_seconds=result.watched_seconds,
                            ad_seconds=result.ad_seconds,
                            video_duration=result.video_duration,
                            playback_finished=result.playback_finished,
                            success=result.completed,
                            interrupted=result.interrupted,
                        )
                    )
                else:
                    emit(self.log_queue, "[Browsing Loop] No targeted channel video found on Home feed. Moving to YouTube search.")

            # ----------------------------------------------------------
            # Continuous Browsing Loop: Suggested -> Search -> Suggested -> ...
            # ----------------------------------------------------------
            loop_cycle = 0

            while not self.stop_event.is_set():
                loop_cycle += 1
                emit(self.log_queue, f"\n=== [Browsing Loop] Starting Loop Cycle #{loop_cycle} (Total Videos Watched: {videos_watched_count}) ===")

                # If currently on a watch page (from Home feed or prior search), explore suggested section first
                if on_watch_page and not self.stop_event.is_set():
                    # Watch 2 or 3 suggested videos randomly
                    suggested_target_count = random.randint(2, 3)
                    emit(
                        self.log_queue,
                        f"[Browsing Loop] Exploring Suggested Videos section (target: {suggested_target_count} videos randomly)...",
                    )

                    suggested_watched_in_cycle = 0
                    for s_step in range(1, suggested_target_count + 1):
                        if self.stop_event.is_set():
                            break

                        s_video = find_and_click_suggested_video(
                            self.driver,
                            target_channels=target_channels,
                            prefer_target_channels=True,
                            max_scrolls=2,
                            log_queue=self.log_queue,
                        )

                        if not s_video:
                            emit(
                                self.log_queue,
                                "[Browsing Loop] Could not locate next video in suggested section. Switching immediately to keyword research / YouTube search.",
                                logging.INFO,
                            )
                            break

                        if not self._check_account_login_before_play(content_type="suggested video", content_title=s_video.get("title", "")):
                            break
                        emit(
                            self.log_queue,
                            f"[Browsing Loop] Watching suggested video ({s_step}/{suggested_target_count}): '{s_video.get('title')}'...",
                        )
                        s_dur = self._default_browsing_duration()
                        s_res = watch_video(
                            self.driver,
                            s_dur,
                            log_queue=self.log_queue,
                            stop_event=self.stop_event,
                            full_timeout_seconds=self._full_video_timeout_seconds(),
                            skip_ads=self.skip_ads,
                            max_ad_seconds=self.max_ad_seconds,
                            end_buffer_seconds=self._end_buffer_seconds(),
                        )
                        videos_watched_count += 1
                        suggested_watched_in_cycle += 1

                        s_url = str(getattr(self.driver, "current_url", "") or s_video.get("url", ""))
                        s_key = extract_video_id(s_url) or s_video.get("title", "")
                        self._record(
                            TaskOutcome(
                                video_key=s_key,
                                task_key=s_key,
                                label=s_video.get("title", s_key),
                                channel=s_video.get("channel", ""),
                                attempts=1,
                                watched_seconds=s_res.watched_seconds,
                                ad_seconds=s_res.ad_seconds,
                                video_duration=s_res.video_duration,
                                playback_finished=s_res.playback_finished,
                                success=s_res.completed,
                                interrupted=s_res.interrupted,
                            )
                        )

                    emit(
                        self.log_queue,
                        f"[Browsing Loop] Finished suggested section exploration ({suggested_watched_in_cycle} video(s) watched). Switching to YouTube search...",
                    )

                if self.stop_event.is_set():
                    break

                # ------------------------------------------------------
                # Phase 2: YouTube Search for Queued & Targeted Channel Videos
                # ------------------------------------------------------
                emit(self.log_queue, "\n--- [Browsing Loop] Searching YouTube for Targeted Videos ---")

                # Get active runnable tasks from queue
                active_tasks = [
                    t for t in self.video_tasks if is_video_task_runnable(t, self.channels, self.stats_store)
                ]

                # Randomize candidate pool
                candidate_tasks = list(active_tasks)
                random.shuffle(candidate_tasks)

                search_video_found = False

                # Search through candidates until a video is found
                for candidate_idx, task in enumerate(candidate_tasks, start=1):
                    if self.stop_event.is_set():
                        break

                    title_queries = task.get_title_queries()
                    if not title_queries:
                        title_queries = [task.title or task.keywords or task.channel]

                    for query in title_queries:
                        if self.stop_event.is_set():
                            break
                        if not query:
                            continue

                        emit(
                            self.log_queue,
                            f"[Browsing Loop] Searching YouTube for candidate #{candidate_idx}/{len(candidate_tasks)}: '{query}'...",
                        )

                        search_ok = perform_youtube_search(self.driver, query, log_queue=self.log_queue)
                        if not search_ok:
                            continue

                        found_item = find_and_click_search_result(
                            self.driver,
                            task=task,
                            target_channels=target_channels,
                            max_scrolls=3,
                            log_queue=self.log_queue,
                        )

                        if found_item:
                            if not self._check_account_login_before_play(content_type="search video", content_title=found_item.get("title", task.title or "")):
                                break
                            emit(
                                self.log_queue,
                                f"[Browsing Loop] Successfully found and opened video in current tab: '{found_item.get('title')}'!",
                            )
                            # Watch the found video
                            watch_dur = task.parse_duration_seconds() or self._default_browsing_duration()
                            w_res = watch_video(
                                self.driver,
                                watch_dur,
                                log_queue=self.log_queue,
                                stop_event=self.stop_event,
                                full_timeout_seconds=self._full_video_timeout_seconds(),
                                skip_ads=self.skip_ads,
                                max_ad_seconds=self.max_ad_seconds,
                                end_buffer_seconds=self._end_buffer_seconds(),
                            )
                            videos_watched_count += 1
                            on_watch_page = True
                            search_video_found = True

                            cur_url = str(getattr(self.driver, "current_url", "") or found_item.get("url", ""))
                            task_key = resolve_video_key(task, cur_url)
                            self._record(
                                TaskOutcome(
                                    video_key=task_key,
                                    task_key=task_key,
                                    label=task.title or found_item.get("title", task_key),
                                    channel=task.channel or found_item.get("channel", ""),
                                    attempts=1,
                                    watched_seconds=w_res.watched_seconds,
                                    ad_seconds=w_res.ad_seconds,
                                    video_duration=w_res.video_duration,
                                    playback_finished=w_res.playback_finished,
                                    success=w_res.completed,
                                    interrupted=w_res.interrupted,
                                )
                            )
                            break

                    if search_video_found:
                        break
                    else:
                        emit(
                            self.log_queue,
                            f"[Browsing Loop] Video for candidate #{candidate_idx} not found in search results. Trying next candidate...",
                            logging.INFO,
                        )

                # Fallback: If no queued candidate matched or queue is empty, search by target channel names directly
                if not search_video_found and not self.stop_event.is_set() and target_channels:
                    for ch_name in target_channels:
                        if self.stop_event.is_set():
                            break
                        emit(self.log_queue, f"[Browsing Loop] Direct channel search fallback for: '{ch_name}'...")
                        search_ok = perform_youtube_search(self.driver, ch_name, log_queue=self.log_queue)
                        if search_ok:
                            found_item = find_and_click_search_result(
                                self.driver,
                                target_channels=[ch_name],
                                max_scrolls=3,
                                log_queue=self.log_queue,
                            )
                            if found_item:
                                if not self._check_account_login_before_play(content_type="browse video", content_title=found_item.get("title", "")):
                                    break
                                emit(self.log_queue, f"[Browsing Loop] Found channel video in search results: '{found_item.get('title')}'!")
                                w_res = watch_video(
                                    self.driver,
                                    self._default_browsing_duration(),
                                    log_queue=self.log_queue,
                                    stop_event=self.stop_event,
                                    full_timeout_seconds=self._full_video_timeout_seconds(),
                                    skip_ads=self.skip_ads,
                                    max_ad_seconds=self.max_ad_seconds,
                                    end_buffer_seconds=self._end_buffer_seconds(),
                                )
                                videos_watched_count += 1
                                on_watch_page = True
                                search_video_found = True

                                cur_url = str(getattr(self.driver, "current_url", "") or found_item.get("url", ""))
                                vid_key = extract_video_id(cur_url) or found_item.get("title", "")
                                self._record(
                                    TaskOutcome(
                                        video_key=vid_key,
                                        task_key=vid_key,
                                        label=found_item.get("title", vid_key),
                                        channel=ch_name,
                                        attempts=1,
                                        watched_seconds=w_res.watched_seconds,
                                        ad_seconds=w_res.ad_seconds,
                                        video_duration=w_res.video_duration,
                                        playback_finished=w_res.playback_finished,
                                        success=w_res.completed,
                                        interrupted=w_res.interrupted,
                                    )
                                )
                                break

            emit(self.log_queue, f"Browsing session finished for {self.account.email}. Total videos watched: {videos_watched_count}.")
            self._report_status(f"Browsing session stopped. Watched {videos_watched_count} video(s).", "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during browsing worker session for {self.account.email}: {exc}", logging.ERROR)
            self._report_status(f"Browsing worker error for {self.account.email}: {exc}", "error")
        finally:
            close_driver(self.driver, log_queue=self.log_queue)
