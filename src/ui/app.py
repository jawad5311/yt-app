import queue
import threading
from pathlib import Path
from typing import List, Optional
import customtkinter as ctk

from ..config.manager import ConfigManager
from ..models.account import Account
from ..models.video_task import VideoTask
from ..automation.worker import AutomationWorker
from ..utils.logger import init_logging, emit
from .views.dashboard_view import DashboardView
from .views.accounts_view import AccountsView
from .views.queue_view import QueueView
from .views.settings_view import SettingsView
from .views.logs_view import LogsView


class YTBotApp(ctk.CTk):
    def __init__(self, config_mgr: Optional[ConfigManager] = None):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("YouTube Views + Comment Organic Bot - V2.1 (Modular)")
        self.geometry("1100x700")
        self.minsize(950, 600)

        self.config_mgr = config_mgr or ConfigManager()
        self.log_file = init_logging()
        self.log_queue = queue.Queue()

        self.loaded_accounts: List[Account] = []
        self.queued_video_tasks: List[VideoTask] = []
        self.active_threads: List[threading.Thread] = []
        self.stop_event = threading.Event()
        self.is_running = False

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
        self.sidebar.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(
            self.sidebar,
            text="YT Organic Bot",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 20))

        self.nav_buttons = {}
        nav_items = [
            ("dashboard", "📊 Dashboard"),
            ("accounts", "👤 Accounts"),
            ("queue", "🎥 Video Queue"),
            ("settings", "⚙ Settings"),
            ("logs", "📋 Diagnostics Logs"),
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

        self.views = {
            "dashboard": DashboardView(self.container, self),
            "accounts": AccountsView(self.container, self),
            "queue": QueueView(self.container, self),
            "settings": SettingsView(self.container, self),
            "logs": LogsView(self.container, self),
        }

        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

        self.show_view("dashboard")

    def show_view(self, name: str):
        for view_name, view_widget in self.views.items():
            if view_name == name:
                view_widget.tkraise()
                self.nav_buttons[view_name].configure(fg_color=("gray75", "gray25"))
            else:
                self.nav_buttons[view_name].configure(fg_color="transparent")

    def on_accounts_changed(self, accounts: List[Account]):
        self.loaded_accounts = accounts
        self.views["dashboard"].update_stats(
            len(self.loaded_accounts),
            len(self.queued_video_tasks),
            "Running" if self.is_running else "Idle",
        )

    def on_queue_changed(self, tasks: List[VideoTask]):
        self.queued_video_tasks = tasks
        self.views["dashboard"].update_stats(
            len(self.loaded_accounts),
            len(self.queued_video_tasks),
            "Running" if self.is_running else "Idle",
        )

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

        self.is_running = True
        self.stop_event.clear()
        self.views["dashboard"].set_running_state(True)
        emit(self.log_queue, "=== Starting YouTube Automation Session ===")

        # Spawn worker thread(s)
        self.active_threads.clear()
        parallel_limit = self.config_mgr.get_int("ParallelBrowsers", 1)

        # Distribute accounts across worker threads
        for acc in self.loaded_accounts[:parallel_limit]:
            worker = AutomationWorker(
                account=acc,
                video_tasks=self.queued_video_tasks,
                config_mgr=self.config_mgr,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
            )
            thread = threading.Thread(target=worker.run, daemon=True)
            self.active_threads.append(thread)
            thread.start()

        # Monitor thread completion
        threading.Thread(target=self._monitor_execution_finish, daemon=True).start()

    def stop_automation(self):
        if not self.is_running:
            return
        emit(self.log_queue, "Stopping automation session... Please wait for workers to cleanup.")
        self.stop_event.set()

    def _monitor_execution_finish(self):
        for t in self.active_threads:
            t.join()

        self.is_running = False
        self.after(0, lambda: self.views["dashboard"].set_running_state(False))
        emit(self.log_queue, "=== Automation Session Completed ===")

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.views["logs"].append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)
