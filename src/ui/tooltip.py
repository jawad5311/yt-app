"""
Tooltip component for CustomTkinter and Tkinter widgets.
Provides lightweight, flicker-free, accessible hover descriptions.
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional


class ToolTip:
    """A lightweight tooltip popup for Tkinter and CustomTkinter widgets.

    Displays a floating message when the user hovers over the widget for
    `delay_ms` milliseconds, and automatically hides on leave, click, or destroy.
    """

    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        *,
        delay_ms: int = 350,
        wraplength: int = 280,
    ) -> None:
        self.widget = widget
        self.text = text.strip()
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._tip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None
        self._leave_id: Optional[str] = None

        self._bind_events(self.widget)

    def _bind_events(self, w: tk.Widget) -> None:
        try:
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            w.bind("<ButtonPress>", self._on_click, add="+")
            w.bind("<Destroy>", self._on_destroy, add="+")
        except Exception:
            pass

        # Also bind internal CustomTkinter child components if present
        for child_attr in ("_canvas", "_text_label", "_image_label"):
            child = getattr(w, child_attr, None)
            if child is not None and isinstance(child, tk.Widget):
                try:
                    child.bind("<Enter>", self._on_enter, add="+")
                    child.bind("<Leave>", self._on_leave, add="+")
                    child.bind("<ButtonPress>", self._on_click, add="+")
                except Exception:
                    pass

        # Bind any existing child widgets
        try:
            for child in w.winfo_children():
                if isinstance(child, tk.Widget):
                    child.bind("<Enter>", self._on_enter, add="+")
                    child.bind("<Leave>", self._on_leave, add="+")
                    child.bind("<ButtonPress>", self._on_click, add="+")
        except Exception:
            pass

    def _on_enter(self, event=None) -> None:
        if self._leave_id:
            try:
                self.widget.after_cancel(self._leave_id)
            except Exception:
                pass
            self._leave_id = None

        if self._tip_window or self._after_id or not self.text:
            return

        try:
            self._after_id = self.widget.after(self.delay_ms, self.show)
        except Exception:
            pass

    def _on_leave(self, event=None) -> None:
        if self._leave_id:
            try:
                self.widget.after_cancel(self._leave_id)
            except Exception:
                pass

        # Brief delay to check if cursor merely moved between internal sub-widgets
        try:
            self._leave_id = self.widget.after(80, self._check_leave)
        except Exception:
            self.hide()

    def _check_leave(self) -> None:
        self._leave_id = None
        try:
            if not self.widget.winfo_exists():
                self.hide()
                return

            x = self.widget.winfo_pointerx()
            y = self.widget.winfo_pointery()
            under_pointer = self.widget.winfo_containing(x, y)
            if under_pointer is not None:
                w_str = str(self.widget)
                p_str = str(under_pointer)
                if p_str == w_str or p_str.startswith(w_str + "."):
                    return
        except Exception:
            pass

        self.hide()

    def _on_click(self, event=None) -> None:
        self.hide()

    def _on_destroy(self, event=None) -> None:
        self.hide()

    def set_text(self, text: str) -> None:
        """Update the tooltip text dynamically."""
        self.text = text.strip()
        if self._tip_window:
            self.hide()

    def show(self) -> None:
        """Render and display the tooltip window."""
        self._after_id = None
        if self._tip_window or not self.text:
            return

        try:
            if not self.widget.winfo_exists():
                return

            # Compute initial target position (below the widget by default)
            x = self.widget.winfo_rootx() + (self.widget.winfo_width() // 2)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6

            tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            try:
                tw.wm_attributes("-topmost", True)
            except Exception:
                pass

            # Modern dark slate styling
            container = tk.Frame(
                tw,
                background="#1e293b",
                highlightbackground="#475569",
                highlightthickness=1,
                padx=8,
                pady=5,
            )
            container.pack(fill="both", expand=True)

            label = tk.Label(
                container,
                text=self.text,
                justify="left",
                background="#1e293b",
                foreground="#f8fafc",
                font=("Segoe UI", 9),
                wraplength=self.wraplength,
            )
            label.pack()

            tw.update_idletasks()
            tip_w = tw.winfo_width()
            tip_h = tw.winfo_height()

            # Center horizontally relative to widget
            tip_x = x - (tip_w // 2)
            tip_y = y

            # Keep inside screen boundaries
            screen_w = tw.winfo_screenwidth()
            screen_h = tw.winfo_screenheight()

            if tip_x + tip_w > screen_w - 10:
                tip_x = screen_w - tip_w - 10
            if tip_x < 10:
                tip_x = 10

            # If placing below would overflow the bottom edge, place above the widget
            if tip_y + tip_h > screen_h - 15:
                tip_y = max(10, self.widget.winfo_rooty() - tip_h - 6)

            tw.wm_geometry(f"+{tip_x}+{tip_y}")
            self._tip_window = tw
        except Exception:
            self._tip_window = None

    def hide(self) -> None:
        """Cancel pending timer and destroy tooltip window."""
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

        if self._leave_id:
            try:
                self.widget.after_cancel(self._leave_id)
            except Exception:
                pass
            self._leave_id = None

        if self._tip_window:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None


def add_tooltip(widget: tk.Widget, text: str, *, delay_ms: int = 350, wraplength: int = 280) -> ToolTip:
    """Helper function to create and attach a ToolTip to a widget."""
    return ToolTip(widget, text, delay_ms=delay_ms, wraplength=wraplength)
