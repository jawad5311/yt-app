import logging
import queue
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, check_video_task_eligibility
from ..models.video_task import VideoTask
from ..models.watch_stats import TaskOutcome, resolve_video_key
from ..utils.helpers import extract_video_id
from ..utils.logger import emit
from .dispatcher import TaskDispatcher
from .driver import close_driver, is_driver_alive, setup_driver
from .email_reporter import EmailReporter, SessionReport
from .login import check_account_logged_in, is_logged_in, perform_google_login
from .navigation import navigate_to_video_organically
from .session_tracker import SessionChannelTracker
from .youtube_actions import DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS, watch_short, watch_video

# Retry a failed task across up to 3 titles, with 3 attempts per title (up to 9 attempts total).
ATTEMPTS_PER_TITLE = 3
MAX_TITLES_PER_TASK = 3
MAX_ATTEMPTS_PER_TASK = MAX_TITLES_PER_TASK * ATTEMPTS_PER_TITLE  # 9


class AutomationWorker:
    """Worker thread manager to execute automation tasks across video tasks for an account."""

    def __init__(
        self,
        account: Account,
        video_tasks: List[VideoTask],
        config_mgr: ConfigManager,
        log_queue: Optional[queue.Queue] = None,
        stop_event: Optional[threading.Event] = None,
        event_queue: Optional[queue.Queue] = None,
        worker_id: int = 0,
        stats_store: Optional[StatsStore] = None,
        dispatcher: Optional[TaskDispatcher] = None,
        pause_event: Optional[threading.Event] = None,
    ):
        self.account = account
        self.video_tasks = video_tasks
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self.event_queue = event_queue
        self.worker_id = worker_id
        self.stats_store = stats_store
        self.dispatcher = dispatcher
        self.driver: Any = None
        self.skip_ads = True
        self.max_ad_seconds = 9
        ch_objs = [Channel.from_dict(c) for c in self.config_mgr.get_channels()] if self.config_mgr else []
        self.channel_tracker = SessionChannelTracker(
            config_mgr=self.config_mgr,
            stats_store=self.stats_store,
            channels=ch_objs,
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
        self.has_started_automation = False

    # ------------------------------------------------------------------ events

    def _emit_event(self, event: Tuple[Any, ...]) -> None:
        """Push a structured UI event ((progress, ...) or (status, ...)) safely."""
        if self.event_queue is None:
            return
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

    def _report_progress(self, processed: int, total: int) -> None:
        self._emit_event(("progress", self.worker_id, processed, total))

    def _report_status(self, message: str, level: str = "info") -> None:
        self._emit_event(("status", message, level))

    def _full_video_timeout_seconds(self) -> int:
        """Full-video guard in seconds. 0 (or a negative value) disables the guard."""
        minutes = self.config_mgr.get_int("FullVideoTimeoutMinutes", 5)
        if minutes < 0:
            return DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS
        return minutes * 60

    def _ad_settings(self) -> Tuple[bool, int]:
        """Read the Block Ads (Skip Ads) toggle and its cumulative ad-watch cap."""
        skip_ads = self.config_mgr.get_bool("BlockAds", self.config_mgr.get_bool("SkipAds", False))
        max_ad_seconds = self.config_mgr.get_int("MaxAdWatchSeconds", 9)
        if max_ad_seconds < 0:
            max_ad_seconds = 0
        return skip_ads, max_ad_seconds

    def _end_buffer_seconds(self) -> float:
        """Early stop buffer in seconds (5-15s) to avoid autoplay. 0 = randomized 7-12s."""
        buffer_val = self.config_mgr.get_int("AutoplayEndBufferSeconds", 10)
        return float(max(0, buffer_val))

    def _interactivity_settings(self) -> Tuple[bool, Dict[str, Any]]:
        """Read natural human interactivity simulation settings."""
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

    # ------------------------------------------------------------------- tasks

    def _run_task(self, task: VideoTask, direct_link_fallback: bool) -> TaskOutcome:
        """Run one task with a single retry, returning one recordable outcome.

        Retries fold into a single outcome: ``attempts`` counts the tries and
        ``watched_seconds`` accumulates the watch time actually delivered.
        """
        label = task.title or task.keywords or task.url
        task_key = resolve_video_key(task)
        task_url = (task.url or "").strip()
        task_vid_id = extract_video_id(task_url) if task_url else ""
        task_label = (task.title or task.keywords or "").strip()

        raw_queue = self.config_mgr.get_video_queue() if self.config_mgr else []
        initial_in_queue = False
        for item in raw_queue:
            if not isinstance(item, dict):
                continue
            item_url = (item.get("url") or "").strip()
            if task_vid_id:
                if extract_video_id(item_url) == task_vid_id:
                    initial_in_queue = True
                    break
            elif task_url:
                if item_url == task_url:
                    initial_in_queue = True
                    break
            else:
                t_temp = VideoTask.from_dict(item)
                if resolve_video_key(t_temp) == task_key:
                    initial_in_queue = True
                    break
                t_label = (t_temp.title or t_temp.keywords or "").strip()
                if task_label and t_label.lower() == task_label.lower():
                    initial_in_queue = True
                    break
        if not initial_in_queue and bool(self.video_tasks):
            initial_in_queue = True

        outcome = TaskOutcome(
            video_key=task_key,
            task_key=task_key,
            label=label,
            channel=task.channel,
        )
        title_queries = task.get_title_queries()
        if not title_queries:
            title_queries = [task.title or task.keywords or ""]

        # Each configured title line is attempted ATTEMPTS_PER_TITLE times (e.g. 3 titles * 3 tries = 9 max attempts)
        total_max_attempts = len(title_queries) * ATTEMPTS_PER_TITLE
        overall_attempt = 0

        for title_idx, active_query in enumerate(title_queries, start=1):
            for try_num in range(1, ATTEMPTS_PER_TITLE + 1):
                if self.stop_event.is_set():
                    outcome.interrupted = True
                    return outcome

                overall_attempt += 1
                outcome.attempts = overall_attempt

                # On the 3rd attempt for a title, append channel name to search query if available
                query_to_search = active_query
                channel_name = (task.channel or "").strip()
                if try_num == ATTEMPTS_PER_TITLE and channel_name:
                    if query_to_search:
                        query_to_search = f"{query_to_search} {channel_name}"
                    else:
                        query_to_search = channel_name

                if overall_attempt > 1:
                    emit(
                        self.log_queue,
                        f"Retrying task (attempt {overall_attempt}/{total_max_attempts} - Title {title_idx}/{len(title_queries)}, try {try_num}/{ATTEMPTS_PER_TITLE}) using title: '{query_to_search}'...",
                    )

                # Fallback to direct navigation URL only on the final possible attempt
                is_last_attempt = (overall_attempt == total_max_attempts)
                allow_direct = direct_link_fallback if is_last_attempt else False

                if hasattr(self, "channel_tracker") and self.channel_tracker:
                    self.channel_tracker.record_search_query()
                try:
                    nav_ok = navigate_to_video_organically(
                        self.driver,
                        task,
                        direct_link_fallback=allow_direct,
                        log_queue=self.log_queue,
                        search_query_override=query_to_search,
                    )
                except Exception as exc:
                    emit(self.log_queue, f"Navigation error: {exc}", logging.ERROR)
                    nav_ok = False

                if not nav_ok:
                    emit(self.log_queue, f"Could not navigate to task #{label} (attempt {overall_attempt}/{total_max_attempts}).", logging.WARNING)
                    continue

                # The resolved page URL identifies keyword-only tasks across runs.
                try:
                    resolved_url = str(getattr(self.driver, "current_url", "") or "")
                except Exception:
                    resolved_url = ""
                outcome.video_key = resolve_video_key(task, resolved_url)

                def _is_task_channel_disabled() -> Optional[str]:
                    # 1. Read latest video queue if task belongs to queue
                    latest_task: Optional[VideoTask] = None
                    if initial_in_queue and self.config_mgr is not None:
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
                            emit(self.log_queue, f"Video '{task_label or task_url}' was removed or replaced. Stopping playback.")
                            return "task_removed"

                        latest_task = VideoTask.from_dict(matched_item)
                        if not latest_task.enabled:
                            emit(self.log_queue, f"Video '{latest_task.title or task_label}' was unchecked/disabled. Stopping playback.")
                            return "task_disabled"

                        latest_max_runs = latest_task.max_runs
                        latest_multiplier = latest_task.target_multiplier

                        if self.stats_store is not None:
                            rec = self.stats_store.find_for_task(latest_task)
                            if rec is None and task is not None:
                                rec = self.stats_store.find_for_task(task)
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
                                    return "task_target_reached"
                    else:
                        latest_task = task
                        if latest_task and latest_task.target_multiplier > 0.0 and self.stats_store is not None:
                            rec = self.stats_store.find_for_task(latest_task)
                            prev_watched = rec.watch_seconds if rec else 0.0
                            curr_watch = self.channel_tracker.get_active_watch_delta() if hasattr(self, "channel_tracker") else 0.0
                            total_watched = prev_watched + curr_watch
                            eff_dur = (rec.video_duration if rec and rec.video_duration > 0 else 0.0) or getattr(latest_task, "duration_seconds", 0.0)
                            if eff_dur > 0.0 and total_watched >= latest_task.target_multiplier * eff_dur:
                                return "task_target_reached"

                    # 2. Check channel status
                    ch_name = (latest_task.channel if latest_task else task.channel) or ""
                    ch_name = ch_name.strip()
                    if ch_name:
                        if hasattr(self, "channel_tracker") and self.channel_tracker.is_channel_target_achieved(ch_name):
                            return "channel_target_achieved"
                        if self.config_mgr is not None:
                            try:
                                raw_channels = self.config_mgr.get_channels()
                            except Exception as exc:
                                emit(self.log_queue, f"Control state read error (channels): {exc}", logging.ERROR)
                                return "control_read_failed"

                            clean_name = ch_name.lower().lstrip("@")
                            found_ch = None
                            for c in raw_channels:
                                if not isinstance(c, dict):
                                    continue
                                if (c.get("name") or "").strip().lower().lstrip("@") == clean_name:
                                    found_ch = Channel.from_dict(c)
                                    break

                            if found_ch is None:
                                emit(self.log_queue, f"Channel '{ch_name}' not found or removed. Stopping playback.")
                                return "channel_disabled"
                            if not found_ch.enabled or found_ch.archived:
                                emit(self.log_queue, f"Channel '{ch_name}' is disabled or archived. Stopping playback.")
                                return "channel_disabled"
                            if found_ch.target_achieved:
                                emit(self.log_queue, f"Channel '{ch_name}' target watch time achieved. Stopping playback.")
                                return "channel_target_achieved"
                            try:
                                from .warmup_manager import WarmupManager
                                curr_watch = self.channel_tracker.get_active_watch_delta() if hasattr(self, "channel_tracker") else 0.0
                                acc_email = (getattr(getattr(self, "account", None), "email", None) or "").strip() or None
                                if WarmupManager(self.config_mgr).is_channel_daily_quota_reached(
                                    found_ch.name, found_ch.url, account_email=acc_email, extra_seconds=curr_watch
                                ):
                                    emit(self.log_queue, f"Channel '{ch_name}' daily warm-up quota reached. Stopping playback.")
                                    return "channel_warmup_quota_reached"
                            except Exception:
                                pass

                    return None

                try:
                    enable_interactivity, interactivity_cfg = self._interactivity_settings()
                    ch_name = task.channel or "Uncategorized"
                    self.channel_tracker.set_active_channel(ch_name)
                    if getattr(task, 'is_short', False) or ('/shorts/' in (getattr(task, 'url', '') or '')):
                        def _on_short_view_counted(loop_num: int, cur_t: float, dur: float):
                            if task and hasattr(self, 'stats_store') and self.stats_store:
                                self.stats_store.record_view(task, views=1)
                                emit(
                                    self.log_queue,
                                    f"Credited 1 view to Short '{task.title}' (Loop {loop_num}, reached {cur_t:.1f}s / {dur:.1f}s).",
                                    logging.INFO,
                                )

                        if not self._check_account_login_before_play(content_type="short", content_title=task.title or ""):
                            outcome.error = "Manual action required on this PC"
                            self._report_status(f"Manual action required on this PC for {self.account.email}.", "error")
                            break
                        result = watch_short(
                            self.driver,
                            stop_event=self.stop_event,
                            log_queue=self.log_queue,
                            pause_event=self.pause_event,
                            should_stop_callback=_is_task_channel_disabled,
                            on_watch_tick=lambda d: self.channel_tracker.add_watch_delta(ch_name, d),
                            on_view_counted=_on_short_view_counted,
                        )
                        outcome.runs = 0
                    else:
                        if not self._check_account_login_before_play(content_type="video", content_title=task.title or ""):
                            outcome.error = "Manual action required on this PC"
                            self._report_status(f"Manual action required on this PC for {self.account.email}.", "error")
                            break
                        result = watch_video(
                            self.driver,
                            task.parse_duration_seconds(),
                            log_queue=self.log_queue,
                            stop_event=self.stop_event,
                            full_timeout_seconds=self._full_video_timeout_seconds(),
                            skip_ads=self.skip_ads,
                            max_ad_seconds=self.max_ad_seconds,
                            end_buffer_seconds=self._end_buffer_seconds(),
                            enable_interactivity=enable_interactivity,
                            interactivity_config=interactivity_cfg,
                            pause_event=self.pause_event,
                            should_stop_callback=_is_task_channel_disabled,
                            on_watch_tick=lambda d: self.channel_tracker.add_watch_delta(ch_name, d),
                        )
                    self.channel_tracker.set_active_channel(None)
                    outcome.watched_seconds += max(0.0, result.watched_seconds)
                    outcome.ad_seconds += max(0.0, result.ad_seconds)
                    if result.video_duration > 0:
                        outcome.video_duration = result.video_duration
                    if result.playback_finished:
                        outcome.playback_finished = True
                    if hasattr(self, "channel_tracker") and self.channel_tracker and (outcome.watched_seconds > 0 or outcome.success):
                        is_s = getattr(task, "is_short", False) or ("/shorts/" in (getattr(task, "url", "") or ""))
                        self.channel_tracker.record_video_watched(is_short=is_s, channel_name=task.channel, source="search")
                    if getattr(result, "watched_seconds", 0.0) > 0 and task.channel and self.config_mgr is not None:
                        try:
                            from .warmup_manager import WarmupManager
                            clean_ch = task.channel.strip().lower().lstrip("@")
                            matched_url = ""
                            matched_raw = None
                            for c_dict in self.config_mgr.get_channels() or []:
                                if isinstance(c_dict, dict) and (c_dict.get("name") or "").strip().lower().lstrip("@") == clean_ch:
                                    matched_raw = c_dict
                                    matched_url = str(c_dict.get("url") or "")
                                    break
                            if matched_raw is not None:
                                acc_email = (getattr(getattr(self, "account", None), "email", None) or "").strip() or None
                                WarmupManager(self.config_mgr).record_channel_watch_time(
                                    task.channel, float(result.watched_seconds), channel_url=matched_url, account_email=acc_email
                                )
                        except Exception as cw_err:
                            logging.debug(f"Could not record channel warm-up watch time in AutomationWorker: {cw_err}")
                    if result.interrupted:
                        outcome.interrupted = True
                        return outcome
                    if result:
                        outcome.success = True
                        outcome.runs = 1 if (result.playback_finished or result.completed) else None
                        return outcome
                except Exception as exc:
                    emit(self.log_queue, f"Watch error: {exc}", logging.ERROR)

                if self.stop_event.is_set():
                    outcome.interrupted = True
                    return outcome

        emit(self.log_queue, f"Task failed after {overall_attempt} attempts across {len(title_queries)} titles. Skipping to next task.", logging.ERROR)
        return outcome


    def _record(self, outcome: TaskOutcome) -> None:
        """Persist one finished task; a stats error never aborts a session."""
        if self.stats_store is None or not outcome.attempts:
            return
        try:
            self.stats_store.record(outcome)
        except Exception as exc:
            emit(self.log_queue, f"Could not record watch statistics: {exc}", logging.ERROR)
            if self.dispatcher is not None:
                key = outcome.video_key or outcome.task_key
                self.dispatcher.exclude_video_key(key, reason=str(exc))

    # -------------------------------------------------------------------- run

    def run(self) -> None:
        """Main execution flow for account thread."""
        emit(self.log_queue, f"Starting automation session for account: {self.account.email}")
        direct_link_fallback = self.config_mgr.get_bool("DirectLinkFallback", False)

        if self.dispatcher is not None:
            total = self.dispatcher.register_worker(self.worker_id)
        else:
            total = len(self.video_tasks)

        processed = 0
        succeeded = 0

        try:
            self.skip_ads, self.max_ad_seconds = self._ad_settings()
            known_raw = self.config_mgr.get_accounts() if self.config_mgr else None
            known = [Account.from_dict(a) if isinstance(a, dict) else a for a in known_raw] if known_raw else None
            self.driver = setup_driver(self.account, log_queue=self.log_queue, known_accounts=known)
            self.has_started_automation = True

            if self.stop_event.is_set():
                return

            # Wait for sign-in and verify it successfully before dispatching session starting email notification
            max_retries = None
            if self.config_mgr:
                try:
                    mr = self.config_mgr.get_setting("SignInMaxRetries", None)
                    if mr is not None:
                        max_retries = int(mr)
                except Exception:
                    pass
            login_ok = self._ensure_signed_in_and_wait(max_retries=max_retries)

            if login_ok and self.worker_id == 0:
                # Sign-in verified! Send start notification in headless browser asynchronously
                self.reporting_thread = threading.Thread(
                    target=self._async_init_and_send_start_notification,
                    name="ReportingStartNotificationThread",
                    daemon=True,
                )
                self.reporting_thread.start()

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
                self._send_critical_alert(alert_type, details)
                session_status = alert_type
                session_err = details
                return

            # Iterate through video tasks
            task_idx = 0
            while not self.stop_event.is_set():
                if self.dispatcher is not None:
                    task = self.dispatcher.acquire_next_task(self.worker_id, stop_event=self.stop_event)
                    if task is None:
                        break
                else:
                    if task_idx >= len(self.video_tasks):
                        break
                    task = self.video_tasks[task_idx]

                task_idx += 1
                raw_channels = self.config_mgr.get_channels()
                channel_objs = [Channel.from_dict(c) for c in raw_channels]

                if task.channel:
                    matching_ch = [c for c in channel_objs if c.name.lower() == task.channel.lower()]
                    target_hit = False
                    if matching_ch:
                        ch_obj = matching_ch[0]
                        if (hasattr(self, "channel_tracker") and self.channel_tracker.is_channel_target_achieved(ch_obj.name)) or ch_obj.target_achieved:
                            target_hit = True
                    if target_hit:
                        emit(
                            self.log_queue,
                            f"Skipping task '{task.title or task.keywords or task.url}': channel '{task.channel}' achieved target watch time ({matching_ch[0].watch_seconds/3600:.1f}/{matching_ch[0].target_watch_hours:.1f}h).",
                        )
                        if self.dispatcher is not None:
                            self.dispatcher.release_task(task)
                        continue
                    if matching_ch:
                        try:
                            from .warmup_manager import WarmupManager
                            acc_email = (getattr(getattr(self, "account", None), "email", None) or "").strip() or None
                            if WarmupManager(self.config_mgr).is_channel_daily_quota_reached(
                                matching_ch[0].name, matching_ch[0].url, account_email=acc_email
                            ):
                                emit(
                                    self.log_queue,
                                    f"Skipping task '{task.title or task.keywords or task.url}': channel '{task.channel}' reached daily warm-up quota for this account.",
                                )
                                if self.dispatcher is not None:
                                    self.dispatcher.release_task(task)
                                continue
                        except Exception:
                            pass

                cooldown_h = self.config_mgr.get_float('VideoCooldownHours', 18.0)
                eligible, unrunnable_reason = check_video_task_eligibility(
                    task, channel_objs, self.stats_store, cooldown_hours=cooldown_h
                )
                if not eligible:
                    emit(
                        self.log_queue,
                        f"Skipping task '{task.title or task.keywords or task.url}': {unrunnable_reason}.",
                    )
                    if self.dispatcher is not None:
                        self.dispatcher.release_task(task)
                    continue

                emit(self.log_queue, f"\n--- Processing Video Task {task_idx}/{total}: {task.title or task.keywords or task.url} ---")

                try:
                    outcome = self._run_task(task, direct_link_fallback)
                    if outcome.success:
                        succeeded += 1
                    try:
                        self._record(outcome)
                    except Exception as rec_exc:
                        emit(self.log_queue, f"Persistence error for task '{task.title or task.keywords or task.url}': {rec_exc}. Video excluded.", logging.ERROR)
                    processed += 1
                    self._report_progress(processed, total)
                finally:
                    if self.dispatcher is not None:
                        self.dispatcher.release_task(task)

                if self.stop_event.is_set():
                    emit(self.log_queue, "Automation stopped by user.")
                    break

            failed = processed - succeeded
            summary = f"Session completed for {self.account.email}: {succeeded}/{processed} tasks watched"
            if failed:
                summary += f", {failed} failed"
            emit(self.log_queue, summary + ".", logging.ERROR if failed else logging.INFO)
            if processed:
                self._report_status(summary + ".", "error" if failed else "info")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during worker session for {self.account.email}: {exc}", logging.ERROR)
            self._report_status(f"Worker crashed for {self.account.email}: {exc}", "error")
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
            # ONLY if automation successfully started and was not aborted during setup/driver launch
            if getattr(self, "has_started_automation", False):
                self._send_session_report(status=session_status, error=session_err)

            # Wait briefly if background start notification thread is still finishing
            rep_thread = getattr(self, "reporting_thread", None)
            if rep_thread and rep_thread.is_alive():
                rep_thread.join(timeout=5)

            # Close persistent headless reporting browser
            if self.reporting_driver:
                close_driver(self.reporting_driver, log_queue=self.log_queue)
                self.reporting_driver = None

    def _ensure_signed_in_and_wait(self, wait_seconds: float = 120.0, max_retries: Optional[int] = None) -> bool:
        """Verify account login before starting or resuming session.

        If signed out or sign-in is incomplete:
        - Waits for the account to be signed in (default 2 minutes / 120s).
        - Re-checks periodically and after 2 minutes.
        - Once signed in successfully, returns True.
        """
        if not self.account or not getattr(self.account, "email", "").strip():
            return True

        if self.stop_event.is_set():
            return False

        if self.config_mgr:
            try:
                wait_seconds = float(self.config_mgr.get_setting("SignInWaitSeconds", wait_seconds))
            except Exception:
                pass

        attempt = 0
        while not self.stop_event.is_set():
            attempt += 1
            emit(self.log_queue, f"Checking Google sign-in status for {self.account.email} (Attempt #{attempt})...", logging.INFO)

            login_ok = perform_google_login(
                self.driver,
                self.account,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
                config_mgr=self.config_mgr,
                alert_callback=self._send_critical_alert,
            )
            if login_ok:
                emit(self.log_queue, f"Sign-in verified successfully for {self.account.email}!", logging.INFO)
                return True

            if self.stop_event.is_set():
                return False

            if max_retries is not None and attempt >= max_retries:
                emit(self.log_queue, f"Maximum sign-in retry attempts reached for {self.account.email}.", logging.WARNING)
                return False

            wait_mins = wait_seconds / 60.0
            wait_str = f"{int(wait_mins)} minute{'s' if int(wait_mins) != 1 else ''}" if wait_mins.is_integer() else f"{wait_mins:.1f} minutes"
            emit(
                self.log_queue,
                f"Account {self.account.email} is not signed in. Waiting {wait_str} for sign-in...",
                logging.WARNING,
            )

            wait_start = time.time()
            wait_end = wait_start + wait_seconds

            while time.time() < wait_end and not self.stop_event.is_set():
                if is_logged_in(self.driver, self.account):
                    emit(self.log_queue, f"Sign-in detected for {self.account.email} during wait!", logging.INFO)
                    return True

                self.stop_event.wait(timeout=min(2.0, max(0.1, wait_end - time.time())))

            if self.stop_event.is_set():
                return False

            emit(
                self.log_queue,
                f"{wait_str} elapsed. Re-checking sign-in status for {self.account.email}...",
                logging.INFO,
            )

        return False

    def _async_init_and_send_start_notification(self) -> None:
        """Initialize headless reporting browser and send start notification asynchronously in background with retry and auto-recovery."""
        if not self.config_mgr or self.worker_id != 0:
            return

        delays = getattr(self, "_start_notification_delays", [5.0, 10.0, 15.0, 20.0])
        max_attempts = len(delays) + 1

        for attempt in range(1, max_attempts + 1):
            if self.stop_event.is_set():
                return

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

                delivered = self._send_start_notification()
                if delivered:
                    return
            except Exception as exc:
                logging.debug(f"Start notification attempt {attempt} failed: {exc}")

            if self.stop_event.is_set():
                return

            if attempt < max_attempts:
                wait_sec = delays[attempt - 1]
                emit(
                    self.log_queue,
                    f"Start notification email delivery attempt {attempt} did not succeed. Waiting {int(wait_sec)}s before retry (attempt {attempt + 1}/{max_attempts})...",
                    logging.WARNING,
                )
                # Cleanup failed reporting driver so next attempt can re-sync profile cookies
                if getattr(self, "reporting_driver", None):
                    try:
                        self.reporting_driver.quit()
                    except Exception:
                        pass
                    self.reporting_driver = None

                sleep_start = time.time()
                while time.time() - sleep_start < wait_sec:
                    if self.stop_event.is_set():
                        return
                    time.sleep(1.0)
            else:
                emit(
                    self.log_queue,
                    f"[Start Notification Alert] Failed to deliver session start notification email after {max_attempts} attempts. "
                    f"Please check your Google account credentials or email reporting configuration.",
                    logging.ERROR,
                )

    def _send_start_notification(self) -> bool:
        """Deliver start notification email via headless browser. Returns True on success, False otherwise."""
        if not self.config_mgr or self.worker_id != 0:
            return False
        try:
            total_sec = 0.0
            if hasattr(self, "channel_tracker") and self.channel_tracker:
                total_sec = sum(self.channel_tracker.initial_channel_watch_seconds.values())
            elif self.config_mgr:
                total_sec = sum(float(c.get("watch_seconds", 0.0) or 0.0) for c in self.config_mgr.get_channels())

            ok = EmailReporter.send_start_notification(
                account=self.account,
                session_mode="Automation Queue",
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
            return bool(ok)
        except Exception as exc:
            logging.debug(f"Failed dispatching start notification email: {exc}")
            return False

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
                log_queue=self.log_queue,
                thread_url=getattr(self, "session_thread_url", None),
                thread_message_id=getattr(self, "session_thread_message_id", None),
                thread_info=getattr(self, "session_thread_info", None),
                driver=getattr(self, "reporting_driver", None) or self.driver,
            )
            if getattr(self, "session_thread_info", None) and isinstance(self.session_thread_info, dict):
                if self.session_thread_info.get("thread_url"):
                    self.session_thread_url = self.session_thread_info["thread_url"]
                if self.session_thread_info.get("message_id"):
                    self.session_thread_message_id = self.session_thread_info["message_id"]
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
                session_mode="Automation Queue",
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
                self._emit_event(("report_sent", True, "Session report sent successfully!"))
        except Exception as exc:
            emit(self.log_queue, f"Failed dispatching session email report: {exc}", logging.WARNING)
            self._emit_event(("report_sent", False, f"Failed dispatching session email report: {exc}"))

    def stop(self) -> None:
        """Stop worker execution."""
        self.stop_event.set()
        if self.dispatcher is not None:
            self.dispatcher.wake_all()
        if hasattr(self, "channel_tracker"):
            self.channel_tracker.on_stop()
