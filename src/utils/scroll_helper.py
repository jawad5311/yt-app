"""Smooth and fast mouse wheel scrolling utility for CTkScrollableFrame."""

from typing import Optional

import customtkinter as ctk


def setup_smooth_scrolling(
    scroll_frame: ctk.CTkScrollableFrame,
    notch_pixels: float = 120.0,
    damping: float = 0.35,
    max_accum: float = 600.0,
) -> None:
    """Install high-performance, smooth, animated mouse wheel scrolling on a CTkScrollableFrame.

    - Neutralizes CustomTkinter's default slow 20px step recursion.
    - Uses ease-out damping for smooth deceleration.
    - Accurately checks widget hierarchy so stacked views never swallow each other's wheel events.
    - Supports traditional mouse wheels (notch deltas of 120), precision touchpads, and Linux buttons.
    """
    canvas = getattr(scroll_frame, "_parent_canvas", None)
    if canvas is None:
        return

    # Neutralize CustomTkinter's internal slow mousewheel recursion
    scroll_frame.check_if_master_is_canvas = lambda widget: False

    canvas_str = str(canvas)
    canvas_prefix = canvas_str + "."

    target_accum = 0.0
    anim_job: Optional[str] = None

    def _is_mouse_over(event) -> bool:
        try:
            if not canvas.winfo_exists():
                return False
            w = getattr(event, "widget", None)
            w_str = str(w) if w else ""
            if w_str == canvas_str or w_str.startswith(canvas_prefix):
                return True
            if hasattr(event, "x_root") and hasattr(event, "y_root"):
                top_w = canvas.winfo_containing(event.x_root, event.y_root)
                top_str = str(top_w) if top_w else ""
                if top_str == canvas_str or top_str.startswith(canvas_prefix):
                    return True
            return False
        except Exception:
            return False

    def _step_anim():
        nonlocal target_accum, anim_job
        anim_job = None
        if not canvas.winfo_exists():
            target_accum = 0.0
            return

        try:
            y_view = canvas.yview()
            # If at top and trying to scroll up, halt immediately
            if target_accum < 0 and y_view[0] <= 0.0:
                target_accum = 0.0
                return

            if abs(target_accum) < 1.0:
                if abs(target_accum) > 0.001:
                    canvas.yview("scroll", int(round(target_accum)), "units")
                target_accum = 0.0
                return

            # Ease-out damping: move fraction of remaining distance
            step = target_accum * damping
            if abs(step) < 2.0:
                step = 2.0 if target_accum > 0 else -2.0
            if abs(step) > abs(target_accum):
                step = target_accum

            old_top = y_view[0]
            canvas.yview("scroll", int(round(step)), "units")
            new_top = canvas.yview()[0]

            # If attempting to scroll down but position didn't move at all and we are at the bottom, halt
            if target_accum > 0 and new_top == old_top and canvas.yview()[1] >= 1.0:
                target_accum = 0.0
                return

            target_accum -= step
            anim_job = canvas.after(16, _step_anim)
        except Exception:
            target_accum = 0.0

    def _on_mousewheel(event):
        nonlocal target_accum, anim_job
        if not _is_mouse_over(event):
            return

        delta = getattr(event, "delta", 0)
        if abs(delta) >= 120:
            # Standard mouse wheel notch on Windows (120 units)
            step = -int((delta / 120.0) * notch_pixels)
        else:
            # Precision touchpad with small frequent deltas
            step = -int(delta * 2.5)

        target_accum += step
        target_accum = max(-max_accum, min(max_accum, target_accum))

        if anim_job is None and canvas.winfo_exists():
            anim_job = canvas.after(0, _step_anim)

        return "break"

    def _on_linux_scroll_up(event):
        nonlocal target_accum, anim_job
        if not _is_mouse_over(event):
            return
        target_accum = max(-max_accum, target_accum - notch_pixels)
        if anim_job is None and canvas.winfo_exists():
            anim_job = canvas.after(0, _step_anim)
        return "break"

    def _on_linux_scroll_down(event):
        nonlocal target_accum, anim_job
        if not _is_mouse_over(event):
            return
        target_accum = min(max_accum, target_accum + notch_pixels)
        if anim_job is None and canvas.winfo_exists():
            anim_job = canvas.after(0, _step_anim)
        return "break"

    scroll_frame._smooth_mousewheel = _on_mousewheel
    canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")
    canvas.bind_all("<Button-4>", _on_linux_scroll_up, add="+")
    canvas.bind_all("<Button-5>", _on_linux_scroll_down, add="+")
