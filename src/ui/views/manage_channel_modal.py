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
from ..tooltip import add_tooltip

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
        shorts_only: Optional[bool] = None,
    ):
        super().__init__(parent)
        self.parent_view = parent
        self.channel = channel
        self.group_videos = group_videos
        self.app_controller = app_controller
        self.on_applied = on_applied

        if shorts_only is not None:
            self.shorts_only = bool(shorts_only)
        else:
            self.shorts_only = bool(getattr(parent, "shorts_only", False))

        ch_title = channel.name or "Uncategorized"
        self.title(f"Manage Channel — {ch_title}")
        if self.shorts_only:
            self.geometry("540x350")
            self.minsize(480, 310)
        else:
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
        if self.shorts_only:
            stats_txt = f"{len(self.group_videos)} short(s) in queue"
        else:
            stats_txt = f"{len(self.group_videos)} video(s) in queue  •  Current watch time: {curr_hrs:.1f} hours"
        ctk.CTkLabel(
            header,
            text=stats_txt,
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray65"),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        current_row = 1

        # 2. Box 1: Target Watch Multiplier (per Video) - ONLY for Video Queue
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
            self.init_mult_str = str(int(init_mult)) if init_mult.is_integer() else f"{init_mult:.1f}"

            mult_row = ctk.CTkFrame(card_mult, fg_color="transparent")
            mult_row.pack(fill="x", padx=16, pady=(0, 10))

            self.multiplier_entry = ctk.CTkEntry(mult_row, width=90, font=ctk.CTkFont(size=13))
            self.multiplier_entry.insert(0, self.init_mult_str)
            self.multiplier_entry.pack(side="left")

            ctk.CTkLabel(
                mult_row,
                text="x target multiplier (e.g. 2.0 or 0 for unlimited)",
                font=ctk.CTkFont(size=13),
                text_color=("gray30", "gray70"),
            ).pack(side="left", padx=(10, 0))

        # 3. Box 2: Max Views Cap (per Short for shorts queue, per Video for video queue)
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

        init_views = 0
        if self.group_videos:
            for _, t in self.group_videos:
                if t.max_runs > 0:
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

        # 4. Box 3: Target Channel Watch Time (Hours) - ONLY for Video Queue
        if not self.shorts_only:
            card_target = ctk.CTkFrame(self, fg_color=("gray92", "gray17"), corner_radius=8)
            card_target.grid(row=current_row, column=0, padx=24, pady=(6, 6), sticky="ew")
            current_row += 1

            card_target_hdr = ctk.CTkFrame(card_target, fg_color="transparent")
            card_target_hdr.pack(fill="x", padx=16, pady=(10, 2))

            ctk.CTkLabel(
                card_target_hdr,
                text="Target Channel Watch Time (Hours)",
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
            ).pack(side="left")

            self.target_switch = ctk.CTkSwitch(
                card_target_hdr,
                text="",
                width=44,
                command=self._on_target_toggled,
            )
            self.target_switch.pack(side="right")
            self.target_switch.deselect()
            add_tooltip(
                self.target_switch,
                "Turn ON to edit and apply Target Channel Watch Time.\nTurn OFF to keep current values without updating.",
            )

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

            self.init_target_str = str(int(init_target_hrs)) if init_target_hrs.is_integer() else f"{init_target_hrs:.1f}"

            self.target_entry = ctk.CTkEntry(target_row, width=90, font=ctk.CTkFont(size=13))
            self.target_entry.insert(0, self.init_target_str)
            self.target_entry.configure(state="disabled")
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
                    f"✓ Target Achieved ({curr_hrs:.1f} / {self.init_target_str}h) — Channel playback stopped"
                    if is_achieved
                    else f"Status: {curr_hrs:.1f} / {self.init_target_str} hours generated"
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
        self.err_lbl.grid(row=current_row, column=0, padx=24, pady=(2, 4), sticky="w")
        current_row += 1

        # 6. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=current_row, column=0, padx=24, pady=(6, 16), sticky="ew")

        del_txt = "🗑 Delete all shorts" if self.shorts_only else "🗑 Delete all videos"
        ctk.CTkButton(
            btn_frame,
            text=del_txt,
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

    def _on_target_toggled(self):
        is_on = bool(self.target_switch.get()) if hasattr(self, "target_switch") and self.target_switch else False
        init_str = getattr(self, "init_target_str", "0.0")
        if hasattr(self, "target_entry") and self.target_entry:
            if is_on:
                self.target_entry.configure(state="normal")
            else:
                self.target_entry.configure(state="normal")
                self.target_entry.delete(0, "end")
                self.target_entry.insert(0, init_str)
                self.target_entry.configure(state="disabled")

    def _apply(self):
        shorts_only = getattr(self, "shorts_only", False)

        is_mult_on = bool(self.multiplier_switch.get()) if getattr(self, "multiplier_switch", None) is not None else hasattr(self, "multiplier_entry")
        is_views_on = bool(self.views_switch.get()) if getattr(self, "views_switch", None) is not None else (hasattr(self, "max_views_entry") or hasattr(self, "run_entry"))
        is_target_on = bool(self.target_switch.get()) if getattr(self, "target_switch", None) is not None else hasattr(self, "target_entry")

        # 1. Validate target multiplier
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

        # 2. Validate max views cap
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

        # 3. Validate target watch hours
        target_hrs = 0.0
        if not shorts_only and is_target_on and hasattr(self, "target_entry"):
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

        # 4. Apply target multiplier and max views cap to all items in this channel group
        changed_any = False
        for _, task in self.group_videos:
            if not shorts_only and is_mult_on:
                task.target_multiplier = mult_val
                changed_any = True
            if is_views_on:
                task.max_runs = max_views_val
                changed_any = True

        if changed_any:
            if hasattr(self.parent_view, "save_queue"):
                self.parent_view.save_queue()
            if hasattr(self.parent_view, "refresh_list"):
                self.parent_view.refresh_list()

        # 5. Apply target watch seconds to channel (Video Queue only)
        if not shorts_only and is_target_on:
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

        if callable(getattr(self, "on_applied", None)):
            self.on_applied()

        self.destroy()

    def _delete_all_videos(self, confirm: bool = True):
        """Remove all video tasks or shorts for this channel from the queue while preserving all historical stats and watch data."""
        shorts_only = getattr(self, "shorts_only", False)
        ch_name = self.channel.name or "Uncategorized"
        target_ch_name = (self.channel.name or "").strip().lower()
        item_singular = "short" if shorts_only else "video"
        item_plural = "shorts" if shorts_only else "videos"

        current_tasks = getattr(self.parent_view, "video_tasks", []) or []

        def task_channel_matches(t):
            if target_ch_name:
                return (t.channel or "").strip().lower() == target_ch_name
            return not bool((t.channel or "").strip())

        def task_type_matches(t):
            if shorts_only:
                return getattr(t, "is_short", False)
            return not getattr(t, "is_short", False)

        matching = [t for t in current_tasks if task_channel_matches(t) and task_type_matches(t)]

        if not matching:
            if confirm:
                messagebox.showinfo(
                    f"No {item_plural.capitalize()}",
                    f"There are no {item_plural} in the queue for '{ch_name}'.",
                    parent=self,
                )
            return

        if confirm:
            num = len(matching)
            ok = messagebox.askyesno(
                f"Delete All {item_plural.capitalize()}",
                f"Are you sure you want to remove all {num} {item_singular}(s) of '{ch_name}' from the queue?\n\n"
                "Note: Watch time history, runs, and statistics will be safely preserved in storage.",
                parent=self,
            )
            if not ok:
                return

        # Filter out matching items without touching StatRecords
        self.parent_view.video_tasks = [
            t for t in current_tasks
            if not (task_channel_matches(t) and task_type_matches(t))
        ]

        if hasattr(self.app_controller, "queued_video_tasks"):
            self.app_controller.queued_video_tasks = self.parent_view.video_tasks

        if hasattr(self.parent_view, "save_queue"):
            self.parent_view.save_queue()

        if hasattr(self.parent_view, "refresh_list"):
            self.parent_view.refresh_list()

        if callable(getattr(self, "on_applied", None)):
            self.on_applied()

        self.destroy()
