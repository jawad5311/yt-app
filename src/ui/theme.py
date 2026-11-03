"""TTK Theme and styling system for YT Organic Bot.

Provides consistent, dark and light mode themes using standard ttk.Style
with the 'clam' engine, reproducing the modern card-based desktop interface.
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any, Dict, Tuple

DARK_THEME: Dict[str, str] = {
    "bg": "#1e1e1e",
    "sidebar_bg": "#252526",
    "card_bg": "#2d2d30",
    "card_border": "#3e3e42",
    "fg": "#ffffff",
    "fg_dim": "#cccccc",
    "fg_muted": "#8c8c8c",
    "accent": "#1f6aa5",
    "accent_hover": "#144870",
    "accent_active": "#0f3655",
    "nav_hover": "#333333",
    "nav_active": "#1f6aa5",
    "success": "#2e7d32",
    "success_hover": "#1b5e20",
    "danger": "#d32f2f",
    "danger_hover": "#9a0007",
    "warning": "#f57c00",
    "warning_hover": "#b26a00",
    "input_bg": "#3c3c3c",
    "input_fg": "#ffffff",
    "select_bg": "#1f6aa5",
    "select_fg": "#ffffff",
    "trough": "#252526",
    "scrollbar_thumb": "#4e4e52",
    "scrollbar_hover": "#68686d",
    "tab_bg": "#252526",
    "tab_selected_bg": "#2d2d30",
}

LIGHT_THEME: Dict[str, str] = {
    "bg": "#f3f3f3",
    "sidebar_bg": "#e8e8e8",
    "card_bg": "#ffffff",
    "card_border": "#dcdcdc",
    "fg": "#1f1f1f",
    "fg_dim": "#444444",
    "fg_muted": "#777777",
    "accent": "#1f6aa5",
    "accent_hover": "#144870",
    "accent_active": "#0f3655",
    "nav_hover": "#dddddd",
    "nav_active": "#1f6aa5",
    "success": "#2e7d32",
    "success_hover": "#1b5e20",
    "danger": "#d32f2f",
    "danger_hover": "#9a0007",
    "warning": "#f57c00",
    "warning_hover": "#b26a00",
    "input_bg": "#ffffff",
    "input_fg": "#1f1f1f",
    "select_bg": "#1f6aa5",
    "select_fg": "#ffffff",
    "trough": "#e0e0e0",
    "scrollbar_thumb": "#b0b0b0",
    "scrollbar_hover": "#8e8e8e",
    "tab_bg": "#e0e0e0",
    "tab_selected_bg": "#ffffff",
}

_current_mode: str = "Dark"


def get_theme_colors(mode: str = "Dark") -> Dict[str, str]:
    return DARK_THEME if mode.lower() == "dark" else LIGHT_THEME


def get_default_font_family() -> str:
    if sys.platform == "win32":
        return "Segoe UI"
    elif sys.platform == "darwin":
        return "SF Pro Text"
    return "Helvetica"


def get_font(size: int = 11, weight: str = "normal", slant: str = "roman") -> Tuple[str, int, str]:
    family = get_default_font_family()
    weight_str = "bold" if "bold" in str(weight).lower() else "normal"
    if slant and "italic" in str(slant).lower():
        return (family, size, f"{weight_str} italic")
    return (family, size, weight_str)


def setup_ttk_styles(root: tk.Misc, mode: str = "Dark") -> ttk.Style:
    """Configures the global TTK styles on root matching the mode (Dark or Light)."""
    global _current_mode
    _current_mode = mode if mode.lower() in ("dark", "light") else "Dark"
    colors = get_theme_colors(_current_mode)
    family = get_default_font_family()

    style = ttk.Style(root)
    # Use clam as the cross-platform base theme
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Root background
    if hasattr(root, "configure"):
        try:
            root.configure(background=colors["bg"])
        except Exception:
            pass

    # Generic elements
    style.configure(".", background=colors["bg"], foreground=colors["fg"], font=(family, 10))

    # Frames
    style.configure("TFrame", background=colors["bg"])
    style.configure("Sidebar.TFrame", background=colors["sidebar_bg"])
    style.configure("Card.TFrame", background=colors["card_bg"], relief="flat", borderwidth=1)
    style.configure("CardInner.TFrame", background=colors["card_bg"])
    style.configure("Transparent.TFrame", background=colors["bg"])
    style.configure("Action.TFrame", background=colors["card_bg"])

    # Labels
    style.configure("TLabel", background=colors["bg"], foreground=colors["fg"], font=(family, 10))
    style.configure("Card.TLabel", background=colors["card_bg"], foreground=colors["fg"], font=(family, 10))
    style.configure("Sidebar.TLabel", background=colors["sidebar_bg"], foreground=colors["fg"], font=(family, 10))
    style.configure("Header.TLabel", background=colors["bg"], foreground=colors["fg"], font=(family, 16, "bold"))
    style.configure("Subheader.TLabel", background=colors["bg"], foreground=colors["fg"], font=(family, 12, "bold"))
    style.configure("CardTitle.TLabel", background=colors["card_bg"], foreground=colors["fg_muted"], font=(family, 10))
    style.configure("CardValue.TLabel", background=colors["card_bg"], foreground=colors["fg"], font=(family, 16, "bold"))
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["fg_muted"], font=(family, 9))
    style.configure("CardMuted.TLabel", background=colors["card_bg"], foreground=colors["fg_muted"], font=(family, 9))
    style.configure("Error.TLabel", background=colors["bg"], foreground=colors["danger"], font=(family, 10))
    style.configure("Success.TLabel", background=colors["bg"], foreground=colors["success"], font=(family, 10))
    style.configure("Warning.TLabel", background=colors["bg"], foreground=colors["warning"], font=(family, 10))

    # Base Buttons
    style.configure(
        "TButton",
        background=colors["card_bg"],
        foreground=colors["fg"],
        font=(family, 10),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("active", colors["accent_hover"]), ("pressed", colors["accent_active"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Primary Action Button
    style.configure(
        "Primary.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        font=(family, 11, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(12, 7),
    )
    style.map(
        "Primary.TButton",
        background=[("active", colors["accent_hover"]), ("pressed", colors["accent_active"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Danger Button
    style.configure(
        "Danger.TButton",
        background=colors["danger"],
        foreground="#ffffff",
        font=(family, 10, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "Danger.TButton",
        background=[("active", colors["danger_hover"]), ("pressed", colors["danger_hover"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Success Button
    style.configure(
        "Success.TButton",
        background=colors["success"],
        foreground="#ffffff",
        font=(family, 10, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "Success.TButton",
        background=[("active", colors["success_hover"]), ("pressed", colors["success_hover"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Warning Button
    style.configure(
        "Warning.TButton",
        background=colors["warning"],
        foreground="#ffffff",
        font=(family, 10, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "Warning.TButton",
        background=[("active", colors["warning_hover"]), ("pressed", colors["warning_hover"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Sidebar Nav Buttons
    style.configure(
        "Nav.TButton",
        background=colors["sidebar_bg"],
        foreground=colors["fg_dim"],
        font=(family, 11),
        borderwidth=0,
        focuscolor="none",
        anchor="w",
        relief="flat",
        padding=(14, 8),
    )
    style.map(
        "Nav.TButton",
        background=[("active", colors["nav_hover"]), ("pressed", colors["nav_hover"])],
        foreground=[("active", colors["fg"]), ("pressed", colors["fg"])],
    )

    style.configure(
        "NavActive.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        font=(family, 11, "bold"),
        borderwidth=0,
        focuscolor="none",
        anchor="w",
        relief="flat",
        padding=(14, 8),
    )
    style.map(
        "NavActive.TButton",
        background=[("active", colors["accent_hover"]), ("pressed", colors["accent_active"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Small Action Buttons
    style.configure(
        "Small.TButton",
        background=colors["card_bg"],
        foreground=colors["fg"],
        font=(family, 9),
        borderwidth=0,
        relief="flat",
        padding=(6, 3),
    )
    style.configure(
        "SmallDanger.TButton",
        background=colors["danger"],
        foreground="#ffffff",
        font=(family, 9, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(6, 3),
    )
    style.map("SmallDanger.TButton", background=[("active", colors["danger_hover"])])
    style.configure(
        "SmallPrimary.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        font=(family, 9, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(6, 3),
    )
    style.map("SmallPrimary.TButton", background=[("active", colors["accent_hover"])])
    style.configure(
        "SmallSuccess.TButton",
        background=colors["success"],
        foreground="#ffffff",
        font=(family, 9, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(6, 3),
    )
    style.map("SmallSuccess.TButton", background=[("active", colors["success_hover"])])

    # Entries
    style.configure(
        "TEntry",
        fieldbackground=colors["input_bg"],
        foreground=colors["input_fg"],
        insertcolor=colors["input_fg"],
        font=(family, 10),
        relief="flat",
        padding=(6, 4),
    )

    # Checkbuttons
    style.configure(
        "TCheckbutton",
        background=colors["bg"],
        foreground=colors["fg"],
        font=(family, 10),
        focuscolor="none",
    )
    style.map(
        "TCheckbutton",
        background=[("active", colors["bg"])],
        foreground=[("active", colors["fg"])],
    )
    style.configure(
        "Card.TCheckbutton",
        background=colors["card_bg"],
        foreground=colors["fg"],
        font=(family, 10),
        focuscolor="none",
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", colors["card_bg"])],
        foreground=[("active", colors["fg"])],
    )

    # Comboboxes
    style.configure(
        "TCombobox",
        fieldbackground=colors["input_bg"],
        background=colors["card_bg"],
        foreground=colors["input_fg"],
        arrowcolor=colors["fg"],
        font=(family, 10),
        relief="flat",
        padding=(4, 4),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", colors["input_bg"])],
        foreground=[("readonly", colors["input_fg"])],
        selectbackground=[("readonly", colors["select_bg"])],
        selectforeground=[("readonly", colors["select_fg"])],
    )

    # Notebook & Tabs
    style.configure(
        "TNotebook",
        background=colors["bg"],
        tabmargins=[2, 4, 2, 0],
        borderwidth=0,
    )
    style.configure(
        "TNotebook.Tab",
        background=colors["tab_bg"],
        foreground=colors["fg_dim"],
        font=(family, 10, "bold"),
        padding=[14, 8],
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["accent"]), ("active", colors["card_bg"])],
        foreground=[("selected", "#ffffff"), ("active", colors["fg"])],
    )

    # Progressbar
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=colors["trough"],
        background=colors["accent"],
        borderwidth=0,
        thickness=8,
    )
    style.configure(
        "Success.Horizontal.TProgressbar",
        troughcolor=colors["trough"],
        background=colors["success"],
        borderwidth=0,
        thickness=8,
    )

    # Scrollbars
    style.configure(
        "Vertical.TScrollbar",
        troughcolor=colors["trough"],
        background=colors["scrollbar_thumb"],
        arrowcolor=colors["fg_muted"],
        relief="flat",
        borderwidth=0,
        width=10,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", colors["scrollbar_hover"])],
    )

    return style
