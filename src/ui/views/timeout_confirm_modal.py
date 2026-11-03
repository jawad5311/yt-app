"""Full Video Timeout Confirmation Modal.

Shown when automation starts if 'Full Video Timeout' is configured to any value
other than 0, alerting the user to the maximum watch duration limit per video.
"""

from typing import Optional
import customtkinter as ctk


class FullVideoTimeoutConfirmModal(ctk.CTkToplevel):
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
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=24, pady=(20, 6))

        ctk.CTkLabel(
            header_frame,
            text="⏱️ Full Video Timeout Active",
            font=ctk.CTkFont(size=17, weight="bold"),
            anchor="w",
            text_color=("#D97706", "#F59E0B"),  # Warm amber/warning color
        ).pack(anchor="w")

        # 2. Main Timeout Information
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(fill="x", padx=24, pady=(6, 12))

        ctk.CTkLabel(
            info_frame,
            text=f"Timeout is set to {self.timeout_minutes} minute(s).",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkLabel(
            info_frame,
            text=(
                f"The application will only watch each video for a maximum of {self.timeout_minutes} minute(s) "
                f"before forcibly moving on to the next task."
            ),
            font=ctk.CTkFont(size=12),
            text_color=("gray20", "gray80"),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", pady=(0, 6))

        ctk.CTkLabel(
            info_frame,
            text="(To watch videos for their full duration without any limit, set Full Video Timeout to 0 in Settings > Playback & Video.)",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w")

        # 3. Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(12, 16), side="bottom")

        confirm_btn = ctk.CTkButton(
            btn_frame,
            text="Confirm & Start",
            font=ctk.CTkFont(weight="bold"),
            height=36,
            command=self._on_confirm,
        )
        confirm_btn.pack(side="right", padx=(10, 0))

        cancel_btn = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            height=36,
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray38"),
            text_color=("gray10", "gray90"),
            command=self._on_cancel,
        )
        cancel_btn.pack(side="right")

        # Default focus on confirmation button
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
