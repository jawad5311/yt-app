"""Manage Channel Modal.

Allows users to:
1. Set the target watch multiplier (e.g. 2.0x) for each video under a channel.
2. Set the maximum number of views a video can have (cap to avoid YouTube red flags).
3. Set the target total watch time (in hours) to generate for this channel.
4. Delete all videos of this channel from the queue while preserving historical stats.
"""

from tkinter import messagebox
from typing import Any, Callable, List, Optional, Tuple

from .. import ttk_adapter as ctk
from ..components import Toplevel

from ...models.channel import Channel
from ...models.video_task import VideoTask


class ManageChannelModal(Toplevel):
    """Modal dialog for configuring channel-wide target multipliers, max views caps, and target watch time."""

    def __init__(
        self,
        parent,
        channel: Channel,
        group_videos: List[Tuple[int, VideoTask]],
        app_controller: Any,
        on_applied: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.parent_view = parent
        self.channel = channel
        self.group_videos = group_videos
        self.app_controller = app_controller
        self.on_applied = on_applied

        ch_title = channel.name or "Uncategorized"
        self.title(f"Manage Channel — {ch_title}")
        self.geometry("540x630")
        self.minsize(480, 560)
        self.grab_set()

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # 1. Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        ch_name = self.channel.name or "Uncategorized"
        ctk.CTkLabel(
            header,
            text=f"⚙ Manage: {ch_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x")

        curr_hrs = self.channel.watch_seconds / 3600.0
        stats_txt = f"{len(self.group_videos)} video(s) in queue  •  Current watch time: {curr_hrs:.1f} hours"
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
            text="Stop viewing each video once it generates this multiplier of its duration (e.g. 2.0 = 2x length). Set to 0 for unlimited.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        init_mult = 0.0
        if self.group_videos:
            for _, t in self.group_videos:
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
            text="x target multiplier (e.g. 2.0 or 0 for unlimited)",
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

        init_views = 0
        if self.group_videos:
            for _, t in self.group_videos:
                if t.max_runs > 0:
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

        # 4. Box 3: Target Watch Time for Channel (Hours)
        card_target = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
        card_target.grid(row=3, column=0, padx=24, pady=(6, 6), sticky="ew")

        ctk.CTkLabel(
            card_target,
            text="Target Channel Watch Time (Hours)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 2))

        ctk.CTkLabel(
            card_target,
            text="The script stops playing this channel's videos once this total watch time is achieved. Set to 0 for unlimited.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        target_row = ctk.CTkFrame(card_target, fg_color="transparent")
        target_row.pack(fill="x", padx=16, pady=(0, 8))

        init_target_hrs = 0.0
        if getattr(self.channel, "target_watch_seconds", 0.0) > 0:
            init_target_hrs = round(self.channel.target_watch_seconds / 3600.0, 1)

        init_target_str = str(int(init_target_hrs)) if init_target_hrs.is_integer() else f"{init_target_hrs:.1f}"

        self.target_entry = ctk.CTkEntry(target_row, width=90, font=ctk.CTkFont(size=13))
        self.target_entry.insert(0, init_target_str)
        self.target_entry.pack(side="left")

        ctk.CTkLabel(
            target_row,
            text="hours (e.g. 5.0 or 10)",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
        ).pack(side="left", padx=(10, 0))

        # Status badge if target set
        if getattr(self.channel, "target_watch_seconds", 0.0) > 0:
            status_row = ctk.CTkFrame(card_target, fg_color="transparent")
            status_row.pack(fill="x", padx=16, pady=(0, 10))
            is_achieved = self.channel.target_achieved
            color = "#10B981" if is_achieved else "#2563EB"
            status_text = (
                f"✓ Target Achieved ({curr_hrs:.1f} / {init_target_hrs:.1f}h) — Channel playback stopped"
                if is_achieved
                else f"Status: {curr_hrs:.1f} / {init_target_hrs:.1f} hours generated"
            )
            ctk.CTkLabel(
                status_row,
                text=status_text,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=color,
                anchor="w",
            ).pack(fill="x")
        else:
            ctk.CTkFrame(card_target, fg_color="transparent", height=4).pack()

        # 5. Error message area
        self.err_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#EF4444",
            anchor="w",
        )
        self.err_lbl.grid(row=4, column=0, padx=24, pady=(2, 4), sticky="w")

        # 6. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=5, column=0, padx=24, pady=(6, 16), sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="🗑 Delete all videos",
            width=140,
            fg_color="#DC2626",
            hover_color="#991B1B",
            command=self._delete_all_videos,
        ).pack(side="left")

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

        # 3. Validate target watch hours
        target_hrs = 0.0
        if hasattr(self, "target_entry"):
            raw_target = self.target_entry.get().strip()
            if raw_target:
                try:
                    target_hrs = float(raw_target)
                    if target_hrs < 0:
                        self.err_lbl.configure(text="Target watch time cannot be negative.")
                        return
                except ValueError:
                    self.err_lbl.configure(text="Please enter a valid number for target watch hours.")
                    return

        target_seconds = round(target_hrs * 3600.0, 3)

        # 4. Apply target multiplier and max views cap to all videos in this channel group
        for _, task in self.group_videos:
            task.target_multiplier = mult_val
            task.max_runs = max_views_val

        if hasattr(self.parent_view, "save_queue"):
            self.parent_view.save_queue()
        if hasattr(self.parent_view, "refresh_list"):
            self.parent_view.refresh_list()

        # 5. Apply target watch seconds to channel
        ch_name_clean = (self.channel.name or "").strip().lower()
        if ch_name_clean:
            channels = [Channel.from_dict(c) for c in self.app_controller.config_mgr.get_channels()]
            for ch in channels:
                if ch.name.strip().lower() == ch_name_clean:
                    ch.target_watch_seconds = target_seconds
            self.app_controller.config_mgr.set_channels([c.to_dict() for c in channels])
            self.channel.target_watch_seconds = target_seconds

            ch_view = getattr(self.app_controller, "views", {}).get("channels")
            if ch_view is not None and hasattr(ch_view, "load_channels"):
                ch_view.load_channels()

        if callable(self.on_applied):
            self.on_applied()

        self.destroy()

    def _delete_all_videos(self, confirm: bool = True):
        """Remove all video tasks for this channel from the queue while preserving all historical stats and watch data."""
        ch_name = self.channel.name or "Uncategorized"
        target_ch_name = (self.channel.name or "").strip().lower()

        current_tasks = getattr(self.parent_view, "video_tasks", []) or []
        if target_ch_name:
            matching = [t for t in current_tasks if (t.channel or "").strip().lower() == target_ch_name]
        else:
            matching = [t for t in current_tasks if not bool((t.channel or "").strip())]

        if not matching:
            if confirm:
                messagebox.showinfo("No Videos", f"There are no videos in the queue for '{ch_name}'.", parent=self)
            return

        if confirm:
            num = len(matching)
            ok = messagebox.askyesno(
                "Delete All Videos",
                f"Are you sure you want to remove all {num} video(s) of '{ch_name}' from the queue?\n\n"
                "Note: Watch time history, runs, and statistics will be safely preserved in storage.",
                parent=self,
            )
            if not ok:
                return

        # Filter out this channel's videos without touching StatRecords
        if target_ch_name:
            self.parent_view.video_tasks = [
                t for t in current_tasks
                if (t.channel or "").strip().lower() != target_ch_name
            ]
        else:
            self.parent_view.video_tasks = [
                t for t in current_tasks
                if bool((t.channel or "").strip())
            ]

        if hasattr(self.app_controller, "queued_video_tasks"):
            self.app_controller.queued_video_tasks = self.parent_view.video_tasks

        if hasattr(self.parent_view, "save_queue"):
            self.parent_view.save_queue()

        if hasattr(self.parent_view, "refresh_list"):
            self.parent_view.refresh_list()

        if callable(self.on_applied):
            self.on_applied()

        self.destroy()
