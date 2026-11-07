"""Apple macOS HIG styled popup dropdown menu for YT Organic Bot.

Replaces standard Win32 / OS native tk.Menu with an elegant, theme-aware
floating card dropdown menu adhering to Apple Human Interface Guidelines.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

from .theme import get_font, get_theme_colors


class AppleDropdownMenu:
    """Apple macOS HIG styled popup dropdown menu.

    Features:
    - Sleek card container with 1px border and dark/light theme alignment
    - Left checkmark slot indicating selected sort criteria
    - Right badge slot indicating sort direction (e.g., '↓ Desc', '↑ Asc')
    - Apple System Blue hover highlights with crisp typography
    - Automatic dismissal on outside click, Escape key, or item selection
    - Multi-monitor / screen boundary auto-adjustment
    """

    def __init__(self, master: Optional[tk.Misc] = None, mode: Optional[str] = None):
        self.master = master
        self._mode = mode
        self._items: List[Dict[str, Any]] = []
        self._top: Optional[tk.Toplevel] = None
        self._root_bind_id: Optional[str] = None

    def _resolve_mode(self) -> str:
        if self._mode:
            return self._mode
        # Detect mode from master or app controller
        if self.master:
            app = getattr(self.master, "app_controller", None) or getattr(self.master, "master", None)
            mode = getattr(app, "appearance_mode", None)
            if mode in ("Dark", "Light"):
                return mode
        return "Dark"

    def add_command(
        self,
        label: str,
        command: Optional[Callable[[], None]] = None,
        active: bool = False,
        badge: Optional[str] = None,
    ) -> None:
        """Add a selectable command item to the dropdown menu."""
        self._items.append({
            "type": "command",
            "label": label,
            "command": command,
            "active": active,
            "badge": badge,
        })

    def add_separator(self) -> None:
        """Add a horizontal divider line between menu item sections."""
        self._items.append({"type": "separator"})

    def is_open(self) -> bool:
        """Return True if the dropdown menu window is currently visible."""
        return self._top is not None and self._top.winfo_exists()

    def grab_release(self) -> None:
        """Compatibility no-op for tk.Menu.grab_release()."""
        pass

    def tk_popup(self, x: int, y: int) -> None:
        """Compatibility alias for popup presentation."""
        self.show(x, y)

    def close(self) -> None:
        """Close and destroy the dropdown popup window."""
        self.destroy()

    def destroy(self) -> None:
        """Clean up bindings and destroy the toplevel popup."""
        if self._root_bind_id and self.master:
            try:
                root = self.master.winfo_toplevel()
                root.unbind("<Button-1>", self._root_bind_id)
            except Exception:
                pass
            self._root_bind_id = None

        if self._top:
            try:
                self._top.destroy()
            except Exception:
                pass
            self._top = None

    def show(self, x: int, y: int) -> None:
        """Display the Apple HIG dropdown menu at the specified root coordinates."""
        self.destroy()
        if not self.master:
            return

        mode = self._resolve_mode()
        colors = get_theme_colors(mode)
        bg = colors.get("card_bg", "#202024")
        border = colors.get("card_border", "#323238")
        fg = colors.get("fg", "#ffffff")
        accent = colors.get("accent", "#0a84ff")
        hover_bg = colors.get("accent", "#0a84ff")
        hover_fg = "#ffffff"
        muted_fg = colors.get("fg_muted", "#98989d")

        top = tk.Toplevel(self.master)
        self._top = top
        top.wm_overrideredirect(True)
        try:
            top.wm_attributes("-topmost", True)
        except Exception:
            pass

        # Outer 1px Apple border
        outer_frame = tk.Frame(
            top,
            bg=border,
            padx=1,
            pady=1,
        )
        outer_frame.pack(fill="both", expand=True)

        container = tk.Frame(
            outer_frame,
            bg=bg,
            padx=4,
            pady=4,
        )
        container.pack(fill="both", expand=True)

        for item in self._items:
            if item["type"] == "separator":
                sep = tk.Frame(container, bg=border, height=1)
                sep.pack(fill="x", padx=6, pady=4)
                continue

            raw_label = item["label"]
            cmd = item["command"]
            is_active = item.get("active", False)
            badge = item.get("badge")

            # Parse string if formatted as '✓ Title (↓ Desc)' or '   Title'
            title = str(raw_label).strip()
            if title.startswith("✓"):
                is_active = True
                title = title[1:].strip()
            if "(" in title and title.endswith(")"):
                idx = title.rfind("(")
                badge = title[idx + 1 : -1].strip()
                title = title[:idx].strip()

            row = tk.Frame(container, bg=bg, cursor="hand2")
            row.pack(fill="x", padx=2, pady=1)

            chk_text = "✓" if is_active else "  "
            chk_color = accent if is_active else bg
            chk_lbl = tk.Label(
                row,
                text=chk_text,
                font=get_font(size=10, weight="bold"),
                bg=bg,
                fg=chk_color,
                width=2,
                anchor="center",
                cursor="hand2",
            )
            chk_lbl.pack(side="left", padx=(4, 2), pady=4)

            title_lbl = tk.Label(
                row,
                text=title,
                font=get_font(size=9, weight="bold" if is_active else "normal"),
                bg=bg,
                fg=fg,
                anchor="w",
                cursor="hand2",
            )
            title_lbl.pack(side="left", padx=(2, 8), pady=4, fill="x", expand=True)

            badge_lbl = None
            if badge:
                badge_lbl = tk.Label(
                    row,
                    text=badge,
                    font=get_font(size=8, weight="normal"),
                    bg=bg,
                    fg=muted_fg,
                    anchor="e",
                    cursor="hand2",
                )
                badge_lbl.pack(side="right", padx=(4, 6), pady=4)

            def make_handlers(
                r=row,
                c_lbl=chk_lbl,
                t_lbl=title_lbl,
                b_lbl=badge_lbl,
                c=cmd,
                act=is_active,
            ):
                def _enter(e):
                    r.configure(bg=hover_bg)
                    t_lbl.configure(bg=hover_bg, fg=hover_fg)
                    c_lbl.configure(bg=hover_bg, fg=hover_fg if act else hover_bg)
                    if b_lbl:
                        b_lbl.configure(bg=hover_bg, fg="#e0e0e0")

                def _leave(e):
                    r.configure(bg=bg)
                    t_lbl.configure(bg=bg, fg=fg)
                    c_lbl.configure(bg=bg, fg=accent if act else bg)
                    if b_lbl:
                        b_lbl.configure(bg=bg, fg=muted_fg)

                def _click(e=None):
                    self.destroy()
                    if c:
                        c()

                targets = [r, c_lbl, t_lbl]
                if b_lbl:
                    targets.append(b_lbl)
                for w in targets:
                    w.bind("<Enter>", _enter)
                    w.bind("<Leave>", _leave)
                    w.bind("<Button-1>", _click)
                    w.bind("<ButtonRelease-1>", _click)
                    setattr(w, "_click", _click)

            make_handlers()

        # Measure requested size
        top.update_idletasks()
        w = max(185, top.winfo_reqwidth())
        h = top.winfo_reqheight()

        # Adjust position if near screen edge
        try:
            screen_w = top.winfo_screenwidth()
            screen_h = top.winfo_screenheight()
            if x + w > screen_w:
                x = screen_w - w - 8
            if y + h > screen_h:
                y = y - h - 30
        except Exception:
            pass

        top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        top.deiconify()
        top.lift()

        # Auto-dismiss on click outside or Escape
        def _on_root_click(event):
            if not self.is_open():
                return
            try:
                ex, ey = event.x_root, event.y_root
                tx = top.winfo_rootx()
                ty = top.winfo_rooty()
                tw = top.winfo_width()
                th = top.winfo_height()
                if not (tx <= ex <= tx + tw and ty <= ey <= ty + th):
                    self.destroy()
            except Exception:
                self.destroy()

        try:
            root = self.master.winfo_toplevel()
            self._root_bind_id = root.bind("<Button-1>", _on_root_click, add="+")
            top.bind("<Escape>", lambda e: self.destroy())
        except Exception:
            pass
