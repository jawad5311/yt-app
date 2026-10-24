import customtkinter as ctk

# Status line colours for (light, dark) appearance modes.
STATUS_INFO_COLOR = ("#555555", "#BBBBBB")
STATUS_ERROR_COLOR = ("#C62828", "#FF6B6B")


class DashboardView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller

        self.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Dashboard Overview",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, columnspan=4, padx=20, pady=(20, 10), sticky="w")

        # Stats Cards
        self.card_accounts = self._create_card("Loaded Accounts", "0", row=1, col=0)
        self.card_videos = self._create_card("Queued Videos", "0", row=1, col=1)
        self.card_status = self._create_card("Bot Status", "Idle", row=1, col=2)
        self.card_watch_time = self._create_card("Total Watch Time", "0s", row=1, col=3)

        # Action Frame
        self.action_frame = ctk.CTkFrame(self, corner_radius=8)
        self.action_frame.grid(row=2, column=0, columnspan=4, padx=20, pady=20, sticky="nsew")
        self.action_frame.grid_columnconfigure(0, weight=1)

        # Loop / Recurring Runs Control
        self.loop_frame = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.loop_frame.pack(padx=30, pady=(25, 10), fill="x")

        self.loop_label = ctk.CTkLabel(
            self.loop_frame,
            text="Run Automation times:",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.loop_label.pack(side="left", padx=(0, 10))

        initial_loops = self.app_controller.config_mgr.get_int("AutomationLoops", 1)
        self.loop_entry = ctk.CTkEntry(self.loop_frame, width=90, font=ctk.CTkFont(size=13))
        self.loop_entry.insert(0, str(max(1, initial_loops)))
        self.loop_entry.pack(side="left")

        self.loop_hint = ctk.CTkLabel(
            self.loop_frame,
            text="(e.g. 2 = run all queue tasks 2 times sequentially)",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self.loop_hint.pack(side="left", padx=(12, 0))

        # Action buttons row: Start Automation and Start Browsing side by side
        self.btn_row = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.btn_row.pack(padx=30, pady=(10, 5), fill="x")
        self.btn_row.grid_columnconfigure((0, 1), weight=1)

        self.start_btn = ctk.CTkButton(
            self.btn_row,
            text="▶ Start Automation",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            height=45,
            command=self.app_controller.start_automation,
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.start_watching_btn = ctk.CTkButton(
            self.btn_row,
            text="▶ Start Browsing",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#1F6AA5",
            hover_color="#144870",
            height=45,
            command=self.app_controller.start_watching,
        )
        self.start_watching_btn.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.stop_btn = ctk.CTkButton(
            self.action_frame,
            text="⏹ Stop Automation",
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#D32F2F",
            hover_color="#9A0007",
            height=45,
            state="disabled",
            command=self.app_controller.stop_automation,
        )
        self.stop_btn.pack(padx=30, pady=(5, 10), fill="x")

        # Progress Bar
        self.progress_label = ctk.CTkLabel(self.action_frame, text="Ready to launch tasks.", font=ctk.CTkFont(size=13))
        self.progress_label.pack(padx=30, pady=(20, 5), anchor="w")

        self.progress_bar = ctk.CTkProgressBar(self.action_frame)
        self.progress_bar.pack(padx=30, pady=(0, 30), fill="x")
        self.progress_bar.set(0)

    def _create_card(self, title: str, initial_value: str, row: int, col: int):
        card = ctk.CTkFrame(self, corner_radius=8)
        card.grid(row=row, column=col, padx=15, pady=10, sticky="nsew")

        t_lbl = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="normal"))
        t_lbl.pack(padx=15, pady=(15, 2))

        v_lbl = ctk.CTkLabel(card, text=initial_value, font=ctk.CTkFont(size=20, weight="bold"))
        v_lbl.pack(padx=15, pady=(0, 15))
        return v_lbl

    def update_stats(self, accounts_count: int, videos_count: int, status: str):
        self.card_accounts.configure(text=str(accounts_count))
        self.card_videos.configure(text=str(videos_count))
        self.card_status.configure(text=status)

    def update_watch_totals(self, formatted_duration: str):
        """Lifetime watch time delivered across every recorded video."""
        self.card_watch_time.configure(text=formatted_duration)

    def reset_progress(self):
        """Clear the progress bar and status line before a run starts."""
        self.progress_bar.set(0)
        self.progress_label.configure(text="Starting...", text_color=STATUS_INFO_COLOR)

    def set_progress(self, done: int, total: int):
        """Update the progress bar from aggregated worker progress."""
        self.progress_bar.set((done / total) if total else 0.0)
        text = f"Completed {done} / {total} tasks"
        if total and done >= total:
            text += " — finished"
        self.progress_label.configure(text=text, text_color=STATUS_INFO_COLOR)

    def set_status(self, message: str, level: str = "info"):
        """Surface a worker status message (e.g. a failed sign-in) on the dashboard."""
        color = STATUS_ERROR_COLOR if level == "error" else STATUS_INFO_COLOR
        self.progress_label.configure(text=message, text_color=color)

    def get_loop_count(self) -> int:
        """Read and validate the configured number of automation loops."""
        try:
            val = int(self.loop_entry.get().strip())
            return max(1, val)
        except (ValueError, AttributeError):
            return 1

    def set_running_state(self, is_running: bool, mode: str = "automation"):
        if is_running:
            self.start_btn.configure(state="disabled")
            self.start_watching_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.loop_entry.configure(state="disabled")
            if mode == "watching":
                self.stop_btn.configure(text="⏹ Stop Watching")
                self.card_status.configure(text="Watching", text_color="#1F6AA5")
            else:
                self.stop_btn.configure(text="⏹ Stop Automation")
                self.card_status.configure(text="Running", text_color="#2FA572")
        else:
            self.start_btn.configure(state="normal")
            self.start_watching_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled", text="⏹ Stop Automation")
            self.loop_entry.configure(state="normal")
            self.card_status.configure(text="Idle", text_color="white")
