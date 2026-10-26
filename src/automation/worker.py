from .session_tracker import SessionChannelTracker
import logging
import queue
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, is_video_task_runnable, check_video_task_eligibility
from ..models.video_task import VideoTask
from ..models.watch_stats import TaskOutcome, resolve_video_key
from ..utils.helpers import extract_video_id
from ..utils.logger import emit
from .dispatcher import TaskDispatcher
from .driver import close_driver, setup_driver
from .login import perform_google_login
from .navigation import navigate_to_video_organically
from .youtube_actions import DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS, watch_video

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
        self.max_ad_seconds = 32
        ch_objs = [Channel.from_dict(c) for c in self.config_mgr.get_channels()] if self.config_mgr else []
        self.channel_tracker = SessionChannelTracker(
            config_mgr=self.config_mgr,
            stats_store=self.stats_store,
            channels=ch_objs,
            emit_event=self._emit_event,
            log_queue=self.log_queue,
        )

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
        max_ad_seconds = self.config_mgr.get_int("MaxAdWatchSeconds", 32)
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

                    return None

                try:
                    enable_interactivity, interactivity_cfg = self._interactivity_settings()
                    ch_name = task.channel or "Uncategorized"
                    self.channel_tracker.set_active_channel(ch_name)
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
                    if result.interrupted:
                        outcome.interrupted = True
                        return outcome
                    if result:
                        outcome.success = True
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
            raise

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
            self.driver = setup_driver(self.account, log_queue=self.log_queue)

            if self.stop_event.is_set():
                return

            # Sign-in phase
            login_ok = perform_google_login(
                self.driver,
                self.account,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
            )

            if not login_ok:
                if self.stop_event.is_set():
                    emit(self.log_queue, "Session stopped during sign-in.", logging.WARNING)
                    return
                emit(
                    self.log_queue,
                    f"Skipping video tasks for {self.account.email} due to failed login.",
                    logging.ERROR,
                )
                self._report_status(
                    f"Sign-in failed for {self.account.email} — complete CAPTCHA / 2FA manually or remove the account.",
                    "error",
                )
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

                cooldown_h = self.config_mgr.get_float('VideoCooldownHours', 12.0)
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
        finally:
            if hasattr(self, "channel_tracker"):
                self.channel_tracker.on_stop()
            close_driver(self.driver, log_queue=self.log_queue)

    def stop(self) -> None:
        """Stop worker execution."""
        self.stop_event.set()
        if self.dispatcher is not None:
            self.dispatcher.wake_all()
        if hasattr(self, "channel_tracker"):
            self.channel_tracker.on_stop()
