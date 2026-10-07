import logging
import queue
import threading
from typing import Any, List, Optional, Tuple

from ..config.manager import ConfigManager
from ..models.account import Account
from ..models.video_task import VideoTask
from ..utils.logger import emit
from .adblock import ensure_adblock
from .driver import close_driver, setup_driver
from .login import perform_google_login
from .navigation import navigate_to_video_organically
from .youtube_actions import DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS, watch_video

# Retry a failed task (navigation failure or watch error) once before skipping it.
MAX_ATTEMPTS_PER_TASK = 2


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
    ):
        self.account = account
        self.video_tasks = video_tasks
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event or threading.Event()
        self.event_queue = event_queue
        self.worker_id = worker_id
        self.driver: Any = None
        self.skip_ads = True
        self.max_ad_seconds = 32

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
        minutes = self.config_mgr.get_int("FullVideoTimeoutMinutes", 360)
        if minutes < 0:
            return DEFAULT_FULL_VIDEO_TIMEOUT_SECONDS
        return minutes * 60

    def _ad_settings(self) -> Tuple[bool, int]:
        """Read the Skip Ads toggle and its cumulative ad-watch cap."""
        skip_ads = self.config_mgr.get_bool("SkipAds", True)
        max_ad_seconds = self.config_mgr.get_int("MaxAdWatchSeconds", 32)
        if max_ad_seconds < 0:
            max_ad_seconds = 0
        return skip_ads, max_ad_seconds

    # ------------------------------------------------------------------- tasks

    def _run_task(self, task: VideoTask, direct_link_fallback: bool) -> bool:
        """Run one task with a single retry. Returns True when the watch completed."""
        for attempt in range(1, MAX_ATTEMPTS_PER_TASK + 1):
            if self.stop_event.is_set():
                return False

            if attempt > 1:
                emit(self.log_queue, f"Retrying task once (attempt {attempt}/{MAX_ATTEMPTS_PER_TASK})...")

            try:
                nav_ok = navigate_to_video_organically(
                    self.driver,
                    task,
                    direct_link_fallback=direct_link_fallback,
                    log_queue=self.log_queue,
                )
            except Exception as exc:
                emit(self.log_queue, f"Navigation error: {exc}", logging.ERROR)
                nav_ok = False

            if not nav_ok:
                emit(self.log_queue, f"Could not navigate to task #{task.title or task.keywords or task.url}.", logging.WARNING)
                continue

            try:
                if watch_video(
                    self.driver,
                    task.parse_duration_seconds(),
                    log_queue=self.log_queue,
                    stop_event=self.stop_event,
                    full_timeout_seconds=self._full_video_timeout_seconds(),
                    skip_ads=self.skip_ads,
                    max_ad_seconds=self.max_ad_seconds,
                ):
                    return True
            except Exception as exc:
                emit(self.log_queue, f"Watch error: {exc}", logging.ERROR)

            if self.stop_event.is_set():
                return False

        emit(self.log_queue, "Task failed after retry. Skipping to next task.", logging.ERROR)
        return False

    # -------------------------------------------------------------------- run

    def run(self) -> None:
        """Main execution flow for account thread."""
        emit(self.log_queue, f"Starting automation session for account: {self.account.email}")
        direct_link_fallback = self.config_mgr.get_bool("DirectLinkFallback", True)
        total = len(self.video_tasks)
        processed = 0
        succeeded = 0

        try:
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
            for idx, task in enumerate(self.video_tasks, 1):
                if self.stop_event.is_set():
                    emit(self.log_queue, "Automation stopped by user.")
                    break

                emit(self.log_queue, f"\n--- Processing Video Task {idx}/{total}: {task.title or task.keywords or task.url} ---")

                success = self._run_task(task, direct_link_fallback)

                processed += 1
                if success:
                    succeeded += 1
                self._report_progress(processed, total)

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
            close_driver(self.driver, log_queue=self.log_queue)
