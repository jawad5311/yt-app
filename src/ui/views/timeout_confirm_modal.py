"""Full Video Timeout Confirmation Modal.

Shown when automation starts if 'Full Video Timeout' is configured to any value
other than 0, alerting the user to the maximum watch duration limit per video.
"""
from __future__ import annotations

from typing import Optional

from ..components import Button, Font, Frame, Label, Toplevel


class FullVideoTimeoutConfirmModal(Toplevel):
    """Modal dialog to warn user of active Full Video Timeout before automation begins."""

    def __init__(self, parent, timeout_minutes: int):
        super().__init__(parent)
        self.parent_window = parent
        self.timeout_minutes = timeout_minutes
        self.confirmed = False

        self.title("Full Video Timeout Notice")
        width, height = 480, 260
        self.geometry(f"{width}x{height}")
        self.minsize(440, 240)
        self.resizable(False, False)

        # Center on parent window
        self.update_idletasks()
        try:
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = parent.winfo_width()
            parent_h = parent.winfo_height()
            x = max(0, parent_x + (parent_w - width) // 2)
            y = max(0, parent_y + (parent_h - height) // 2)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.geometry(f"{width}x{height}")

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda e: self._on_cancel())
        self.bind("<Return>", lambda e: self._on_confirm())

        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # 1. Header Frame with Warning Icon
        header_frame = Frame(self, style="Transparent.TFrame")
        header_frame.pack(fill="x", padx=24, pady=(20, 6))

        Label(
            header_frame,
            text="⏱️ Full Video Timeout Active",
            font=Font(size=17, weight="bold"),
            anchor="w",
            style="Warning.TLabel",
        ).pack(anchor="w")

        # 2. Main Timeout Information
        info_frame = Frame(self, style="Transparent.TFrame")
        info_frame.pack(fill="x", padx=24, pady=(6, 12))

        Label(
            info_frame,
            text=f"Timeout is set to {self.timeout_minutes} minute(s).",
            font=Font(size=14, weight="bold"),
            anchor="w",
            justify="left",
            style="Header.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        Label(
            info_frame,
            text=(
                f"The application will only watch each video for a maximum of {self.timeout_minutes} minute(s) "
                f"before forcibly moving on to the next task."
            ),
            font=Font(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
            style="TLabel",
        ).pack(anchor="w", pady=(0, 6))

        Label(
            info_frame,
            text="(To watch videos for their full duration without any limit, set Full Video Timeout to 0 in Settings > Playback & Video.)",
            font=Font(size=11),
            style="Muted.TLabel",
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w")

        # 3. Action Buttons
        btn_frame = Frame(self, style="Transparent.TFrame")
        btn_frame.pack(fill="x", padx=24, pady=(12, 16), side="bottom")

        confirm_btn = Button(
            btn_frame,
            text="Confirm & Start",
            font=Font(weight="bold"),
            style="Primary.TButton",
            command=self._on_confirm,
        )
        confirm_btn.pack(side="right", padx=(10, 0))

        cancel_btn = Button(
            btn_frame,
            text="Cancel",
            style="TButton",
            command=self._on_cancel,
        )
        cancel_btn.pack(side="right")

        confirm_btn.focus_set()

    def _on_confirm(self):
        self.confirmed = True
        self.destroy()

    def _on_cancel(self):
        self.confirmed = False
        self.destroy()

    def show(self) -> bool:
        """Show modal synchronously and return True if user confirmed, False if cancelled."""
        self.wait_window()
        return self.confirmed
