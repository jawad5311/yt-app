"""Apple macOS HIG styled themed confirmation dialog for YT Organic Bot.

Replaces standard Win32 / OS native messagebox.askyesno with an elegant,
theme-aware modal dialog adhering to Apple Human Interface Guidelines.
"""
from __future__ import annotations

import os
import tkinter as tk
from typing import Any, Callable, Optional

from . import ttk_adapter as ctk
from .theme import get_font, get_theme_colors


class ThemedConfirmDialog(tk.Toplevel):
    """Themed Apple HIG confirmation modal dialog."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str = "Confirm Action",
        heading: str = "Are you sure?",
        message: str = "Do you want to proceed with this action?",
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        confirm_color: str = "#D32F2F",
        confirm_hover: str = "#9A0007",
        icon: str = "⚠️",
        mode: Optional[str] = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.confirmed: bool = False
        self._mode = mode or self._resolve_mode(parent)

        colors = get_theme_colors(self._mode)
        bg = colors.get("bg", "#161619")
        card_bg = colors.get("card_bg", "#202024")
        card_border = colors.get("card_border", "#323238")
        fg = colors.get("fg", "#ffffff")
        fg_dim = colors.get("fg_dim", "#d4d4d8")
        muted_action = colors.get("muted_action", "#374151")
        muted_action_hover = colors.get("muted_action_hover", "#4B5563")

        self.title(title)
        self.configure(bg=bg)
        self.resizable(False, False)

        try:
            top_level = parent.winfo_toplevel()
            self.transient(top_level)
            self.grab_set()
        except Exception:
            pass

        # Outer card container
        card = tk.Frame(
            self,
            bg=card_bg,
            highlightbackground=card_border,
            highlightthickness=1,
            padx=20,
            pady=18,
        )
        card.pack(fill="both", expand=True, padx=12, pady=12)

        # Header with icon and heading
        header_frame = tk.Frame(card, bg=card_bg)
        header_frame.pack(fill="x", pady=(0, 10))

        icon_lbl = tk.Label(
            header_frame,
            text=icon,
            font=get_font(size=18, weight="bold"),
            bg=card_bg,
            fg=fg,
        )
        icon_lbl.pack(side="left", padx=(0, 10))

        heading_lbl = tk.Label(
            header_frame,
            text=heading,
            font=get_font(size=12, weight="bold"),
            bg=card_bg,
            fg=fg,
            anchor="w",
            justify="left",
        )
        heading_lbl.pack(side="left", fill="x", expand=True)

        # Message body
        msg_lbl = tk.Label(
            card,
            text=message,
            font=get_font(size=9, weight="normal"),
            bg=card_bg,
            fg=fg_dim,
            justify="left",
            anchor="w",
            wraplength=380,
        )
        msg_lbl.pack(fill="x", pady=(0, 18))

        # Bottom Action Bar
        action_bar = tk.Frame(card, bg=card_bg)
        action_bar.pack(fill="x", side="bottom")

        confirm_btn = ctk.CTkButton(
            action_bar,
            text=confirm_text,
            width=110,
            height=32,
            fg_color=confirm_color,
            hover_color=confirm_hover,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_confirm,
        )
        confirm_btn.pack(side="right", padx=(8, 0))

        cancel_btn = ctk.CTkButton(
            action_bar,
            text=cancel_text,
            width=85,
            height=32,
            fg_color=muted_action,
            hover_color=muted_action_hover,
            font=ctk.CTkFont(size=10, weight="normal"),
            command=self._on_cancel,
        )
        cancel_btn.pack(side="right")

        # Key bindings
        self.bind("<Escape>", lambda e: self._on_cancel())
        self.bind("<Return>", lambda e: self._on_confirm())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Positioning & centering
        self.update_idletasks()
        width = 440
        height = max(200, self.winfo_reqheight() + 24)
        try:
            top_level = parent.winfo_toplevel()
            top_level.update_idletasks()
            px = top_level.winfo_rootx()
            py = top_level.winfo_rooty()
            pw = top_level.winfo_width()
            ph = top_level.winfo_height()
            x = px + max(0, (pw - width) // 2)
            y = py + max(0, (ph - height) // 2)
        except Exception:
            x, y = 150, 150

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.focus_set()

    def _resolve_mode(self, parent: tk.Misc) -> str:
        app = getattr(parent, "app_controller", None) or getattr(parent, "master", None)
        mode = getattr(app, "appearance_mode", None)
        return mode if mode in ("Dark", "Light") else "Dark"

    def _on_confirm(self) -> None:
        self.confirmed = True
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.confirmed = False
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def show_themed_confirm_dialog(
    parent: tk.Misc,
    title: str = "Confirm Action",
    heading: str = "Are you sure?",
    message: str = "Do you want to proceed with this action?",
    confirm_text: str = "Confirm",
    cancel_text: str = "Cancel",
    confirm_color: str = "#D32F2F",
    confirm_hover: str = "#9A0007",
    icon: str = "⚠️",
    mode: Optional[str] = None,
) -> bool:
    """Display a modal Apple HIG confirmation dialog and return True if confirmed."""
    dialog = ThemedConfirmDialog(
        parent=parent,
        title=title,
        heading=heading,
        message=message,
        confirm_text=confirm_text,
        cancel_text=cancel_text,
        confirm_color=confirm_color,
        confirm_hover=confirm_hover,
        icon=icon,
        mode=mode,
    )
    try:
        dialog.wait_window()
    except Exception:
        pass
    return getattr(dialog, "confirmed", False)


class ThemedAlertDialog(tk.Toplevel):
    """Themed Apple HIG alert / notification modal dialog (single action button)."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str = "Notification",
        heading: str = "Notice",
        message: str = "",
        button_text: str = "OK",
        button_color: Optional[str] = None,
        button_hover: Optional[str] = None,
        icon: str = "✓",
        mode: Optional[str] = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self._mode = mode or self._resolve_mode(parent)

        colors = get_theme_colors(self._mode)
        bg = colors.get("bg", "#161619")
        card_bg = colors.get("card_bg", "#202024")
        card_border = colors.get("card_border", "#323238")
        fg = colors.get("fg", "#ffffff")
        fg_dim = colors.get("fg_dim", "#d4d4d8")

        # Apple HIG icon color and button color
        if icon == "✓":
            icon_color = colors.get("success", "#34c759" if self._mode == "Light" else "#30d158")
            btn_bg = button_color or colors.get("accent", "#007aff" if self._mode == "Light" else "#0a84ff")
            btn_hover = button_hover or colors.get("accent_hover", "#0062cc" if self._mode == "Light" else "#0071e3")
        elif icon in ("⚠️", "!"):
            icon_color = colors.get("warning", "#ff9500" if self._mode == "Light" else "#ff9f0a")
            btn_bg = button_color or colors.get("accent", "#007aff" if self._mode == "Light" else "#0a84ff")
            btn_hover = button_hover or colors.get("accent_hover", "#0062cc" if self._mode == "Light" else "#0071e3")
        else:  # Error / failure
            icon_color = colors.get("danger", "#ff3b30" if self._mode == "Light" else "#ff453a")
            btn_bg = button_color or colors.get("danger", "#ff3b30" if self._mode == "Light" else "#ff453a")
            btn_hover = button_hover or colors.get("danger_hover", "#d70015")

        self.title(title)
        self.configure(bg=bg)
        self.resizable(False, False)

        try:
            top_level = parent.winfo_toplevel()
            self.transient(top_level)
            self.grab_set()
        except Exception:
            pass

        # Outer card container
        card = tk.Frame(
            self,
            bg=card_bg,
            highlightbackground=card_border,
            highlightthickness=1,
            padx=20,
            pady=18,
        )
        card.pack(fill="both", expand=True, padx=12, pady=12)

        # Header with icon and heading
        header_frame = tk.Frame(card, bg=card_bg)
        header_frame.pack(fill="x", pady=(0, 10))

        icon_lbl = tk.Label(
            header_frame,
            text=icon,
            font=get_font(size=18, weight="bold"),
            bg=card_bg,
            fg=icon_color,
        )
        icon_lbl.pack(side="left", padx=(0, 10))

        heading_lbl = tk.Label(
            header_frame,
            text=heading,
            font=get_font(size=12, weight="bold"),
            bg=card_bg,
            fg=fg,
            anchor="w",
            justify="left",
        )
        heading_lbl.pack(side="left", fill="x", expand=True)

        # Message body
        msg_lbl = tk.Label(
            card,
            text=message,
            font=get_font(size=9, weight="normal"),
            bg=card_bg,
            fg=fg_dim,
            justify="left",
            anchor="w",
            wraplength=380,
        )
        msg_lbl.pack(fill="x", pady=(0, 18))

        # Bottom Action Bar
        action_bar = tk.Frame(card, bg=card_bg)
        action_bar.pack(fill="x", side="bottom")

        ok_btn = ctk.CTkButton(
            action_bar,
            text=button_text,
            width=95,
            height=32,
            fg_color=btn_bg,
            hover_color=btn_hover,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_close,
        )
        self.btn_ok = ok_btn
        ok_btn.pack(side="right")

        # Key bindings
        self.bind("<Escape>", lambda e: self._on_close())
        self.bind("<Return>", lambda e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Positioning & centering
        self.update_idletasks()
        width = 440
        height = max(180, self.winfo_reqheight() + 24)
        try:
            top_level = parent.winfo_toplevel()
            top_level.update_idletasks()
            px = top_level.winfo_rootx()
            py = top_level.winfo_rooty()
            pw = top_level.winfo_width()
            ph = top_level.winfo_height()
            x = px + max(0, (pw - width) // 2)
            y = py + max(0, (ph - height) // 2)
        except Exception:
            x, y = 150, 150

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.focus_set()

    def _resolve_mode(self, parent: tk.Misc) -> str:
        app = getattr(parent, "app_controller", None) or getattr(parent, "master", None)
        mode = getattr(app, "appearance_mode", None)
        return mode if mode in ("Dark", "Light") else "Dark"

    def _on_close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def show_themed_alert_dialog(
    parent: tk.Misc,
    title: str = "Notification",
    heading: str = "Notice",
    message: str = "",
    button_text: str = "OK",
    button_color: Optional[str] = None,
    button_hover: Optional[str] = None,
    icon: str = "✓",
    mode: Optional[str] = None,
) -> ThemedAlertDialog:
    """Display an Apple HIG themed alert modal dialog with a single confirm button."""
    dialog = ThemedAlertDialog(
        parent=parent,
        title=title,
        heading=heading,
        message=message,
        button_text=button_text,
        button_color=button_color,
        button_hover=button_hover,
        icon=icon,
        mode=mode,
    )
    if "PYTEST_CURRENT_TEST" in os.environ:
        dialog.update_idletasks()
        return dialog

    try:
        dialog.wait_window()
    except Exception:
        pass
    return dialog
