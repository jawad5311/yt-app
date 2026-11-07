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
    "bg": "#161619",
    "sidebar_bg": "#121215",
    "card_bg": "#202024",
    "card_border": "#323238",
    "fg": "#ffffff",
    "fg_dim": "#d4d4d8",
    "fg_muted": "#98989d",
    "accent": "#0a84ff",
    "accent_hover": "#0071e3",
    "accent_active": "#005bb5",
    "nav_hover": "#202025",
    "nav_active": "#0a84ff",
    "success": "#30d158",
    "success_hover": "#28b84d",
    "danger": "#ff453a",
    "danger_hover": "#d70015",
    "warning": "#ff9f0a",
    "warning_hover": "#c97800",
    "teal": "#0f766e",
    "teal_hover": "#115e59",
    "muted_action": "#2a2a30",
    "muted_action_hover": "#3a3a42",
    "input_bg": "#18181c",
    "input_fg": "#ffffff",
    "select_bg": "#0a84ff",
    "select_fg": "#ffffff",
    "trough": "#161619",
    "scrollbar_thumb": "#3a3a42",
    "scrollbar_hover": "#50505a",
    "tab_bg": "#18181c",
    "tab_selected_bg": "#202024",
}

LIGHT_THEME: Dict[str, str] = {
    "bg": "#f5f5f7",
    "sidebar_bg": "#ebebef",
    "card_bg": "#ffffff",
    "card_border": "#e5e5ea",
    "fg": "#1d1d1f",
    "fg_dim": "#48484a",
    "fg_muted": "#86868b",
    "accent": "#007aff",
    "accent_hover": "#0062cc",
    "accent_active": "#004eb5",
    "nav_hover": "#e0e0e6",
    "nav_active": "#007aff",
    "success": "#34c759",
    "success_hover": "#2db04d",
    "danger": "#ff3b30",
    "danger_hover": "#d70015",
    "warning": "#ff9500",
    "warning_hover": "#c97800",
    "teal": "#0d9488",
    "teal_hover": "#0f766e",
    "muted_action": "#e5e5ea",
    "muted_action_hover": "#d1d1d6",
    "input_bg": "#ffffff",
    "input_fg": "#1d1d1f",
    "select_bg": "#007aff",
    "select_fg": "#ffffff",
    "trough": "#e5e5ea",
    "scrollbar_thumb": "#b0b0b8",
    "scrollbar_hover": "#8e8e96",
    "tab_bg": "#e8e8ed",
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

    # Teal Button (Apple Deep Sea Teal - dark, eye-friendly, high contrast)
    style.configure(
        "Teal.TButton",
        background=colors.get("teal", "#0f766e"),
        foreground="#ffffff",
        font=(family, 9, "bold"),
        bordercolor=colors.get("teal", "#0f766e"),
        lightcolor=colors.get("teal", "#0f766e"),
        darkcolor=colors.get("teal", "#0f766e"),
        borderwidth=1,
        focuscolor="none",
        relief="solid",
        padding=(12, 5),
    )
    style.map(
        "Teal.TButton",
        background=[("active", colors.get("teal_hover", "#115e59")), ("pressed", "#09403b")],
        bordercolor=[("active", colors.get("teal_hover", "#115e59")), ("pressed", "#09403b")],
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


    # ---------------------------------------------------------
    # Apple macOS Design System Styles (Sonoma / Sequoia HIG)
    # ---------------------------------------------------------
    is_dark = _current_mode.lower() == "dark"
    apple = {
        "card_bg": "#202024" if is_dark else "#ffffff",
        "card_border": "#323238" if is_dark else "#e5e5ea",
        "inset_bg": "#18181c" if is_dark else "#f2f2f7",
        "inset_border": "#28282e" if is_dark else "#d1d1d6",
        "title_fg": "#ffffff" if is_dark else "#1d1d1f",
        "subtitle_fg": "#98989d" if is_dark else "#86868b",
        "category_fg": "#86868b" if is_dark else "#8e8e93",
        "blue": "#0a84ff" if is_dark else "#007aff",
        "blue_hover": "#0071e3" if is_dark else "#0062cc",
        "blue_active": "#005bb5" if is_dark else "#004eb5",
        "red": "#ff453a" if is_dark else "#ff3b30",
        "red_hover": "#d70015",
        "orange": "#ff9f0a" if is_dark else "#ff9500",
        "orange_hover": "#c97800",
        "green": "#30d158" if is_dark else "#34c759",
        "trough": "#161619" if is_dark else "#e5e5ea",
    }

    # Apple Frames
    
    # Apple macOS Treeview Tables
    style.configure(
        "Treeview",
        background=colors["card_bg"],
        fieldbackground=colors["card_bg"],
        foreground=colors["fg"],
        bordercolor=colors["card_border"],
        lightcolor=colors["card_border"],
        darkcolor=colors["card_border"],
        borderwidth=1,
        relief="solid",
        rowheight=28,
        font=(family, 9),
    )
    style.map(
        "Treeview",
        background=[("selected", colors["accent"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=colors["tab_bg"],
        foreground=colors["fg"],
        font=(family, 9, "bold"),
        bordercolor=colors["card_border"],
        relief="flat",
        borderwidth=0,
        padding=(6, 4),
    )

    style.configure(
        "AppleCard.TFrame",
        background=apple["card_bg"],
        bordercolor=apple["card_border"],
        lightcolor=apple["card_border"],
        darkcolor=apple["card_border"],
        relief="solid",
        borderwidth=1,
    )
    style.configure("AppleCardInner.TFrame", background=apple["card_bg"])
    style.configure(
        "AppleInset.TFrame",
        background=apple["inset_bg"],
        bordercolor=apple["inset_border"],
        lightcolor=apple["inset_border"],
        darkcolor=apple["inset_border"],
        relief="solid",
        borderwidth=1,
    )
    style.configure("AppleDivider.TFrame", background=apple["card_border"], relief="flat", borderwidth=0)

    # Apple Status Pill Frame
    style.configure(
        "ApplePill.TFrame",
        background=apple["inset_bg"],
        bordercolor=apple["card_border"],
        lightcolor=apple["card_border"],
        darkcolor=apple["card_border"],
        relief="solid",
        borderwidth=1,
    )

    # Apple Labels
    style.configure("AppleHeader.TLabel", background=colors["bg"], foreground=apple["title_fg"], font=(family, 20, "bold"))
    style.configure("AppleSubtitle.TLabel", background=colors["bg"], foreground=apple["subtitle_fg"], font=(family, 9))
    style.configure("AppleCategory.TLabel", background=apple["card_bg"], foreground=apple["category_fg"], font=(family, 8, "bold"))
    style.configure("AppleMetric.TLabel", background=apple["card_bg"], foreground=apple["title_fg"], font=(family, 22, "bold"))
    style.configure("AppleMetricFooter.TLabel", background=apple["card_bg"], foreground=apple["subtitle_fg"], font=(family, 8))
    style.configure("AppleSectionTitle.TLabel", background=apple["card_bg"], foreground=apple["category_fg"], font=(family, 8, "bold"))
    style.configure("AppleInset.TLabel", background=apple["inset_bg"], foreground=colors["fg"], font=(family, 9, "bold"))
    style.configure("AppleInsetMuted.TLabel", background=apple["inset_bg"], foreground=apple["subtitle_fg"], font=(family, 8))
    style.configure("AppleCard.TLabel", background=apple["card_bg"], foreground=colors["fg"], font=(family, 9))
    style.configure("AppleCardMuted.TLabel", background=apple["card_bg"], foreground=apple["subtitle_fg"], font=(family, 8))
    style.configure("ApplePill.TLabel", background=apple["inset_bg"], foreground=apple["title_fg"], font=(family, 8, "bold"))

    # Apple Buttons
    style.configure(
        "ApplePrimary.TButton",
        background=apple["blue"],
        foreground="#ffffff",
        bordercolor=apple["blue"],
        lightcolor=apple["blue"],
        darkcolor=apple["blue"],
        borderwidth=1,
        relief="solid",
        padding=(16, 7),
        font=(family, 10, "bold"),
    )
    style.map(
        "ApplePrimary.TButton",
        background=[("active", apple["blue_hover"]), ("pressed", apple["blue_active"])],
        bordercolor=[("active", apple["blue_hover"]), ("pressed", apple["blue_active"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    style.configure(
        "AppleDanger.TButton",
        background=apple["red"],
        foreground="#ffffff",
        bordercolor=apple["red"],
        lightcolor=apple["red"],
        darkcolor=apple["red"],
        borderwidth=1,
        relief="solid",
        padding=(16, 7),
        font=(family, 10, "bold"),
    )
    style.map(
        "AppleDanger.TButton",
        background=[("active", apple["red_hover"]), ("pressed", "#b91c1c")],
        bordercolor=[("active", apple["red_hover"]), ("pressed", "#b91c1c")],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    style.configure(
        "AppleWarning.TButton",
        background=apple["orange"],
        foreground="#ffffff",
        bordercolor=apple["orange"],
        lightcolor=apple["orange"],
        darkcolor=apple["orange"],
        borderwidth=1,
        relief="solid",
        padding=(16, 7),
        font=(family, 10, "bold"),
    )
    style.map(
        "AppleWarning.TButton",
        background=[("active", apple["orange_hover"]), ("pressed", "#b45309")],
        bordercolor=[("active", apple["orange_hover"]), ("pressed", "#b45309")],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )

    # Apple Progressbar
    style.configure(
        "Apple.Horizontal.TProgressbar",
        troughcolor=apple["trough"],
        background=apple["blue"],
        bordercolor=apple["card_border"],
        lightcolor=apple["blue"],
        darkcolor=apple["blue"],
        borderwidth=0,
        thickness=7,
    )

    # Apple Checkbutton
    style.configure(
        "AppleInset.TCheckbutton",
        background=apple["inset_bg"],
        foreground=colors["fg"],
        font=(family, 9),
        focuscolor="none",
        indicatorsize=13,
        indicatormargin=(1, 1, 6, 1),
        indicatorbackground=apple["card_bg"],
        indicatorforeground=apple["blue"],
        upperbordercolor=apple["inset_border"],
        lowerbordercolor=apple["inset_border"],
    )
    style.map(
        "AppleInset.TCheckbutton",
        background=[("active", apple["inset_bg"])],
        foreground=[("active", colors["fg"])],
        indicatorbackground=[("selected", apple["blue"]), ("active", apple["card_bg"])],
        indicatorforeground=[("selected", "#ffffff"), ("active", apple["blue"])],
    )

    # Apple Entry
    style.configure(
        "Apple.TEntry",
        fieldbackground=apple["inset_bg"],
        foreground=colors["input_fg"],
        insertcolor=apple["blue"],
        bordercolor=apple["inset_border"],
        lightcolor=apple["inset_border"],
        darkcolor=apple["inset_border"],
        borderwidth=1,
        relief="solid",
        padding=(6, 4),
        font=(family, 9),
    )

    return style
