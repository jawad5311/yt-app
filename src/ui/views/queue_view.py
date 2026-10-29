import webbrowser
from tkinter import TclError, messagebox
from typing import Any, Callable, List, Optional

import customtkinter as ctk

from ...models.channel import NO_CHANNEL_VALUE, Channel, VideoGroup, group_videos_by_channel
from ...models.video_task import DEFAULT_MAX_RUNS, DEFAULT_TARGET_MULTIPLIER, DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS, VideoTask
from ...models.watch_stats import format_totals_line, format_video_watch_ratio, format_watch_line, format_watch_multiplier, resolve_video_key
from ...utils.helpers import clean_youtube_url
from ...utils.scroll_helper import setup_smooth_scrolling


class QueueView(ctk.CTkFrame):
    shorts_only: bool = False

    def __init__(self, parent, app_controller, shorts_only: bool = False):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.shorts_only = shorts_only
        self.video_tasks: List[VideoTask] = []
        self.collapsed_groups: set = set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        header_title = "Shorts Automation Queue" if getattr(self, "shorts_only", False) else "Video Automation Queue"
        ctk.CTkLabel(
            header_frame,
            text=header_title,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        add_btn_text = "+ Add Target Short" if getattr(self, "shorts_only", False) else "+ Add Target Video"
        ctk.CTkButton(
            btn_frame,
            text=add_btn_text,
            command=lambda: self.open_video_modal(),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="⚡ Bulk Apply",
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self.open_bulk_apply_modal,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="🗑 Reset Stats",
            fg_color="#616161",
            hover_color="#3F3F3F",
            command=self.reset_stats,
        ).pack(side="right", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="🗑 Clear Queue",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_queue,
        ).pack(side="right")

        # Scrollable Task List
        scroll_label = "Queued Target Shorts" if getattr(self, "shorts_only", False) else "Queued Target Videos"
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text=scroll_label)
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)
        self._setup_fast_scrolling()

        self.load_initial_queue()

    def _setup_fast_scrolling(self):
        """Install smooth, responsive, animated mouse wheel scrolling on the scroll frame."""
        setup_smooth_scrolling(self.scroll_frame, notch_pixels=120.0, damping=0.35)

    def _default_watch_duration(self) -> str:
        """Default duration for newly created tasks (Settings -> Default Watch Duration)."""
        configured = self.app_controller.config_mgr.get_setting("WatchtimeDuration", DEFAULT_WATCHTIME_DURATION)
        return configured if configured in WATCHTIME_OPTIONS else DEFAULT_WATCHTIME_DURATION

    def _stats(self):
        """Watch statistics store (None when the app controller has none)."""
        return getattr(self.app_controller, "stats_store", None)

    def _group_totals_line(self, group: VideoGroup) -> str:
        """``27 full runs · 12h 04m watched`` for one channel section."""
        store = self._stats()
        if store is None:
            return ""
        totals = store.channel_totals(group.channel.name)
        return format_totals_line(totals) if totals.runs or totals.watch_seconds else ""

    @staticmethod
    def _restore_video_modal_grab(modal):
        """Callback that re-grabs the task modal after a nested dialog closes."""

        def restore() -> None:
            try:
                if modal.winfo_exists():
                    modal.grab_set()
            except TclError:
                pass

        return restore

    def load_initial_queue(self):
        raw_list = self.app_controller.config_mgr.get_video_queue()
        self.video_tasks = [VideoTask.from_dict(v) for v in raw_list]
        self.refresh_list()
        self.app_controller.on_queue_changed(self.video_tasks)

    def save_queue(self):
        dict_list = [v.to_dict() for v in self.video_tasks]
        self.app_controller.config_mgr.set_video_queue(dict_list)
        self.app_controller.on_queue_changed(self.video_tasks)

    def show_popup_notification(self, title: str, message: str) -> None:
        """Display a pop-up notification dialog for user updates."""
        try:
            messagebox.showinfo(title, message, parent=self)
        except Exception:
            try:
                messagebox.showinfo(title, message)
            except Exception:
                pass

    # -------------------------------------------------------------- channels

    def _channels(self) -> List[Channel]:
        return [Channel.from_dict(entry) for entry in self.app_controller.config_mgr.get_channels()]

    def _channel_values(self) -> List[str]:
        """Dropdown values for the task modal: every channel plus '(None)'."""
        return [NO_CHANNEL_VALUE] + [channel.name for channel in self._channels()]

    def open_bulk_apply_modal(self):
        """Open modal dialog to bulk configure multiplier and max views for all queue videos."""
        matching_tasks = [
            t for t in self.video_tasks
            if (getattr(t, "is_short", False) if getattr(self, "shorts_only", False) else not getattr(t, "is_short", False))
        ]
        if not matching_tasks:
            queue_name = "shorts" if getattr(self, "shorts_only", False) else "video"
            messagebox.showinfo("Empty Queue", f"There are no videos in the {queue_name} queue to configure.", parent=self)
            return

        from .bulk_apply_modal import BulkApplyModal
        BulkApplyModal(
            self,
            tasks=matching_tasks,
            app_controller=self.app_controller,
            on_applied=self.refresh_list,
        )

    def open_manage_channel_modal(self, group: Any):
        """Open modal dialog to manage run limits and target watch time for this channel."""
        from .manage_channel_modal import ManageChannelModal
        ManageChannelModal(
            self,
            channel=group.channel,
            group_videos=group.videos,
            app_controller=self.app_controller,
            on_applied=self.refresh_list,
        )

    def reassign_channel(self, old_name: str, new_name: str) -> None:
        """Follow a channel rename so the video relation stays intact."""
        changed = False
        for task in self.video_tasks:
            if task.channel == old_name:
                task.channel = new_name
                changed = True
        if changed:
            self.save_queue()
        store = self._stats()
        if store is not None:
            store.rename_channel(old_name, new_name)
        self.refresh_list()

    def unassign_channel(self, name: str) -> int:
        """Detach videos from a deleted channel; returns how many were detached."""
        count = 0
        for task in self.video_tasks:
            if task.channel == name:
                task.channel = ""
                count += 1
        if count:
            self.save_queue()
        store = self._stats()
        if store is not None:
            store.unassign_channel(name)
        self.refresh_list()
        return count

    def unassign_all_channels(self) -> None:
        changed = False
        for task in self.video_tasks:
            if task.channel:
                task.channel = ""
                changed = True
        if changed:
            self.save_queue()
        store = self._stats()
        if store is not None:
            store.clear_channel_assignments()
        self.refresh_list()

    def open_video_url(self, url: str):
        if url and url.strip():
            webbrowser.open(url.strip())
        else:
            messagebox.showinfo("Notice", "No direct video URL is specified for this task.")

    def open_video_modal(
        self,
        edit_index: Optional[int] = None,
        preselected_channel: Optional[str] = None,
        on_saved: Optional[Any] = None,
    ):
        index = edit_index if edit_index is not None else -1
        is_editing = 0 <= index < len(self.video_tasks)
        if is_editing:
            target_task = self.video_tasks[index]
        else:
            target_task = VideoTask(
                watchtime_duration=self._default_watch_duration(),
                channel=preselected_channel or "",
                target_multiplier=DEFAULT_TARGET_MULTIPLIER,
                max_runs=DEFAULT_MAX_RUNS,
            )

        modal = ctk.CTkToplevel(self)
        if is_editing:
            modal_title = "Edit Target Short Task" if getattr(self, "shorts_only", False) else "Edit Target Video Task"
        else:
            modal_title = "Add Target Short Task" if getattr(self, "shorts_only", False) else "Add Target Video Task"
        modal.title(modal_title)
        modal.geometry("520x700")
        modal.grab_set()

        url_label_text = "Short URL:" if getattr(self, "shorts_only", False) else "Video URL:"
        url_placeholder = "https://www.youtube.com/shorts/..." if getattr(self, "shorts_only", False) else "https://www.youtube.com/watch?v=..."
        ctk.CTkLabel(modal, text=url_label_text, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(15, 2))
        url_entry = ctk.CTkEntry(modal, placeholder_text=url_placeholder, width=460)
        url_entry.insert(0, target_task.url)
        url_entry.pack(padx=20, pady=(0, 10))

        def _auto_clean_url(*_):
            raw = url_entry.get().strip()
            if raw:
                cleaned = clean_youtube_url(raw, as_short=getattr(self, "shorts_only", False))
                if cleaned and cleaned != raw:
                    url_entry.delete(0, "end")
                    url_entry.insert(0, cleaned)

        url_entry.bind("<FocusOut>", _auto_clean_url)
        url_entry.bind("<KeyRelease>", lambda e: modal.after(50, _auto_clean_url))
        url_entry.bind("<<Paste>>", lambda e: modal.after(50, _auto_clean_url))
        url_entry.bind("<Control-v>", lambda e: modal.after(50, _auto_clean_url))
        url_entry.bind("<Control-V>", lambda e: modal.after(50, _auto_clean_url))

        title1_label = "Short Title 1 (Primary):" if getattr(self, "shorts_only", False) else "Video Title 1 (Primary):"
        title1_placeholder = "Primary short title or query (Attempt 1)" if getattr(self, "shorts_only", False) else "Primary video title or query (Attempt 1)"
        ctk.CTkLabel(modal, text=title1_label, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title1_entry = ctk.CTkEntry(modal, placeholder_text=title1_placeholder, width=460)
        title1_entry.insert(0, target_task.title)
        title1_entry.pack(padx=20, pady=(0, 6))

        title2_label = "Short Title 2 (Retry 2 - Optional):" if getattr(self, "shorts_only", False) else "Video Title 2 (Retry 2 - Optional):"
        title2_placeholder = "Alternative title or variation (Attempt 2 if 1 fails)"
        ctk.CTkLabel(modal, text=title2_label, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title2_entry = ctk.CTkEntry(modal, placeholder_text=title2_placeholder, width=460)
        title2_entry.insert(0, target_task.title_2)
        title2_entry.pack(padx=20, pady=(0, 6))

        title3_label = "Short Title 3 (Retry 3 - Optional):" if getattr(self, "shorts_only", False) else "Video Title 3 (Retry 3 - Optional):"
        title3_placeholder = "Alternative title or variation (Attempt 3 if 2 fails)"
        ctk.CTkLabel(modal, text=title3_label, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title3_entry = ctk.CTkEntry(modal, placeholder_text=title3_placeholder, width=460)
        title3_entry.insert(0, target_task.title_3)
        title3_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Watch Duration:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        duration_option = ctk.CTkOptionMenu(modal, values=WATCHTIME_OPTIONS, width=460)
        duration_option.set(target_task.watchtime_duration or self._default_watch_duration())
        duration_option.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Channel:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        channel_option = ctk.CTkOptionMenu(modal, values=self._channel_values(), width=460)
        chosen_channel = target_task.channel or preselected_channel or ""
        channel_option.set(chosen_channel if chosen_channel in self._channel_values() else NO_CHANNEL_VALUE)
        channel_option.pack(padx=20, pady=(0, 6))

        def refresh_channel_options(selected: str = "") -> None:
            """Repaint the dropdown after the channel list changed underneath it."""
            values = self._channel_values()
            channel_option.configure(values=values)
            current = channel_option.get()
            if selected in values:
                channel_option.set(selected)
            elif current in values:
                channel_option.set(current)
            else:
                channel_option.set(NO_CHANNEL_VALUE)

        def open_channel_dialog() -> None:
            views = getattr(self.app_controller, "views", None)
            channels_view = views.get("channels") if isinstance(views, dict) else None
            if channels_view is None:
                return
            channels_view.open_channel_modal(
                on_saved=lambda channel: refresh_channel_options(selected=channel.name),
                on_close=self._restore_video_modal_grab(modal),
            )

        ctk.CTkButton(
            modal,
            text="+ New Channel",
            width=130,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=open_channel_dialog,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Target Multiplier (e.g. 3.0x, 0 or blank = unlimited, default 3x):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        target_multiplier_entry = ctk.CTkEntry(modal, placeholder_text="e.g. 3.0 (0 or blank = unlimited)", width=460)
        if getattr(target_task, "target_multiplier", 0.0) > 0:
            mult_val = target_task.target_multiplier
            target_multiplier_entry.insert(0, str(int(mult_val)) if mult_val.is_integer() else f"{mult_val:.1f}")
        target_multiplier_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Max Views Cap (Full Runs Limit, 0 or blank = unlimited, default 5):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        max_runs_entry = ctk.CTkEntry(modal, placeholder_text="e.g. 5 (0 or blank = unlimited)", width=460)
        if target_task.max_runs > 0:
            max_runs_entry.insert(0, str(target_task.max_runs))
        max_runs_entry.pack(padx=20, pady=(0, 10))

        def on_submit():
            url = clean_youtube_url(url_entry.get().strip(), as_short=getattr(self, "shorts_only", False))
            t1 = title1_entry.get().strip()
            t2 = title2_entry.get().strip()
            t3 = title3_entry.get().strip()
            if not url and not t1 and not t2 and not t3:
                err_msg = "Please provide at least a Short URL or Title!" if getattr(self, "shorts_only", False) else "Please provide at least a URL or Video Title!"
                messagebox.showerror("Error", err_msg, parent=modal)
                return

            raw_mult = target_multiplier_entry.get().strip()
            try:
                target_mult_val = max(0.0, float(raw_mult)) if raw_mult else (DEFAULT_TARGET_MULTIPLIER if not is_editing else 0.0)
            except ValueError:
                target_mult_val = DEFAULT_TARGET_MULTIPLIER if not is_editing else 0.0

            raw_limit = max_runs_entry.get().strip()
            try:
                max_runs_val = max(0, int(raw_limit)) if raw_limit else (DEFAULT_MAX_RUNS if not is_editing else 0)
            except ValueError:
                max_runs_val = DEFAULT_MAX_RUNS if not is_editing else 0

            updated_task = VideoTask(
                url=url,
                title=t1,
                title_2=t2,
                title_3=t3,
                keywords=t1,
                keywords_2=t2,
                keywords_3=t3,
                watchtime_duration=duration_option.get(),
                channel="" if channel_option.get() == NO_CHANNEL_VALUE else channel_option.get(),
                enabled=target_task.enabled,
                max_runs=max_runs_val,
                target_multiplier=target_mult_val,
                duration_seconds=target_task.duration_seconds,
                duration_text=target_task.duration_text,
            )

            if is_editing:
                self._apply_task_edit(index, updated_task)
            else:
                self.video_tasks.append(updated_task)

            self.save_queue()
            self.refresh_list()
            modal.destroy()
            if on_saved is not None:
                on_saved(updated_task)

        btn_text = "Save Changes" if is_editing else ("Add Short Task" if getattr(self, "shorts_only", False) else "Add Task")
        btn_color = "#1F6AA5" if is_editing else "#2FA572"
        btn_hover = "#144870" if is_editing else "#1E7B52"

        ctk.CTkButton(modal, text=btn_text, fg_color=btn_color, hover_color=btn_hover, command=on_submit).pack(pady=10)

    def _apply_task_edit(self, index: int, updated_task: VideoTask) -> None:
        old_task = self.video_tasks[index]
        if resolve_video_key(old_task) != resolve_video_key(updated_task):
            # Duration belongs to the old identity, just like its history.
            updated_task.duration_seconds = 0
            updated_task.duration_text = ""
        store = self._stats()
        if store is not None:
            store.handle_task_edited(old_task, updated_task)
        self.video_tasks[index] = updated_task

    def remove_task(self, index: int):
        if 0 <= index < len(self.video_tasks):
            del self.video_tasks[index]
            self.save_queue()
            self.refresh_list()

    def clear_queue(self):
        if getattr(self, "shorts_only", False):
            self.video_tasks = [t for t in self.video_tasks if not getattr(t, "is_short", False)]
        else:
            self.video_tasks = [t for t in self.video_tasks if getattr(t, "is_short", False)]
        self.save_queue()
        self.refresh_list()

    def reset_stats(self):
        """Delete every recorded watch statistic after a confirmation."""
        if not messagebox.askyesno(
            "Reset Statistics",
            "Delete all recorded watch statistics?\n\nThis cannot be undone.",
        ):
            return
        if hasattr(self, "app_controller") and hasattr(self.app_controller, "reset_all_watch_stats"):
            self.app_controller.reset_all_watch_stats()
        else:
            store = self._stats()
            if store is not None:
                store.clear_all()
            if hasattr(self, "app_controller") and getattr(self.app_controller, "config_mgr", None):
                self.app_controller.config_mgr.reset_channel_watch_times()
            refresh = getattr(self.app_controller, "refresh_watch_stats", None)
            if callable(refresh):
                refresh()
            else:
                self.refresh_list()

    def refresh_list(self):
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        matching_tasks = [
            t for t in self.video_tasks
            if (t.is_short if getattr(self, "shorts_only", False) else not t.is_short)
        ]

        if not matching_tasks:
            empty_msg = (
                "Shorts queue is empty. Click '+ Add Target Short' to add tasks."
                if getattr(self, "shorts_only", False)
                else "Queue is empty. Click '+ Add Target Video' to add tasks."
            )
            ctk.CTkLabel(
                self.scroll_frame,
                text=empty_msg,
                font=ctk.CTkFont(size=14, slant="italic"),
            ).pack(pady=30)
            return

        for group in group_videos_by_channel(self.video_tasks, self._channels(), include_archived=False):
            if getattr(self, "shorts_only", False):
                filtered_videos = [(idx, t) for (idx, t) in group.videos if t.is_short]
            else:
                filtered_videos = [(idx, t) for (idx, t) in group.videos if not t.is_short]

            if not filtered_videos:
                continue

            filtered_group = VideoGroup(channel=group.channel, videos=filtered_videos)
            self._render_group(filtered_group)

    def _render_group(self, group: VideoGroup):
        """Render one channel section (header + its video cards)."""
        container = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        container.pack(fill="x", padx=5, pady=(6, 4))
        container.grid_columnconfigure(0, weight=1)

        is_collapsed = group.name in self.collapsed_groups

        def _toggle_collapse(event=None):
            if group.name in self.collapsed_groups:
                self.collapsed_groups.remove(group.name)
            else:
                self.collapsed_groups.add(group.name)
            self.refresh_list()

        header = ctk.CTkFrame(container, corner_radius=6, fg_color=("gray88", "gray22"), cursor="hand2")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.bind("<Button-1>", _toggle_collapse)

        col_offset = 0
        if not group.is_uncategorized:
            def _toggle_channel():
                new_state = bool(ch_chk.get())
                group.channel.enabled = new_state
                channels = self._channels()
                for ch in channels:
                    if ch.name.lower() == group.channel.name.lower():
                        ch.enabled = new_state
                if not new_state:
                    # Move channel to the bottom of the channels list
                    matching = [c for c in channels if c.name.lower() == group.channel.name.lower()]
                    if matching:
                        ch_obj = matching[0]
                        channels.remove(ch_obj)
                        channels.append(ch_obj)
                    # Also move all videos belonging to this channel to the bottom of self.video_tasks
                    ch_name = group.channel.name.lower()
                    non_ch_tasks = [t for t in self.video_tasks if (t.channel or "").strip().lower() != ch_name]
                    ch_tasks = [t for t in self.video_tasks if (t.channel or "").strip().lower() == ch_name]
                    self.video_tasks = non_ch_tasks + ch_tasks
                    self.save_queue()

                self.app_controller.config_mgr.set_channels([c.to_dict() for c in channels])
                self.refresh_list()
                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()

            ch_chk = ctk.CTkCheckBox(
                header,
                text="",
                width=24,
                command=_toggle_channel,
            )
            if group.channel.enabled:
                ch_chk.select()
            else:
                ch_chk.deselect()
            ch_chk.grid(row=0, column=0, rowspan=2, padx=(10, 2), pady=6, sticky="w")
            col_offset = 1

        arrow = "▶ " if is_collapsed else "▼ "
        title = f"{arrow}📺 {group.name}"
        if not group.is_uncategorized and not group.channel.enabled:
            title += " [DISABLED]"
        elif not group.is_uncategorized and group.channel.target_achieved:
            title += " [TARGET ACHIEVED]"
        item_word = "short(s)" if getattr(self, "shorts_only", False) else "video(s)"
        title += f"  •  {group.count} {item_word}"

        totals_line = self._group_totals_line(group)
        if totals_line:
            title += f"  •  {totals_line}"
        if not group.is_uncategorized and getattr(group.channel, "target_watch_seconds", 0.0) > 0:
            title += f"  •  Target: {group.channel.target_watch_hours:.1f}h"
        title_lbl = ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            justify="left",
            anchor="w",
            cursor="hand2",
            text_color=("gray50", "gray50") if (not group.is_uncategorized and not group.channel.enabled) else None,
        )
        title_lbl.grid(row=0, column=col_offset, padx=12, pady=(6, 0), sticky="ew")
        title_lbl.bind("<Button-1>", _toggle_collapse)

        if group.url:
            url_lbl = ctk.CTkLabel(
                header,
                text=group.url,
                font=ctk.CTkFont(size=12),
                text_color=("gray30", "gray70"),
                justify="left",
                anchor="w",
                cursor="hand2",
            )
            url_lbl.grid(row=1, column=col_offset, padx=12, pady=(0, 6), sticky="ew")
            url_lbl.bind("<Button-1>", _toggle_collapse)

        header_btns = ctk.CTkFrame(header, fg_color="transparent")
        header_btns.grid(row=0, column=col_offset + 1, rowspan=2, padx=8, pady=6, sticky="e")

        if not group.is_uncategorized:
            def _archive_channel(ch_name=group.channel.name):
                channels = self._channels()
                for ch in channels:
                    if ch.name.lower() == ch_name.lower():
                        ch.archived = True
                self.app_controller.config_mgr.set_channels([c.to_dict() for c in channels])
                self.refresh_list()
                channels_view = getattr(self.app_controller, "views", {}).get("channels")
                if channels_view is not None:
                    channels_view.load_channels()
                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()

            ctk.CTkButton(
                header_btns,
                text="📁 Archive",
                width=80,
                fg_color="#D97706",
                hover_color="#B45309",
                command=_archive_channel,
            ).pack(side="left", padx=(0, 4))

        if group.url:
            ctk.CTkButton(
                header_btns,
                text="🔗 Open Channel",
                width=110,
                fg_color="#2E7D32",
                hover_color="#1B5E20",
                command=lambda u=group.url: webbrowser.open(u),
            ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            header_btns,
            text="⚙ Manage",
            width=85,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=lambda g=group: self.open_manage_channel_modal(g),
        ).pack(side="left")

        if is_collapsed:
            return

        if not group.videos:
            item_noun = "shorts" if getattr(self, "shorts_only", False) else "videos"
            add_btn = "+ Add Target Short" if getattr(self, "shorts_only", False) else "+ Add Target Video"
            ctk.CTkLabel(
                container,
                text=f"No {item_noun} in this channel yet. Click '{add_btn}' to add one.",
                font=ctk.CTkFont(size=13, slant="italic"),
                anchor="w",
            ).grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")
            return

        total_in_group = len(group.videos)
        for g_idx, (real_index, task) in enumerate(group.videos):
            is_first = (g_idx == 0)
            is_last = (g_idx == total_in_group - 1)

            def _move_up(idx=g_idx):
                if idx > 0:
                    curr_real = group.videos[idx][0]
                    prev_real = group.videos[idx - 1][0]
                    self.video_tasks[curr_real], self.video_tasks[prev_real] = (
                        self.video_tasks[prev_real],
                        self.video_tasks[curr_real],
                    )
                    self.save_queue()
                    self.refresh_list()

            def _move_down(idx=g_idx):
                if idx < total_in_group - 1:
                    curr_real = group.videos[idx][0]
                    next_real = group.videos[idx + 1][0]
                    self.video_tasks[curr_real], self.video_tasks[next_real] = (
                        self.video_tasks[next_real],
                        self.video_tasks[curr_real],
                    )
                    self.save_queue()
                    self.refresh_list()

            card = self._build_task_card(
                container,
                g_idx + 1,
                task,
                real_index,
                channel_enabled=group.channel.enabled if not group.is_uncategorized else True,
                can_move_up=not is_first,
                can_move_down=not is_last,
                on_move_up=_move_up,
                on_move_down=_move_down,
                group=group,
            )
            card.grid(row=g_idx + 1, column=0, sticky="ew", pady=(0, 5))

    def _build_task_card(
        self,
        parent,
        position: int,
        task: VideoTask,
        index: int,
        channel_enabled: bool = True,
        can_move_up: bool = False,
        can_move_down: bool = False,
        on_move_up: Optional[Callable[[], None]] = None,
        on_move_down: Optional[Callable[[], None]] = None,
        group: Optional[VideoGroup] = None,
    ):
        """Build a single video card; ``index`` is the task's real queue index."""
        # Outer row wrapper: places reorder buttons outside the video box
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.grid_columnconfigure(0, weight=0)
        row_frame.grid_columnconfigure(1, weight=1)

        def _toggle_task():
            new_state = bool(task_chk.get())
            task.enabled = new_state
            if not new_state and group is not None and getattr(group, "videos", None):
                # Move this unchecked video to the bottom of the group list
                try:
                    curr_real = self.video_tasks.index(task)
                    last_task = group.videos[-1][1]
                    last_real = self.video_tasks.index(last_task)
                    if curr_real != last_real:
                        t = self.video_tasks.pop(curr_real)
                        last_real = self.video_tasks.index(last_task)
                        self.video_tasks.insert(last_real + 1, t)
                except (ValueError, IndexError):
                    pass
            self.save_queue()
            self.refresh_list()

        # Reorder up / down arrows stacked on top of each other OUTSIDE the video box
        reorder_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        reorder_frame.grid(row=0, column=0, padx=(2, 6), sticky="")

        ctk.CTkButton(
            reorder_frame,
            text="▲",
            width=24,
            height=20,
            corner_radius=4,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#374151" if can_move_up else "gray25",
            hover_color="#4B5563" if can_move_up else "gray25",
            state="normal" if can_move_up else "disabled",
            command=on_move_up,
        ).pack(side="top", pady=(0, 2))

        ctk.CTkButton(
            reorder_frame,
            text="▼",
            width=24,
            height=20,
            corner_radius=4,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#374151" if can_move_down else "gray25",
            hover_color="#4B5563" if can_move_down else "gray25",
            state="normal" if can_move_down else "disabled",
            command=on_move_down,
        ).pack(side="bottom", pady=(2, 0))

        # The video box itself
        card = ctk.CTkFrame(row_frame, corner_radius=6)
        card.grid(row=0, column=1, sticky="ew")
        card.grid_columnconfigure(0, weight=0)  # Checkbox
        card.grid_columnconfigure(1, weight=1)  # Details
        card.grid_columnconfigure(2, weight=0)  # Action entries & delete button

        task_chk = ctk.CTkCheckBox(
            card,
            text="",
            width=24,
            command=_toggle_task,
        )
        task_chk.configure(state="normal")
        if task.enabled:
            task_chk.select()
        else:
            task_chk.deselect()

        task_chk.grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=10, sticky="w")

        # Lifetime watch statistics for this video (recorded runs only).
        store = self._stats()
        record = store.find_for_task(task) if store is not None else None
        current_runs = record.runs if record is not None else 0
        watch_sec = record.watch_seconds if record else 0.0

        effective_duration = float(record.video_duration if record and record.video_duration > 0 else getattr(task, "duration_seconds", 0.0))
        mult_str = format_watch_multiplier(watch_sec, effective_duration)

        is_limit_reached = task.is_target_reached(
            current_runs=current_runs,
            watch_seconds=watch_sec,
            video_duration=effective_duration,
        )

        titles = [t.strip() for t in [task.title, task.title_2, task.title_3] if t and t.strip()]
        title_text = " | ".join(titles) if titles else (task.url or "Untitled Video")
        if len(title_text) > 85:
            title_text = title_text[:82] + "..."

        status_prefix = ""
        if not channel_enabled and not task.enabled:
            status_prefix = "[CHANNEL & VIDEO DISABLED] "
        elif not channel_enabled:
            status_prefix = "[CHANNEL DISABLED] "
        elif not task.enabled:
            status_prefix = "[DISABLED] "
        elif is_limit_reached:
            target_hit = task.target_multiplier > 0 and effective_duration > 0 and (watch_sec / effective_duration) >= task.target_multiplier
            views_hit = task.max_runs > 0 and current_runs >= task.max_runs
            if target_hit and views_hit:
                status_prefix = "[TARGET & MAX VIEWS REACHED] "
            elif target_hit:
                status_prefix = "[TARGET MULTIPLIER REACHED] "
            else:
                status_prefix = "[MAX VIEWS REACHED] "

        mult = float(getattr(task, "target_multiplier", 0.0) or 0.0)
        target_sec = effective_duration * mult if mult > 0 else 0.0

        if effective_duration >= 60.0:
            length_part = f"Length: {max(1, int(round(effective_duration / 60.0)))} min"
        elif effective_duration > 0.0:
            length_part = f"Length: {int(effective_duration)}s"
        else:
            length_part = ""

        ratio_part = format_video_watch_ratio(watch_sec, target_sec)
        if length_part:
            stats_second_line = f"{length_part} | Watch Time: {ratio_part}"
        else:
            stats_second_line = f"Watch Time: {ratio_part}"

        info_text = f"#{position}. {status_prefix}{title_text}\n{stats_second_line}"
        lbl = ctk.CTkLabel(
            card,
            text=info_text,
            justify="left",
            font=ctk.CTkFont(size=13),
            anchor="w",
            text_color=("gray50", "gray50") if (not task.enabled or not channel_enabled or is_limit_reached) else None,
        )
        lbl.grid(row=0, column=1, padx=10, pady=(10, 0), sticky="ew")

        stats_lbl = ctk.CTkLabel(
            card,
            text=format_watch_line(record, max_runs=task.max_runs, use_hours=True, task_duration=effective_duration),
            justify="left",
            font=ctk.CTkFont(size=12),
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        stats_lbl.grid(row=1, column=1, padx=10, pady=(2, 6 if (task.max_runs > 0 or getattr(task, "target_multiplier", 0.0) > 0) else 10), sticky="ew")

        # Action Buttons Frame (Fixed to column 3 on the right edge)
        action_btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        action_btn_frame.grid(row=0, column=2, rowspan=2, padx=10, pady=10, sticky="e")

        ctk.CTkLabel(action_btn_frame, text="Target:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 2))
        target_entry = ctk.CTkEntry(
            action_btn_frame,
            width=46,
            height=28,
            placeholder_text="0",
            justify="center",
        )
        target_val = getattr(task, "target_multiplier", 0.0)
        target_entry.insert(0, str(int(target_val)) if target_val.is_integer() else f"{target_val:.1f}")
        target_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(action_btn_frame, text="x", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(action_btn_frame, text="Max Views:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 2))
        views_entry = ctk.CTkEntry(
            action_btn_frame,
            width=46,
            height=28,
            placeholder_text="0",
            justify="center",
        )
        views_entry.insert(0, str(task.max_runs))
        views_entry.pack(side="left", padx=(0, 8))

        updating_target = False

        def _revert_target(*_):
            if updating_target:
                return
            try:
                curr_stored = getattr(task, "target_multiplier", 0.0)
                target_entry.delete(0, "end")
                target_entry.insert(0, str(int(curr_stored)) if curr_stored.is_integer() else f"{curr_stored:.1f}")
            except Exception:
                pass

        def _on_target_enter(*_):
            nonlocal updating_target
            val_str = target_entry.get().strip()
            if not val_str or val_str in ("0", "0.0", "∞"):
                new_val = 0.0
            else:
                try:
                    new_val = max(0.0, float(val_str))
                except ValueError:
                    _revert_target()
                    return

            updating_target = True
            try:
                task.target_multiplier = new_val
                self.save_queue()
                self.refresh_list()
            finally:
                updating_target = False

            val_disp = f"{int(new_val)}x" if new_val.is_integer() else f"{new_val:.1f}x"
            t_title = task.title or task.url or f"Video #{position}"
            if len(t_title) > 35:
                t_title = t_title[:32] + "..."
            self.show_popup_notification(
                "Target Multiplier Updated",
                f"Target value updated to {val_disp} for:\n'{t_title}'",
            )

        updating_views = False

        def _revert_views(*_):
            if updating_views:
                return
            try:
                curr_stored = task.max_runs
                views_entry.delete(0, "end")
                views_entry.insert(0, str(curr_stored))
            except Exception:
                pass

        def _on_views_enter(*_):
            nonlocal updating_views
            val_str = views_entry.get().strip()
            if not val_str or val_str in ("0", "∞"):
                new_val = 0
            else:
                try:
                    new_val = max(0, int(val_str))
                except ValueError:
                    _revert_views()
                    return

            updating_views = True
            try:
                task.max_runs = new_val
                self.save_queue()
                self.refresh_list()
            finally:
                updating_views = False

            val_disp = str(new_val) if new_val > 0 else "0 (unlimited)"
            t_title = task.title or task.url or f"Video #{position}"
            if len(t_title) > 35:
                t_title = t_title[:32] + "..."
            self.show_popup_notification(
                "Max Views Updated",
                f"Max views value updated to {val_disp} for:\n'{t_title}'",
            )

        target_entry.bind("<FocusOut>", _revert_target)
        target_entry.bind("<Return>", _on_target_enter)
        views_entry.bind("<FocusOut>", _revert_views)
        views_entry.bind("<Return>", _on_views_enter)

        ctk.CTkButton(
            action_btn_frame,
            text="✏ Edit",
            width=60,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=lambda i=index: self.open_video_modal(edit_index=i),
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            action_btn_frame,
            text="🗑 Delete",
            width=65,
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=lambda i=index: self.remove_task(i),
        ).pack(side="left")

        # Progress bar with dynamic color transition
        has_target = getattr(task, "target_multiplier", 0.0) > 0 and effective_duration > 0
        has_views_cap = task.max_runs > 0
        if has_target or has_views_cap:
            r_target = (watch_sec / (task.target_multiplier * effective_duration)) if has_target else 0.0
            r_views = (current_runs / task.max_runs) if has_views_cap else 0.0
            ratio = min(1.0, max(0.0, max(r_target, r_views)))
            if is_limit_reached or ratio >= 1.0:
                p_color = ("#1B5E20", "#2E7D32")
            else:
                sub_ratio = ratio
                r_light = int(255 + (129 - 255) * sub_ratio)
                g_light = int(241 + (199 - 241) * sub_ratio)
                b_light = int(118 + (132 - 118) * sub_ratio)
                r_dark = int(251 + (102 - 251) * sub_ratio)
                g_dark = int(192 + (187 - 192) * sub_ratio)
                b_dark = int(45 + (106 - 45) * sub_ratio)
                p_color = (f"#{r_light:02X}{g_light:02X}{b_light:02X}", f"#{r_dark:02X}{g_dark:02X}{b_dark:02X}")

            bar = ctk.CTkProgressBar(
                card,
                height=8,
                corner_radius=4,
                progress_color=p_color,
                fg_color=("gray85", "gray26"),
            )
            bar.set(ratio)
            bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 8))

        return row_frame
