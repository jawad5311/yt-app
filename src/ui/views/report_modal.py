"""Pop-up modal shown when session is stopped or finished to indicate report generation."""
from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Callable, Optional

from ..components import Button, Font, Frame, Label, ProgressBar, Toplevel


class ReportProgressModal(Toplevel):
    """Modal dialog displaying report generation progress and disallowing exit until delivered."""

    def __init__(
        self,
        parent,
        title: str = "Generating Report",
        message: str = "Generating report, do not close the app.",
        on_close_callback: Optional[Callable[[], None]] = None,
        auto_hide_seconds: Optional[float] = None,
    ):
        super().__init__(parent)
        self.parent_window = parent
        self.on_close_callback = on_close_callback
        self.is_generating: bool = True
        self._auto_hide_id: Optional[str] = None

        self.title("Session Report")
        self.geometry("480x250")
        self.minsize(440, 230)
        self.resizable(False, False)

        try:
            self.transient(parent)
        except Exception:
            pass

        # Center over parent
        try:
            parent.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            if pw > 100 and ph > 100:
                x = px + max(0, (pw - 480) // 2)
                y = py + max(0, (ph - 250) // 2)
                self.geometry(f"+{x}+{y}")
        except Exception:
            pass

        try:
            self.protocol("WM_DELETE_WINDOW", self._on_window_delete)
        except Exception:
            pass

        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui(title, message)
        if auto_hide_seconds is not None and auto_hide_seconds > 0:
            self.schedule_auto_hide(auto_hide_seconds)

    def _build_ui(self, title: str, message: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.card = Frame(self, style="Card.TFrame")
        self.card.pack(fill="both", expand=True, padx=16, pady=16)

        self.title_label = Label(
            self.card,
            text=f"⏳ {title}",
            font=Font(size=18, weight="bold"),
            style="Card.TLabel",
        )
        self.title_label.pack(pady=(20, 8), padx=20)

        self.msg_label = Label(
            self.card,
            text=message,
            font=Font(size=13),
            wraplength=380,
            justify="center",
            style="Card.TLabel",
        )
        self.msg_label.pack(pady=(0, 15), padx=20)

        self.progress_bar = ProgressBar(self.card, mode="indeterminate", width=360)
        self.progress_bar.pack(pady=(0, 10), padx=20)
        try:
            self.progress_bar.start()
        except Exception:
            pass

        self.action_btn = Button(
            self.card,
            text="OK",
            font=Font(size=14, weight="bold"),
            style="Success.TButton",
            width=120,
            command=self.close_modal,
        )

    def schedule_auto_hide(self, seconds: float = 2.0) -> None:
        """Schedule the dialog to temporarily withdraw so the user can interact with the app."""
        if getattr(self, "_auto_hide_id", None):
            try:
                self.after_cancel(self._auto_hide_id)
            except Exception:
                pass
        self._auto_hide_id = self.after(int(seconds * 1000), self.hide_temporarily)

    def hide_temporarily(self) -> None:
        """Release modal grab and withdraw window during report generation."""
        self._auto_hide_id = None
        if not getattr(self, "is_generating", False):
            return
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.withdraw()
        except Exception:
            pass

    def set_completed(
        self,
        success: bool = True,
        title: Optional[str] = None,
        message: Optional[str] = None,
        auto_close: bool = False,
    ) -> None:
        """Update modal state once report generation and email dispatch finishes."""
        if getattr(self, "_auto_hide_id", None):
            try:
                self.after_cancel(self._auto_hide_id)
            except Exception:
                pass
            self._auto_hide_id = None

        self.is_generating = False
        try:
            self.progress_bar.stop()
            self.progress_bar.pack_forget()
        except Exception:
            pass

        if success:
            t = title or "Report Sent Successfully!"
            self.title_label.configure(text=f"✅ {t}")
            m = message or "The session report has been sent to your email.\nYou may now safely close the app."
        else:
            t = title or "Session Completed"
            self.title_label.configure(text=f"ℹ️ {t}")
            m = message or "Session wrap-up finished.\nYou may now close the app."

        self.msg_label.configure(text=m)

        # Re-surface the dialog window if it was temporarily hidden
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass

        if not auto_close:
            self.action_btn.pack(pady=(12, 10))

    def _on_window_delete(self) -> None:
        if getattr(self.parent_window, "is_closing", False) is True:
            return
        if self.is_generating:
            try:
                from tkinter import messagebox
                messagebox.showwarning(
                    "Please Wait",
                    "Session report is currently generating.\nPlease do not close the app until the email has been sent.",
                    parent=self,
                )
            except Exception:
                pass
            return
        self.close_modal()

    def close_modal(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        if self.on_close_callback:
            try:
                self.on_close_callback()
            except Exception:
                pass
