"""Bulk Apply Modal.

Allows users to configure target watch multiplier and max views cap
for all channels and all videos in the queue simultaneously.
"""

from tkinter import messagebox
from typing import Any, Callable, List, Optional
import customtkinter as ctk

from ...models.video_task import DEFAULT_MAX_RUNS, DEFAULT_TARGET_MULTIPLIER, VideoTask


class BulkApplyModal(ctk.CTkToplevel):
    """Modal dialog for bulk configuring target multiplier and max views cap across all queued videos."""

    def __init__(
        self,
        parent,
        tasks: Optional[List[VideoTask]] = None,
        app_controller: Any = None,
        on_applied: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.parent_view = parent
        self.app_controller = app_controller or getattr(parent, "app_controller", None)
        self.on_applied = on_applied

        if tasks is not None:
            self.tasks = tasks
        else:
            is_shorts = getattr(parent, "shorts_only", False)
            all_tasks = getattr(parent, "video_tasks", []) or []
            self.tasks = [
                t for t in all_tasks
                if (getattr(t, "is_short", False) if is_shorts else not getattr(t, "is_short", False))
            ]

        self.title("Bulk Apply — Queue Settings")
        self.geometry("540x480")
        self.minsize(480, 440)
        self.grab_set()

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # 1. Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        ctk.CTkLabel(
            header,
            text="⚡ Bulk Apply Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x")

        ch_names = set((t.channel or "Uncategorized").strip() for t in self.tasks) if self.tasks else set()
        ch_count = len(ch_names)
        stats_txt = f"{len(self.tasks)} video(s) across {ch_count} channel(s) in queue"
        ctk.CTkLabel(
            header,
            text=stats_txt,
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # 2. Box 1: Target Watch Multiplier (per Video)
        card_mult = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
        card_mult.grid(row=1, column=0, padx=24, pady=(6, 6), sticky="ew")

        ctk.CTkLabel(
            card_mult,
            text="Target Watch Multiplier (per Video)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 2))

        ctk.CTkLabel(
            card_mult,
            text="Stop viewing each video once it generates this multiplier of its duration (e.g. 3.0 = 3x length). Set to 0 for unlimited.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        init_mult = DEFAULT_TARGET_MULTIPLIER
        if self.tasks:
            for t in self.tasks:
                if getattr(t, "target_multiplier", 0.0) > 0:
                    init_mult = t.target_multiplier
                    break
        init_mult_str = str(int(init_mult)) if init_mult.is_integer() else f"{init_mult:.1f}"

        mult_row = ctk.CTkFrame(card_mult, fg_color="transparent")
        mult_row.pack(fill="x", padx=16, pady=(0, 10))

        self.multiplier_entry = ctk.CTkEntry(mult_row, width=90, font=ctk.CTkFont(size=13))
        self.multiplier_entry.insert(0, init_mult_str)
        self.multiplier_entry.pack(side="left")

        ctk.CTkLabel(
            mult_row,
            text="x target multiplier (e.g. 3.0 or 0 for unlimited)",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
        ).pack(side="left", padx=(10, 0))

        # 3. Box 2: Max Views Cap (per Video)
        card_views = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
        card_views.grid(row=2, column=0, padx=24, pady=(6, 6), sticky="ew")

        ctk.CTkLabel(
            card_views,
            text="Max Views Cap (per Video)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 2))

        ctk.CTkLabel(
            card_views,
            text="Maximum number of views a video can have. Stops playing once reached to avoid YouTube algorithm flags. Set to 0 for unlimited.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        init_views = DEFAULT_MAX_RUNS
        if self.tasks:
            for t in self.tasks:
                if getattr(t, "max_runs", 0) > 0:
                    init_views = t.max_runs
                    break

        views_row = ctk.CTkFrame(card_views, fg_color="transparent")
        views_row.pack(fill="x", padx=16, pady=(0, 10))

        self.max_views_entry = ctk.CTkEntry(views_row, width=90, font=ctk.CTkFont(size=13))
        self.max_views_entry.insert(0, str(init_views))
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
        self.err_lbl.grid(row=3, column=0, padx=24, pady=(4, 4), sticky="w")

        # 5. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=24, pady=(6, 16), sticky="ew")

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

    def _apply(self):
        # 1. Validate target multiplier
        mult_val = 0.0
        if hasattr(self, "multiplier_entry"):
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

        # 2. Validate max views cap
        views_entry = getattr(self, "max_views_entry", getattr(self, "run_entry", None))
        max_views_val = 0
        if views_entry is not None:
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

        # 3. Apply target multiplier and max views cap to all tasks
        for task in self.tasks:
            task.target_multiplier = mult_val
            task.max_runs = max_views_val

        if hasattr(self.parent_view, "save_queue"):
            self.parent_view.save_queue()
        if hasattr(self.parent_view, "refresh_list"):
            self.parent_view.refresh_list()

        ch_view = getattr(self.app_controller, "views", {}).get("channels")
        if ch_view is not None and hasattr(ch_view, "load_channels"):
            ch_view.load_channels()

        if callable(self.on_applied):
            self.on_applied()

        self.destroy()
