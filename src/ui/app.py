import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk
from .theme import DARK_THEME, get_font, get_theme_colors, setup_ttk_styles

from ..automation.dispatcher import TaskDispatcher
from ..automation.driver import close_driver
from ..automation.watching_worker import WatchingWorker
from ..automation.worker import AutomationWorker
from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, is_video_task_runnable
from ..models.video_task import VideoTask
from ..models.watch_stats import format_duration, resolve_video_key
from ..utils.logger import emit, init_logging
from .background_jobs import BackgroundJobs
from .views.accounts_view import AccountsView
from .views.channels_view import ChannelsView
from .views.dashboard_view import DashboardView
from .views.logs_view import LogsView
from .views.queue_view import QueueView
from .views.settings_view import SettingsView
from ..utils.quota_tracker import get_quota_tracker
from .tooltip import add_tooltip


class SessionEventQueue:
    """Wraps an event queue to tag all events with their session ID."""
    def __init__(self, target_queue: queue.Queue, session_id: int):
        self._target = target_queue
        self.session_id = session_id

    def put(self, item, block=True, timeout=None):
        self._target.put(("session_event", self.session_id, item), block=block, timeout=timeout)

    def put_nowait(self, item):
        self._target.put_nowait(("session_event", self.session_id, item))

    def get(self, *args, **kwargs):
        return self._target.get(*args, **kwargs)

    def get_nowait(self):
        return self._target.get_nowait()

    def empty(self):
        return self._target.empty()


def sanitize_error(exc: Any, accounts: Optional[List[Any]] = None) -> str:
    """Format exception message without leaking account passwords."""
    msg = str(exc)
    if accounts:
        for acc in accounts:
            pwd = getattr(acc, "password", None)
            if pwd and len(pwd) > 0 and pwd in msg:
                msg = msg.replace(pwd, "[REDACTED_PASSWORD]")
    return msg


class YTBotApp(tk.Tk):
    def __init__(self, config_mgr: Optional[ConfigManager] = None, stats_store: Optional[StatsStore] = None):
        super().__init__()
        self.config_mgr = config_mgr or ConfigManager()
        self.stats_store = stats_store or StatsStore()

        self.title("YouTube Playback Automation Tool - V2.1 (Modular)")
        self.geometry("1180x740")
        self.minsize(1020, 620)
        self.maximize_window()

        self.apply_appearance_mode(self.config_mgr.get_setting("AppearanceMode", "Dark"), persist=False)

        self.log_file = init_logging()
        self.log_queue: queue.Queue = queue.Queue()
        # Structured worker events: ("progress", worker_id, done, total) / ("status", text, level)
        self.event_queue: queue.Queue = queue.Queue()
        self.background_jobs = BackgroundJobs()
        self.worker_progress: dict = {}

        self.loaded_accounts: List[Account] = []
        raw_videos = self.config_mgr.get_video_queue() if hasattr(self, "config_mgr") and self.config_mgr else []
        self.queued_video_tasks: List[VideoTask] = [VideoTask.from_dict(v) for v in raw_videos]
        self.active_threads: List[threading.Thread] = []
        self.active_workers: List[Any] = []
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.is_running = False
        self.is_closing = False
        self.current_mode = "idle"
        self.current_view_name = "dashboard"
        self._queue_needs_refresh = False
        self._shorts_queue_needs_refresh = False
        self._channels_needs_refresh = False
        self.session_manager_thread: Optional[threading.Thread] = None
        self._session_counter: int = 0
        self.current_session_id: int = 0
        self._poll_job: Optional[str] = None
        self._close_check_job: Optional[str] = None
        self._close_start_time: float = 0.0
        self._close_timeout: float = 30.0
        self._report_finish_time: Optional[float] = None
        self.report_modal: Optional[Any] = None
        self._close_button_enabled: Optional[bool] = None
        self._quota_check_job: Optional[str] = None
        self._quota_tooltip: Optional[Any] = None
        self.quota_tracker = get_quota_tracker()
        self.quota_tracker.register_listener(self._on_quota_updated)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._update_close_button_state()
        self._poll_job = self.after(50, self._poll_log_queue)
        self.after(50, self.maximize_window)
        self.after(100, self._update_close_button_state)
        self._poll_quota_clock()

        # Pre-load accounts fallback if saved list is empty and accounts.txt exists
        if not self.loaded_accounts:
            accounts_file = self.config_mgr.get_setting("AccountsFile", "accounts.txt")
            if Path(accounts_file).exists():
                self.views["accounts"].import_accounts_file(accounts_file)

        # Auto-enrich queued video tasks with missing published date or duration
        self._start_background_metadata_enrichment()

    def _start_background_metadata_enrichment(self) -> None:
        """Enrich queued video tasks with missing published date or duration via YouTube API."""
        if not hasattr(self, "config_mgr") or not self.config_mgr:
            return
        api_key = (self.config_mgr.get_setting("YouTubeApiKey", "") or "").strip()
        if not api_key:
            return

        needs = [
            t for t in self.queued_video_tasks
            if not getattr(t, "published_at", "") or getattr(t, "duration_seconds", 0) <= 0
        ]
        if not needs:
            return

        def _worker():
            try:
                from ..utils.channel_fetcher import enrich_video_tasks
                updated = enrich_video_tasks(self.queued_video_tasks, api_key=api_key)
                if updated > 0:
                    raw = [t.to_dict() for t in self.queued_video_tasks]
                    self.config_mgr.set_video_queue(raw)
                    def _update_ui():
                        self.on_queue_changed(self.queued_video_tasks)
                        self.refresh_stats_views()
                    self.after(0, _update_ui)
            except Exception as err:
                logging.getLogger("views_app").debug("Background enrichment failed: %s", err)

        threading.Thread(target=_worker, daemon=True, name="bg-metadata-enrichment").start()

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Navigation Sidebar (Apple macOS Sidebar)
        self.sidebar = ttk.Frame(self, style="Sidebar.TFrame", width=210)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(9, weight=1)

        # macOS Sidebar Header
        self.sidebar_header = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self.sidebar_header.grid(row=0, column=0, padx=16, pady=(18, 14), sticky="ew")

        ttk.Label(
            self.sidebar_header,
            text="YT Organic Bot",
            style="Sidebar.TLabel",
            font=get_font(size=12, weight="bold"),
        ).pack(anchor="w")

        ttk.Label(
            self.sidebar_header,
            text="AUTOMATION SUITE",
            style="Sidebar.TLabel",
            foreground="#98989d",
            font=get_font(size=8, weight="bold"),
        ).pack(anchor="w", pady=(2, 0))

        self.nav_buttons: Dict[str, Any] = {}
        nav_items = [
            ("dashboard", "📊  Dashboard"),
            ("accounts", "👤  Accounts"),
            ("channels", "📺  Channels"),
            ("queue", "🎥  Video Queue"),
            ("shorts_queue", "⚡  Shorts Queue"),
            ("settings", "⚙  Settings"),
            ("logs", "📋  Logs"),
        ]

        for idx, (name, label_text) in enumerate(nav_items, start=1):
            btn = ttk.Button(
                self.sidebar,
                text=label_text,
                style="Nav.TButton",
                command=lambda n=name: self.show_view(n),
            )
            btn.grid(row=idx, column=0, padx=10, pady=4, sticky="ew")
            self.nav_buttons[name] = btn

        # Main Views Container
        self.container = ttk.Frame(self, style="Transparent.TFrame")
        self.container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)

        self.views: Dict[str, Any] = {
            "dashboard": DashboardView(self.container, self),
            "accounts": AccountsView(self.container, self),
            "channels": ChannelsView(self.container, self),
            "queue": QueueView(self.container, self, shorts_only=False),
            "shorts_queue": QueueView(self.container, self, shorts_only=True),
            "settings": SettingsView(self.container, self),
            "logs": LogsView(self.container, self),
        }

        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

        self.show_view("dashboard")
        self.update_dashboard_stats()
        self.refresh_watch_stats()

        # Daily YouTube API Quota badge under the minimize button
        self.quota_label = ttk.Label(
            self,
            text=f"Today's Quota: {self.quota_tracker.get_used():,}",
            style="QuotaBadge.TLabel",
            cursor="arrow",
        )
        self.quota_label.place(relx=1.0, x=-115, y=7, anchor="n")
        self.quota_label.lift()
        self._quota_tooltip = add_tooltip(
            self.quota_label,
            f"YouTube Data API quota used today ({self.quota_tracker.get_date()}): {self.quota_tracker.get_used():,} units.\nResets automatically when a new day starts.",
        )

    def _poll_quota_clock(self) -> None:
        """Periodic check (every 1s) to detect system clock calendar day change and reset quota."""
        if self.is_closing:
            return
        try:
            self.quota_tracker.check_day_rollover()
        except Exception as exc:
            logging.getLogger("views_app").debug("Error in quota day rollover check: %s", exc)
        finally:
            if not self.is_closing:
                self._quota_check_job = self.after(1000, self._poll_quota_clock)

    def _on_quota_updated(self, used: int) -> None:
        """Thread-safe callback from QuotaTracker on quota increments or day rollovers."""
        try:
            self.after(0, self._render_quota, used)
        except Exception:
            pass

    def _render_quota(self, used: int) -> None:
        """Update the quota label display and its tooltip."""
        if not hasattr(self, "quota_label") or not self.quota_label:
            return
        try:
            self.quota_label.configure(text=f"Today's Quota: {used:,}")
            self.quota_label.lift()
            if getattr(self, "_quota_tooltip", None):
                today_str = self.quota_tracker.get_date()
                self._quota_tooltip.text = (
                    f"YouTube Data API quota used today ({today_str}): {used:,} units.\n"
                    f"Resets automatically when a new day starts."
                )
        except Exception:
            pass

    def get_runnable_video_count(self) -> int:
        """Count only the enabled, runnable video tasks that will be processed by automation."""
        channels = (
            [Channel.from_dict(c) for c in self.config_mgr.get_channels()]
            if hasattr(self, "config_mgr")
            else []
        )
        stats = getattr(self, "stats_store", None)
        cooldown_h = self.config_mgr.get_float("VideoCooldownHours", 12.0) if hasattr(self, "config_mgr") else 0.0
        return sum(1 for t in self.queued_video_tasks if is_video_task_runnable(t, channels, stats, cooldown_hours=cooldown_h))

    def reset_all_watch_stats(self) -> None:
        """Completely reset all watch statistics across stats.json, settings channel counters, and running workers."""
        if hasattr(self, "stats_store") and self.stats_store:
            self.stats_store.clear_all()

        if hasattr(self, "config_mgr") and self.config_mgr:
            self.config_mgr.reset_channel_watch_times()

        # Reset in-memory trackers of active workers
        for worker in list(self.__dict__.get("active_workers", [])):
            tracker = getattr(worker, "channel_tracker", None)
            if tracker and hasattr(tracker, "reset"):
                tracker.reset()
            for ch in getattr(worker, "channels", []):
                ch.watch_seconds = 0.0

        # Reload channels view so its in-memory list has 0.0 counters
        if hasattr(self, "views") and isinstance(self.views, dict):
            ch_view = self.views.get("channels")
            if ch_view and hasattr(ch_view, "load_channels"):
                ch_view.load_channels()

        self.refresh_watch_stats()
        self.update_dashboard_stats()

    def update_dashboard_stats(self) -> None:
        """Refresh dashboard overview cards with live accounts, runnable queue, and status."""
        if hasattr(self, "views") and isinstance(self.views, dict) and "dashboard" in self.views:
            status = "Idle"
            if self.is_running:
                status = "Watching" if getattr(self, "current_mode", "automation") == "watching" else "Running"
            self.views["dashboard"].update_stats(
                len(self.loaded_accounts),
                self.get_runnable_video_count(),
                status,
            )

    def refresh_watch_stats(self) -> None:
        """Re-render everything that shows watch statistics (main thread only, without rebuilding hidden tabs)."""
        if not hasattr(self, "views"):
            return
        cur_view = getattr(self, "current_view_name", "dashboard")
        for name in ("queue", "shorts_queue", "channels"):
            view = self.views.get(name)
            if view is not None and hasattr(view, "refresh_list"):
                if cur_view == name:
                    view.refresh_list()
                else:
                    if name == "queue":
                        self._queue_needs_refresh = True
                    elif name == "shorts_queue":
                        self._shorts_queue_needs_refresh = True
                    elif name == "channels":
                        self._channels_needs_refresh = True
        dashboard = self.views.get("dashboard")
        if dashboard is not None:
            totals = self.stats_store.lifetime_totals()
            dashboard.update_watch_totals(format_duration(totals.watch_seconds))
            self.update_dashboard_stats()

    def show_view(self, name: str):
        self.current_view_name = name
        for view_name, view_widget in self.views.items():
            if view_name == name:
                view_widget.tkraise()
                if view_name in self.nav_buttons:
                    self.nav_buttons[view_name].configure(style="NavActive.TButton")
                # If switching to a view that has pending updates, refresh it smoothly
                if name in ("queue", "shorts_queue"):
                    tasks_changed = getattr(view_widget, "video_tasks", None) != self.queued_video_tasks
                    if getattr(view_widget, "video_tasks", None) is not self.queued_video_tasks:
                        view_widget.video_tasks = self.queued_video_tasks

                    has_children = bool(getattr(view_widget, "scroll_frame", None) and view_widget.scroll_frame.winfo_children())
                    needs_refresh = getattr(self, f"_{name}_needs_refresh", False) or tasks_changed or not has_children

                    if needs_refresh:
                        view_widget.refresh_list()
                        setattr(self, f"_{name}_needs_refresh", False)
                elif name == "channels" and getattr(self, "_channels_needs_refresh", False):
                    view_widget.refresh_list()
                    self._channels_needs_refresh = False
            else:
                if view_name in self.nav_buttons:
                    self.nav_buttons[view_name].configure(style="Nav.TButton")
        if hasattr(self, "quota_label") and self.quota_label:
            self.quota_label.lift()

    def apply_appearance_mode(self, mode: str, persist: bool = True) -> None:
        """Apply and remember the appearance mode (Dark / Light / System)."""
        valid = ("Dark", "Light", "System")
        selected_mode = mode if mode in valid else "Dark"
        effective_mode = selected_mode if selected_mode != "System" else "Dark"
        self.appearance_mode = effective_mode
        try:
            from .ttk_adapter import set_appearance_mode
            set_appearance_mode(effective_mode)
        except Exception:
            pass

        setup_ttk_styles(self, mode=effective_mode)
        colors = get_theme_colors(effective_mode)
        try:
            self.configure(background=colors["bg"])
        except Exception:
            pass

        if hasattr(self, "quota_label") and self.quota_label:
            try:
                self.quota_label.configure(style="QuotaBadge.TLabel")
            except Exception:
                pass

        # Update all active ScrollableFrames and Switches
        try:
            from .components import update_all_scrollable_frames, update_all_switches
            update_all_scrollable_frames(effective_mode)
            update_all_switches()
        except Exception:
            pass

        # Update views if built
        if hasattr(self, "views") and isinstance(self.views, dict):
            logs_view = self.views.get("logs")
            if logs_view and hasattr(logs_view, "apply_theme"):
                try:
                    logs_view.apply_theme(effective_mode)
                except Exception:
                    pass

            cur_view = getattr(self, "current_view_name", None)
            if hasattr(self, "nav_buttons"):
                for name, btn in self.nav_buttons.items():
                    try:
                        if name == cur_view:
                            btn.configure(style="NavActive.TButton")
                        else:
                            btn.configure(style="Nav.TButton")
                    except Exception:
                        pass

            for v_name, view in self.views.items():
                if hasattr(view, "apply_theme"):
                    try:
                        view.apply_theme(effective_mode)
                    except Exception:
                        pass
                if v_name in ("accounts", "channels", "queue", "shorts_queue"):
                    if hasattr(view, "refresh_list"):
                        try:
                            view.refresh_list()
                        except Exception:
                            pass
                elif v_name == "dashboard":
                    if hasattr(view, "update_dashboard_stats"):
                        try:
                            view.update_dashboard_stats()
                        except Exception:
                            pass

        if persist and hasattr(self, "config_mgr") and self.config_mgr is not None:
            self.config_mgr.set_setting("AppearanceMode", selected_mode)

    def on_accounts_changed(self, accounts: List[Account]):
        seen = set()
        deduped = []
        duplicates = []
        for a in accounts:
            if a.email in seen:
                duplicates.append(a.email)
            else:
                seen.add(a.email)
                deduped.append(a)
        if duplicates:
            emit(self.log_queue, f"Duplicate account(s) omitted: {', '.join(duplicates)}", logging.WARNING)
        self.loaded_accounts = deduped
        self.update_dashboard_stats()

    def on_queue_changed(self, tasks: List[VideoTask]):
        self.queued_video_tasks = tasks
        for name in ("queue", "shorts_queue"):
            view = getattr(self, "views", {}).get(name)
            if view is not None and getattr(view, "video_tasks", None) is not tasks:
                view.video_tasks = tasks
                if getattr(self, "current_view_name", "") == name:
                    view.refresh_list()
                else:
                    if name == "queue":
                        self._queue_needs_refresh = True
                    elif name == "shorts_queue":
                        self._shorts_queue_needs_refresh = True
        self.update_dashboard_stats()

    def _confirm_full_video_timeout_if_needed(self) -> bool:
        """Prompt confirmation popup if Full Video Timeout is set to anything other than 0."""
        timeout_val = self.config_mgr.get_setting("FullVideoTimeoutMinutes", 0) if self.config_mgr else 0
        try:
            timeout_mins = int(timeout_val)
        except (ValueError, TypeError):
            try:
                timeout_mins = int(float(timeout_val))
            except Exception:
                timeout_mins = 0

        if timeout_mins != 0 and timeout_mins > 0:
            from .views.timeout_confirm_modal import FullVideoTimeoutConfirmModal
            try:
                modal = FullVideoTimeoutConfirmModal(self, timeout_minutes=timeout_mins)
                confirmed = modal.show()
                if not confirmed:
                    emit(
                        self.log_queue,
                        f"Automation start cancelled by user (Full Video Timeout is set to {timeout_mins}m).",
                        logging.INFO,
                    )
                    dashboard = getattr(self, "views", {}).get("dashboard")
                    if dashboard is not None and hasattr(dashboard, "set_running_state"):
                        dashboard.set_running_state(False)
                    return False
            except Exception as err:
                emit(self.log_queue, f"Timeout confirmation skipped ({err}).", logging.DEBUG)
                return True
        return True

    def start_automation(self):
        if self.is_running or self.__dict__.get("is_closing", False) or bool(self._get_owned_threads()):
            emit(self.log_queue, "Previous session is still cleaning up. Please wait.", logging.WARNING)
            return

        if not self.loaded_accounts:
            self.show_view("accounts")
            emit(self.log_queue, "Please load at least one Google account before starting!")
            return

        if not self.queued_video_tasks:
            self.show_view("queue")
            emit(self.log_queue, "Please add at least one video task to the queue before starting!")
            return

        channels = [Channel.from_dict(c) for c in self.config_mgr.get_channels()]
        cooldown_h = self.config_mgr.get_float("VideoCooldownHours", 12.0)
        active_video_tasks = [t for t in self.queued_video_tasks if is_video_task_runnable(t, channels, self.stats_store, cooldown_hours=cooldown_h)]
        if not active_video_tasks:
            self.show_view("queue")
            emit(
                self.log_queue,
                "No runnable video tasks to run! Check that videos or channels are enabled and have not reached their run limit.",
                logging.WARNING,
            )
            return

        # Read queue pass count from dashboard entry and persist
        total_loops = 1
        dashboard = self.views.get("dashboard")
        if dashboard is not None:
            try:
                if hasattr(dashboard, "get_queue_passes"):
                    total_loops = dashboard.get_queue_passes()
                elif hasattr(dashboard, "get_loop_count"):
                    total_loops = dashboard.get_loop_count()
            except ValueError as err:
                emit(self.log_queue, str(err), logging.ERROR)
                if hasattr(dashboard, "set_status"):
                    dashboard.set_status(str(err), "error")
                return

        self.config_mgr.set_setting("AutomationLoops", str(total_loops))

        if not self._confirm_full_video_timeout_if_needed():
            dashboard = getattr(self, "views", {}).get("dashboard")
            if dashboard is not None and hasattr(dashboard, "set_running_state"):
                dashboard.set_running_state(False)
            return

        self.is_running = True
        self.current_mode = "automation"
        self._update_close_button_state()
        self._session_counter = self.__dict__.get("_session_counter", 0) + 1
        session_id = self._session_counter
        self.current_session_id = session_id

        self.update_dashboard_stats()
        self.stop_event.clear()
        self.worker_progress.clear()
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
        self.views["dashboard"].set_running_state(True, mode="automation")
        self.views["dashboard"].reset_progress()

        # Run the full automation session across the requested number of loops in a background manager thread
        try:
            self.session_manager_thread = threading.Thread(
                target=self._run_automation_session,
                args=(active_video_tasks, total_loops, session_id),
                daemon=True,
            )
            self.session_manager_thread.start()
        except Exception as exc:
            self.is_running = False
            self.current_mode = "idle"
            self.session_manager_thread = None
            err_msg = f"Failed to start automation manager thread: {sanitize_error(exc, self.loaded_accounts)}"
            emit(self.log_queue, err_msg, logging.ERROR)
            dashboard = self.views.get("dashboard")
            if dashboard is not None:
                try:
                    dashboard.set_running_state(False)
                    dashboard.set_status(err_msg, "error")
                except Exception:
                    pass

    def _run_automation_session(self, active_video_tasks: List[VideoTask], total_loops: int, session_id: int = 0):
        """Execute the automation session across the requested number of loops."""
        session_event_queue = SessionEventQueue(self.event_queue, session_id)
        session_error: Optional[str] = None
        try:
            parallel_limit = self.config_mgr.get_int("ParallelBrowsers", 1)

            for current_loop in range(1, total_loops + 1):
                if self.stop_event.is_set():
                    break

                # Filter tasks to ensure any video that reached its run limit is excluded
                channels = [Channel.from_dict(c) for c in self.config_mgr.get_channels()]
                cooldown_h = self.config_mgr.get_float("VideoCooldownHours", 12.0)
                current_tasks = [t for t in active_video_tasks if is_video_task_runnable(t, channels, self.stats_store, cooldown_hours=cooldown_h)]
                if not current_tasks:
                    emit(self.log_queue, "All active video tasks have reached their run limit or are in cooldown. Ending automation session.")
                    break

                loop_header = (
                    f"=== Starting YouTube Automation Session (Loop {current_loop}/{total_loops}) ==="
                    if total_loops > 1
                    else "=== Starting YouTube Automation Session ==="
                )
                emit(self.log_queue, loop_header)

                self.worker_progress.clear()
                self.active_threads.clear()

                # Thread-safe dispatcher coordinates randomized, non-overlapping task assignment
                dispatcher = TaskDispatcher(tasks=current_tasks, randomize=True, stats_store=self.stats_store, cooldown_hours=cooldown_h)

                # Ensure unique accounts across workers to prevent duplicate sessions
                unique_accounts = []
                seen_worker_emails = set()
                for acc in self.loaded_accounts:
                    if acc.email not in seen_worker_emails:
                        seen_worker_emails.add(acc.email)
                        unique_accounts.append(acc)
                    else:
                        emit(self.log_queue, f"Skipping duplicate account session for '{acc.email}'.", logging.WARNING)

                active_workers_count = min(len(unique_accounts), parallel_limit)
                emit(
                    self.log_queue,
                    f"Dispatching {len(current_tasks)} enabled video(s) across {active_workers_count} parallel browser(s) with random non-overlapping assignment.",
                )

                # Distribute accounts across worker threads
                for worker_id, acc in enumerate(unique_accounts[:parallel_limit]):
                    worker = AutomationWorker(
                        account=acc,
                        video_tasks=active_video_tasks,
                        config_mgr=self.config_mgr,
                        log_queue=self.log_queue,
                        stop_event=self.stop_event,
                        event_queue=session_event_queue,
                        worker_id=worker_id,
                        stats_store=self.stats_store,
                        dispatcher=dispatcher,
                    )
                    self.active_workers.append(worker)
                    thread = threading.Thread(target=worker.run, daemon=True)
                    self.active_threads.append(thread)
                    thread.start()

                # Wait for all workers in this loop iteration to finish
                for t in list(self.active_threads):
                    t.join()

                if self.stop_event.is_set():
                    emit(self.log_queue, "Automation stopped by user.")
                    break

                if current_loop < total_loops:
                    emit(
                        self.log_queue,
                        f"Completed automation loop {current_loop}/{total_loops}. Restarting queue for loop {current_loop + 1}/{total_loops}...",
                    )
        except Exception as exc:
            if not self.stop_event.is_set():
                session_error = f"Automation session error: {sanitize_error(exc, self.loaded_accounts)}"
                emit(self.log_queue, session_error, logging.ERROR)
            self.stop_event.set()
            for worker in list(self.active_workers):
                if hasattr(worker, "stop"):
                    try:
                        worker.stop()
                    except Exception:
                        pass
            for t in list(self.active_threads):
                if t.is_alive():
                    try:
                        t.join(timeout=10.0)
                    except Exception:
                        pass
        finally:
            try:
                self.active_threads.clear()
                self.active_workers.clear()
                self.session_manager_thread = None
                self.is_running = False
                self.current_mode = "idle"
            except Exception:
                self.is_running = False
                self.current_mode = "idle"

            try:
                session_event_queue.put_nowait(("finished", session_error))
            except Exception:
                pass
            if not session_error and not self.stop_event.is_set():
                emit(self.log_queue, "=== Automation Session Completed ===")

    def start_watching(self):
        """Start the continuous watching recommendation loop."""
        if self.is_running or self.__dict__.get("is_closing", False) or bool(self._get_owned_threads()):
            emit(self.log_queue, "Previous session is still cleaning up. Please wait.", logging.WARNING)
            return

        if not self.loaded_accounts:
            self.show_view("accounts")
            emit(self.log_queue, "Please load at least one Google account before starting!")
            return

        stored_channel_dicts = self.config_mgr.get_channels()
        stored_channels = [Channel.from_dict(c) for c in stored_channel_dicts]
        active_channels = [c for c in stored_channels if c.name and c.enabled and not c.archived]

        if not active_channels:
            derived_channel_names = set()
            for t in self.queued_video_tasks:
                if t.channel and t.enabled:
                    derived_channel_names.add(t.channel.strip())
            if derived_channel_names:
                active_channels = [Channel(name=name, enabled=True) for name in derived_channel_names]

        if not active_channels and not self.queued_video_tasks:
            self.show_view("channels")
            emit(
                self.log_queue,
                "No enabled channels or queued videos found! Please add a channel in the Channels tab or video tasks in the Queue before starting.",
                logging.WARNING,
            )
            return

        dashboard = self.views.get("dashboard")
        browsing_limit = 1
        is_unlimited = False
        if dashboard is not None and hasattr(dashboard, "get_browsing_limit"):
            try:
                browsing_limit, is_unlimited = dashboard.get_browsing_limit()
            except ValueError as err:
                emit(self.log_queue, str(err), logging.ERROR)
                if hasattr(dashboard, "set_status"):
                    dashboard.set_status(str(err), "error")
                return
        else:
            is_unlimited = self.config_mgr.get_bool("BrowsingUnlimited", True)
            browsing_limit = None if is_unlimited else self.config_mgr.get_int("BrowsingTargetLimit", 1)

        try:
            self.config_mgr.update_settings({
                "BrowsingUnlimited": is_unlimited,
                "BrowsingTargetLimit": browsing_limit if browsing_limit is not None else 1,
            })
        except Exception as err:
            err_msg = f"Failed to save browsing settings: {sanitize_error(err, self.loaded_accounts)}"
            emit(self.log_queue, err_msg, logging.ERROR)
            if dashboard is not None and hasattr(dashboard, "set_status"):
                dashboard.set_status(err_msg, "error")
            return

        if not self._confirm_full_video_timeout_if_needed():
            dashboard = getattr(self, "views", {}).get("dashboard")
            if dashboard is not None and hasattr(dashboard, "set_running_state"):
                dashboard.set_running_state(False)
            return

        self.is_running = True
        self.current_mode = "watching"
        self._update_close_button_state()
        self._session_counter = self.__dict__.get("_session_counter", 0) + 1
        session_id = self._session_counter
        self.current_session_id = session_id

        self.update_dashboard_stats()
        self.stop_event.clear()
        self.pause_event.clear()
        self.worker_progress.clear()
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

        if dashboard is not None:
            if hasattr(dashboard, "set_running_state"):
                dashboard.set_running_state(True, mode="watching")
            if hasattr(dashboard, "reset_progress"):
                dashboard.reset_progress()
            if hasattr(dashboard, "set_status"):
                dashboard.set_status("Starting watch session...", "info")

        try:
            self.session_manager_thread = threading.Thread(
                target=self._run_watching_session,
                args=(self.loaded_accounts[0], active_channels, browsing_limit, is_unlimited, session_id),
                daemon=True,
            )
            self.session_manager_thread.start()
        except Exception as exc:
            self.is_running = False
            self.current_mode = "idle"
            self.session_manager_thread = None
            err_msg = f"Failed to start watching manager thread: {sanitize_error(exc, [self.loaded_accounts[0]])}"
            emit(self.log_queue, err_msg, logging.ERROR)
            dashboard = self.views.get("dashboard")
            if dashboard is not None:
                try:
                    dashboard.set_running_state(False)
                    dashboard.set_status(err_msg, "error")
                except Exception:
                    pass

    def _run_watching_session(
        self,
        account: Account,
        channels: List[Channel],
        browsing_limit: Optional[int] = 1,
        is_unlimited: bool = False,
        session_id: int = 0,
        total_loops: Optional[int] = None,
        **kwargs: Any,
    ):
        """Execute the continuous watching recommendation session."""
        if total_loops is not None and (browsing_limit is None or browsing_limit == 1):
            browsing_limit = total_loops
        session_event_queue = SessionEventQueue(self.event_queue, session_id)
        session_error: Optional[str] = None
        worker = None
        try:
            worker = WatchingWorker(
                account=account,
                channels=channels,
                config_mgr=self.config_mgr,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
                event_queue=session_event_queue,
                stats_store=self.stats_store,
                pause_event=self.pause_event,
                target_limit=browsing_limit,
                is_unlimited=is_unlimited,
                open_videos=True,
            )
            self.active_workers.append(worker)
            worker.run()
        except Exception as exc:
            if not self.stop_event.is_set():
                session_error = f"Watching session error: {sanitize_error(exc, [account])}"
                emit(self.log_queue, session_error, logging.ERROR)
            self.stop_event.set()
            for w in list(self.active_workers):
                if hasattr(w, "stop"):
                    try:
                        w.stop()
                    except Exception:
                        pass
        finally:
            try:
                if worker in self.active_workers:
                    self.active_workers.remove(worker)
                self.active_threads.clear()
                self.active_workers.clear()
                self.session_manager_thread = None
                self.is_running = False
                self.current_mode = "idle"
            except Exception:
                self.is_running = False
                self.current_mode = "idle"

            try:
                session_event_queue.put_nowait(("finished", session_error))
            except Exception:
                pass
            if not session_error and not self.stop_event.is_set():
                emit(self.log_queue, "=== Watching Session Completed ===")

    def toggle_pause_watching(self):
        """Toggle pause/resume state during browsing session."""
        if not self.is_running or self.current_mode != "watching":
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            if "dashboard" in self.views and hasattr(self.views["dashboard"], "set_paused_state"):
                self.views["dashboard"].set_paused_state(False)
        else:
            self.pause_event.set()
            if "dashboard" in self.views and hasattr(self.views["dashboard"], "set_paused_state"):
                self.views["dashboard"].set_paused_state(True)

    def show_report_modal(
        self,
        message: str = "Generating report, do not close the app.",
        auto_hide_seconds: Optional[float] = 2.0,
    ) -> None:
        """Display the modal pop-up indicating report generation in progress."""
        if self.__dict__.get("report_modal", None) is not None:
            try:
                if self.report_modal.winfo_exists():
                    self.report_modal.deiconify()
                    self.report_modal.lift()
                    if not self.__dict__.get("is_closing", False) and auto_hide_seconds:
                        if hasattr(self.report_modal, "schedule_auto_hide"):
                            self.report_modal.schedule_auto_hide(auto_hide_seconds)
                    return
            except Exception:
                pass
        try:
            from .views.report_modal import ReportProgressModal

            self.report_modal = ReportProgressModal(
                parent=self,
                title="Generating Report",
                message=message,
                on_close_callback=self._on_report_modal_closed,
                auto_hide_seconds=auto_hide_seconds if not self.__dict__.get("is_closing", False) else None,
            )
        except Exception as exc:
            logging.debug(f"Could not open ReportProgressModal (headless or mock): {exc}")
            self.report_modal = None

    def update_report_modal_completed(self, success: bool, message: str = "") -> None:
        """Update the report modal to indicate report completion and enable exit."""
        is_closing = self.__dict__.get("is_closing", False)
        title = "Report Sent Successfully!" if success else ("Session Ended" if is_closing else "Session Wrap-up Finished")
        user_msg = (
            ("The session report has been sent to your email.\nClosing application..." if is_closing else "The session report has been sent to your email.\nYou may now safely close the app.")
            if success
            else (message or ("Session wrap-up finished.\nClosing application..." if is_closing else "Session wrap-up finished. You may now close the app."))
        )

        if self.__dict__.get("report_modal", None) is not None:
            try:
                if self.report_modal.winfo_exists():
                    self.report_modal.set_completed(success=success, title=title, message=user_msg, auto_close=is_closing)
                    return
            except Exception as exc:
                logging.debug(f"Could not update report modal: {exc}")
                self.report_modal = None

        if not is_closing:
            try:
                from .views.report_modal import ReportProgressModal

                modal = ReportProgressModal(
                    parent=self,
                    title="Report Sent Successfully!" if success else "Session Completed",
                    message=user_msg,
                    on_close_callback=self._on_report_modal_closed,
                )
                modal.set_completed(success=success, title=title, message=user_msg, auto_close=False)
                self.report_modal = modal
            except Exception as exc:
                logging.debug(f"Could not open completed ReportProgressModal: {exc}")

        if not is_closing:
            self._update_close_button_state()

    def _on_report_modal_closed(self) -> None:
        """Callback when report modal is dismissed."""
        self.report_modal = None
        if self.__dict__.get("is_closing", False):
            self.on_closing()
        else:
            self._update_close_button_state()

    def stop_automation(self):
        if not self.is_running:
            self._update_close_button_state()
            return
        mode_label = "browsing" if getattr(self, "current_mode", "automation") == "watching" else "automation"
        emit(self.log_queue, f"Stopping {mode_label} session... Please wait for workers to cleanup.")
        self.show_report_modal("Generating report, do not close the app.", auto_hide_seconds=2.0)
        dashboard = self.views.get("dashboard")
        if dashboard is not None:
            dashboard.set_status("Stopping session... Cleaning up.", "warning")
            if hasattr(dashboard, "stop_btn"):
                try:
                    dashboard.stop_btn.configure(state="disabled", text="Stopping...")
                except Exception:
                    pass
        self.pause_event.clear()
        self.stop_event.set()
        for worker in list(self.__dict__.get("active_workers", [])):
            if hasattr(worker, "stop"):
                try:
                    worker.stop()
                except Exception:
                    pass

    def _handle_worker_event(self, event) -> None:
        """Apply a structured worker event to the dashboard (main thread only)."""
        if not event:
            return

        if isinstance(event, tuple) and len(event) >= 3 and event[0] == "session_event":
            _, event_session_id, inner_event = event
            current_sid = self.__dict__.get("current_session_id", 0)
            if event_session_id != current_sid:
                # Stale event from a prior session! Discard to prevent resetting or modifying newer run.
                return
            event = inner_event

        if not event:
            return

        kind = event[0]

        # During shutdown, process report and lifecycle events, skipping dashboard redraws
        if self.__dict__.get("is_closing", False):
            if kind == "report_generating":
                msg = event[1] if len(event) > 1 else "Generating session report... Please do not close the app."
                self.show_report_modal(msg)
            elif kind == "report_sent":
                ok = event[1] if len(event) > 1 else True
                msg = event[2] if len(event) > 2 else ""
                self.update_report_modal_completed(ok, msg)
            elif kind == "finished":
                error_msg = event[1] if len(event) > 1 else None
                if self.__dict__.get("report_modal", None) is not None and getattr(self.report_modal, "is_generating", False):
                    self.update_report_modal_completed(error_msg is None, error_msg or "Session finished.")
            return

        if kind == "progress" and len(event) == 4:
            _, worker_id, done, total = event
            self.worker_progress[worker_id] = (done, total)
            done_sum = sum(d for d, _ in self.worker_progress.values())
            total_sum = sum(t for _, t in self.worker_progress.values())
            mode = getattr(self, "current_mode", "automation")
            self.views["dashboard"].set_progress(done_sum, total_sum, mode=mode)
            # Watch counters are written when a task finishes: repaint them.
            self.refresh_watch_stats()
        elif kind == "add_video_task" and len(event) == 2:
            task_dict = event[1]
            queue_view = self.views.get("queue")
            if queue_view is not None:
                vid_key = resolve_video_key(VideoTask.from_dict(task_dict), task_dict.get("url", ""))
                match_found = False
                for t in queue_view.video_tasks:
                    if resolve_video_key(t, t.url) == vid_key:
                        match_found = True
                        if not t.channel and task_dict.get("channel"):
                            t.channel = task_dict.get("channel")
                        if t.max_runs == 0 and task_dict.get("max_runs", 0) > 0:
                            t.max_runs = int(task_dict.get("max_runs", 0))
                        break
                if not match_found:
                    new_t = VideoTask.from_dict(task_dict)
                    queue_view.video_tasks.append(new_t)
                queue_view.save_queue()
                if getattr(self, "current_view_name", "") == "queue":
                    queue_view.refresh_list()
                else:
                    self._queue_needs_refresh = True
                shorts_view = self.views.get("shorts_queue")
                if shorts_view is not None:
                    shorts_view.video_tasks = queue_view.video_tasks
                    if getattr(self, "current_view_name", "") == "shorts_queue":
                        shorts_view.refresh_list()
                    else:
                        self._shorts_queue_needs_refresh = True
                self.queued_video_tasks = queue_view.video_tasks
            self.refresh_watch_stats()
        elif kind == "update_video_duration" and len(event) == 3:
            task_dict, dur_sec = event[1], event[2]
            queue_view = self.views.get("queue")
            if queue_view is not None:
                vid_key = resolve_video_key(VideoTask.from_dict(task_dict), task_dict.get("url", ""))
                for t in queue_view.video_tasks:
                    if resolve_video_key(t, t.url) == vid_key:
                        t.duration_seconds = int(dur_sec)
                        m, s = divmod(int(dur_sec), 60)
                        h, m = divmod(m, 60)
                        t.duration_text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                        break
                queue_view.save_queue()
                if getattr(self, "current_view_name", "") == "queue":
                    queue_view.refresh_list()
                else:
                    self._queue_needs_refresh = True
                shorts_view = self.views.get("shorts_queue")
                if shorts_view is not None:
                    shorts_view.video_tasks = queue_view.video_tasks
                    if getattr(self, "current_view_name", "") == "shorts_queue":
                        shorts_view.refresh_list()
                    else:
                        self._shorts_queue_needs_refresh = True
            self.refresh_watch_stats()
        elif kind == "channel_watch_time_updated":
            self.refresh_watch_stats()
        elif kind == "status" and len(event) == 3:
            _, message, level = event
            self.views["dashboard"].set_status(message, level)
        elif kind == "report_generating":
            msg = event[1] if len(event) > 1 else "Generating session report... Please do not close the app."
            self.show_report_modal(msg)
        elif kind == "report_sent":
            ok = event[1] if len(event) > 1 else True
            msg = event[2] if len(event) > 2 else ""
            self.update_report_modal_completed(ok, msg)
            self._update_close_button_state()
        elif kind == "finished":
            error_msg = event[1] if len(event) > 1 else None
            if self.__dict__.get("report_modal", None) is not None and getattr(self.report_modal, "is_generating", False):
                self.update_report_modal_completed(error_msg is None, error_msg or "Session finished.")
            dashboard = self.views.get("dashboard")
            if dashboard is not None and not self.__dict__.get("is_closing", False):
                try:
                    dashboard.set_running_state(False)
                except Exception:
                    pass
                if error_msg:
                    try:
                        dashboard.set_status(error_msg, "error")
                    except Exception:
                        pass
            try:
                self.refresh_watch_stats()
            except Exception:
                pass
            self._update_close_button_state()

    def _poll_log_queue(self):
        if self.__dict__.get("is_closing", False):
            self._poll_job = None
            return

        # Network jobs have their own queue so starting a session cannot erase
        # a pending dialog result when it clears worker progress events.
        jobs = self.__dict__.get("background_jobs")
        if jobs is not None:
            jobs.drain()
        # 1. Batch up to 25 log messages per tick to prevent UI lockup
        log_batch = []
        try:
            for _ in range(25):
                log_batch.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass

        if log_batch:
            logs_view = self.views.get("logs")
            if logs_view is not None:
                try:
                    logs_view.append_log("\n".join(log_batch))
                except Exception:
                    logging.exception("Failed to update the log view")

        # 2. Process up to 10 worker events per tick
        for _ in range(10):
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_worker_event(event)
            except Exception:
                logging.exception("Failed to apply worker event")

        # 3. Synchronize native window close (X) button state with active task status
        try:
            self._update_close_button_state()
        except Exception:
            pass

        self._poll_job = self.after(50, self._poll_log_queue)

    def _get_owned_threads(self) -> List[threading.Thread]:
        """Collect all live session threads managed by this application."""
        threads: List[threading.Thread] = []
        mgr = self.__dict__.get("session_manager_thread", None)
        if mgr is not None and mgr.is_alive():
            threads.append(mgr)
        for t in self.__dict__.get("active_threads", []):
            if t is not None and t.is_alive() and t not in threads:
                threads.append(t)
        return threads

    def _cancel_scheduled_callbacks(self) -> None:
        """Cancel all pending Tk after callbacks before widget destruction."""
        if self.__dict__.get("_poll_job", None) is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self.__dict__.get("_close_check_job", None) is not None:
            try:
                self.after_cancel(self._close_check_job)
            except Exception:
                pass
            self._close_check_job = None
        if self.__dict__.get("_quota_check_job", None) is not None:
            try:
                self.after_cancel(self._quota_check_job)
            except Exception:
                pass
            self._quota_check_job = None
        quota_tracker = self.__dict__.get("quota_tracker", None)
        if quota_tracker is not None:
            try:
                quota_tracker.unregister_listener(self._on_quota_updated)
            except Exception:
                pass

    def is_any_task_running(self) -> bool:
        """Check whether any worker, browsing session, background job, or email report is active."""
        if getattr(self, "is_running", False):
            return True
        if bool(self._get_owned_threads()):
            return True
        # Check if email report modal is active/generating
        report_modal = self.__dict__.get("report_modal", None)
        if report_modal is not None:
            try:
                if report_modal.winfo_exists() and getattr(report_modal, "is_generating", False):
                    return True
            except Exception:
                pass
        # Check background jobs
        bg = self.__dict__.get("background_jobs", None)
        if bg is not None:
            jobs = getattr(bg, "_jobs", {})
            if any(getattr(j, "is_alive", lambda: False)() for j in jobs.values()):
                return True
        # If closing transition is active (e.g. stopping session before exit)
        if self.__dict__.get("is_closing", False):
            return True
        return False

    def _update_close_button_state(self) -> None:
        """Dynamically enable or gray out the title bar close (X) button based on active task status."""
        is_busy = self.is_any_task_running()
        should_enable = not is_busy
        if getattr(self, "_close_button_enabled", None) is not should_enable:
            self._close_button_enabled = should_enable
            self._set_native_close_button_enabled(should_enable)

    def maximize_window(self) -> None:
        """Maximize the application window (like clicking the title bar maximize button)."""
        try:
            self.state("zoomed")
        except Exception:
            try:
                self.attributes("-zoomed", True)
            except Exception:
                pass

    def _set_native_close_button_enabled(self, enabled: bool) -> None:
        """Gray out and disable, or re-enable the native Windows title bar close (X) button."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # Retrieve the outer top-level window frame HWND containing the title bar
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            if not hwnd:
                return

            SC_CLOSE = 0xF060
            MF_BYCOMMAND = 0x00000000
            MF_ENABLED = 0x00000000
            MF_GRAYED = 0x00000001
            MF_DISABLED = 0x00000002

            if enabled:
                try:
                    user32.GetSystemMenu(hwnd, True)
                except Exception:
                    pass

            hmenu = user32.GetSystemMenu(hwnd, False)
            if hmenu:
                flags = MF_BYCOMMAND | (MF_ENABLED if enabled else (MF_DISABLED | MF_GRAYED))
                user32.EnableMenuItem(hmenu, SC_CLOSE, flags)
                user32.DrawMenuBar(hwnd)

                SWP_FRAMECHANGED = 0x0020
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
                )
        except Exception as exc:
            logging.debug(f"Could not update native close button: {exc}")

    def _safe_exit(self, force: bool = False) -> None:
        """Destroy UI safely and exit the application process at all cost."""
        self.is_closing = True
        self._cancel_scheduled_callbacks()

        if self.__dict__.get("report_modal", None) is not None:
            try:
                self.report_modal.destroy()
            except Exception:
                pass
            self.report_modal = None

        # Cleanly stop any worker drivers if still active
        for worker in list(self.__dict__.get("active_workers", [])):
            if hasattr(worker, "driver") and worker.driver:
                try:
                    close_driver(worker.driver)
                    worker.driver = None
                except Exception:
                    pass
            if hasattr(worker, "reporting_driver") and worker.reporting_driver:
                try:
                    close_driver(worker.reporting_driver)
                    worker.reporting_driver = None
                except Exception:
                    pass

        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

        # Ensure lingering daemon/browser processes do not keep the Python process running
        delay = 0.1 if force else 0.8

        def _force_exit():
            time.sleep(delay)
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True, name="force-exit").start()

    def on_closing(self) -> None:
        """Orderly application shutdown handler registered with WM_DELETE_WINDOW."""
        if self.__dict__.get("is_closing", False):
            # User clicked the X button again while closing is in progress: force close immediately
            emit(self.log_queue, "Force exit requested on second close. Closing immediately.")
            self._safe_exit(force=True)
            return

        self.is_closing = True
        self._report_finish_time = None

        active_threads = self._get_owned_threads()
        has_active_session = self.is_running or bool(active_threads)

        # If report modal is already open and generating, transition seamlessly to closing
        if self.__dict__.get("report_modal", None) is not None:
            try:
                if self.report_modal.winfo_exists():
                    self.report_modal.title("Closing Application")
                    if hasattr(self.report_modal, "title_label"):
                        self.report_modal.title_label.configure(text="⏳ Closing Application")
                    if hasattr(self.report_modal, "msg_label"):
                        self.report_modal.msg_label.configure(
                            text="Session report is being delivered before exit...\nPlease wait, the app will close automatically."
                        )
                    self.report_modal.deiconify()
                    self.report_modal.lift()
            except Exception:
                pass
            self._close_start_time = time.monotonic()
            self._check_shutdown_progress()
            return

        if has_active_session:
            emit(self.log_queue, "Closing application requested. Stopping active session and sending session report before exit...")
            self.stop_automation()

            # Ensure the modal states that the app will automatically exit
            if self.__dict__.get("report_modal", None) is not None:
                try:
                    if self.report_modal.winfo_exists():
                        self.report_modal.title("Closing Application")
                        if hasattr(self.report_modal, "title_label"):
                            self.report_modal.title_label.configure(text="⏳ Closing Application")
                        if hasattr(self.report_modal, "msg_label"):
                            self.report_modal.msg_label.configure(
                                text="Stopping session and sending report before closing...\nPlease wait, the app will exit automatically."
                            )
                        self.report_modal.deiconify()
                        self.report_modal.lift()
                except Exception:
                    pass

            dashboard = self.views.get("dashboard")
            if dashboard is not None:
                try:
                    dashboard.set_status("Closing application... Waiting for report to be sent.", "warning")
                except Exception:
                    pass

            self._close_start_time = time.monotonic()
            self._check_shutdown_progress()
            return

        # Idle: clean exit immediately
        self._safe_exit()

    def _check_shutdown_progress(self) -> None:
        if not self.__dict__.get("is_closing", False):
            return

        # 1. Drain all worker events during shutdown so report_sent & finished are processed
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_worker_event(event)
            except Exception:
                pass

        # 2. Check if report is actively generating/sending
        is_report_in_progress = False
        if self.__dict__.get("report_modal", None) is not None:
            try:
                if self.report_modal.winfo_exists() and getattr(self.report_modal, "is_generating", False):
                    is_report_in_progress = True
            except Exception:
                pass

        if not is_report_in_progress:
            for w in list(self.__dict__.get("active_workers", [])):
                rep_t = getattr(w, "reporting_thread", None)
                if rep_t and rep_t.is_alive():
                    is_report_in_progress = True
                    break

        living_threads = self._get_owned_threads()

        # 3. If report is finished (or was never started), close the app!
        if not is_report_in_progress:
            if self.__dict__.get("_report_finish_time", None) is None:
                self._report_finish_time = time.monotonic()

            report_finish = self.__dict__.get("_report_finish_time") or time.monotonic()
            report_elapsed = time.monotonic() - report_finish
            if not living_threads or report_elapsed >= 3.0:
                emit(self.log_queue, "Session report finished. Application exiting now.")
                self._safe_exit()
                return

        # 4. If report delivery is still in progress, enforce maximum timeout (30s)
        close_start = self.__dict__.get("_close_start_time", 0.0)
        close_timeout = self.__dict__.get("_close_timeout", 30.0)

        if close_start > 0:
            elapsed = time.monotonic() - close_start
            if elapsed >= close_timeout:
                emit(
                    self.log_queue,
                    "Shutdown timeout reached while waiting for report/workers. Forcing application exit.",
                    logging.WARNING,
                )
                self._safe_exit(force=True)
                return

        self._close_check_job = self.after(100, self._check_shutdown_progress)
