"""Smooth and fast mouse wheel scrolling utility for CTkScrollableFrame / TTK ScrollableFrame."""

from typing import Any, Optional


def setup_smooth_scrolling(
    scroll_frame: Any,
    notch_pixels: float = 65.0,
    damping: float = 0.28,
    max_accum: float = 400.0,
) -> None:
    """Install high-performance, smooth, animated mouse wheel scrolling on a ScrollableFrame.

    - Sets yscrollincrement=1 on the underlying canvas for pixel-precise smooth scrolling.
    - Uses ease-out damping for smooth deceleration on stepped mouse wheels (60 FPS animation loop).
    - Immediate 1:1 pixel movement for precision touchpads.
    - Accurately checks widget hierarchy and topmost window so stacked views never swallow each other's events.
    - Prevents accumulation at boundaries (top and bottom limits).
    """
    canvas = getattr(scroll_frame, "_parent_canvas", None)
    if canvas is None:
        return

    if getattr(scroll_frame, "_smooth_scrolling_installed", False):
        return
    scroll_frame._smooth_scrolling_installed = True

    parent_frame = getattr(scroll_frame, "_parent_frame", None)

    # Ensure 1 unit = 1 pixel for genuine pixel-level smooth interpolation
    try:
        canvas.configure(yscrollincrement=1)
    except Exception:
        pass

    # Neutralize CustomTkinter's internal slow mousewheel recursion
    if hasattr(scroll_frame, "check_if_master_is_canvas"):
        scroll_frame.check_if_master_is_canvas = lambda widget: False

    canvas_str = str(canvas)
    canvas_prefix = canvas_str + "."

    target_accum = 0.0
    anim_job: Optional[str] = None

    def _is_descendant(child: Any) -> bool:
        cur = child
        while cur is not None:
            if cur == canvas or cur == scroll_frame or (parent_frame is not None and cur == parent_frame):
                return True
            cur = getattr(cur, "master", None)
        return False

    def _is_mouse_over(event) -> bool:
        try:
            if not canvas.winfo_exists():
                return False

            # 1. Direct widget or ancestor check
            w = getattr(event, "widget", None)
            if w is not None:
                w_str = str(w)
                if w_str == canvas_str or w_str.startswith(canvas_prefix) or _is_descendant(w):
                    return True

            # 2. Geometry / cursor position check (winfo_containing)
            xr = getattr(event, "x_root", None)
            yr = getattr(event, "y_root", None)
            if isinstance(xr, (int, float)) and isinstance(yr, (int, float)):
                top_w = canvas.winfo_containing(int(xr), int(yr))
                if top_w is not None:
                    top_str = str(top_w)
                    if top_str == canvas_str or top_str.startswith(canvas_prefix) or _is_descendant(top_w):
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
            # If at bottom and trying to scroll down, halt immediately
            if target_accum > 0 and y_view[1] >= 1.0:
                target_accum = 0.0
                return

            if abs(target_accum) < 1.0:
                if abs(target_accum) > 0.01:
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

            # Boundary checks
            if target_accum > 0 and new_top == old_top and canvas.yview()[1] >= 1.0:
                target_accum = 0.0
                return
            if target_accum < 0 and new_top == old_top and canvas.yview()[0] <= 0.0:
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
        if not delta:
            return

        if abs(delta) >= 120:
            # Standard mouse wheel notch on Windows (120 units per notch)
            step = -int((delta / 120.0) * notch_pixels)
            target_accum += step
            target_accum = max(-max_accum, min(max_accum, target_accum))

            if anim_job is None and canvas.winfo_exists():
                anim_job = canvas.after(0, _step_anim)
        else:
            # Precision touchpad: direct 1:1 responsive pixel scrolling
            step = -int(delta * 1.5)
            if step != 0 and canvas.winfo_exists():
                y_view = canvas.yview()
                if (step < 0 and y_view[0] > 0.0) or (step > 0 and y_view[1] < 1.0):
                    canvas.yview("scroll", step, "units")

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
