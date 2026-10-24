import logging
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from ..automation.dispatcher import TaskDispatcher
from ..automation.watching_worker import WatchingWorker
from ..automation.worker import AutomationWorker
from ..config.manager import ConfigManager
from ..config.stats_store import StatsStore
from ..models.account import Account
from ..models.channel import Channel, is_video_task_runnable
from ..models.video_task import VideoTask
from ..models.watch_stats import format_duration
from ..utils.logger import emit, init_logging
from .views.accounts_view import AccountsView
from .views.channels_view import ChannelsView
from .views.dashboard_view import DashboardView
from .views.logs_view import LogsView
from .views.queue_view import QueueView
from .views.settings_view import SettingsView


class YTBotApp(ctk.CTk):
    def __init__(self, config_mgr: Optional[ConfigManager] = None, stats_store: Optional[StatsStore] = None):
        super().__init__()
        self.config_mgr = config_mgr or ConfigManager()
        self.stats_store = stats_store or StatsStore()

        self.title("YouTube Playback Automation Tool - V2.1 (Modular)")
        self.geometry("1100x700")
        self.minsize(950, 600)

        self.apply_appearance_mode(self.config_mgr.get_setting("AppearanceMode", "Dark"))
        ctk.set_default_color_theme("blue")

        self.log_file = init_logging()
        self.log_queue: queue.Queue = queue.Queue()
        # Structured worker events: ("progress", worker_id, done, total) / ("status", text, level)
        self.event_queue: queue.Queue = queue.Queue()
        self.worker_progress: dict = {}

        self.loaded_accounts: List[Account] = []
        self.queued_video_tasks: List[VideoTask] = []
        self.active_threads: List[threading.Thread] = []
        self.stop_event = threading.Event()
        self.is_running = False
        self.current_mode = "idle"

        self._build_ui()
        self.after(100, self._poll_log_queue)

        # Pre-load accounts fallback if saved list is empty and accounts.txt exists
        if not self.loaded_accounts:
            accounts_file = self.config_mgr.get_setting("AccountsFile", "accounts.txt")
            if Path(accounts_file).exists():
                self.views["accounts"].import_accounts_file(accounts_file)

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Navigation Sidebar
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(7, weight=1)

        ctk.CTkLabel(
            self.sidebar,
            text="YT Organic Bot",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 20))

        self.nav_buttons: Dict[str, Any] = {}
        nav_items = [
            ("dashboard", "📊 Dashboard"),
            ("accounts", "👤 Accounts"),
            ("queue", "🎥 Video Queue"),
            ("channels", "📺 Channels"),
            ("settings", "⚙ Settings"),
            ("logs", "📋 Logs"),
        ]

        for idx, (name, label_text) in enumerate(nav_items, start=1):
            btn = ctk.CTkButton(
                self.sidebar,
                text=label_text,
                height=40,
                corner_radius=6,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray70", "gray30"),
                anchor="w",
                command=lambda n=name: self.show_view(n),
            )
            btn.grid(row=idx, column=0, padx=10, pady=5, sticky="ew")
            self.nav_buttons[name] = btn

        # Main Views Container
        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)

        self.views: Dict[str, Any] = {
            "dashboard": DashboardView(self.container, self),
            "accounts": AccountsView(self.container, self),
            "queue": QueueView(self.container, self),
            "channels": ChannelsView(self.container, self),
            "settings": SettingsView(self.container, self),
            "logs": LogsView(self.container, self),
        }

        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

        self.show_view("dashboard")
        self.update_dashboard_stats()
        self.refresh_watch_stats()

    def get_runnable_video_count(self) -> int:
        """Count only the enabled, runnable video tasks that will be processed by automation."""
        channels = (
            [Channel.from_dict(c) for c in self.config_mgr.get_channels()]
            if hasattr(self, "config_mgr")
            else []
        )
        stats = getattr(self, "stats_store", None)
        return sum(1 for t in self.queued_video_tasks if is_video_task_runnable(t, channels, stats))

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
        """Re-render everything that shows watch statistics (main thread only)."""
        if not hasattr(self, "views"):
            return
        for name in ("queue", "channels"):
            view = self.views.get(name)
            if view is not None and hasattr(view, "refresh_list"):
                view.refresh_list()
        dashboard = self.views.get("dashboard")
        if dashboard is not None:
            totals = self.stats_store.lifetime_totals()
            dashboard.update_watch_totals(format_duration(totals.watch_seconds))
            self.update_dashboard_stats()

    def show_view(self, name: str):
        for view_name, view_widget in self.views.items():
            if view_name == name:
                view_widget.tkraise()
                self.nav_buttons[view_name].configure(fg_color=("gray75", "gray25"))
            else:
                self.nav_buttons[view_name].configure(fg_color="transparent")

    def apply_appearance_mode(self, mode: str) -> None:
        """Apply and remember the appearance mode (Dark / Light / System)."""
        valid = ("Dark", "Light", "System")
        ctk.set_appearance_mode(mode if mode in valid else "Dark")
        if hasattr(self, "config_mgr") and self.config_mgr is not None:
            self.config_mgr.set_setting("AppearanceMode", mode if mode in valid else "Dark")

    def on_accounts_changed(self, accounts: List[Account]):
        self.loaded_accounts = accounts
        self.update_dashboard_stats()

    def on_queue_changed(self, tasks: List[VideoTask]):
        self.queued_video_tasks = tasks
        self.update_dashboard_stats()

    def start_automation(self):
        if self.is_running:
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
        active_video_tasks = [t for t in self.queued_video_tasks if is_video_task_runnable(t, channels, self.stats_store)]
        if not active_video_tasks:
            self.show_view("queue")
            emit(
                self.log_queue,
                "No runnable video tasks to run! Check that videos or channels are enabled and have not reached their run limit.",
                logging.WARNING,
            )
            return

        # Read loop count from dashboard entry and persist
        total_loops = 1
        if "dashboard" in self.views and hasattr(self.views["dashboard"], "get_loop_count"):
            total_loops = self.views["dashboard"].get_loop_count()
        self.config_mgr.set_setting("AutomationLoops", str(total_loops))

        self.is_running = True
        self.current_mode = "automation"
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
        threading.Thread(
            target=self._run_automation_session,
            args=(active_video_tasks, total_loops),
            daemon=True,
        ).start()

    def _run_automation_session(self, active_video_tasks: List[VideoTask], total_loops: int):
        """Execute the automation session across the requested number of loops."""
        parallel_limit = self.config_mgr.get_int("ParallelBrowsers", 1)

        for current_loop in range(1, total_loops + 1):
            if self.stop_event.is_set():
                break

            # Filter tasks to ensure any video that reached its run limit is excluded
            channels = [Channel.from_dict(c) for c in self.config_mgr.get_channels()]
            current_tasks = [t for t in active_video_tasks if is_video_task_runnable(t, channels, self.stats_store)]
            if not current_tasks:
                emit(self.log_queue, "All active video tasks have reached their run limit. Ending automation session.")
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
            dispatcher = TaskDispatcher(tasks=current_tasks, randomize=True, stats_store=self.stats_store)
            active_workers_count = min(len(self.loaded_accounts), parallel_limit)
            emit(
                self.log_queue,
                f"Dispatching {len(current_tasks)} enabled video(s) across {active_workers_count} parallel browser(s) with random non-overlapping assignment.",
            )

            # Distribute accounts across worker threads
            for worker_id, acc in enumerate(self.loaded_accounts[:parallel_limit]):
                worker = AutomationWorker(
                    account=acc,
                    video_tasks=active_video_tasks,
                    config_mgr=self.config_mgr,
                    log_queue=self.log_queue,
                    stop_event=self.stop_event,
                    event_queue=self.event_queue,
                    worker_id=worker_id,
                    stats_store=self.stats_store,
                    dispatcher=dispatcher,
                )
                thread = threading.Thread(target=worker.run, daemon=True)
                self.active_threads.append(thread)
                thread.start()

            # Wait for all workers in this loop iteration to finish
            for t in self.active_threads:
                t.join()

            if self.stop_event.is_set():
                emit(self.log_queue, "Automation stopped by user.")
                break

            if current_loop < total_loops:
                emit(
                    self.log_queue,
                    f"Completed automation loop {current_loop}/{total_loops}. Restarting queue for loop {current_loop + 1}/{total_loops}...",
                )

        self.is_running = False
        self.current_mode = "idle"
        try:
            self.event_queue.put_nowait(("finished",))
        except queue.Full:
            pass
        emit(self.log_queue, "=== Automation Session Completed ===")

    def start_watching(self):
        """Start the continuous watching recommendation loop."""
        if self.is_running:
            return

        if not self.loaded_accounts:
            self.show_view("accounts")
            emit(self.log_queue, "Please load at least one Google account before starting!")
            return

        stored_channel_dicts = self.config_mgr.get_channels()
        stored_channels = [Channel.from_dict(c) for c in stored_channel_dicts]
        active_channels = [c for c in stored_channels if c.name and c.enabled and not c.archived]

        if not active_channels:
            self.show_view("channels")
            emit(
                self.log_queue,
                "No enabled channels found! Please check and enable at least one channel in the Channels tab before starting.",
                logging.WARNING,
            )
            return

        self.is_running = True
        self.current_mode = "watching"
        self.update_dashboard_stats()
        self.stop_event.clear()
        self.worker_progress.clear()
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

        self.views["dashboard"].set_running_state(True, mode="watching")
        self.views["dashboard"].reset_progress()
        self.views["dashboard"].set_status("Starting watch session...", "info")

        threading.Thread(
            target=self._run_watching_session,
            args=(self.loaded_accounts[0], active_channels),
            daemon=True,
        ).start()

    def _run_watching_session(self, account: Account, channels: List[Channel]):
        """Execute the continuous watching recommendation session."""
        worker = WatchingWorker(
            account=account,
            channels=channels,
            config_mgr=self.config_mgr,
            log_queue=self.log_queue,
            stop_event=self.stop_event,
            event_queue=self.event_queue,
            stats_store=self.stats_store,
        )
        worker.run()

        self.is_running = False
        self.current_mode = "idle"
        try:
            self.event_queue.put_nowait(("finished",))
        except queue.Full:
            pass
        emit(self.log_queue, "=== Watching Session Completed ===")

    def stop_automation(self):
        if not self.is_running:
            return
        mode_label = "watching" if getattr(self, "current_mode", "automation") == "watching" else "automation"
        emit(self.log_queue, f"Stopping {mode_label} session... Please wait for workers to cleanup.")
        self.stop_event.set()

    def _handle_worker_event(self, event) -> None:
        """Apply a structured worker event to the dashboard (main thread only)."""
        if not event:
            return
        kind = event[0]

        if kind == "progress" and len(event) == 4:
            _, worker_id, done, total = event
            self.worker_progress[worker_id] = (done, total)
            done_sum = sum(d for d, _ in self.worker_progress.values())
            total_sum = sum(t for _, t in self.worker_progress.values())
            self.views["dashboard"].set_progress(done_sum, total_sum)
            # Watch counters are written when a task finishes: repaint them.
            self.refresh_watch_stats()
        elif kind == "status" and len(event) == 3:
            _, message, level = event
            self.views["dashboard"].set_status(message, level)
        elif kind == "finished":
            self.views["dashboard"].set_running_state(False)
            self.refresh_watch_stats()

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.views["logs"].append_log(msg)
        except queue.Empty:
            pass

        try:
            while True:
                self._handle_worker_event(self.event_queue.get_nowait())
        except queue.Empty:
            pass

        self.after(100, self._poll_log_queue)
