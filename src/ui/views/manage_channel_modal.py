"""Manage Channel Modal.

Allows users to:
1. Adjust the run limit for all videos under a channel.
2. Set the target total watch time (in hours) to generate for this channel.
   When this watch time is achieved, the automation stops playing this channel's videos.
"""

from typing import Any, Callable, List, Optional, Tuple
import customtkinter as ctk

from ...models.channel import Channel
from ...models.video_task import VideoTask


class ManageChannelModal(ctk.CTkToplevel):
    """Modal dialog for configuring channel-wide run limits and target watch time."""

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
        self.geometry("520x490")
        self.minsize(460, 430)
        self.grab_set()

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # 1. Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(20, 10), sticky="ew")

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

        # 2. Box 1: Run Limit for All Videos
        card_runs = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
        card_runs.grid(row=1, column=0, padx=24, pady=(10, 10), sticky="ew")

        ctk.CTkLabel(
            card_runs,
            text="Run Limit for All Videos",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 2))

        ctk.CTkLabel(
            card_runs,
            text="Apply this run limit to every video currently under this channel.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        # Initial run limit from first video or default 3
        init_runs = 3
        if self.group_videos:
            for _, t in self.group_videos:
                if t.max_runs > 0:
                    init_runs = t.max_runs
                    break

        run_row = ctk.CTkFrame(card_runs, fg_color="transparent")
        run_row.pack(fill="x", padx=16, pady=(0, 14))

        self.run_entry = ctk.CTkEntry(run_row, width=90, font=ctk.CTkFont(size=13))
        self.run_entry.insert(0, str(init_runs))
        self.run_entry.pack(side="left")

        ctk.CTkLabel(
            run_row,
            text="runs per video (e.g. 3)",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
        ).pack(side="left", padx=(10, 0))

        # 3. Box 2: Target Watch Time for Channel (Hours)
        card_target = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
        card_target.grid(row=2, column=0, padx=24, pady=(10, 10), sticky="ew")

        ctk.CTkLabel(
            card_target,
            text="Target Channel Watch Time (Hours)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 2))

        ctk.CTkLabel(
            card_target,
            text="The script stops playing this channel's videos once this watch time is achieved. Set to 0 for unlimited.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            wraplength=440,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

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
            status_row.pack(fill="x", padx=16, pady=(0, 12))
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
            # Spacer
            ctk.CTkFrame(card_target, fg_color="transparent", height=4).pack()

        # 4. Error message area
        self.err_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#EF4444",
            anchor="w",
        )
        self.err_lbl.grid(row=3, column=0, padx=24, pady=(2, 6), sticky="w")

        # 5. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=24, pady=(8, 20), sticky="ew")

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
        # 1. Validate run limit
        raw_runs = self.run_entry.get().strip()
        try:
            run_val = int(raw_runs)
            if run_val < 1:
                self.err_lbl.configure(text="Run limit must be at least 1.")
                return
        except ValueError:
            self.err_lbl.configure(text="Please enter a valid whole number for run limit.")
            return

        # 2. Validate target watch hours
        raw_target = self.target_entry.get().strip()
        try:
            target_hrs = float(raw_target)
            if target_hrs < 0:
                self.err_lbl.configure(text="Target watch time cannot be negative.")
                return
        except ValueError:
            self.err_lbl.configure(text="Please enter a valid number for target watch hours.")
            return

        target_seconds = round(target_hrs * 3600.0, 3)

        # 3. Apply run limit to all videos in this channel group
        for _, task in self.group_videos:
            task.max_runs = run_val

        if hasattr(self.parent_view, "save_queue"):
            self.parent_view.save_queue()

        # 4. Apply target watch seconds to channel
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
