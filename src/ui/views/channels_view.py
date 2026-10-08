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
                channel = Channel.parse(name_entry.get(), url_entry.get(), existing=candidates)
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

        if not self.channels:
            ctk.CTkLabel(
                self.scroll_frame,
                text="No channels yet. Click '+ Add Channel' to create one, then attach videos to it in the queue.",
                font=ctk.CTkFont(size=14, slant="italic"),
                wraplength=520,
                justify="left",
            ).pack(pady=30)
            return

        for idx, channel in enumerate(self.channels):
            row = ctk.CTkFrame(self.scroll_frame, corner_radius=6)
            row.pack(fill="x", padx=5, pady=4)
            row.grid_columnconfigure(0, weight=1)

            count = self.video_count(channel.name)
            subtitle = channel.url or "No URL"
            info_text = f"{idx + 1}. {channel.name}\n{subtitle}  |  {count} video(s)"
            stats_line = self.channel_stats_line(channel.name)
            if stats_line:
                info_text += f"\n{stats_line}"
            ctk.CTkLabel(
                row,
                text=info_text,
                font=ctk.CTkFont(size=13),
                justify="left",
                anchor="w",
            ).grid(row=0, column=0, padx=15, pady=8, sticky="ew")

            action_frame = ctk.CTkFrame(row, fg_color="transparent")
            action_frame.grid(row=0, column=1, padx=10, pady=8, sticky="e")

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
                text="✏ Edit",
                width=60,
                fg_color="#1F6AA5",
                hover_color="#144870",
                command=lambda i=idx: self.open_channel_modal(edit_index=i),
            ).pack(side="left", padx=(0, 4))

            ctk.CTkButton(
                action_frame,
                text="🗑 Delete",
                width=65,
                fg_color="#D32F2F",
                hover_color="#9A0007",
                command=lambda i=idx: self.remove_channel(i),
            ).pack(side="left")
