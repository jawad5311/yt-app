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

        # Queue Automation Passes Control
        self.loop_frame = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.loop_frame.pack(padx=30, pady=(15, 4), fill="x")

        self.loop_label = ctk.CTkLabel(
            self.loop_frame,
            text="Automation Passes:",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.loop_label.pack(side="left", padx=(0, 10))

        initial_loops = self.app_controller.config_mgr.get_int("AutomationLoops", 1)
        self.loop_entry = ctk.CTkEntry(self.loop_frame, width=80, font=ctk.CTkFont(size=13))
        self.loop_entry.insert(0, str(max(1, initial_loops)))
        self.loop_entry.pack(side="left")

        self.loop_hint = ctk.CTkLabel(
            self.loop_frame,
            text="(queue repetitions; e.g. 2 = run all queue tasks 2 times)",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self.loop_hint.pack(side="left", padx=(12, 0))

        # Browsing Target Video Limit Control
        self.browsing_limit_frame = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.browsing_limit_frame.pack(padx=30, pady=(4, 10), fill="x")

        self.browsing_limit_label = ctk.CTkLabel(
            self.browsing_limit_frame,
            text="Browsing Target Limit:",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.browsing_limit_label.pack(side="left", padx=(0, 10))

        initial_browsing_limit = self.app_controller.config_mgr.get_int("BrowsingTargetLimit", 1)
        self.browsing_limit_entry = ctk.CTkEntry(self.browsing_limit_frame, width=80, font=ctk.CTkFont(size=13))
        self.browsing_limit_entry.insert(0, str(max(1, initial_browsing_limit)))
        self.browsing_limit_entry.pack(side="left")

        is_unlimited = self.app_controller.config_mgr.get_bool("BrowsingUnlimited", True)
        self.browsing_unlimited_cb = ctk.CTkCheckBox(
            self.browsing_limit_frame,
            text="Unlimited browsing",
            font=ctk.CTkFont(size=13),
            command=self._on_browsing_unlimited_toggle,
        )
        if is_unlimited:
            self.browsing_unlimited_cb.select()
            self.browsing_limit_entry.configure(state="disabled")
        else:
            self.browsing_unlimited_cb.deselect()
        self.browsing_unlimited_cb.pack(side="left", padx=(15, 10))

        self.browsing_hint = ctk.CTkLabel(
            self.browsing_limit_frame,
            text="(target video attempts; 1 = at most 1 target video attempt)",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self.browsing_hint.pack(side="left")

        # Action buttons 2x2 grid:
        # Row 0: Start Automation | Start Browsing
        # Row 1: Stop             | Pause
        self.btn_grid = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.btn_grid.pack(padx=30, pady=(10, 10), fill="x")
        self.btn_grid.grid_columnconfigure((0, 1), weight=1, uniform="btn_col")

        self.btn_row = self.btn_grid
        self.stop_row = self.btn_grid

        self.start_btn = ctk.CTkButton(
            self.btn_grid,
            text="▶ Start Automation",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            height=45,
            command=self.app_controller.start_automation,
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 6), pady=(0, 6), sticky="ew")

        self.start_watching_btn = ctk.CTkButton(
            self.btn_grid,
            text="▶ Start Browsing",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#1F6AA5",
            hover_color="#144870",
            height=45,
            command=self.app_controller.start_watching,
        )
        self.start_watching_btn.grid(row=0, column=1, padx=(6, 0), pady=(0, 6), sticky="ew")

        self.stop_btn = ctk.CTkButton(
            self.btn_grid,
            text="⏹ Stop",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#D32F2F",
            hover_color="#9A0007",
            height=45,
            state="disabled",
            command=self.app_controller.stop_automation,
        )
        self.stop_btn.grid(row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="ew")

        self.pause_btn = ctk.CTkButton(
            self.btn_grid,
            text="⏸ Pause",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#D97706",
            hover_color="#B45309",
            height=45,
            state="disabled",
            command=self.app_controller.toggle_pause_watching,
        )
        self.pause_btn.grid(row=1, column=1, padx=(6, 0), pady=(6, 0), sticky="ew")

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

    def _on_browsing_unlimited_toggle(self):
        if hasattr(self, "browsing_unlimited_cb") and hasattr(self, "browsing_limit_entry"):
            if self.browsing_unlimited_cb.get():
                self.browsing_limit_entry.configure(state="disabled")
            else:
                self.browsing_limit_entry.configure(state="normal")

    def set_progress(self, done: int, total: int, mode: str = "automation"):
        """Update the progress bar and description matching the active mode."""
        if mode == "watching":
            if total > 0:
                self.progress_bar.set(min(1.0, (done / total) if total else 0.0))
                text = f"Browsing target attempts: {done} / {total}"
                if done >= total:
                    text += " — limit reached"
            else:
                self.progress_bar.set(0.0)
                text = f"Browsing target attempts: {done} (unlimited)"
        else:
            self.progress_bar.set((done / total) if total else 0.0)
            text = f"Completed {done} / {total} tasks"
            if total and done >= total:
                text += " — finished"
        self.progress_label.configure(text=text, text_color=STATUS_INFO_COLOR)

    def set_status(self, message: str, level: str = "info"):
        """Surface a worker status message (e.g. a failed sign-in) on the dashboard."""
        color = STATUS_ERROR_COLOR if level == "error" else STATUS_INFO_COLOR
        self.progress_label.configure(text=message, text_color=color)

    def get_queue_passes(self) -> int:
        """Read and validate the configured number of queue automation passes."""
        raw = self.loop_entry.get().strip() if hasattr(self, "loop_entry") else "1"
        try:
            if not raw or "." in raw:
                raise ValueError
            val = int(raw)
            if val < 1:
                raise ValueError
            return val
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Automation passes must be a valid positive integer (>= 1).")

    def get_loop_count(self) -> int:
        """Backward compatibility alias for get_queue_passes."""
        return self.get_queue_passes()

    def get_browsing_limit(self):
        """Read and validate the browsing target limit, returning (limit, is_unlimited)."""
        if hasattr(self, "browsing_unlimited_cb") and self.browsing_unlimited_cb.get():
            return (None, True)
        raw = self.browsing_limit_entry.get().strip() if hasattr(self, "browsing_limit_entry") else "1"
        try:
            if not raw or "." in raw:
                raise ValueError
            val = int(raw)
            if val < 1:
                raise ValueError
            return (val, False)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Browsing target limit must be a valid positive integer (>= 1) or select Unlimited.")

    def set_running_state(self, is_running: bool, mode: str = "automation"):
        if is_running:
            self.start_btn.configure(state="disabled")
            self.start_watching_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.loop_entry.configure(state="disabled")
            if hasattr(self, "browsing_limit_entry"):
                self.browsing_limit_entry.configure(state="disabled")
            if hasattr(self, "browsing_unlimited_cb"):
                self.browsing_unlimited_cb.configure(state="disabled")
            if mode == "watching":
                self.stop_btn.configure(text="⏹ Stop Browsing")
                self.pause_btn.configure(state="normal", text="⏸ Pause", fg_color="#D97706", hover_color="#B45309")
                self.card_status.configure(text="Browsing", text_color="#1F6AA5")
            else:
                self.stop_btn.configure(text="⏹ Stop Automation")
                self.pause_btn.configure(state="disabled", text="⏸ Pause", fg_color="#D97706")
                self.card_status.configure(text="Running", text_color="#2FA572")
        else:
            self.start_btn.configure(state="normal")
            self.start_watching_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled", text="⏹ Stop")
            self.pause_btn.configure(state="disabled", text="⏸ Pause", fg_color="#D97706")
            self.loop_entry.configure(state="normal")
            if hasattr(self, "browsing_unlimited_cb"):
                self.browsing_unlimited_cb.configure(state="normal")
                if not self.browsing_unlimited_cb.get() and hasattr(self, "browsing_limit_entry"):
                    self.browsing_limit_entry.configure(state="normal")
            elif hasattr(self, "browsing_limit_entry"):
                self.browsing_limit_entry.configure(state="normal")
            self.card_status.configure(text="Idle", text_color="white")

    def set_paused_state(self, is_paused: bool):
        """Update pause button text/color and status indicator."""
        if hasattr(self, "pause_btn"):
            if is_paused:
                self.pause_btn.configure(state="normal", text="▶ Resume", fg_color="#2563EB", hover_color="#1D4ED8")
                self.card_status.configure(text="Paused", text_color="#D97706")
            else:
                self.pause_btn.configure(state="normal", text="⏸ Pause", fg_color="#D97706", hover_color="#B45309")
                self.card_status.configure(text="Browsing", text_color="#1F6AA5")
