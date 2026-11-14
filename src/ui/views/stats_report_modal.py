"""Send Channel Stats Report Modal.

Provides a theme-aligned Apple modal dialog to:
- Confirm or customize the recipient email address.
- Display the report scope (All Channels vs Single Channel) and timeframe.
- Dispatch the report in a non-blocking background thread with immediate UI feedback.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from ..components import Button, Entry, Font, Frame, Label, Toplevel
from ..tooltip import add_tooltip


class SendStatsReportModal(Toplevel):
    """Modal dialog prompting the recipient email and sending channel stats report."""

    def __init__(
        self,
        parent,
        app_controller: Any,
        stats: Dict[str, Any],
        selected_channel: str = "All Channels",
        time_filter: str = "All Time",
        queued_videos: Optional[List[Any]] = None,
        records: Optional[Dict[str, Any]] = None,
        on_success_callback: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.parent_view = parent
        self.app_controller = app_controller
        self.stats = stats
        self.selected_channel = selected_channel
        self.time_filter = time_filter
        self.queued_videos = queued_videos or []
        self.records = records or {}
        self.on_success_callback = on_success_callback

        self.is_sending: bool = False

        self.title("Send Channel Stats Report")
        width, height = 520, 340
        self.geometry(f"{width}x{height}")
        self.minsize(480, 320)
        self.resizable(False, False)

        # Center on parent window or screen
        try:
            self.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            if pw > 100 and ph > 100:
                x = px + max(0, (pw - width) // 2)
                y = py + max(0, (ph - height) // 2)
                self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda e: self._on_close())
        self.bind("<Return>", lambda e: self._on_send_click())

        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Main Card
        self.card = Frame(self, style="AppleCard.TFrame")
        self.card.pack(fill="both", expand=True, padx=16, pady=16)

        inner = Frame(self.card, style="AppleCardInner.TFrame")
        inner.pack(fill="both", expand=True, padx=20, pady=20)

        # Header Title & Subtitle
        Label(
            inner,
            text="✉️  Send Channel Stats Report",
            font=Font(size=16, weight="bold"),
            style="AppleHeader.TLabel",
        ).pack(anchor="w")

        is_single = bool(self.selected_channel and self.selected_channel != "All Channels")
        scope_text = f"Single Channel: {self.selected_channel}" if is_single else f"All Channels ({self.stats.get('total_channels_count', len(self.stats.get('channels', [])))} channels)"

        sub_lbl = Label(
            inner,
            text=f"Delivering analytics report for {scope_text} • Timeframe: {self.time_filter}",
            font=Font(size=9),
            style="AppleCardMuted.TLabel",
        )
        sub_lbl.pack(anchor="w", pady=(2, 14))

        # Scope & Summary Pill Box
        info_box = Frame(inner, style="AppleInset.TFrame")
        info_box.pack(fill="x", pady=(0, 16))

        info_inner = Frame(info_box, style="AppleCardInner.TFrame")
        info_inner.pack(fill="x", padx=14, pady=10)

        watch_val = self.stats.get("total_watch_formatted", "0s")
        if is_single:
            ch_meta = next((c for c in self.stats.get("channels", []) if c.get("name", "").strip().lower() == self.selected_channel.strip().lower()), None)
            if ch_meta:
                watch_val = ch_meta.get("watch_formatted", "0s")

        Label(
            info_inner,
            text=f"📊 Report Scope: {scope_text}",
            font=Font(size=9, weight="bold"),
            style="AppleCard.TLabel",
        ).pack(anchor="w")

        Label(
            info_inner,
            text=f"⏱ Recorded Watch Time: {watch_val}  ·  Filter: {self.time_filter}",
            font=Font(size=8),
            style="AppleCardMuted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # Recipient Email Input
        Label(
            inner,
            text="Recipient Email Address:",
            font=Font(size=9, weight="bold"),
            style="AppleCard.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        from ...automation.email_reporter import DEFAULT_ADMIN_RECIPIENT_EMAIL, resolve_report_recipients
        config_mgr = getattr(self.app_controller, "config_mgr", None)
        default_recipient = resolve_report_recipients(config_mgr)[0] if config_mgr else DEFAULT_ADMIN_RECIPIENT_EMAIL

        self.email_entry = Entry(inner, width=420)
        self.email_entry.pack(fill="x", pady=(0, 8))
        self.email_entry.insert(0, default_recipient)
        self.email_entry.focus_set()

        # Status / Feedback Label
        self.status_lbl = Label(
            inner,
            text="",
            font=Font(size=8, weight="bold"),
            style="AppleCardMuted.TLabel",
        )
        self.status_lbl.pack(anchor="w", pady=(0, 12))

        # Action Buttons
        btn_box = Frame(inner, style="Transparent.TFrame")
        btn_box.pack(fill="x", side="bottom")

        self.cancel_btn = Button(
            btn_box,
            text="Cancel",
            font=Font(size=9),
            style="AppleDefault.TButton",
            width=90,
            cursor="hand2",
            command=self._on_close,
        )
        self.cancel_btn.pack(side="right", padx=(8, 0))

        self.send_btn = Button(
            btn_box,
            text="✉️ Send Report",
            font=Font(size=9, weight="bold"),
            style="ApplePrimary.TButton",
            width=130,
            cursor="hand2",
            command=self._on_send_click,
        )
        self.send_btn.pack(side="right")
        add_tooltip(self.send_btn, "Dispatch report email to the specified recipient.")

    def _on_send_click(self) -> None:
        if self.is_sending:
            return

        recipient = self.email_entry.get().strip()
        if not recipient or "@" not in recipient or "." not in recipient:
            self.status_lbl.configure(text="⚠️ Please enter a valid email address.", text_color="#EF4444")
            return

        self.is_sending = True
        self.send_btn.configure(text="⏳ Sending...", state="disabled")
        self.cancel_btn.configure(state="disabled")
        self.status_lbl.configure(text=f"Connecting and sending report to {recipient}...", text_color="#3B82F6")

        threading.Thread(
            target=self._dispatch_background,
            args=(recipient,),
            daemon=True,
        ).start()

    def _dispatch_background(self, recipient: str) -> None:
        from ...automation.stats_reporter import send_channel_stats_report
        try:
            ok, msg = send_channel_stats_report(
                app_controller=self.app_controller,
                stats=self.stats,
                selected_channel=self.selected_channel,
                recipient_email=recipient,
                time_filter=self.time_filter,
                queued_videos=self.queued_videos,
                records=self.records,
            )
        except Exception as exc:
            ok, msg = False, str(exc)

        self.after(0, lambda: self._on_dispatch_finished(ok, msg, recipient))

    def _on_dispatch_finished(self, ok: bool, msg: str, recipient: str) -> None:
        self.is_sending = False
        self.cancel_btn.configure(state="normal")

        if ok:
            self.status_lbl.configure(text=f"✅ {msg}", text_color="#10B981")
            self.send_btn.configure(text="Sent!", state="disabled")
            self.cancel_btn.configure(text="Close")
            if self.on_success_callback:
                try:
                    self.on_success_callback(recipient)
                except Exception:
                    pass
        else:
            self.status_lbl.configure(text=f"❌ {msg}", text_color="#EF4444")
            self.send_btn.configure(text="✉️ Send Report", state="normal")

    def _on_close(self) -> None:
        if self.is_sending:
            return
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
