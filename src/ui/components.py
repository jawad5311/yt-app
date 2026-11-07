"""Native TTK reusable UI components for YT Organic Bot.

Provides TTK-based widgets (Frame, Label, Button, Entry, CheckBox, Switch,
OptionMenu, ProgressBar, Tabview, ScrollableFrame, Toplevel, InputDialog)
matching the application's clean desktop look while remaining 100% native.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .theme import DARK_THEME, get_font, get_theme_colors
from .dropdown_menu import AppleDropdownMenu


def _resolve_font(font: Any) -> Any:
    if font is None:
        return None
    if isinstance(font, (tuple, list, str)):
        return font
    size = getattr(font, "_size", 10)
    weight = getattr(font, "_weight", "normal")
    slant = getattr(font, "_slant", "roman")
    return get_font(size=size, weight=weight, slant=slant)


class Frame(ttk.Frame):
    """TTK Frame with optional card styling."""

    def __init__(self, master=None, corner_radius: Optional[int] = None, fg_color: Optional[str] = None, style: Optional[str] = None, **kwargs):
        for k in ("border_width", "border_color", "bg_color", "border_width"):
            kwargs.pop(k, None)
        # Determine style
        if style is None:
            if fg_color == "transparent":
                style = "Transparent.TFrame"
            elif corner_radius is not None or fg_color:
                style = "Card.TFrame"
            else:
                style = "TFrame"
        super().__init__(master, style=style, **kwargs)

    def configure(self, **kwargs):
        for k in ("border_width", "border_color", "bg_color", "corner_radius"):
            kwargs.pop(k, None)
        if "fg_color" in kwargs:
            color = kwargs.pop("fg_color")
            self._fg_color = color
            if color == "transparent":
                super().configure(style="Transparent.TFrame")
            else:
                super().configure(style="Card.TFrame")
        if kwargs:
            super().configure(**kwargs)


class Label(ttk.Label):
    """TTK Label with font, color, and wrap support."""

    def __init__(
        self,
        master=None,
        text: str = "",
        font: Optional[Any] = None,
        text_color: Optional[Any] = None,
        style: Optional[str] = None,
        **kwargs,
    ):
        for k in ("corner_radius", "fg_color", "bg_color", "height"):
            kwargs.pop(k, None)
        if style is None:
            style = "TLabel"
        if font is not None and not isinstance(font, (tuple, list, str)):
            # Handle font objects with size/weight
            size = getattr(font, "_size", 10)
            weight = getattr(font, "_weight", "normal")
            slant = getattr(font, "_slant", "roman")
            font = get_font(size=size, weight=weight, slant=slant)

        if text_color is not None:
            if isinstance(text_color, (tuple, list)):
                text_color = text_color[1] if len(text_color) > 1 else text_color[0]
            kwargs["foreground"] = text_color

        super().__init__(master, text=text, font=font, style=style, **kwargs)

    def configure(self, **kwargs):
        for k in ("corner_radius", "fg_color", "bg_color", "height"):
            kwargs.pop(k, None)
        if "text_color" in kwargs:
            tc = kwargs.pop("text_color")
            if isinstance(tc, (tuple, list)):
                tc = tc[1] if len(tc) > 1 else tc[0]
            kwargs["foreground"] = tc
        if "font" in kwargs:
            f = kwargs["font"]
            if f is not None and not isinstance(f, (tuple, list, str)):
                size = getattr(f, "_size", 10)
                weight = getattr(f, "_weight", "normal")
                slant = getattr(f, "_slant", "roman")
                kwargs["font"] = get_font(size=size, weight=weight, slant=slant)
        super().configure(**kwargs)


class Button(ttk.Button):
    """TTK Button supporting primary, success, danger, warning styles."""

    def __init__(
        self,
        master=None,
        text: str = "",
        command: Optional[Callable[[], None]] = None,
        font: Optional[Any] = None,
        fg_color: Optional[str] = None,
        hover_color: Optional[str] = None,
        style: Optional[str] = None,
        width: Optional[int] = None,
        **kwargs,
    ):
        # Discard CTk visual properties that don't apply to TTK Button
        for k in ("corner_radius", "text_color", "border_width", "border_color", "bg_color", "height"):
            kwargs.pop(k, None)

        if style is None:
            if fg_color in ("#1F6AA5", "#1f6aa5", "#0E639C", "#2563EB", "#2563eb", "#1D4ED8"):
                style = "Primary.TButton"
            elif fg_color in ("#0284C7", "#0284c7", "#0369A1", "#0369a1", "#0EA5E9", "#0ea5e9"):
                style = "Cerulean.TButton"
            elif fg_color in ("#2E7D32", "#2e7d32", "#388E3C", "#388e3c", "#059669", "#047857"):
                style = "Success.TButton"
            elif fg_color in ("#D32F2F", "#d32f2f", "#C62828", "#c62828"):
                style = "Danger.TButton"
            elif fg_color in ("#F57C00", "#f57c00", "#EF6C00", "#ef6c00", "#D97706", "#d97706", "#B45309"):
                style = "Warning.TButton"
            elif fg_color in ("#0D9488", "#0d9488", "#0F766E", "#0f766e", "#14B8A6"):
                style = "Teal.TButton"
            elif fg_color in ("#374151", "#4B5563", "#4b5563", "gray30"):
                style = "MutedAction.TButton"
            elif fg_color == "transparent":
                style = "Nav.TButton"
            elif fg_color is None:
                style = "Primary.TButton"
            else:
                style = "TButton"

        if width is not None:
            raw_len = len(text) if text else 0
            for ch in (text or ""):
                if ord(ch) > 127:
                    raw_len += 1
            char_w = max(raw_len + 2, int(width / 7.0)) if width > 20 else max(raw_len + 2, width)
            kwargs["width"] = -char_w
            self._min_width = char_w
        else:
            self._min_width = 0

        self._command = command
        self._text = text
        self._style = style
        self._fg_color = fg_color
        self._hover_color = hover_color
        super().__init__(master, text=text, command=command, style=style, **kwargs)

    def configure(self, **kwargs):
        if "hover_color" in kwargs:
            self._hover_color = kwargs.pop("hover_color")
        for k in ("corner_radius", "text_color", "border_width", "border_color", "bg_color", "height"):
            kwargs.pop(k, None)
        if "command" in kwargs:
            self._command = kwargs["command"]
        if "text" in kwargs:
            self._text = kwargs["text"]
            if getattr(self, "_min_width", 0) > 0:
                raw_len = len(self._text)
                for ch in self._text:
                    if ord(ch) > 127:
                        raw_len += 1
                kwargs["width"] = -max(raw_len + 2, self._min_width)
        if "font" in kwargs:
            kwargs["font"] = _resolve_font(kwargs["font"])
        if "style" in kwargs:
            self._style = kwargs["style"]
        if "fg_color" in kwargs:
            color = kwargs.pop("fg_color")
            cur_style = getattr(self, "_style", None) or (self.cget("style") if hasattr(self, "cget") else "")
            is_apple = "Apple" in str(cur_style)
            if color in ("#1F6AA5", "#1f6aa5", "#0E639C", "#2563EB", "#2563eb", "#1D4ED8"):
                kwargs["style"] = "ApplePrimary.TButton" if is_apple else "Primary.TButton"
            elif color in ("#0284C7", "#0284c7", "#0369A1", "#0369a1", "#0EA5E9", "#0ea5e9"):
                kwargs["style"] = "Cerulean.TButton"
            elif color in ("#2E7D32", "#2e7d32", "#388E3C", "#388e3c", "#059669", "#047857"):
                kwargs["style"] = "Success.TButton"
            elif color in ("#D32F2F", "#d32f2f", "#C62828", "#c62828"):
                kwargs["style"] = "AppleDanger.TButton" if is_apple else "Danger.TButton"
            elif color in ("#F57C00", "#f57c00", "#EF6C00", "#ef6c00", "#D97706", "#d97706", "#B45309"):
                kwargs["style"] = "AppleWarning.TButton" if is_apple else "Warning.TButton"
            elif color in ("#0D9488", "#0d9488", "#0F766E", "#0f766e", "#14B8A6"):
                kwargs["style"] = "Teal.TButton"
            elif color in ("#374151", "#4B5563", "#4b5563", "gray30"):
                kwargs["style"] = "MutedAction.TButton"
            elif color == "transparent":
                kwargs["style"] = "Nav.TButton"
        if "width" in kwargs:
            w = kwargs.pop("width")
            if w is not None:
                cur_text = getattr(self, "_text", "")
                raw_len = len(cur_text)
                for ch in cur_text:
                    if ord(ch) > 127:
                        raw_len += 1
                char_w = max(raw_len + 2, int(w / 7.0)) if w > 20 else max(raw_len + 2, w)
                self._min_width = char_w
                kwargs["width"] = -char_w
        super().configure(**kwargs)

    def cget(self, prop: str) -> Any:
        if prop == "text" and hasattr(self, "_text") and self._text:
            return self._text
        if prop == "fg_color" and hasattr(self, "_fg_color"):
            return self._fg_color
        if prop == "hover_color" and hasattr(self, "_hover_color"):
            return self._hover_color
        val = super().cget(prop)
        if not isinstance(val, (str, int, float, bool, list, tuple, dict)):
            return str(val)
        return val


class Entry(ttk.Entry):
    """TTK Entry supporting placeholders and get/insert/delete."""

    def __init__(
        self,
        master=None,
        placeholder_text: str = "",
        font: Optional[Any] = None,
        width: Optional[int] = None,
        **kwargs,
    ):
        for k in ("corner_radius", "border_width", "border_color", "fg_color", "text_color", "height", "bg_color"):
            kwargs.pop(k, None)

        if width is not None and width > 30:
            char_width = max(5, width // 10)
            kwargs["width"] = char_width

        font = _resolve_font(font)
        super().__init__(master, font=font, **kwargs)
        self.placeholder_text = placeholder_text
        self._has_placeholder = False

        if placeholder_text:
            self._set_placeholder()
            self.bind("<FocusIn>", self._clear_placeholder, add="+")
            self.bind("<FocusOut>", self._restore_placeholder, add="+")

    def configure(self, **kwargs):
        for k in ("corner_radius", "border_width", "border_color", "fg_color", "text_color", "height", "bg_color"):
            kwargs.pop(k, None)
        if "font" in kwargs:
            kwargs["font"] = _resolve_font(kwargs["font"])
        if "placeholder_text" in kwargs:
            self.placeholder_text = kwargs.pop("placeholder_text")
            if not super().get():
                self._set_placeholder()
        if "width" in kwargs:
            w = kwargs["width"]
            if w and w > 30:
                kwargs["width"] = max(5, w // 10)
        super().configure(**kwargs)

    def cget(self, prop: str) -> Any:
        val = super().cget(prop)
        if not isinstance(val, (str, int, float, bool, list, tuple, dict)):
            return str(val)
        return val

    def _set_placeholder(self):
        if not super().get() and self.placeholder_text:
            super().insert(0, self.placeholder_text)
            self._has_placeholder = True

    def _clear_placeholder(self, event=None):
        if self._has_placeholder:
            super().delete(0, "end")
            self._has_placeholder = False

    def _restore_placeholder(self, event=None):
        if not super().get():
            self._set_placeholder()

    def get(self) -> str:
        if self._has_placeholder:
            return ""
        return super().get()

    def insert(self, index, string):
        if self._has_placeholder:
            super().delete(0, "end")
            self._has_placeholder = False
        super().insert(index, string)

    def delete(self, first, last=None):
        self._has_placeholder = False
        super().delete(first, last)


class AppleToggleSwitch(ttk.Frame):
    """Native Apple macOS / iOS Human Interface Guidelines Toggle Switch."""

    def __init__(
        self,
        master=None,
        text: str = "",
        command: Optional[Callable[[], None]] = None,
        variable: Optional[tk.Variable] = None,
        state: str = "normal",
        width: Optional[int] = None,
        height: Optional[int] = None,
        font: Optional[Any] = None,
        **kwargs,
    ):
        for k in (
            "corner_radius", "border_width", "border_color", "fg_color",
            "progress_color", "button_color", "button_hover_color",
            "text_color", "switch_width", "switch_height", "style",
            "bg_color", "checkbox_width", "checkbox_height",
        ):
            kwargs.pop(k, None)

        super().__init__(master, style="Transparent.TFrame", **kwargs)

        self._command = command
        self._state = state
        self._text = text
        self._width = max(34, width if width and width < 60 else 38)
        self._height = max(18, height if height and height < 40 else 22)
        self._var = variable if variable is not None else tk.IntVar(value=0)
        self.variable = self._var
        self._font = font

        # Apple HIG Palette:
        # ON track: Apple System Green (#34C759 / #30D158)
        # OFF track: Dark (#3A3A3C), Light (#D1D1D6)
        self._on_track = "#34C759"
        self._on_border = "#30D158"
        self._off_track_dark = "#3A3A3C"
        self._off_border_dark = "#48484A"
        self._off_track_light = "#D1D1D6"
        self._off_border_light = "#C7C7CC"
        self._thumb_color = "#FFFFFF"

        parent_bg = self._resolve_bg()

        self._canvas = tk.Canvas(
            self,
            width=self._width,
            height=self._height,
            highlightthickness=0,
            borderwidth=0,
            background=parent_bg,
            cursor="hand2" if self._state == "normal" else "arrow",
        )
        self._canvas.pack(side="left")
        self._canvas.bind("<Button-1>", self._on_click)

        if self._text:
            self._label = ttk.Label(self, text=self._text, font=_resolve_font(self._font) or get_font(size=9))
            self._label.pack(side="left", padx=(8, 0))
            self._label.bind("<Button-1>", self._on_click)
        else:
            self._label = None

        self._draw()

    def _resolve_bg(self) -> str:
        try:
            m = self.master
            while m:
                if hasattr(m, "cget"):
                    bg = m.cget("background")
                    if bg and isinstance(bg, str) and (bg.startswith("#") or bg in ("white", "black")):
                        return bg
                m = getattr(m, "master", None)
        except Exception:
            pass
        return get_theme_colors().get("card_bg", "#202024")

    def _draw(self):
        self._canvas.delete("all")
        is_on = bool(self._var.get())
        w = self._width
        h = self._height
        r = h / 2.0

        colors = get_theme_colors()
        is_dark = colors.get("bg", "#161619") != "#f5f5f7"

        if is_on:
            bg_track = self._on_track
            border_track = self._on_border
        else:
            bg_track = self._off_track_dark if is_dark else self._off_track_light
            border_track = self._off_border_dark if is_dark else self._off_border_light

        if self._state == "disabled":
            bg_track = "#2C2C2E" if is_dark else "#E5E5EA"
            border_track = "#3A3A3C" if is_dark else "#D1D1D6"

        # Pill track
        self._canvas.create_arc(0, 0, 2 * r, 2 * r, start=90, extent=180, fill=bg_track, outline=border_track, width=1)
        self._canvas.create_arc(w - 2 * r, 0, w, 2 * r, start=270, extent=180, fill=bg_track, outline=border_track, width=1)
        self._canvas.create_rectangle(r, 0, w - r, 2 * r, fill=bg_track, outline=bg_track)
        self._canvas.create_line(r, 0, w - r, 0, fill=border_track, width=1)
        self._canvas.create_line(r, 2 * r, w - r, 2 * r, fill=border_track, width=1)

        # White knob / thumb
        thumb_r = r - 2.5
        thumb_cx = (w - r) if is_on else r
        self._canvas.create_oval(
            thumb_cx - thumb_r,
            r - thumb_r,
            thumb_cx + thumb_r,
            r + thumb_r,
            fill=self._thumb_color,
            outline="#E5E5EA" if not is_on else "#FFFFFF",
            width=1,
        )

    def _on_click(self, event=None):
        if self._state == "disabled":
            return
        self.toggle()

    def toggle(self):
        cur = bool(self._var.get())
        new_val = 0 if cur else 1
        self._var.set(new_val)
        self._draw()
        if self._command:
            try:
                self._command()
            except Exception:
                pass

    def invoke(self):
        return self.toggle()

    def select(self):
        self._var.set(1)
        self._draw()

    def deselect(self):
        self._var.set(0)
        self._draw()

    def get(self) -> int:
        return int(self._var.get())

    def set(self, val):
        self._var.set(1 if val else 0)
        self._draw()

    def configure(self, **kwargs):
        for k in (
            "corner_radius", "border_width", "border_color", "fg_color",
            "progress_color", "button_color", "button_hover_color",
            "text_color", "switch_width", "switch_height", "style",
            "bg_color", "checkbox_width", "checkbox_height",
        ):
            kwargs.pop(k, None)

        if "state" in kwargs:
            self._state = kwargs.pop("state")
            cursor = "hand2" if self._state == "normal" else "arrow"
            self._canvas.configure(cursor=cursor)
            if self._label:
                self._label.configure(state=self._state)

        if "command" in kwargs:
            self._command = kwargs.pop("command")

        if "text" in kwargs:
            self._text = kwargs.pop("text")
            if self._label:
                self._label.configure(text=self._text)
            elif self._text:
                self._label = ttk.Label(self, text=self._text, font=_resolve_font(self._font) or get_font(size=9))
                self._label.pack(side="left", padx=(8, 0))
                self._label.bind("<Button-1>", self._on_click)

        if "variable" in kwargs:
            self._var = kwargs.pop("variable")
            self.variable = self._var

        if "font" in kwargs:
            self._font = kwargs.pop("font")
            if self._label:
                self._label.configure(font=_resolve_font(self._font))

        self._draw()

    def cget(self, prop: str) -> Any:
        if prop == "state":
            return self._state
        if prop == "text":
            return self._text
        if prop == "variable":
            return self._var
        return ""

    def __getitem__(self, key: str) -> Any:
        return self.cget(key)

    def __setitem__(self, key: str, value: Any):
        self.configure(**{key: value})


class Switch(AppleToggleSwitch):
    """Native Apple macOS / iOS Human Interface Guidelines Toggle Switch."""
    pass


class CheckBox(AppleToggleSwitch):
    """Apple UI toggle switch replacement for standard checkboxes."""
    pass


class OptionMenu(ttk.Combobox):
    """TTK Combobox styled and behaving as an OptionMenu dropdown."""

    def __init__(
        self,
        master=None,
        values: Optional[List[str]] = None,
        command: Optional[Callable[[str], None]] = None,
        variable: Optional[tk.StringVar] = None,
        width: Optional[int] = None,
        **kwargs,
    ):
        for k in (
            "font", "corner_radius", "border_width", "border_color", "fg_color",
            "button_color", "button_hover_color", "dropdown_fg_color",
            "dropdown_text_color", "dropdown_hover_color", "text_color", "height", "bg_color"
        ):
            kwargs.pop(k, None)

        self._command = command
        self._var = variable if variable is not None else tk.StringVar()
        val_list = list(values) if values else []

        char_width = max(10, width // 10) if width and width > 20 else width
        super().__init__(
            master,
            values=val_list,
            textvariable=self._var,
            state="readonly",
            width=char_width,
            **kwargs,
        )

        if val_list and not self._var.get():
            self._var.set(val_list[0])

        self.bind("<<ComboboxSelected>>", self._on_selected)

    def configure(self, **kwargs):
        if "values" in kwargs:
            self["values"] = list(kwargs.pop("values"))
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        for k in (
            "font", "corner_radius", "border_width", "border_color", "fg_color",
            "button_color", "button_hover_color", "dropdown_fg_color",
            "dropdown_text_color", "dropdown_hover_color", "text_color", "height", "bg_color"
        ):
            kwargs.pop(k, None)
        super().configure(**kwargs)

    def _on_selected(self, event=None):
        if self._command:
            try:
                self._command(self.get())
            except Exception:
                pass

    def set(self, value: str):
        self._var.set(value)
        super().set(value)

    def get(self) -> str:
        return self._var.get() or super().get()

    def cget(self, prop: str) -> Any:
        if prop == "values":
            val = super().cget("values")
            return list(val) if isinstance(val, (tuple, list)) else val
        return super().cget(prop)


class ProgressBar(ttk.Progressbar):
    """TTK Progressbar with 0.0-1.0 fraction get/set compatibility."""

    def __init__(self, master=None, style: str = "Horizontal.TProgressbar", mode: str = "determinate", **kwargs):
        for k in ("corner_radius", "border_width", "border_color", "fg_color", "progress_color", "height", "width", "bg_color"):
            kwargs.pop(k, None)
        mode = kwargs.pop("mode", mode)
        orient = kwargs.pop("orient", "horizontal")
        maximum = kwargs.pop("maximum", 100.0)
        super().__init__(master, orient=orient, mode=mode, maximum=maximum, style=style, **kwargs)

    def configure(self, **kwargs):
        for k in ("corner_radius", "border_width", "border_color", "fg_color", "progress_color", "height", "width", "bg_color"):
            kwargs.pop(k, None)
        super().configure(**kwargs)

    def set(self, fraction: float):
        try:
            self["value"] = max(0.0, min(100.0, float(fraction) * 100.0))
        except Exception:
            pass

    def get(self) -> float:
        try:
            return float(self["value"]) / 100.0
        except Exception:
            return 0.0


class ScrollableFrame(ttk.Frame):
    """High-performance TTK scrollable container wrapping tk.Canvas and ttk.Scrollbar."""

    def __init__(self, master=None, label_text: str = "", width: int = 200, height: int = 200, **kwargs):
        kwargs.pop("corner_radius", None)
        kwargs.pop("fg_color", None)
        kwargs.pop("border_width", None)
        kwargs.pop("border_color", None)

        # Parent wrapper frame
        self._parent_frame = ttk.Frame(master)
        self._parent_frame.grid_columnconfigure(0, weight=1)
        self._parent_frame.grid_rowconfigure(1 if label_text else 0, weight=1)

        row_offset = 0
        if label_text:
            self._label = ttk.Label(self._parent_frame, text=label_text, style="Subheader.TLabel")
            self._label.grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 4))
            row_offset = 1
        else:
            self._label = None

        colors = get_theme_colors()
        self._parent_canvas = tk.Canvas(
            self._parent_frame,
            borderwidth=0,
            highlightthickness=0,
            background=colors["bg"],
            width=width,
            height=height,
            yscrollincrement=1,
        )
        self._scrollbar = ttk.Scrollbar(
            self._parent_frame,
            orient="vertical",
            command=self._parent_canvas.yview,
            style="Vertical.TScrollbar",
        )
        self._parent_canvas.configure(yscrollcommand=self._scrollbar.set)

        self._parent_canvas.grid(row=row_offset, column=0, sticky="nsew")
        self._scrollbar.grid(row=row_offset, column=1, sticky="ns")

        # Inner frame where child widgets will be attached
        super().__init__(self._parent_canvas, style="TFrame")
        self._window_id = self._parent_canvas.create_window(0, 0, window=self, anchor="nw")

        self.bind("<Configure>", self._on_frame_configure)
        self._parent_canvas.bind("<Configure>", self._on_canvas_configure)

        from ..utils.scroll_helper import setup_smooth_scrolling
        setup_smooth_scrolling(self, notch_pixels=65.0, damping=0.28)

    def _on_frame_configure(self, event=None):
        if self._parent_canvas.winfo_exists():
            self._parent_canvas.configure(scrollregion=self._parent_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        if self._parent_canvas.winfo_exists():
            self._parent_canvas.itemconfig(self._window_id, width=event.width)

    # Delegate grid, pack, place to parent container
    def grid(self, **kwargs):
        self._parent_frame.grid(**kwargs)

    def pack(self, **kwargs):
        self._parent_frame.pack(**kwargs)

    def place(self, **kwargs):
        self._parent_frame.place(**kwargs)

    def grid_columnconfigure(self, *args, **kwargs):
        # Apply column configure on the inner scrollable frame for child widgets
        super().grid_columnconfigure(*args, **kwargs)

    def grid_rowconfigure(self, *args, **kwargs):
        super().grid_rowconfigure(*args, **kwargs)

    def update_idletasks(self):
        super().update_idletasks()
        if self._parent_frame.winfo_exists():
            self._parent_frame.update_idletasks()


class _DummySegmentedButton:
    """Compatibility shim for tests checking view.tabview._segmented_button."""

    def __init__(self, tabview: Tabview):
        self._tabview = tabview
        self._buttons_dict: Dict[str, Any] = {}

    def cget(self, prop: str) -> Any:
        if prop == "height":
            return 36
        return None

    def configure(self, **kwargs):
        pass

    def update_idletasks(self):
        pass


class Tabview(ttk.Notebook):
    """TTK Notebook-based Tabview with CTkTabview-compatible .add(), .tab(), .set(), and .get()."""

    def __init__(self, master=None, anchor: str = "w", **kwargs):
        kwargs.pop("corner_radius", None)
        kwargs.pop("fg_color", None)
        super().__init__(master, style="TNotebook", **kwargs)
        self._anchor = anchor
        self._tab_dict: Dict[str, ttk.Frame] = {}
        self._segmented_button = _DummySegmentedButton(self)

    def add(self, name: str) -> ttk.Frame:
        """Create and add a tab frame, returning the frame."""
        frame = ttk.Frame(self, style="TFrame")
        self.add_tab(frame, name)
        return frame

    def add_tab(self, frame: ttk.Frame, name: str):
        super().add(frame, text=name)
        self._tab_dict[name] = frame
        dummy_btn = tk.Frame(self)
        dummy_btn.configure = lambda **kw: None
        dummy_btn.winfo_reqwidth = lambda: 80
        self._segmented_button._buttons_dict[name] = dummy_btn

    def tab(self, name: str) -> Optional[ttk.Frame]:
        return self._tab_dict.get(name)

    def set(self, name: str):
        frame = self._tab_dict.get(name)
        if frame:
            self.select(frame)

    def get(self) -> str:
        selected_id = self.select()
        if selected_id:
            return self.tab(selected_id, "text")
        return ""


class SegmentedButton(ttk.Frame):
    """TTK Segmented Button bar with .get(), .set(val), and callback."""

    def __init__(self, master=None, values: Optional[List[str]] = None, command: Optional[Callable[[str], None]] = None, **kwargs):
        kwargs.pop("font", None)
        kwargs.pop("corner_radius", None)
        super().__init__(master, style="Card.TFrame", **kwargs)
        self.values = list(values) if values else []
        self.command = command
        self._current_value = self.values[0] if self.values else ""
        self._buttons: Dict[str, ttk.Button] = {}
        self._buttons_dict = self._buttons

        for val in self.values:
            btn = ttk.Button(
                self,
                text=val,
                style="SegmentedActive.TButton" if val == self._current_value else "Segmented.TButton",
                command=lambda v=val: self._on_btn_click(v),
            )
            btn.pack(side="left", padx=1, pady=1)
            self._buttons[val] = btn

    def _on_btn_click(self, val: str):
        self.set(val)
        if self.command:
            try:
                self.command(val)
            except Exception:
                pass

    def set(self, val: str):
        self._current_value = val
        for v, btn in self._buttons.items():
            btn.configure(style="SegmentedActive.TButton" if v == val else "Segmented.TButton")

    def get(self) -> str:
        return self._current_value

    def configure(self, **kwargs):
        if "values" in kwargs:
            self.values = list(kwargs.pop("values"))
            for btn in self._buttons.values():
                btn.destroy()
            self._buttons.clear()
            for val in self.values:
                btn = ttk.Button(
                    self,
                    text=val,
                    style="SegmentedActive.TButton" if val == self._current_value else "Segmented.TButton",
                    command=lambda v=val: self._on_btn_click(v),
                )
                btn.pack(side="left", padx=1, pady=1)
                self._buttons[val] = btn

        if "command" in kwargs:
            self.command = kwargs.pop("command")

        for key in [
            "font",
            "corner_radius",
            "fg_color",
            "selected_color",
            "unselected_color",
            "selected_hover_color",
            "unselected_hover_color",
            "text_color",
            "text_color_disabled",
            "height",
            "width",
        ]:
            kwargs.pop(key, None)

        if kwargs:
            super().configure(**kwargs)

    def cget(self, prop: str) -> Any:
        if prop == "values":
            return list(self.values)
        return super().cget(prop)


class Toplevel(tk.Toplevel):
    """TTK Toplevel window with dark/light background configuration."""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        colors = DARK_THEME
        self.configure(background=colors["bg"])


class InputDialog:
    """TTK dialog for single-line text input."""

    def __init__(self, text: str = "Enter value:", title: str = "Input", master=None):
        self._result: Optional[str] = simpledialog.askstring(title, text, parent=master)

    def get_input(self) -> Optional[str]:
        return self._result


class Font:
    """Helper representing a font tuple compatible with Tkinter/TTK."""

    def __init__(self, size: int = 10, weight: str = "normal", slant: str = "roman", family: Optional[str] = None):
        self._size = size
        self._weight = weight
        self._slant = slant
        self._family = family

    def _to_tuple(self) -> Tuple[str, int, str]:
        return get_font(size=self._size, weight=self._weight, slant=self._slant)

    def __iter__(self):
        return iter(self._to_tuple())

    def __getitem__(self, idx):
        return self._to_tuple()[idx]

    def __len__(self):
        return 3

    def __str__(self):
        t = self._to_tuple()
        return f"{t[0]} {t[1]} {t[2]}"


DropdownMenu = AppleDropdownMenu
