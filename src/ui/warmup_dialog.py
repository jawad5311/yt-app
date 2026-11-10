"""Themed Apple HIG modal dialog for configuring account warm-up parameters and manual overrides."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Optional

from ..automation.warmup_manager import WarmupAccountConfig, WarmupManager, get_warmup_gradient_color
from ..models.account import Account
from .components import Button, CheckBox, Entry, Font, Frame, Label
from .theme import get_font, get_theme_colors
from .tooltip import add_tooltip


class WarmupDialog(tk.Toplevel):
    """Themed pop-up window for editing account warm-up settings and manual overrides."""

    def __init__(
        self,
        parent: tk.Misc,
        account: Account,
        config_mgr: Any,
        on_save_callback: Optional[Callable[[], None]] = None,
        mode: Optional[str] = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.account = account
        self.config_mgr = config_mgr
        self.on_save_callback = on_save_callback
        self.warmup_mgr = WarmupManager(config_mgr)
        self.cfg: WarmupAccountConfig = self.warmup_mgr.get_account_config(account.email)

        self._mode = mode or "Dark"
        colors = get_theme_colors(self._mode)
        bg = colors.get("bg", "#161619")
        card_bg = colors.get("card_bg", "#202024")
        card_border = colors.get("card_border", "#323238")
        fg = colors.get("fg", "#ffffff")
        fg_dim = colors.get("fg_dim", "#d4d4d8")

        self.title(f"Warm Up Settings - {account.email}")
        self.configure(bg=bg)
        self.resizable(False, False)

        try:
            top_level = parent.winfo_toplevel()
            self.transient(top_level)
            self.grab_set()
        except Exception:
            pass

        card = tk.Frame(
            self,
            bg=card_bg,
            highlightbackground=card_border,
            highlightthickness=1,
            padx=20,
            pady=18,
        )
        card.pack(fill="both", expand=True, padx=14, pady=14)

        # Header
        header = tk.Frame(card, bg=card_bg)
        header.pack(fill="x", pady=(0, 10))

        tk.Label(
            header,
            text="🔥",
            font=get_font(size=22),
            bg=card_bg,
            fg="#f97316",
        ).pack(side="left", padx=(0, 10))

        title_box = tk.Frame(header, bg=card_bg)
        title_box.pack(side="left", fill="x")

        tk.Label(
            title_box,
            text="Account Warm-Up Progression",
            font=get_font(size=14, weight="bold"),
            bg=card_bg,
            fg=fg,
        ).pack(anchor="w")

        tk.Label(
            title_box,
            text=f"Profile: {account.email}",
            font=get_font(size=9),
            bg=card_bg,
            fg=fg_dim,
        ).pack(anchor="w", pady=(1, 0))

        # Status & Gradient Banner
        self.status_banner = tk.Frame(card, bg=card_bg, highlightbackground=card_border, highlightthickness=1, padx=12, pady=10)
        self.status_banner.pack(fill="x", pady=(4, 12))

        self.status_badge = tk.Label(
            self.status_banner,
            text="",
            font=get_font(size=10, weight="bold"),
            padx=10,
            pady=4,
            relief="flat",
        )
        self.status_badge.pack(side="left", padx=(0, 12))

        self.status_detail_lbl = tk.Label(
            self.status_banner,
            text="",
            font=get_font(size=9),
            bg=card_bg,
            fg=fg_dim,
            justify="left",
        )
        self.status_detail_lbl.pack(side="left", fill="x", expand=True)

        # Settings Form Frame
        form = tk.Frame(card, bg=card_bg)
        form.pack(fill="x", pady=(0, 10))

        self.enabled_var = tk.BooleanVar(value=self.cfg.enabled)
        enabled_cb = tk.Checkbutton(
            form,
            text="Enable Warm-Up for this account",
            variable=self.enabled_var,
            bg=card_bg,
            fg=fg,
            selectcolor=card_bg,
            activebackground=card_bg,
            activeforeground=fg,
            font=get_font(size=10, weight="bold"),
            command=self._on_values_changed,
        )
        enabled_cb.pack(anchor="w", pady=(0, 10))

        # Total Target Hours
        tk.Label(form, text="Total Warm-Up Target Limit (Hours):", font=get_font(size=9, weight="bold"), bg=card_bg, fg=fg).pack(anchor="w", pady=(2, 2))
        self.target_entry = ttk.Entry(form, width=30)
        self.target_entry.insert(0, str(self.cfg.target_hours))
        self.target_entry.pack(anchor="w", pady=(0, 8))
        self.target_entry.bind("<KeyRelease>", lambda e: self._on_values_changed())

        # Daily Increment Hours
        tk.Label(form, text="Per-Day Increment (Hours):", font=get_font(size=9, weight="bold"), bg=card_bg, fg=fg).pack(anchor="w", pady=(2, 2))
        self.inc_entry = ttk.Entry(form, width=30)
        self.inc_entry.insert(0, str(self.cfg.daily_increment_hours))
        self.inc_entry.pack(anchor="w", pady=(0, 8))
        self.inc_entry.bind("<KeyRelease>", lambda e: self._on_values_changed())

        # Starting Day 1 Watch Time
        tk.Label(form, text="Starting Watch Time - Day 1 (Hours):", font=get_font(size=9, weight="bold"), bg=card_bg, fg=fg).pack(anchor="w", pady=(2, 2))
        self.start_entry = ttk.Entry(form, width=30)
        self.start_entry.insert(0, str(self.cfg.start_hours))
        self.start_entry.pack(anchor="w", pady=(0, 8))
        self.start_entry.bind("<KeyRelease>", lambda e: self._on_values_changed())

        # Manual Override Action Buttons
        override_frame = tk.Frame(card, bg=card_bg)
        override_frame.pack(fill="x", pady=(6, 12))

        tk.Label(override_frame, text="Manual Status Overrides:", font=get_font(size=9, weight="bold"), bg=card_bg, fg=fg_dim).pack(anchor="w", pady=(0, 6))

        btn_row = tk.Frame(override_frame, bg=card_bg)
        btn_row.pack(fill="x")

        self.btn_mark_warmed = tk.Button(
            btn_row,
            text="🟢 Mark as Fully Warmed Up",
            font=get_font(size=9, weight="bold"),
            bg="#065f46",
            fg="#ecfdf5",
            activebackground="#047857",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._mark_as_warmed_up,
        )
        self.btn_mark_warmed.pack(side="left", padx=(0, 10))
        add_tooltip(self.btn_mark_warmed, "Immediately graduate this account as fully mature and warmed up.")

        self.btn_reset_warmup = tk.Button(
            btn_row,
            text="🔄 Reset Warm-Up",
            font=get_font(size=9),
            bg="#374151",
            fg="#f3f4f6",
            activebackground="#4b5563",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._reset_warmup,
        )
        self.btn_reset_warmup.pack(side="left")
        add_tooltip(self.btn_reset_warmup, "Reset account warm-up progress back to Day 1 with 0 watched time.")

        # Bottom Action Buttons
        actions = tk.Frame(card, bg=card_bg)
        actions.pack(fill="x", pady=(8, 0))

        self.btn_save = tk.Button(
            actions,
            text="Save Settings",
            font=get_font(size=10, weight="bold"),
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.save,
        )
        self.btn_save.pack(side="right", padx=(8, 0))

        btn_cancel = tk.Button(
            actions,
            text="Cancel",
            font=get_font(size=10),
            bg="#374151",
            fg="#e5e7eb",
            activebackground="#4b5563",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.destroy,
        )
        btn_cancel.pack(side="right")

        self._refresh_status_ui()
        self._center_window()

    def _center_window(self) -> None:
        self.update_idletasks()
        w = max(440, self.winfo_reqwidth())
        h = max(460, self.winfo_reqheight())
        try:
            px = self.parent.winfo_rootx()
            py = self.parent.winfo_rooty()
            pw = self.parent.winfo_width()
            ph = self.parent.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        except Exception:
            x = 200
            y = 150
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_values_changed(self) -> None:
        """Dynamically update status banner when input values change."""
        try:
            th = float(self.target_entry.get().strip() or 15.0)
            inc = float(self.inc_entry.get().strip() or 1.0)
            st = float(self.start_entry.get().strip() or 1.0)
            self.cfg.target_hours = max(0.5, th)
            self.cfg.daily_increment_hours = max(0.1, inc)
            self.cfg.start_hours = max(0.1, st)
            self.cfg.enabled = self.enabled_var.get()
        except Exception:
            pass
        self._refresh_status_ui()

    def _refresh_status_ui(self) -> None:
        """Render badge color and status text matching the gradient specifications."""
        info = self.cfg.get_status_info()
        color = info["color"]
        text_fg = info["text_fg"]

        if not self.cfg.enabled:
            self.status_badge.configure(text="Warm-Up Disabled", bg="#4b5563", fg="#ffffff")
            self.status_detail_lbl.configure(text="Warm-up restriction is disabled. Account watches continuous loops normally.")
            return

        if self.cfg.is_warmed_up:
            self.status_badge.configure(text="✅ Fully Warmed Up", bg=color, fg=text_fg)
            self.status_detail_lbl.configure(
                text=f"Target daily capacity ({self.cfg.target_hours:.1f}h) achieved!\nAccount is fully mature."
            )
        else:
            self.status_badge.configure(text=f"🔥 {info['stage_label']}", bg=color, fg=text_fg)
            daily_limit_h = info["daily_allowed_hours"]
            watched_h = info["watched_today_hours"]
            rem_h = max(0.0, daily_limit_h - watched_h)
            self.status_detail_lbl.configure(
                text=f"Today's Quota: {watched_h:.1f}h / {daily_limit_h:.1f}h ({rem_h:.1f}h left)\nTarget Goal: {self.cfg.target_hours:.1f}h daily • Ladder: +{self.cfg.daily_increment_hours:.1f}h/day"
            )

    def _mark_as_warmed_up(self) -> None:
        """Immediately graduate account to fully warmed up."""
        self.cfg.mark_as_warmed_up()
        self._refresh_status_ui()

    def _reset_warmup(self) -> None:
        """Reset account progress back to Day 1."""
        self.cfg.reset_warmup()
        self._refresh_status_ui()

    def save(self) -> None:
        """Validate entries and persist account configuration."""
        try:
            th = float(self.target_entry.get().strip())
            inc = float(self.inc_entry.get().strip())
            st = float(self.start_entry.get().strip())
            if th <= 0 or inc <= 0 or st <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror("Error", "Hours values must be positive numbers greater than 0.")
            return

        self.cfg.target_hours = th
        self.cfg.daily_increment_hours = inc
        self.cfg.start_hours = st
        self.cfg.enabled = self.enabled_var.get()

        self.warmup_mgr.save_account_config(self.cfg)

        if self.on_save_callback:
            try:
                self.on_save_callback()
            except Exception:
                pass
        self.destroy()
