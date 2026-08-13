import customtkinter as ctk


class DashboardView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller

        self.grid_columnconfigure((0, 1, 2), weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Dashboard Overview",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, columnspan=3, padx=20, pady=(20, 10), sticky="w")

        # Stats Cards
        self.card_accounts = self._create_card("Loaded Accounts", "0", row=1, col=0)
        self.card_videos = self._create_card("Queued Videos", "0", row=1, col=1)
        self.card_status = self._create_card("Bot Status", "Idle", row=1, col=2)

        # Action Frame
        self.action_frame = ctk.CTkFrame(self, corner_radius=8)
        self.action_frame.grid(row=2, column=0, columnspan=3, padx=20, pady=20, sticky="nsew")
        self.action_frame.grid_columnconfigure(0, weight=1)

        self.start_btn = ctk.CTkButton(
            self.action_frame,
            text="▶ Start Automation",
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            height=45,
            command=self.app_controller.start_automation,
        )
        self.start_btn.pack(padx=30, pady=(40, 10), fill="x")

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
        self.stop_btn.pack(padx=30, pady=10, fill="x")

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

    def set_running_state(self, is_running: bool):
        if is_running:
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.card_status.configure(text="Running", text_color="#2FA572")
        else:
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.card_status.configure(text="Idle", text_color="white")
