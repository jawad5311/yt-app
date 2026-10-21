import webbrowser
from tkinter import messagebox
from typing import Callable, List, Optional

import customtkinter as ctk

from ...models.channel import UNCATEGORIZED_LABEL, Channel
from ...models.watch_stats import format_totals_line

# Called with the saved channel so callers (e.g. the queue modal) can react.
OnChannelSaved = Callable[[Channel], None]
OnDialogClosed = Callable[[], None]


class ChannelsView(ctk.CTkFrame):
    """Manage the named channels that queued videos are grouped under."""

    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.channels: List[Channel] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        ctk.CTkLabel(
            header_frame,
            text="Channels",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="+ Add Channel",
            command=lambda: self.open_channel_modal(),
        ).pack(side="left", padx=(0, 10))

        self.current_tab = "Active"
        self.tab_selector = ctk.CTkSegmentedButton(
            btn_frame,
            values=["Active", "Archived"],
            command=self._on_tab_change,
        )
        self.tab_selector.set("Active")
        self.tab_selector.pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="🗑 Clear All",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_channels,
        ).pack(side="right")

        # Channel list
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Saved Channels")
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.load_channels()

    # ---------------------------------------------------------------- storage

    def load_channels(self) -> None:
        self.channels = [Channel.from_dict(entry) for entry in self.app_controller.config_mgr.get_channels()]
        self.refresh_list()

    def save_channels(self) -> None:
        self.app_controller.config_mgr.set_channels([ch.to_dict() for ch in self.channels])
        self.refresh_list()
        queue = self._queue_view()
        if queue is not None:
            queue.refresh_list()
        update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
        if callable(update_dash):
            update_dash()

    # ------------------------------------------------------- cross-view hooks

    def _queue_view(self):
        """Queue view is built after this one, so look it up lazily."""
        views = getattr(self.app_controller, "views", None)
        return views.get("queue") if isinstance(views, dict) else None

    def video_count(self, channel_name: str) -> int:
        tasks = getattr(self.app_controller, "queued_video_tasks", None) or []
        return sum(1 for task in tasks if task.channel == channel_name)

    def channel_stats_line(self, channel_name: str) -> str:
        """``27 runs · 12h 04m watched`` for one channel row, or ``""``."""
        store = getattr(self.app_controller, "stats_store", None)
        if store is None:
            return ""
        totals = store.channel_totals(channel_name)
        return format_totals_line(totals) if totals.runs else ""

    # ------------------------------------------------------------------ modal

    def open_channel_modal(
        self,
        edit_index: Optional[int] = None,
        on_saved: Optional[OnChannelSaved] = None,
        on_close: Optional[OnDialogClosed] = None,
    ):
        index = edit_index if edit_index is not None else -1
        is_editing = 0 <= index < len(self.channels)
        target = self.channels[index] if is_editing else Channel()

        modal = ctk.CTkToplevel(self)
        modal.title("Edit Channel" if is_editing else "Add Channel")
        modal.geometry("470x230")
        modal.grab_set()

        ctk.CTkLabel(modal, text="Channel Name:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=20, pady=(15, 2)
        )
        name_entry = ctk.CTkEntry(modal, placeholder_text="e.g. My Cooking Channel", width=410)
        name_entry.insert(0, target.name)
        name_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Channel URL:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=20, pady=(5, 2)
        )
        url_entry = ctk.CTkEntry(modal, placeholder_text="https://www.youtube.com/@handle", width=410)
        url_entry.insert(0, target.url)
        url_entry.pack(padx=20, pady=(0, 10))

        def on_submit() -> None:
            candidates = [ch for i, ch in enumerate(self.channels) if not is_editing or i != index]
            try:
                channel = Channel.parse(
                    name_entry.get(), url_entry.get(), existing=candidates, enabled=target.enabled
                )
            except ValueError as exc:
                messagebox.showerror("Error", str(exc), parent=modal)
                return

            old_name = self.channels[index].name if is_editing else ""
            if is_editing:
                self.channels[index] = channel
            else:
                self.channels.append(channel)
            self.save_channels()

            queue = self._queue_view()
            if queue is not None:
                if is_editing and old_name and old_name != channel.name:
                    # Keep the relation intact: videos follow the rename.
                    queue.reassign_channel(old_name, channel.name)
                else:
                    queue.refresh_list()

            modal.destroy()
            if on_saved:
                on_saved(channel)

        btn_text = "Save Changes" if is_editing else "Add Channel"
        btn_color = "#1F6AA5" if is_editing else "#2FA572"
        btn_hover = "#144870" if is_editing else "#1E7B52"
        ctk.CTkButton(
            modal, text=btn_text, fg_color=btn_color, hover_color=btn_hover, command=on_submit
        ).pack(pady=10)

        # Notify the opener once the dialog goes away (saved or cancelled),
        # so it can restore its own input grab.
        def _on_destroy(event) -> None:
            if str(event.widget) != str(modal):
                return
            if on_close:
                on_close()

        modal.bind("<Destroy>", _on_destroy)
        name_entry.focus_set()

    # --------------------------------------------------------------- actions

    def _on_tab_change(self, selected_val: str) -> None:
        self.current_tab = "Archived" if "Archived" in selected_val else "Active"
        self.refresh_list()

    def archive_channel(self, index: int) -> None:
        if not 0 <= index < len(self.channels):
            return
        self.channels[index].archived = True
        self.save_channels()
        queue = self._queue_view()
        if queue is not None:
            queue.refresh_list()

    def unarchive_channel(self, index: int) -> None:
        if not 0 <= index < len(self.channels):
            return
        self.channels[index].archived = False
        self.save_channels()
        queue = self._queue_view()
        if queue is not None:
            queue.refresh_list()

    def open_channel_videos_modal(self, channel: Channel):
        modal = ctk.CTkToplevel(self)
        modal.title(f"Channel Videos — {channel.name}")
        modal.geometry("680x560")
        modal.grab_set()

        header = ctk.CTkFrame(modal, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 8))

        tag = " [ARCHIVED]" if channel.archived else (" [DISABLED]" if not channel.enabled else "")
        ctk.CTkLabel(
            header,
            text=f"📺 {channel.name}{tag}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w")

        if channel.url:
            ctk.CTkLabel(
                header,
                text=channel.url,
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "gray60"),
            ).pack(anchor="w")

        stats = self.channel_stats_line(channel.name)
        if stats:
            ctk.CTkLabel(
                header,
                text=stats,
                font=ctk.CTkFont(size=12, slant="italic"),
            ).pack(anchor="w", pady=(2, 0))

        modal_actions = ctk.CTkFrame(modal, fg_color="transparent")
        modal_actions.pack(fill="x", padx=20, pady=(4, 10))

        def _add_video_for_channel():
            queue = self._queue_view()
            if queue is None:
                return
            queue.open_video_modal(
                preselected_channel=channel.name,
                on_saved=lambda _: _refresh_modal_videos(),
            )

        ctk.CTkButton(
            modal_actions,
            text="+ New Video",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            command=_add_video_for_channel,
        ).pack(side="left")

        scroll = ctk.CTkScrollableFrame(modal, label_text=f"Videos under '{channel.name}'")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        def _refresh_modal_videos():
            for w in scroll.winfo_children():
                w.destroy()

            tasks = getattr(self.app_controller, "queued_video_tasks", []) or []
            channel_tasks = [
                (idx, t) for idx, t in enumerate(tasks)
                if (t.channel or "").strip().lower() == channel.name.lower()
            ]

            if not channel_tasks:
                ctk.CTkLabel(
                    scroll,
                    text="No videos in this channel yet.\nClick '+ New Video' above to add one.",
                    font=ctk.CTkFont(size=14, slant="italic"),
                    justify="center",
                ).pack(pady=40)
                self.refresh_list()
                return

            queue = self._queue_view()
            total_chan_vids = len(channel_tasks)
            for pos, (real_idx, task) in enumerate(channel_tasks, start=1):
                card = ctk.CTkFrame(scroll, corner_radius=6)
                card.pack(fill="x", padx=5, pady=4)
                card.grid_columnconfigure(0, weight=1)
                card.grid_columnconfigure(1, weight=0)

                label_parts = [f"{pos}. {task.title or task.keywords or 'Untitled Video'}"]
                if task.title_2:
                    label_parts.append(f"Retry 2: {task.title_2}")
                if task.title_3:
                    label_parts.append(f"Retry 3: {task.title_3}")
                if task.url:
                    label_parts.append(f"URL: {task.url}")
                status_info = f"Duration: {task.watchtime_duration or 'Full Video'}"
                if task.max_runs > 0:
                    status_info += f"  |  Limit: {task.max_runs} runs"
                label_parts.append(status_info)

                ctk.CTkLabel(
                    card,
                    text="\n".join(label_parts),
                    font=ctk.CTkFont(size=12),
                    justify="left",
                    anchor="w",
                ).grid(row=0, column=0, padx=12, pady=8, sticky="w")

                card_btns = ctk.CTkFrame(card, fg_color="transparent")
                card_btns.grid(row=0, column=1, padx=10, pady=8, sticky="e")

                def _move_modal_up(p_idx=pos - 1):
                    if p_idx > 0 and queue is not None:
                        curr_real = channel_tasks[p_idx][0]
                        prev_real = channel_tasks[p_idx - 1][0]
                        queue.video_tasks[curr_real], queue.video_tasks[prev_real] = (
                            queue.video_tasks[prev_real],
                            queue.video_tasks[curr_real],
                        )
                        queue.save_queue()
                        _refresh_modal_videos()

                def _move_modal_down(p_idx=pos - 1):
                    if p_idx < total_chan_vids - 1 and queue is not None:
                        curr_real = channel_tasks[p_idx][0]
                        next_real = channel_tasks[p_idx + 1][0]
                        queue.video_tasks[curr_real], queue.video_tasks[next_real] = (
                            queue.video_tasks[next_real],
                            queue.video_tasks[curr_real],
                        )
                        queue.save_queue()
                        _refresh_modal_videos()

                can_m_up = (pos > 1)
                can_m_down = (pos < total_chan_vids)

                ctk.CTkButton(
                    card_btns,
                    text="▲",
                    width=30,
                    height=28,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    fg_color="#374151" if can_m_up else "gray30",
                    hover_color="#4B5563" if can_m_up else "gray30",
                    state="normal" if can_m_up else "disabled",
                    command=_move_modal_up,
                ).pack(side="left", padx=(0, 2))

                ctk.CTkButton(
                    card_btns,
                    text="▼",
                    width=30,
                    height=28,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    fg_color="#374151" if can_m_down else "gray30",
                    hover_color="#4B5563" if can_m_down else "gray30",
                    state="normal" if can_m_down else "disabled",
                    command=_move_modal_down,
                ).pack(side="left", padx=(0, 6))

                def _edit(idx_to_edit=real_idx):
                    if queue is not None:
                        queue.open_video_modal(
                            edit_index=idx_to_edit,
                            on_saved=lambda _: _refresh_modal_videos(),
                        )

                def _delete(idx_to_del=real_idx):
                    if queue is not None:
                        queue.remove_task(idx_to_del)
                        _refresh_modal_videos()
                        self.refresh_list()

                ctk.CTkButton(
                    card_btns,
                    text="✏ Edit",
                    width=60,
                    fg_color="#1F6AA5",
                    hover_color="#144870",
                    command=_edit,
                ).pack(side="left", padx=(0, 4))

                ctk.CTkButton(
                    card_btns,
                    text="🗑 Delete",
                    width=60,
                    fg_color="#D32F2F",
                    hover_color="#9A0007",
                    command=_delete,
                ).pack(side="left")

            self.refresh_list()

        _refresh_modal_videos()

    def remove_channel(self, index: int) -> None:
        if not 0 <= index < len(self.channels):
            return
        removed = self.channels.pop(index)
        self.save_channels()

        queue = self._queue_view()
        if queue is None:
            return
        unassigned = queue.unassign_channel(removed.name)
        if unassigned:
            messagebox.showinfo(
                "Channel Removed",
                f"'{removed.name}' was removed. {unassigned} video(s) moved to {UNCATEGORIZED_LABEL}.",
            )

    def clear_channels(self) -> None:
        if not self.channels:
            return
        self.channels.clear()
        self.save_channels()

        queue = self._queue_view()
        if queue is not None:
            queue.unassign_all_channels()

    def open_channel_url(self, url: str) -> None:
        if url and url.strip():
            webbrowser.open(url.strip())
        else:
            messagebox.showinfo("Notice", "This channel has no URL saved.")

    # ------------------------------------------------------------------ list

    def refresh_list(self) -> None:
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        active = [(i, ch) for i, ch in enumerate(self.channels) if not ch.archived]
        archived = [(i, ch) for i, ch in enumerate(self.channels) if ch.archived]

        val_active = f"Active ({len(active)})"
        val_archived = f"Archived ({len(archived)})"
        self.tab_selector.configure(values=[val_active, val_archived])
        self.tab_selector.set(val_archived if self.current_tab == "Archived" else val_active)

        current_items = archived if self.current_tab == "Archived" else active

        if not current_items:
            empty_msg = (
                "No archived channels yet."
                if self.current_tab == "Archived"
                else "No active channels yet. Click '+ Add Channel' to create one, then attach videos to it in the queue."
            )
            ctk.CTkLabel(
                self.scroll_frame,
                text=empty_msg,
                font=ctk.CTkFont(size=14, slant="italic"),
                wraplength=520,
                justify="left",
            ).pack(pady=30)
            return

        total_items = len(current_items)
        for pos, (real_idx, channel) in enumerate(current_items, start=1):
            row = ctk.CTkFrame(self.scroll_frame, corner_radius=6, cursor="hand2")
            row.pack(fill="x", padx=5, pady=4)
            row.grid_columnconfigure(0, weight=0)
            row.grid_columnconfigure(1, weight=1)
            row.grid_columnconfigure(2, weight=0)

            def _toggle_channel(ch=channel):
                ch.enabled = bool(chk.get())
                self.save_channels()
                queue = self._queue_view()
                if queue is not None:
                    queue.refresh_list()

            chk = ctk.CTkCheckBox(
                row,
                text="",
                width=24,
                command=_toggle_channel,
            )
            if channel.enabled:
                chk.select()
            else:
                chk.deselect()
            chk.grid(row=0, column=0, padx=(12, 4), pady=8, sticky="w")

            count = self.video_count(channel.name)
            subtitle = channel.url or "No URL"
            status_tag = ""
            if channel.archived:
                status_tag = " [ARCHIVED]"
            elif not channel.enabled:
                status_tag = " [DISABLED]"

            info_text = f"{pos}. {channel.name}{status_tag}\n{subtitle}  |  {count} video(s)  (Click to view videos)"
            stats_line = self.channel_stats_line(channel.name)
            if stats_line:
                info_text += f"\n{stats_line}"

            def _on_channel_click(event=None, ch=channel):
                self.open_channel_videos_modal(ch)

            row.bind("<Button-1>", _on_channel_click)

            lbl = ctk.CTkLabel(
                row,
                text=info_text,
                font=ctk.CTkFont(size=13),
                justify="left",
                anchor="w",
                cursor="hand2",
                text_color=("gray50", "gray50") if not channel.enabled else None,
            )
            lbl.grid(row=0, column=1, padx=10, pady=8, sticky="ew")
            lbl.bind("<Button-1>", _on_channel_click)

            action_frame = ctk.CTkFrame(row, fg_color="transparent")
            action_frame.grid(row=0, column=2, padx=10, pady=8, sticky="e")

            def _move_channel_up(p=pos):
                if p > 1:
                    curr_real = current_items[p - 1][0]
                    prev_real = current_items[p - 2][0]
                    self.channels[curr_real], self.channels[prev_real] = (
                        self.channels[prev_real],
                        self.channels[curr_real],
                    )
                    self.save_channels()

            def _move_channel_down(p=pos):
                if p < total_items:
                    curr_real = current_items[p - 1][0]
                    next_real = current_items[p][0]
                    self.channels[curr_real], self.channels[next_real] = (
                        self.channels[next_real],
                        self.channels[curr_real],
                    )
                    self.save_channels()

            can_up = (pos > 1)
            can_down = (pos < total_items)

            ctk.CTkButton(
                action_frame,
                text="▲",
                width=30,
                height=28,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="#374151" if can_up else "gray30",
                hover_color="#4B5563" if can_up else "gray30",
                state="normal" if can_up else "disabled",
                command=_move_channel_up,
            ).pack(side="left", padx=(0, 2))

            ctk.CTkButton(
                action_frame,
                text="▼",
                width=30,
                height=28,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="#374151" if can_down else "gray30",
                hover_color="#4B5563" if can_down else "gray30",
                state="normal" if can_down else "disabled",
                command=_move_channel_down,
            ).pack(side="left", padx=(0, 6))

            if channel.url:
                ctk.CTkButton(
                    action_frame,
                    text="🔗 Open",
                    width=65,
                    fg_color="#2E7D32",
                    hover_color="#1B5E20",
                    command=lambda u=channel.url: self.open_channel_url(u),
                ).pack(side="left", padx=(0, 4))

            ctk.CTkButton(
                action_frame,
                text="🎬 Videos",
                width=65,
                fg_color="#3B82F6",
                hover_color="#1D4ED8",
                command=lambda ch=channel: self.open_channel_videos_modal(ch),
            ).pack(side="left", padx=(0, 4))

            ctk.CTkButton(
                action_frame,
                text="✏ Edit",
                width=60,
                fg_color="#1F6AA5",
                hover_color="#144870",
                command=lambda i=real_idx: self.open_channel_modal(edit_index=i),
            ).pack(side="left", padx=(0, 4))

            if channel.archived:
                ctk.CTkButton(
                    action_frame,
                    text="📂 Restore",
                    width=70,
                    fg_color="#059669",
                    hover_color="#047857",
                    command=lambda i=real_idx: self.unarchive_channel(i),
                ).pack(side="left", padx=(0, 4))
            else:
                ctk.CTkButton(
                    action_frame,
                    text="📁 Archive",
                    width=68,
                    fg_color="#D97706",
                    hover_color="#B45309",
                    command=lambda i=real_idx: self.archive_channel(i),
                ).pack(side="left", padx=(0, 4))

            ctk.CTkButton(
                action_frame,
                text="🗑 Delete",
                width=65,
                fg_color="#D32F2F",
                hover_color="#9A0007",
                command=lambda i=real_idx: self.remove_channel(i),
            ).pack(side="left")
