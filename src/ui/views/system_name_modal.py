"""System Name Prompt Modal.

Shown on startup when SystemName is missing or can be explicitly triggered by the user.
System Name is compulsory in settings, but users can skip on startup if needed.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..components import Button, Entry, Font, Frame, Label, Toplevel

logger = logging.getLogger(__name__)


class SystemNamePromptModal(Toplevel):
    """Modal dialog prompting the user to set a compulsory System Name on app startup."""

    def __init__(
        self,
        parent,
        current_name: str = "",
        on_save: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.parent_window = parent
        self.on_save = on_save
        self.saved_name: Optional[str] = None
        self.skipped: bool = False

        self.title("Set System Name")
        width, height = 500, 280
        self.geometry(f"{width}x{height}")
        self.minsize(450, 250)
        self.resizable(False, False)

        # Center on parent window or screen
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

        self.protocol("WM_DELETE_WINDOW", self._on_skip)
        self.bind("<Escape>", lambda e: self._on_skip())
        self.bind("<Return>", lambda e: self._on_save())

        self._build_ui(current_name)
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def _build_ui(self, current_name: str) -> None:
        # 1. Header Frame
        header_frame = Frame(self, style="Transparent.TFrame")
        header_frame.pack(fill="x", padx=24, pady=(20, 6))

        Label(
            header_frame,
            text="💻 Set System Name",
            font=Font(size=16, weight="bold"),
            anchor="w",
            style="Header.TLabel",
        ).pack(anchor="w")

        # 2. Information Frame
        info_frame = Frame(self, style="Transparent.TFrame")
        info_frame.pack(fill="x", padx=24, pady=(2, 10))

        Label(
            info_frame,
            text=(
                "A System Name is required to identify this computer in email reports, "
                "daily summaries, and logs. Please enter a unique identifier for this machine."
            ),
            font=Font(size=12),
            anchor="w",
            justify="left",
            wraplength=450,
            style="TLabel",
        ).pack(anchor="w")

        # 3. Input Frame
        input_frame = Frame(self, style="Transparent.TFrame")
        input_frame.pack(fill="x", padx=24, pady=(4, 6))

        Label(
            input_frame,
            text="System Name (Compulsory):",
            font=Font(size=12, weight="bold"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))

        self.name_entry = Entry(input_frame, width=400, placeholder_text="e.g. Worker-PC-01")
        if current_name:
            self.name_entry.insert(0, current_name)
        self.name_entry.pack(fill="x", pady=(0, 4))

        self.error_label = Label(
            input_frame,
            text="",
            font=Font(size=11),
            text_color="#e74c3c",
            anchor="w",
        )
        self.error_label.pack(anchor="w")

        # 4. Action Buttons Frame
        btn_frame = Frame(self, style="Transparent.TFrame")
        btn_frame.pack(fill="x", padx=24, pady=(12, 16), side="bottom")

        save_btn = Button(
            btn_frame,
            text="Save System Name",
            font=Font(weight="bold"),
            style="Primary.TButton",
            command=self._on_save,
        )
        save_btn.pack(side="right", padx=(10, 0))

        skip_btn = Button(
            btn_frame,
            text="Skip for Now",
            style="TButton",
            command=self._on_skip,
        )
        skip_btn.pack(side="right")

        self.name_entry.focus_set()

    def _on_save(self, event=None) -> None:
        val = self.name_entry.get().strip()
        if not val:
            self.error_label.configure(text="System Name cannot be empty. Please enter a name or click Skip.")
            self.name_entry.focus_set()
            return

        self.saved_name = val
        if self.on_save:
            try:
                self.on_save(val)
            except Exception as e:
                logger.error(f"Error in on_save callback: {e}")
        self.destroy()

    def _on_skip(self, event=None) -> None:
        self.skipped = True
        self.destroy()

    def show(self) -> Optional[str]:
        """Show modal synchronously and return the saved name or None if skipped."""
        self.wait_window()
        return self.saved_name
