import webbrowser
from tkinter import TclError, messagebox
from typing import List, Optional

import customtkinter as ctk

from ...models.channel import NO_CHANNEL_VALUE, Channel, VideoGroup, group_videos_by_channel
from ...models.video_task import DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS, VideoTask
from ...models.watch_stats import format_totals_line, format_watch_line
from ...utils.helpers import clean_youtube_url


class QueueView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.video_tasks: List[VideoTask] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        ctk.CTkLabel(
            header_frame,
            text="Video Automation Queue",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="+ Add Target Video",
            command=lambda: self.open_video_modal(),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="📺 Manage Channels",
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self.open_channels_view,
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
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Queued Target Videos")
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.load_initial_queue()

    def _default_watch_duration(self) -> str:
        """Default duration for newly created tasks (Settings -> Default Watch Duration)."""
        configured = self.app_controller.config_mgr.get_setting("WatchtimeDuration", DEFAULT_WATCHTIME_DURATION)
        return configured if configured in WATCHTIME_OPTIONS else DEFAULT_WATCHTIME_DURATION

    def _stats(self):
        """Watch statistics store (None when the app controller has none)."""
        return getattr(self.app_controller, "stats_store", None)

    def _group_totals_line(self, group: VideoGroup) -> str:
        """``27 runs · 12h 04m watched`` for one channel section."""
        store = self._stats()
        if store is None:
            return ""
        channel_name = "" if group.is_uncategorized else group.channel.name
        totals = store.channel_totals(channel_name)
        return format_totals_line(totals) if totals.runs else ""

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

    # -------------------------------------------------------------- channels

    def _channels(self) -> List[Channel]:
        return [Channel.from_dict(entry) for entry in self.app_controller.config_mgr.get_channels()]

    def _channel_values(self) -> List[str]:
        """Dropdown values for the task modal: every channel plus '(None)'."""
        return [NO_CHANNEL_VALUE] + [channel.name for channel in self._channels()]

    def open_channels_view(self):
        show_view = getattr(self.app_controller, "show_view", None)
        if callable(show_view) and "channels" in getattr(self.app_controller, "views", {}):
            show_view("channels")

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

    def open_video_modal(self, edit_index: Optional[int] = None):
        index = edit_index if edit_index is not None else -1
        is_editing = 0 <= index < len(self.video_tasks)
        if is_editing:
            target_task = self.video_tasks[index]
        else:
            target_task = VideoTask(watchtime_duration=self._default_watch_duration())

        modal = ctk.CTkToplevel(self)
        modal.title("Edit Target Video Task" if is_editing else "Add Target Video Task")
        modal.geometry("520x560")
        modal.grab_set()

        ctk.CTkLabel(modal, text="Video URL:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(15, 2))
        url_entry = ctk.CTkEntry(modal, placeholder_text="https://www.youtube.com/watch?v=...", width=460)
        url_entry.insert(0, target_task.url)
        url_entry.pack(padx=20, pady=(0, 10))

        def _auto_clean_url(*_):
            raw = url_entry.get().strip()
            if raw:
                cleaned = clean_youtube_url(raw)
                if cleaned and cleaned != raw:
                    url_entry.delete(0, "end")
                    url_entry.insert(0, cleaned)

        url_entry.bind("<FocusOut>", _auto_clean_url)
        url_entry.bind("<KeyRelease>", lambda e: modal.after(50, _auto_clean_url))
        url_entry.bind("<<Paste>>", lambda e: modal.after(50, _auto_clean_url))
        url_entry.bind("<Control-v>", lambda e: modal.after(50, _auto_clean_url))
        url_entry.bind("<Control-V>", lambda e: modal.after(50, _auto_clean_url))

        ctk.CTkLabel(modal, text="Video Title 1 (Primary):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title1_entry = ctk.CTkEntry(modal, placeholder_text="Primary video title or query (Attempt 1)", width=460)
        title1_entry.insert(0, target_task.title)
        title1_entry.pack(padx=20, pady=(0, 6))

        ctk.CTkLabel(modal, text="Video Title 2 (Retry 2 - Optional):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title2_entry = ctk.CTkEntry(modal, placeholder_text="Alternative title or variation (Attempt 2 if 1 fails)", width=460)
        title2_entry.insert(0, target_task.title_2)
        title2_entry.pack(padx=20, pady=(0, 6))

        ctk.CTkLabel(modal, text="Video Title 3 (Retry 3 - Optional):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title3_entry = ctk.CTkEntry(modal, placeholder_text="Alternative title or variation (Attempt 3 if 2 fails)", width=460)
        title3_entry.insert(0, target_task.title_3)
        title3_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Watch Duration:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        duration_option = ctk.CTkOptionMenu(modal, values=WATCHTIME_OPTIONS, width=460)
        duration_option.set(target_task.watchtime_duration or self._default_watch_duration())
        duration_option.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Channel:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        channel_option = ctk.CTkOptionMenu(modal, values=self._channel_values(), width=460)
        channel_option.set(target_task.channel if target_task.channel in self._channel_values() else NO_CHANNEL_VALUE)
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

        def on_submit():
            url = clean_youtube_url(url_entry.get().strip())
            t1 = title1_entry.get().strip()
            t2 = title2_entry.get().strip()
            t3 = title3_entry.get().strip()
            if not url and not t1 and not t2 and not t3:
                messagebox.showerror("Error", "Please provide at least a URL or Video Title!", parent=modal)
                return

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
            )

            if is_editing:
                self.video_tasks[index] = updated_task
            else:
                self.video_tasks.append(updated_task)

            self.save_queue()
            self.refresh_list()
            modal.destroy()

        btn_text = "Save Changes" if is_editing else "Add Task"
        btn_color = "#1F6AA5" if is_editing else "#2FA572"
        btn_hover = "#144870" if is_editing else "#1E7B52"

        ctk.CTkButton(modal, text=btn_text, fg_color=btn_color, hover_color=btn_hover, command=on_submit).pack(pady=10)

    def remove_task(self, index: int):
        if 0 <= index < len(self.video_tasks):
            del self.video_tasks[index]
            self.save_queue()
            self.refresh_list()

    def clear_queue(self):
        self.video_tasks.clear()
        self.save_queue()
        self.refresh_list()

    def reset_stats(self):
        """Delete every recorded watch statistic after a confirmation."""
        store = self._stats()
        if store is None:
            return
        if not messagebox.askyesno(
            "Reset Statistics",
            "Delete all recorded watch statistics?\n\nThis cannot be undone.",
        ):
            return
        store.clear_all()
        refresh = getattr(self.app_controller, "refresh_watch_stats", None)
        if callable(refresh):
            refresh()
        else:
            self.refresh_list()

    def refresh_list(self):
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        if not self.video_tasks:
            ctk.CTkLabel(
                self.scroll_frame,
                text="Queue is empty. Click '+ Add Target Video' to add tasks.",
                font=ctk.CTkFont(size=14, slant="italic"),
            ).pack(pady=30)
            return

        for group in group_videos_by_channel(self.video_tasks, self._channels()):
            self._render_group(group)

    def _render_group(self, group: VideoGroup):
        """Render one channel section (header + its video cards)."""
        container = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        container.pack(fill="x", padx=5, pady=(6, 4))
        container.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(container, corner_radius=6, fg_color=("gray88", "gray22"))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        col_offset = 0
        if not group.is_uncategorized:
            def _toggle_channel():
                new_state = bool(ch_chk.get())
                group.channel.enabled = new_state
                channels = self._channels()
                for ch in channels:
                    if ch.name.lower() == group.channel.name.lower():
                        ch.enabled = new_state
                self.app_controller.config_mgr.set_channels([c.to_dict() for c in channels])
                self.refresh_list()

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

        title = f"📺 {group.name}"
        if not group.is_uncategorized and not group.channel.enabled:
            title += " [DISABLED]"
        title += f"  •  {group.count} video(s)"

        totals_line = self._group_totals_line(group)
        if totals_line:
            title += f"  •  {totals_line}"
        ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            justify="left",
            anchor="w",
            text_color=("gray50", "gray50") if (not group.is_uncategorized and not group.channel.enabled) else None,
        ).grid(row=0, column=col_offset, padx=12, pady=(6, 0), sticky="ew")

        if group.url:
            ctk.CTkLabel(
                header,
                text=group.url,
                font=ctk.CTkFont(size=12),
                text_color=("gray30", "gray70"),
                justify="left",
                anchor="w",
            ).grid(row=1, column=col_offset, padx=12, pady=(0, 6), sticky="ew")

        header_btns = ctk.CTkFrame(header, fg_color="transparent")
        header_btns.grid(row=0, column=col_offset + 1, rowspan=2, padx=8, pady=6, sticky="e")

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
            command=self.open_channels_view,
        ).pack(side="left")

        if not group.videos:
            ctk.CTkLabel(
                container,
                text="No videos in this channel yet. Click '+ Add Target Video' to add one.",
                font=ctk.CTkFont(size=13, slant="italic"),
                anchor="w",
            ).grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")
            return

        for row, (index, task) in enumerate(group.videos, start=1):
            card = self._build_task_card(container, row, task, index, channel_enabled=group.channel.enabled if not group.is_uncategorized else True)
            card.grid(row=row, column=0, sticky="ew", pady=(0, 5))

    def _build_task_card(self, parent, position: int, task: VideoTask, index: int, channel_enabled: bool = True):
        """Build a single video card; ``index`` is the task's real queue index."""
        card = ctk.CTkFrame(parent, corner_radius=6)
        card.grid_columnconfigure(0, weight=0)
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(2, weight=0)

        def _toggle_task():
            task.enabled = bool(task_chk.get())
            self.save_queue()
            self.refresh_list()

        task_chk = ctk.CTkCheckBox(
            card,
            text="",
            width=24,
            command=_toggle_task,
        )
        if not channel_enabled:
            task_chk.deselect()
            task_chk.configure(state="disabled")
        else:
            task_chk.configure(state="normal")
            if task.enabled:
                task_chk.select()
            else:
                task_chk.deselect()

        task_chk.grid(row=0, column=0, rowspan=2, padx=(12, 4), pady=10, sticky="w")

        t_text = task.title or task.url
        if len(t_text) > 55:
            t_text = t_text[:52] + "..."

        titles = [t for t in [task.title, task.title_2, task.title_3] if t]
        title_summary = " | ".join(titles) if titles else "N/A"
        if len(title_summary) > 42:
            title_summary = title_summary[:39] + "..."

        status_prefix = ""
        if not channel_enabled:
            status_prefix = "[CHANNEL DISABLED] "
        elif not task.enabled:
            status_prefix = "[DISABLED] "

        info_text = f"#{position}. {status_prefix}{t_text}\nTitles: {title_summary} | Watchtime: {task.watchtime_duration}"
        lbl = ctk.CTkLabel(
            card,
            text=info_text,
            justify="left",
            font=ctk.CTkFont(size=13),
            anchor="w",
            text_color=("gray50", "gray50") if (not task.enabled or not channel_enabled) else None,
        )
        lbl.grid(row=0, column=1, padx=10, pady=(10, 0), sticky="ew")

        # Lifetime watch statistics for this video (recorded runs only).
        store = self._stats()
        record = store.find_for_task(task) if store is not None else None
        stats_lbl = ctk.CTkLabel(
            card,
            text=format_watch_line(record),
            justify="left",
            font=ctk.CTkFont(size=12),
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        stats_lbl.grid(row=1, column=1, padx=10, pady=(2, 10), sticky="ew")

        # Action Buttons Frame (Fixed to column 2 on the right edge)
        action_btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        action_btn_frame.grid(row=0, column=2, rowspan=2, padx=10, pady=10, sticky="e")

        ctk.CTkButton(
            action_btn_frame,
            text="🔗 View",
            width=65,
            fg_color="#2E7D32",
            hover_color="#1B5E20",
            command=lambda u=task.url: self.open_video_url(u),
        ).pack(side="left", padx=(0, 4))

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

        return card
