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
    `delay_ms` milliseconds, and automatically hides on leave, click, scroll, or destroy.
    Smoothly fades in and reliably dismisses even during rapid mouse movements.
    """

    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        *,
        delay_ms: int = 300,
        wraplength: int = 280,
    ) -> None:
        self.widget = widget
        self.text = text.strip()
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._tip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None
        self._leave_id: Optional[str] = None
        self._poll_id: Optional[str] = None
        self._fade_id: Optional[str] = None

        self._bind_events(self.widget)

    def _bind_events(self, w: tk.Widget) -> None:
        for seq, handler in (
            ("<Enter>", self._on_enter),
            ("<Leave>", self._on_leave),
            ("<ButtonPress>", self._on_click),
            ("<Destroy>", self._on_destroy),
            ("<MouseWheel>", self._on_scroll),
            ("<Button-4>", self._on_scroll),
            ("<Button-5>", self._on_scroll),
        ):
            try:
                w.bind(seq, handler, add="+")
            except Exception:
                pass

        # Also bind internal CustomTkinter child components if present
        for child_attr in ("_canvas", "_text_label", "_image_label"):
            child = getattr(w, child_attr, None)
            if child is not None and isinstance(child, tk.Widget):
                for seq, handler in (
                    ("<Enter>", self._on_enter),
                    ("<Leave>", self._on_leave),
                    ("<ButtonPress>", self._on_click),
                    ("<MouseWheel>", self._on_scroll),
                ):
                    try:
                        child.bind(seq, handler, add="+")
                    except Exception:
                        pass

        # Bind any existing child widgets
        try:
            for child in w.winfo_children():
                if isinstance(child, tk.Widget):
                    for seq, handler in (
                        ("<Enter>", self._on_enter),
                        ("<Leave>", self._on_leave),
                        ("<ButtonPress>", self._on_click),
                        ("<MouseWheel>", self._on_scroll),
                    ):
                        try:
                            child.bind(seq, handler, add="+")
                        except Exception:
                            pass
        except Exception:
            pass

    def _is_pointer_over_widget(self) -> bool:
        """Return True if the mouse pointer is currently within the widget's physical bounds."""
        try:
            if not self.widget.winfo_exists():
                return False
            if hasattr(self.widget, "winfo_viewable") and not self.widget.winfo_viewable():
                return False

            x = self.widget.winfo_pointerx()
            y = self.widget.winfo_pointery()

            wx = self.widget.winfo_rootx()
            wy = self.widget.winfo_rooty()
            ww = self.widget.winfo_width()
            wh = self.widget.winfo_height()

            # Strict bounding box check with 1px margin
            if not (wx - 1 <= x <= wx + ww + 1 and wy - 1 <= y <= wy + wh + 1):
                return False

            under = self.widget.winfo_containing(x, y)
            if under is None:
                return False

            w_str = str(self.widget)
            u_str = str(under)
            if u_str == w_str or u_str.startswith(w_str + "."):
                return True

            for child in getattr(self.widget, "winfo_children", lambda: [])():
                c_str = str(child)
                if u_str == c_str or u_str.startswith(c_str + "."):
                    return True

            for attr in ("_canvas", "_text_label", "_image_label"):
                child = getattr(self.widget, attr, None)
                if child is not None:
                    c_str = str(child)
                    if u_str == c_str or u_str.startswith(c_str + "."):
                        return True

            return False
        except Exception:
            return False

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
            self._after_id = self.widget.after(self.delay_ms, self._on_timer_show)
        except Exception:
            pass

    def _on_timer_show(self) -> None:
        self._after_id = None
        if self._tip_window or not self.text:
            return
        if not self._is_pointer_over_widget():
            return
        self.show()

    def _on_leave(self, event=None) -> None:
        # Immediately cancel any pending show timer so fast mouse exit never pops up later
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

        # If pointer has clearly left the widget bounds, hide instantly
        if not self._is_pointer_over_widget():
            self.hide()
        else:
            # Brief check for fast transitions between internal sub-widgets
            try:
                self._leave_id = self.widget.after(35, self._check_leave)
            except Exception:
                self.hide()

    def _check_leave(self) -> None:
        self._leave_id = None
        if not self._is_pointer_over_widget():
            self.hide()

    def _schedule_poll(self) -> None:
        if not self._tip_window:
            return
        try:
            self._poll_id = self.widget.after(40, self._poll_pointer)
        except Exception:
            pass

    def _poll_pointer(self) -> None:
        self._poll_id = None
        if not self._tip_window:
            return
        if not self._is_pointer_over_widget():
            self.hide()
            return
        self._schedule_poll()

    def _fade_step(self, alpha: float) -> None:
        self._fade_id = None
        if not self._tip_window:
            return
        try:
            alpha = min(1.0, alpha + 0.25)
            self._tip_window.wm_attributes("-alpha", alpha)
            if alpha < 1.0:
                self._fade_id = self.widget.after(15, lambda: self._fade_step(alpha))
        except Exception:
            try:
                self._tip_window.wm_attributes("-alpha", 1.0)
            except Exception:
                pass

    def _on_scroll(self, event=None) -> None:
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
        """Render and display the tooltip window with smooth fade-in and positioning."""
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
            tw.wm_withdraw()
            tw.wm_overrideredirect(True)
            try:
                tw.wm_attributes("-topmost", True)
            except Exception:
                pass
            try:
                tw.wm_attributes("-alpha", 0.0)
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
                font=("Segoe UI", 12),
                wraplength=self.wraplength,
            )
            label.pack()

            # Dismiss if user clicks or moves into the tooltip itself
            for target in (tw, container, label):
                target.bind("<ButtonPress>", lambda e: self.hide(), add="+")

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
            tw.wm_deiconify()
            self._tip_window = tw

            # Trigger smooth fade-in
            try:
                self._fade_step(0.0)
            except Exception:
                pass

            # Start watchdog poll to guarantee dismissal when mouse moves away
            self._schedule_poll()
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

        if self._fade_id:
            try:
                self.widget.after_cancel(self._fade_id)
            except Exception:
                pass
            self._fade_id = None

        if self._poll_id:
            try:
                self.widget.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

        if self._tip_window:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None


def add_tooltip(widget: tk.Widget, text: str, *, delay_ms: int = 300, wraplength: int = 280) -> ToolTip:
    """Helper function to create and attach a ToolTip to a widget."""
    return ToolTip(widget, text, delay_ms=delay_ms, wraplength=wraplength)
