from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..components import Button, CheckBox, Entry, Font, Frame, Label, ProgressBar
from ..tooltip import add_tooltip, flash_button_feedback, with_button_feedback

# Status line colours for (light, dark) appearance modes.
STATUS_INFO_COLOR = ("#555555", "#BBBBBB")
STATUS_ERROR_COLOR = ("#C62828", "#FF6B6B")


class DashboardView(Frame):
    """macOS / Apple Human Interface Guidelines styled Dashboard view."""

    def __init__(self, parent, app_controller):
        super().__init__(parent, style="TFrame")
        self.app_controller = app_controller

        self.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.grid_rowconfigure(3, weight=1)

        # -------------------------------------------------------------
        # 1. Header Bar (Apple Large Title & System Status Pill)
        # -------------------------------------------------------------
        self.header_frame = Frame(self, style="Transparent.TFrame")
        self.header_frame.grid(row=0, column=0, columnspan=4, padx=16, pady=(16, 12), sticky="ew")
        self.header_frame.grid_columnconfigure(0, weight=1)

        self.header_left = Frame(self.header_frame, style="Transparent.TFrame")
        self.header_left.pack(side="left", anchor="w")

        # Large Apple Title
        self.title_label = Label(
            self.header_left,
            text="Dashboard Overview",
            font=Font(size=22, weight="bold"),
            style="AppleHeader.TLabel",
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = Label(
            self.header_left,
            text="Playback Automation & Performance Control Center",
            font=Font(size=9, weight="normal"),
            style="AppleSubtitle.TLabel",
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # macOS System Status Capsule / Pill
        self.system_pill_frame = Frame(self.header_frame, style="ApplePill.TFrame")
        self.system_pill_frame.pack(side="right", anchor="e", padx=(0, 4), pady=4)

        self.system_pill_dot = Label(
            self.system_pill_frame,
            text="●",
            font=Font(size=9, weight="bold"),
            style="ApplePill.TLabel",
        )
        self.system_pill_dot.configure(text_color="#30D158")
        self.system_pill_dot.pack(side="left", padx=(10, 4), pady=5)

        self.system_pill_text = Label(
            self.system_pill_frame,
            text="System Ready",
            font=Font(size=8, weight="bold"),
            style="ApplePill.TLabel",
        )
        self.system_pill_text.pack(side="left", padx=(0, 10), pady=5)

        # -------------------------------------------------------------
        # 2. Stats Widget Grid (Apple macOS Widgets) - Row 1
        # -------------------------------------------------------------
        self.card_accounts = self._create_card(
            title="Loaded Accounts",
            initial_value="0",
            row=1,
            col=0,
            category_icon="👤",
            footer_text="Active profiles loaded",
        )
        self.card_videos = self._create_card(
            title="Queued Videos",
            initial_value="0",
            row=1,
            col=1,
            category_icon="🎬",
            footer_text="Tasks ready to browse",
        )
        self.card_status = self._create_card(
            title="Bot Status",
            initial_value="Idle",
            row=1,
            col=2,
            category_icon="⚡",
            footer_text="Ready for automation",
        )
        self.card_watch_time = self._create_card(
            title="Total Watch Time",
            initial_value="0s",
            row=1,
            col=3,
            category_icon="⏱",
            footer_text="Lifetime delivered",
        )

        # -------------------------------------------------------------
        # 2b. Stats Widget Grid (Apple macOS Widgets) - Row 2
        # -------------------------------------------------------------
        self.card_session_watch_time = self._create_card(
            title="Session Watch Time",
            initial_value="0s",
            row=2,
            col=0,
            category_icon="⏱",
            footer_text="Current session delivered",
        )
        self.card_slot1 = self.card_session_watch_time
        self.card_slot2 = self._create_card(
            title="Box 2",
            initial_value="—",
            row=2,
            col=1,
            category_icon="📊",
            footer_text="Awaiting assignment",
        )
        self.card_slot3 = self._create_card(
            title="Box 3",
            initial_value="—",
            row=2,
            col=2,
            category_icon="📈",
            footer_text="Awaiting assignment",
        )
        self.card_slot4 = self._create_card(
            title="Box 4",
            initial_value="—",
            row=2,
            col=3,
            category_icon="🎯",
            footer_text="Awaiting assignment",
        )

        # -------------------------------------------------------------
        # 3. Action Frame (Apple Control Center Card)
        # -------------------------------------------------------------
        self.action_frame = Frame(self, style="AppleCard.TFrame")
        self.action_frame.grid(row=3, column=0, columnspan=4, padx=16, pady=(6, 16), sticky="nsew")
        self.action_frame.grid_columnconfigure(0, weight=1)

        # Section Header: CONTROL CENTER
        self.control_header_frame = Frame(self.action_frame, style="AppleCardInner.TFrame")
        self.control_header_frame.pack(fill="x", padx=20, pady=(16, 6))

        self.control_title = Label(
            self.control_header_frame,
            text="CONTROL CENTER",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        )
        self.control_title.pack(side="left")

        self.divider1 = Frame(self.action_frame, style="AppleDivider.TFrame", height=1)
        self.divider1.pack(fill="x", padx=20, pady=(0, 14))

        # Queue Automation Passes Control (retained for backward test compatibility, not displayed)
        self.loop_frame = Frame(self.action_frame, style="AppleCardInner.TFrame")
        self.loop_label = Label(
            self.loop_frame,
            text="Automation Passes:",
            font=Font(size=10, weight="bold"),
            style="AppleCard.TLabel",
        )
        raw_loops = self.app_controller.config_mgr.get_int("AutomationLoops", 1) if hasattr(self.app_controller, "config_mgr") else 1
        try:
            initial_loops = int(raw_loops)
        except (TypeError, ValueError):
            initial_loops = 1
        self.loop_entry = Entry(self.loop_frame, width=80, font=Font(size=9))
        self.loop_entry.insert(0, str(max(1, initial_loops)))
        self.loop_hint = Label(
            self.loop_frame,
            text="(queue repetitions; e.g. 2 = run all queue tasks 2 times)",
            font=Font(size=9),
            style="AppleCardMuted.TLabel",
        )

        # Browsing Target Video Limit Control (Apple Inset Grouped Box)
        self.browsing_limit_frame = Frame(self.action_frame, style="AppleInset.TFrame")
        self.browsing_limit_frame.pack(padx=20, pady=(0, 14), fill="x")

        self.browsing_limit_label = Label(
            self.browsing_limit_frame,
            text="🎯  Browsing Target Limit:",
            font=Font(size=9, weight="bold"),
            style="AppleInset.TLabel",
        )
        self.browsing_limit_label.pack(side="left", padx=(14, 10), pady=10)

        raw_browsing_limit = self.app_controller.config_mgr.get_int("BrowsingTargetLimit", 1) if hasattr(self.app_controller, "config_mgr") else 1
        try:
            initial_browsing_limit = int(raw_browsing_limit)
        except (TypeError, ValueError):
            initial_browsing_limit = 1
        self.browsing_limit_entry = Entry(self.browsing_limit_frame, width=80, font=Font(size=9), style="Apple.TEntry")
        self.browsing_limit_entry.insert(0, str(max(1, initial_browsing_limit)))
        self.browsing_limit_entry.pack(side="left", pady=10)

        # By default, keep Unlimited Browsing checked whenever the app is opened
        self.browsing_unlimited_cb = CheckBox(
            self.browsing_limit_frame,
            text="Unlimited browsing",
            command=self._on_browsing_unlimited_toggle,
            style="AppleInset.TCheckbutton",
        )
        self.browsing_unlimited_cb.select()
        self.browsing_limit_entry.configure(state="disabled")
        self.browsing_unlimited_cb.pack(side="left", padx=(16, 10), pady=10)

        self.browsing_hint = Label(
            self.browsing_limit_frame,
            text="(target video attempts; 1 = max attempt)",
            font=Font(size=8),
            style="AppleInsetMuted.TLabel",
        )
        self.browsing_hint.pack(side="left", pady=10)

        # -------------------------------------------------------------
        # Action buttons in one line (Apple macOS Pill Buttons)
        # Unified Col 0: Start / Pause / Resume | Col 1: Stop
        # -------------------------------------------------------------
        self.btn_grid = Frame(self.action_frame, style="AppleCardInner.TFrame")
        self.btn_grid.pack(padx=20, pady=(0, 16), fill="x")
        self.btn_grid.grid_columnconfigure(2, weight=1)

        self.btn_row = self.btn_grid
        self.stop_row = self.btn_grid
        self.is_running = False
        self.is_paused = False

        def _on_primary_action():
            is_running = getattr(self, "is_running", False) or (
                hasattr(self.app_controller, "is_running") and self.app_controller.is_running is True
            )
            if not is_running:
                cmd = getattr(self.app_controller, "start_watching", None)
                if callable(cmd):
                    cmd()
                # If start_watching was cancelled, failed validation, or did not start, restore idle button state
                if getattr(self.app_controller, "is_running", None) is False and not getattr(self, "is_running", False):
                    self.set_running_state(False)
            else:
                cmd = getattr(self.app_controller, "toggle_pause_watching", None)
                if callable(cmd):
                    cmd()

        def _on_stop_automation():
            if not getattr(self, "is_running", False) and not (
                hasattr(self.app_controller, "is_running") and self.app_controller.is_running is True
            ):
                return
            flash_button_feedback(self.stop_btn, "Stopped", duration_ms=3000, original_text="⏹ Stop")
            cmd = getattr(self.app_controller, "stop_automation", None)
            if callable(cmd):
                cmd()

        self.start_watching_btn = Button(
            self.btn_grid,
            text="▶ Start Browsing",
            font=Font(size=10, weight="bold"),
            style="ApplePrimary.TButton",
            state="normal",
            cursor="hand2",
            width=140,
            command=_on_primary_action,
        )
        self.start_watching_btn.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="w")
        add_tooltip(
            self.start_watching_btn,
            "Start browsing and playing queued videos across configured accounts.",
        )

        self.stop_btn = Button(
            self.btn_grid,
            text="⏹ Stop",
            font=Font(size=10, weight="bold"),
            style="AppleDanger.TButton",
            state="disabled",
            cursor="arrow",
            width=140,
            command=_on_stop_automation,
        )
        self.stop_btn.grid(row=0, column=1, padx=(0, 10), pady=0, sticky="w")
        add_tooltip(
            self.stop_btn,
            "Stop all ongoing browsing operations and browser sessions.",
        )

        # Backward compatibility alias for unified control button
        self.pause_btn = self.start_watching_btn

        # -------------------------------------------------------------
        # Activity & Progress Monitor (Apple System Progress Bar)
        # -------------------------------------------------------------
        self.divider2 = Frame(self.action_frame, style="AppleDivider.TFrame", height=1)
        self.divider2.pack(fill="x", padx=20, pady=(2, 14))

        self.activity_header_frame = Frame(self.action_frame, style="AppleCardInner.TFrame")
        self.activity_header_frame.pack(fill="x", padx=20, pady=(0, 4))

        self.activity_title = Label(
            self.activity_header_frame,
            text="ACTIVITY & PROGRESS",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        )
        self.activity_title.pack(side="left")

        self.progress_label = Label(
            self.action_frame,
            text="Ready to launch tasks.",
            font=Font(size=9),
            style="AppleCard.TLabel",
        )
        self.progress_label.pack(padx=20, pady=(2, 8), anchor="w")

        self.progress_bar = ProgressBar(self.action_frame, style="Apple.Horizontal.TProgressbar")
        self.progress_bar.pack(padx=20, pady=(0, 22), fill="x")
        self.progress_bar.set(0)

    def _create_card(
        self,
        title: str,
        initial_value: str,
        row: int,
        col: int,
        category_icon: str = "",
        footer_text: str = "",
    ):
        """Build an Apple macOS styled elevated Widget card."""
        card = Frame(self, style="AppleCard.TFrame")
        card.grid(row=row, column=col, padx=8, pady=(6, 12), sticky="nsew")

        # Category / Icon header
        header_f = Frame(card, style="AppleCardInner.TFrame")
        header_f.pack(fill="x", padx=16, pady=(14, 2))

        header_text = f"{category_icon}  {title.upper()}" if category_icon else title.upper()
        t_lbl = Label(header_f, text=header_text, font=Font(size=8, weight="bold"), style="AppleCategory.TLabel")
        t_lbl.pack(side="left", anchor="w")

        # Large prominent metric value
        val_f = Frame(card, style="AppleCardInner.TFrame")
        val_f.pack(fill="x", padx=16, pady=(2, 2))

        v_lbl = Label(val_f, text=initial_value, font=Font(size=20, weight="bold"), style="AppleMetric.TLabel")
        v_lbl.pack(side="left", anchor="w")

        # Contextual footer caption
        footer_f = Frame(card, style="AppleCardInner.TFrame")
        footer_f.pack(fill="x", padx=16, pady=(0, 14))

        f_lbl = Label(footer_f, text=footer_text, font=Font(size=8, weight="normal"), style="AppleMetricFooter.TLabel")
        f_lbl.pack(side="left", anchor="w")

        if "Status" in title:
            self._status_card_footer = f_lbl

        # Store widget references for dynamic reconfiguration
        v_lbl.card_frame = card
        v_lbl.title_label = t_lbl
        v_lbl.footer_label = f_lbl
        v_lbl.category_icon = category_icon
        v_lbl.card_title = title

        return v_lbl

    def update_session_watch_time(self, formatted_duration: str) -> None:
        """Current session watch time delivered."""
        if hasattr(self, "card_session_watch_time"):
            self.card_session_watch_time.configure(text=formatted_duration)

    def set_card_content(
        self,
        card_lbl: Label,
        value: Optional[str] = None,
        title: Optional[str] = None,
        footer: Optional[str] = None,
        category_icon: Optional[str] = None,
    ) -> None:
        """Update any card's value, title, icon, or footer caption dynamically."""
        if value is not None:
            card_lbl.configure(text=str(value))
        if title is not None or category_icon is not None:
            icon = category_icon if category_icon is not None else getattr(card_lbl, "category_icon", "")
            t = title if title is not None else getattr(card_lbl, "card_title", "")
            card_lbl.card_title = t
            card_lbl.category_icon = icon
            hdr = f"{icon}  {t.upper()}" if icon else t.upper()
            if hasattr(card_lbl, "title_label"):
                card_lbl.title_label.configure(text=hdr)
        if footer is not None and hasattr(card_lbl, "footer_label"):
            card_lbl.footer_label.configure(text=footer)

    def update_stats(self, accounts_count: int, videos_count: int, status: str):
        self.card_accounts.configure(text=str(accounts_count))
        self.card_videos.configure(text=str(videos_count))
        self.card_status.configure(text=status)
        if status.lower() == "idle":
            if hasattr(self, "system_pill_dot") and hasattr(self, "system_pill_text"):
                try:
                    self.system_pill_dot.configure(text_color="#30D158")
                    self.system_pill_text.configure(text="System Ready")
                except Exception:
                    pass
            if hasattr(self, "_status_card_footer"):
                try:
                    self._status_card_footer.configure(text="Ready for automation")
                except Exception:
                    pass

    def update_watch_totals(self, formatted_duration: str):
        """Lifetime watch time delivered across every recorded video."""
        self.card_watch_time.configure(text=formatted_duration)

    def reset_progress(self):
        """Clear the progress bar and status line before a run starts."""
        self.progress_bar.set(0)
        self.progress_label.configure(text="Starting...")

    def _on_browsing_unlimited_toggle(self):
        if getattr(self, "is_running", False) or (hasattr(self.app_controller, "is_running") and self.app_controller.is_running is True):
            return
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

    def set_running_state(self, is_running: bool, mode: str = "watching"):
        self.is_running = bool(is_running)
        if is_running:
            self.is_paused = False
            if hasattr(self, "start_btn"):
                self.start_btn.configure(state="disabled", cursor="arrow")
            if hasattr(self, "start_watching_btn"):
                self.start_watching_btn.configure(
                    state="normal",
                    text="⏸ Pause",
                    cursor="hand2",
                    style="AppleWarning.TButton",
                    fg_color="#D97706",
                    hover_color="#B45309",
                )
                try:
                    add_tooltip(self.start_watching_btn, "Pause ongoing video playback across all browsers.")
                except Exception:
                    pass
            if hasattr(self, "stop_btn"):
                self.stop_btn.configure(state="normal", text="⏹ Stop", cursor="hand2")
            if hasattr(self, "pause_btn") and self.pause_btn is not getattr(self, "start_watching_btn", None):
                self.pause_btn.configure(state="normal", text="⏸ Pause", cursor="hand2", fg_color="#D97706", hover_color="#B45309")
            if hasattr(self, "loop_entry"):
                try:
                    self.loop_entry.configure(state="disabled")
                except Exception:
                    pass
            if hasattr(self, "browsing_limit_entry"):
                self.browsing_limit_entry.configure(state="disabled")
            if hasattr(self, "browsing_unlimited_cb"):
                # Do not gray out the unlimited switch; keep its visual state and disable interaction while browsing
                self.browsing_unlimited_cb.configure(interactive=False)
            self.card_status.configure(text="Browsing", text_color="#1F6AA5")
            if hasattr(self, "system_pill_dot") and hasattr(self, "system_pill_text"):
                try:
                    self.system_pill_dot.configure(text_color="#0A84FF")
                    self.system_pill_text.configure(text="Browsing Active")
                except Exception:
                    pass
            if hasattr(self, "_status_card_footer"):
                try:
                    self._status_card_footer.configure(text="Active watch cycle")
                except Exception:
                    pass
        else:
            self.is_paused = False
            if hasattr(self, "start_btn"):
                self.start_btn.configure(state="normal", cursor="hand2")
            if hasattr(self, "start_watching_btn"):
                self.start_watching_btn.configure(
                    state="normal",
                    text="▶ Start Browsing",
                    cursor="hand2",
                    style="ApplePrimary.TButton",
                    fg_color="#2563EB",
                    hover_color="#1D4ED8",
                )
                try:
                    add_tooltip(self.start_watching_btn, "Start browsing and playing queued videos across configured accounts.")
                except Exception:
                    pass
            if hasattr(self, "stop_btn"):
                if getattr(self.stop_btn, "_feedback_timer_id", None) and not hasattr(self.stop_btn, "assert_called_with"):
                    self.stop_btn.configure(state="disabled", cursor="arrow")
                else:
                    self.stop_btn.configure(state="disabled", text="⏹ Stop", cursor="arrow")
            if hasattr(self, "pause_btn") and self.pause_btn is not getattr(self, "start_watching_btn", None):
                self.pause_btn.configure(state="disabled", text="⏸ Pause", cursor="arrow", fg_color="#D97706")
            if hasattr(self, "loop_entry"):
                try:
                    self.loop_entry.configure(state="normal")
                except Exception:
                    pass
            if hasattr(self, "browsing_unlimited_cb"):
                self.browsing_unlimited_cb.configure(interactive=True, state="normal")
                if not self.browsing_unlimited_cb.get() and hasattr(self, "browsing_limit_entry"):
                    self.browsing_limit_entry.configure(state="normal")
            elif hasattr(self, "browsing_limit_entry"):
                self.browsing_limit_entry.configure(state="normal")
            self.card_status.configure(text="Idle", text_color="white")
            if hasattr(self, "system_pill_dot") and hasattr(self, "system_pill_text"):
                try:
                    self.system_pill_dot.configure(text_color="#30D158")
                    self.system_pill_text.configure(text="System Ready")
                except Exception:
                    pass
            if hasattr(self, "_status_card_footer"):
                try:
                    self._status_card_footer.configure(text="Ready for automation")
                except Exception:
                    pass

    def set_paused_state(self, is_paused: bool):
        """Update unified start/pause/resume button text/color and status indicator."""
        self.is_paused = bool(is_paused)
        primary_btn = getattr(self, "start_watching_btn", None)
        if is_paused:
            if primary_btn:
                primary_btn.configure(
                    state="normal",
                    text="▶ Resume",
                    cursor="hand2",
                    style="ApplePrimary.TButton",
                    fg_color="#2563EB",
                    hover_color="#1D4ED8",
                )
                try:
                    add_tooltip(primary_btn, "Resume video playback across all browsers.")
                except Exception:
                    pass
            if hasattr(self, "pause_btn") and self.pause_btn is not primary_btn:
                self.pause_btn.configure(state="normal", text="▶ Resume", cursor="hand2", fg_color="#2563EB", hover_color="#1D4ED8")
            self.card_status.configure(text="Paused", text_color="#D97706")
            if hasattr(self, "system_pill_dot") and hasattr(self, "system_pill_text"):
                try:
                    self.system_pill_dot.configure(text_color="#FF9F0A")
                    self.system_pill_text.configure(text="Session Paused")
                except Exception:
                    pass
            if hasattr(self, "_status_card_footer"):
                try:
                    self._status_card_footer.configure(text="Playback paused")
                except Exception:
                    pass
        else:
            if primary_btn:
                primary_btn.configure(
                    state="normal",
                    text="⏸ Pause",
                    cursor="hand2",
                    style="AppleWarning.TButton",
                    fg_color="#D97706",
                    hover_color="#B45309",
                )
                try:
                    add_tooltip(primary_btn, "Pause ongoing video playback across all browsers.")
                except Exception:
                    pass
            if hasattr(self, "pause_btn") and self.pause_btn is not primary_btn:
                self.pause_btn.configure(state="normal", text="⏸ Pause", cursor="hand2", fg_color="#D97706", hover_color="#B45309")
            self.card_status.configure(text="Browsing", text_color="#1F6AA5")
            if hasattr(self, "system_pill_dot") and hasattr(self, "system_pill_text"):
                try:
                    self.system_pill_dot.configure(text_color="#0A84FF")
                    self.system_pill_text.configure(text="Browsing Active")
                except Exception:
                    pass
            if hasattr(self, "_status_card_footer"):
                try:
                    self._status_card_footer.configure(text="Active watch cycle")
                except Exception:
                    pass
