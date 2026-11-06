"""Bulk Apply Modal.

Allows users to configure target watch multiplier and max views cap
for all channels and all videos in the queue simultaneously.
"""

from typing import Any, Callable, List, Optional

from .. import ttk_adapter as ctk
from ..components import Toplevel
from ..tooltip import add_tooltip

from ...models.video_task import (
    DEFAULT_MAX_RUNS,
    DEFAULT_SHORTS_MAX_RUNS,
    DEFAULT_TARGET_MULTIPLIER,
    VideoTask,
)


def _get_cfg_int(mgr: Any, key: str, default: int) -> int:
    if not mgr:
        return default
    try:
        val = mgr.get_int(key, default)
        return val if type(val) is int else default
    except Exception:
        return default


def _get_cfg_float(mgr: Any, key: str, default: float) -> float:
    if not mgr:
        return default
    try:
        val = mgr.get_float(key, default)
        return float(val) if isinstance(val, (int, float)) else default
    except Exception:
        return default


class BulkApplyModal(Toplevel):
    """Modal dialog for bulk configuring target multiplier and max views cap across all queued videos or shorts."""

    def __init__(
        self,
        parent,
        tasks: Optional[List[VideoTask]] = None,
        app_controller: Any = None,
        on_applied: Optional[Callable[[], None]] = None,
        shorts_only: Optional[bool] = None,
    ):
        super().__init__(parent)
        self.parent_view = parent
        self.app_controller = app_controller or getattr(parent, "app_controller", None)
        self.on_applied = on_applied

        if shorts_only is not None:
            self.shorts_only = bool(shorts_only)
        else:
            self.shorts_only = bool(getattr(parent, "shorts_only", False))

        if tasks is not None:
            self.tasks = tasks
        else:
            all_tasks = getattr(parent, "video_tasks", []) or []
            self.tasks = [
                t for t in all_tasks
                if (getattr(t, "is_short", False) if self.shorts_only else not getattr(t, "is_short", False))
            ]

        if self.shorts_only:
            self.title("Bulk Apply — Shorts Queue Settings")
            self.geometry("540x350")
            self.minsize(480, 310)
        else:
            self.title("Bulk Apply — Video Queue Settings")
            self.geometry("540x510")
            self.minsize(480, 450)
        self.grab_set()

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # 1. Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        title_text = "⚡ Bulk Apply Shorts Settings" if self.shorts_only else "⚡ Bulk Apply Video Settings"
        ctk.CTkLabel(
            header,
            text=title_text,
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x")

        ch_names = set((t.channel or "Uncategorized").strip() for t in self.tasks) if self.tasks else set()
        ch_count = len(ch_names)
        item_lbl = "short(s)" if self.shorts_only else "video(s)"
        stats_txt = f"{len(self.tasks)} {item_lbl} across {ch_count} channel(s) in queue"
        ctk.CTkLabel(
            header,
            text=stats_txt,
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        current_row = 1

        # 2. Box 1: Target Watch Multiplier (per Video) - ONLY for regular Video Queue
        if not self.shorts_only:
            card_mult = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
            card_mult.grid(row=current_row, column=0, padx=24, pady=(6, 6), sticky="ew")
            current_row += 1

            card_mult_hdr = ctk.CTkFrame(card_mult, fg_color="transparent")
            card_mult_hdr.pack(fill="x", padx=16, pady=(10, 2))

            ctk.CTkLabel(
                card_mult_hdr,
                text="Target Watch Multiplier (per Video)",
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
            ).pack(side="left")

            self.multiplier_switch = ctk.CTkSwitch(
                card_mult_hdr,
                text="",
                width=44,
                command=self._on_multiplier_toggled,
            )
            self.multiplier_switch.pack(side="right")
            self.multiplier_switch.select()
            add_tooltip(
                self.multiplier_switch,
                "Turn ON to edit and apply Target Watch Multiplier.\nTurn OFF to keep current values without updating.",
            )

            ctk.CTkLabel(
                card_mult,
                text="Stop viewing each video once it generates this multiplier of its duration (e.g. 3.0 = 3x length). Set to 0 for unlimited.",
                font=ctk.CTkFont(size=12),
                text_color=("gray35", "gray65"),
                wraplength=460,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 6))

            cfg = getattr(self.app_controller, "config_mgr", None)
            init_mult = _get_cfg_float(cfg, "DefaultVideoTargetViews", DEFAULT_TARGET_MULTIPLIER)
            if self.tasks:
                for t in self.tasks:
                    if getattr(t, "target_multiplier", 0.0) > 0:
                        init_mult = t.target_multiplier
                        break
            self.init_mult_str = str(int(init_mult)) if init_mult.is_integer() else f"{init_mult:.1f}"

            mult_row = ctk.CTkFrame(card_mult, fg_color="transparent")
            mult_row.pack(fill="x", padx=16, pady=(0, 10))

            self.multiplier_entry = ctk.CTkEntry(mult_row, width=90, font=ctk.CTkFont(size=13))
            self.multiplier_entry.insert(0, self.init_mult_str)
            self.multiplier_entry.pack(side="left")

            ctk.CTkLabel(
                mult_row,
                text="x target multiplier (e.g. 3.0 or 0 for unlimited)",
                font=ctk.CTkFont(size=13),
                text_color=("gray30", "gray70"),
            ).pack(side="left", padx=(10, 0))

        # 3. Box 2: Max Views Cap
        card_views = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
        card_views.grid(row=current_row, column=0, padx=24, pady=(6, 6), sticky="ew")
        current_row += 1

        card_views_hdr = ctk.CTkFrame(card_views, fg_color="transparent")
        card_views_hdr.pack(fill="x", padx=16, pady=(10, 2))

        views_title = "Max Views Cap (per Short)" if self.shorts_only else "Max Views Cap (per Video)"
        ctk.CTkLabel(
            card_views_hdr,
            text=views_title,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(side="left")

        self.views_switch = ctk.CTkSwitch(
            card_views_hdr,
            text="",
            width=44,
            command=self._on_views_toggled,
        )
        self.views_switch.pack(side="right")
        self.views_switch.select()
        add_tooltip(
            self.views_switch,
            "Turn ON to edit and apply Max Views Cap.\nTurn OFF to keep current values without updating.",
        )

        views_desc = (
            "Maximum number of views a short can have. Stops playing once reached to avoid YouTube algorithm flags. Set to 0 for unlimited."
            if self.shorts_only
            else "Maximum number of views a video can have. Stops playing once reached to avoid YouTube algorithm flags. Set to 0 for unlimited."
        )
        ctk.CTkLabel(
            card_views,
            text=views_desc,
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        cfg = getattr(self.app_controller, "config_mgr", None)
        if self.shorts_only:
            init_views = _get_cfg_int(cfg, "DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS)
        else:
            init_views = _get_cfg_int(cfg, "DefaultVideoMaxViews", DEFAULT_MAX_RUNS)
        if self.tasks:
            for t in self.tasks:
                if getattr(t, "max_runs", 0) > 0:
                    init_views = t.max_runs
                    break
        self.init_views_str = str(init_views)

        views_row = ctk.CTkFrame(card_views, fg_color="transparent")
        views_row.pack(fill="x", padx=16, pady=(0, 10))

        self.max_views_entry = ctk.CTkEntry(views_row, width=90, font=ctk.CTkFont(size=13))
        self.max_views_entry.insert(0, self.init_views_str)
        self.max_views_entry.pack(side="left")
        self.run_entry = self.max_views_entry  # Backwards compatibility alias

        ctk.CTkLabel(
            views_row,
            text="views max (e.g. 5 or 0 for unlimited)",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
        ).pack(side="left", padx=(10, 0))

        # 4. Error message area
        self.err_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#EF4444",
            anchor="w",
        )
        self.err_lbl.grid(row=current_row, column=0, padx=24, pady=(4, 4), sticky="w")
        current_row += 1

        # 5. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=current_row, column=0, padx=24, pady=(6, 16), sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=100,
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "gray90"),
            command=self.destroy,
        ).pack(side="right", padx=(10, 0))

        ctk.CTkButton(
            btn_frame,
            text="Apply Changes",
            width=130,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self._apply,
        ).pack(side="right")

    def _on_multiplier_toggled(self):
        is_on = bool(self.multiplier_switch.get()) if hasattr(self, "multiplier_switch") and self.multiplier_switch else False
        init_str = getattr(self, "init_mult_str", "0")
        if hasattr(self, "multiplier_entry") and self.multiplier_entry:
            if is_on:
                self.multiplier_entry.configure(state="normal")
            else:
                self.multiplier_entry.configure(state="normal")
                self.multiplier_entry.delete(0, "end")
                self.multiplier_entry.insert(0, init_str)
                self.multiplier_entry.configure(state="disabled")

    def _on_views_toggled(self):
        is_on = bool(self.views_switch.get()) if hasattr(self, "views_switch") and self.views_switch else False
        init_str = getattr(self, "init_views_str", "0")
        entry = getattr(self, "max_views_entry", getattr(self, "run_entry", None))
        if entry is not None:
            if is_on:
                entry.configure(state="normal")
            else:
                entry.configure(state="normal")
                entry.delete(0, "end")
                entry.insert(0, init_str)
                entry.configure(state="disabled")

    def _apply(self):
        shorts_only = getattr(self, "shorts_only", False)

        is_mult_on = bool(self.multiplier_switch.get()) if getattr(self, "multiplier_switch", None) is not None else hasattr(self, "multiplier_entry")
        is_views_on = bool(self.views_switch.get()) if getattr(self, "views_switch", None) is not None else (hasattr(self, "max_views_entry") or hasattr(self, "run_entry"))

        # 1. Validate target multiplier (only for video queue and if switch is ON)
        mult_val = 0.0
        if not shorts_only and is_mult_on and hasattr(self, "multiplier_entry"):
            raw_mult = self.multiplier_entry.get().strip()
            if raw_mult:
                try:
                    mult_val = float(raw_mult)
                    if mult_val < 0:
                        self.err_lbl.configure(text="Target multiplier cannot be negative.")
                        return
                except ValueError:
                    self.err_lbl.configure(text="Please enter a valid number for target multiplier.")
                    return

        # 2. Validate max views cap (only if switch is ON)
        views_entry = getattr(self, "max_views_entry", getattr(self, "run_entry", None))
        max_views_val = 0
        if is_views_on and views_entry is not None:
            raw_views = views_entry.get().strip()
            if raw_views:
                try:
                    max_views_val = int(raw_views)
                    if max_views_val < 0:
                        self.err_lbl.configure(text="Max views cap cannot be negative.")
                        return
                except ValueError:
                    self.err_lbl.configure(text="Please enter a valid whole number for max views.")
                    return

        # 3. Apply settings to all tasks selectively based on active switches
        for task in self.tasks:
            if not shorts_only and is_mult_on:
                task.target_multiplier = mult_val
            if is_views_on:
                task.max_runs = max_views_val

        if hasattr(self.parent_view, "save_queue"):
            self.parent_view.save_queue()
        if hasattr(self.parent_view, "refresh_list"):
            self.parent_view.refresh_list()

        ch_view = getattr(self.app_controller, "views", {}).get("channels")
        if ch_view is not None and hasattr(ch_view, "load_channels"):
            ch_view.load_channels()

        if callable(getattr(self, "on_applied", None)):
            self.on_applied()

        self.destroy()
