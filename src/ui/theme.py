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
    "bg": "#131316",
    "sidebar_bg": "#0d0d10",
    "card_bg": "#1c1c20",
    "card_border": "#282830",
    "fg": "#f4f4f5",
    "fg_dim": "#d4d4d8",
    "fg_muted": "#9ca3af",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_active": "#1e40af",
    "nav_hover": "#1c1c20",
    "nav_active": "#2563eb",
    "success": "#16a34a",
    "success_hover": "#15803d",
    "danger": "#dc2626",
    "danger_hover": "#b91c1c",
    "warning": "#d97706",
    "warning_hover": "#b45309",
    "teal": "#0d9488",
    "teal_hover": "#0f766e",
    "muted_action": "#2c2c34",
    "muted_action_hover": "#3f3f4a",
    "input_bg": "#1c1c20",
    "input_fg": "#f4f4f5",
    "select_bg": "#2563eb",
    "select_fg": "#ffffff",
    "trough": "#131316",
    "scrollbar_thumb": "#3b3b44",
    "scrollbar_hover": "#52525e",
    "tab_bg": "#18181c",
    "tab_selected_bg": "#2563eb",
}

LIGHT_THEME: Dict[str, str] = {
    "bg": "#f3f3f5",
    "sidebar_bg": "#e8e8ec",
    "card_bg": "#ffffff",
    "card_border": "#dcdce0",
    "fg": "#1f1f23",
    "fg_dim": "#44444a",
    "fg_muted": "#777780",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_active": "#1e40af",
    "nav_hover": "#dddddf",
    "nav_active": "#2563eb",
    "success": "#16a34a",
    "success_hover": "#15803d",
    "danger": "#dc2626",
    "danger_hover": "#b91c1c",
    "warning": "#d97706",
    "warning_hover": "#b45309",
    "teal": "#0d9488",
    "teal_hover": "#0f766e",
    "muted_action": "#e2e2e8",
    "muted_action_hover": "#d1d1d8",
    "input_bg": "#ffffff",
    "input_fg": "#1f1f23",
    "select_bg": "#2563eb",
    "select_fg": "#ffffff",
    "trough": "#e8e8ec",
    "scrollbar_thumb": "#b0b0b8",
    "scrollbar_hover": "#8e8e96",
    "tab_bg": "#e0e0e5",
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


def get_font(size: Any = 9, weight: str = "normal", slant: str = "roman") -> Tuple[str, int, str]:
    family = get_default_font_family()
    weight_str = "bold" if "bold" in str(weight).lower() else "normal"
    try:
        int_size = int(round(float(size)))
    except (ValueError, TypeError):
        int_size = 9
    if slant and "italic" in str(slant).lower():
        return (family, int_size, f"{weight_str} italic")
    return (family, int_size, weight_str)


def setup_ttk_styles(root: tk.Misc, mode: str = "Dark") -> ttk.Style:
    """Configures the global TTK styles on root matching the mode (Dark or Light)."""
    global _current_mode
    _current_mode = mode if mode.lower() in ("dark", "light") else "Dark"
    colors = get_theme_colors(_current_mode)
    family = get_default_font_family()

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    if hasattr(root, "configure"):
        try:
            root.configure(background=colors["bg"])
        except Exception:
            pass

    # Generic elements
    style.configure(".", background=colors["bg"], foreground=colors["fg"], font=(family, 9))

    # Frames
    style.configure("TFrame", background=colors["bg"])
    style.configure("Sidebar.TFrame", background=colors["sidebar_bg"])
    style.configure("Card.TFrame", background=colors["card_bg"], relief="flat", borderwidth=0)
    style.configure("CardInner.TFrame", background=colors["card_bg"])
    style.configure("Transparent.TFrame", background=colors["bg"])
    style.configure("Action.TFrame", background=colors["card_bg"])

    # Labels
    style.configure("TLabel", background=colors["bg"], foreground=colors["fg"], font=(family, 9))
    style.configure("Card.TLabel", background=colors["card_bg"], foreground=colors["fg"], font=(family, 9))
    style.configure("Sidebar.TLabel", background=colors["sidebar_bg"], foreground=colors["fg"], font=(family, 9))
    style.configure("Header.TLabel", background=colors["bg"], foreground=colors["fg"], font=(family, 14, "bold"))
    style.configure("Subheader.TLabel", background=colors["bg"], foreground=colors["fg"], font=(family, 11, "bold"))
    style.configure("CardTitle.TLabel", background=colors["card_bg"], foreground=colors["fg_muted"], font=(family, 8, "bold"))
    style.configure("CardValue.TLabel", background=colors["card_bg"], foreground=colors["fg"], font=(family, 13, "bold"))
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["fg_muted"], font=(family, 8))
    style.configure("CardMuted.TLabel", background=colors["card_bg"], foreground=colors["fg_muted"], font=(family, 8))
    style.configure("Error.TLabel", background=colors["bg"], foreground=colors["danger"], font=(family, 9))
    style.configure("Success.TLabel", background=colors["bg"], foreground=colors["success"], font=(family, 9))
    style.configure("Warning.TLabel", background=colors["bg"], foreground=colors["warning"], font=(family, 9))

    # Base Button
    style.configure(
        "TButton",
        background=colors["muted_action"],
        foreground=colors["fg"],
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(7, 3),
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
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(8, 3),
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
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(7, 3),
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
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(7, 3),
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
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(7, 3),
    )
    style.map(
        "Warning.TButton",
        background=[("active", colors["warning_hover"]), ("pressed", colors["warning_hover"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Teal Button
    style.configure(
        "Teal.TButton",
        background=colors.get("teal", "#0d9488"),
        foreground="#ffffff",
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(7, 3),
    )
    style.map(
        "Teal.TButton",
        background=[("active", colors.get("teal_hover", "#0f766e")), ("pressed", "#115e59")],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Muted Action Button (Up/Down buttons, subtle secondary icons)
    style.configure(
        "MutedAction.TButton",
        background=colors.get("muted_action", "#2c2c34"),
        foreground="#d4d4d8",
        font=(family, 8, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(4, 2),
    )
    style.map(
        "MutedAction.TButton",
        background=[("active", colors.get("muted_action_hover", "#3f3f4a")), ("pressed", "#1a1a20")],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Segmented Tab Buttons (Pill styling)
    style.configure(
        "Segmented.TButton",
        background="#222226",
        foreground=colors["fg_dim"],
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(10, 3),
    )
    style.map(
        "Segmented.TButton",
        background=[("active", "#32323a"), ("pressed", "#222226")],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    style.configure(
        "SegmentedActive.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        relief="flat",
        padding=(10, 3),
    )
    style.map(
        "SegmentedActive.TButton",
        background=[("active", colors["accent_hover"]), ("pressed", colors["accent_active"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Sidebar Nav Buttons
    style.configure(
        "Nav.TButton",
        background=colors["sidebar_bg"],
        foreground=colors["fg_dim"],
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        anchor="w",
        relief="flat",
        padding=(12, 5),
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
        font=(family, 9, "bold"),
        borderwidth=0,
        focuscolor="none",
        anchor="w",
        relief="flat",
        padding=(12, 5),
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
        font=(family, 8, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(5, 2),
    )
    style.configure(
        "SmallDanger.TButton",
        background=colors["danger"],
        foreground="#ffffff",
        font=(family, 8, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(5, 2),
    )
    style.map("SmallDanger.TButton", background=[("active", colors["danger_hover"])])
    style.configure(
        "SmallPrimary.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        font=(family, 8, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(5, 2),
    )
    style.map("SmallPrimary.TButton", background=[("active", colors["accent_hover"])])
    style.configure(
        "SmallSuccess.TButton",
        background=colors["success"],
        foreground="#ffffff",
        font=(family, 8, "bold"),
        borderwidth=0,
        relief="flat",
        padding=(5, 2),
    )
    style.map("SmallSuccess.TButton", background=[("active", colors["success_hover"])])

    # Entries
    style.configure(
        "TEntry",
        fieldbackground=colors["input_bg"],
        foreground=colors["input_fg"],
        insertcolor=colors["input_fg"],
        font=(family, 9),
        relief="flat",
        padding=(5, 3),
    )

    # Modern Checkbuttons
    style.configure(
        "TCheckbutton",
        background=colors["bg"],
        foreground=colors["fg"],
        font=(family, 9),
        focuscolor="none",
        indicatorsize=13,
        indicatormargin=(1, 1, 5, 1),
        indicatorbackground="#1c1c20",
        indicatorforeground="#2563eb",
        upperbordercolor="#3b3b44",
        lowerbordercolor="#3b3b44",
    )
    style.map(
        "TCheckbutton",
        background=[("active", colors["bg"])],
        foreground=[("active", colors["fg"])],
        indicatorbackground=[("selected", colors["accent"]), ("active", "#282830")],
        indicatorforeground=[("selected", "#ffffff"), ("active", colors["accent"])],
    )
    style.configure(
        "Card.TCheckbutton",
        background=colors["card_bg"],
        foreground=colors["fg"],
        font=(family, 9),
        focuscolor="none",
        indicatorsize=13,
        indicatormargin=(1, 1, 5, 1),
        indicatorbackground="#24242a",
        indicatorforeground="#2563eb",
        upperbordercolor="#3b3b44",
        lowerbordercolor="#3b3b44",
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", colors["card_bg"])],
        foreground=[("active", colors["fg"])],
        indicatorbackground=[("selected", colors["accent"]), ("active", "#303038")],
        indicatorforeground=[("selected", "#ffffff"), ("active", colors["accent"])],
    )

    # Comboboxes
    style.configure(
        "TCombobox",
        fieldbackground=colors["input_bg"],
        background=colors["card_bg"],
        foreground=colors["input_fg"],
        arrowcolor=colors["fg"],
        font=(family, 9),
        relief="flat",
        padding=(4, 3),
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
        font=(family, 9, "bold"),
        padding=[12, 5],
        borderwidth=0,
        lightcolor=colors["bg"],
        darkcolor=colors["bg"],
        bordercolor=colors["card_border"],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["accent"]), ("active", colors["card_bg"])],
        foreground=[("selected", "#ffffff"), ("active", colors["fg"])],
        bordercolor=[("selected", colors["accent"]), ("active", colors["card_border"])],
        lightcolor=[("selected", colors["accent"]), ("active", colors["bg"])],
        darkcolor=[("selected", colors["accent"]), ("active", colors["bg"])],
    )

    # Flat Progressbars
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=colors["trough"],
        background=colors["accent"],
        bordercolor=colors["card_border"],
        lightcolor=colors["accent"],
        darkcolor=colors["accent"],
        borderwidth=0,
        thickness=6,
    )
    style.configure(
        "Success.Horizontal.TProgressbar",
        troughcolor=colors["trough"],
        background=colors["success"],
        bordercolor=colors["card_border"],
        lightcolor=colors["success"],
        darkcolor=colors["success"],
        borderwidth=0,
        thickness=6,
    )

    # Modern Slim Scrollbar (layout without up/down arrows)
    try:
        style.layout(
            "Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
            ]})]
        )
    except Exception:
        pass

    style.configure(
        "Vertical.TScrollbar",
        troughcolor=colors["trough"],
        background=colors["scrollbar_thumb"],
        bordercolor=colors["trough"],
        lightcolor=colors["scrollbar_thumb"],
        darkcolor=colors["scrollbar_thumb"],
        arrowcolor=colors["fg_muted"],
        relief="flat",
        borderwidth=0,
        width=8,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", colors["scrollbar_hover"]), ("pressed", colors["scrollbar_hover"])],
    )

    return style
