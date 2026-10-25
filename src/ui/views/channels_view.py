import re
import threading
import webbrowser
from tkinter import messagebox
from typing import Callable, List, Optional, Set

import customtkinter as ctk

from ...models.channel import (
    UNCATEGORIZED_LABEL,
    Channel,
    canonical_channel_url,
    clean_channel_url,
)
from ...models.video_task import VideoTask
from ...models.watch_stats import format_duration, format_hours, format_totals_line
from ...utils.channel_fetcher import (
    ChannelFetchResult,
    extract_fallback_channel_name,
    fetch_channel_videos,
    normalize_channel_url,
)
from ...utils.helpers import extract_video_id
from .channel_import_modal import ChannelImportModal

# Called with the saved channel so callers (e.g. the queue modal) can react.
OnChannelSaved = Callable[[Channel], None]
OnDialogClosed = Callable[[], None]

FETCH_LIMIT_OPTIONS: List[str] = [
    "5",
    "10",
    "15",
    "20",
    "25",
    "30",
    "50",
    "75",
    "100",
    "All",
    "Custom...",
]


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
        self._setup_fast_scrolling()

        self.load_channels()

    def _setup_fast_scrolling(self):
        """Install smooth, responsive, batched mouse wheel scrolling on the scroll frame."""
        canvas = self.scroll_frame._parent_canvas
        canvas_str = str(canvas)

        self._wheel_accum = 0
        self._wheel_job = None

        def _flush_wheel():
            self._wheel_job = None
            delta = self._wheel_accum
            self._wheel_accum = 0
            if delta == 0:
                return
            try:
                if abs(delta) < 10:
                    step = -int(delta * 25)
                else:
                    step = -int(delta / 2)
                if canvas.winfo_exists() and canvas.yview() != (0.0, 1.0):
                    canvas.yview("scroll", step, "units")
            except Exception:
                pass

        def _on_mousewheel(event):
            w_str = str(event.widget)
            if w_str == canvas_str or w_str.startswith(canvas_str + "."):
                self._wheel_accum += event.delta
                if self._wheel_job is None:
                    self._wheel_job = canvas.after_idle(_flush_wheel)
                return "break"

        def _on_linux_scroll_up(event):
            w_str = str(event.widget)
            if w_str == canvas_str or w_str.startswith(canvas_str + "."):
                if canvas.winfo_exists() and canvas.yview() != (0.0, 1.0):
                    canvas.yview("scroll", -60, "units")
                return "break"

        def _on_linux_scroll_down(event):
            w_str = str(event.widget)
            if w_str == canvas_str or w_str.startswith(canvas_str + "."):
                if canvas.winfo_exists() and canvas.yview() != (0.0, 1.0):
                    canvas.yview("scroll", 60, "units")
                return "break"

        canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")
        canvas.bind_all("<Button-4>", _on_linux_scroll_up, add="+")
        canvas.bind_all("<Button-5>", _on_linux_scroll_down, add="+")

    # ---------------------------------------------------------------- storage

    def load_channels(self) -> None:
        self.channels = [Channel.from_dict(entry) for entry in self.app_controller.config_mgr.get_channels()]
        store = getattr(self.app_controller, "stats_store", None)
        if store:
            for ch in self.channels:
                if getattr(ch, "watch_seconds", 0.0) <= 0.0:
                    hist_totals = store.channel_totals(ch.name)
                    if hist_totals.watch_seconds > 0:
                        ch.watch_seconds = hist_totals.watch_seconds
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

    def get_channel_handle(self, channel: Channel) -> str:
        """Extract a clean @handle from the channel URL or name."""
        url_str = (channel.url or "").strip()
        match = re.search(r"@([a-zA-Z0-9_.-]+)", url_str)
        if match:
            return f"@{match.group(1).rstrip('/')}"
        name_str = (channel.name or "").strip()
        match_name = re.search(r"@([a-zA-Z0-9_.-]+)", name_str)
        if match_name:
            return f"@{match_name.group(1).rstrip('/')}"
        for prefix in ("/c/", "/user/"):
            if prefix in url_str:
                part = url_str.split(prefix)[-1].split("/")[0].split("?")[0]
                if part:
                    return f"@{part}"
        if name_str:
            clean_handle = name_str.lstrip("@").replace(" ", "")
            return f"@{clean_handle}"
        return "@channel"

    def get_channel_watch_time(self, channel: Channel) -> str:
        """Calculate total watch time from all recorded videos for this channel in hours (1-digit float)."""
        store = getattr(self.app_controller, "stats_store", None)
        if not store:
            return "0.0 hours"
        clean = (channel.name or "").strip()
        names = {
            clean.lower(),
            clean.lstrip("@").lower(),
            f"@{clean.lstrip('@')}".lower(),
        }
        total_sec = 0.0
        with store._lock:
            for record in store.records.values():
                ch_rec = (record.channel or "").strip().lower()
                if ch_rec in names:
                    total_sec += float(record.watch_seconds)
        return format_hours(total_sec)

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
        modal.geometry("520x300" if not is_editing else "470x230")
        modal.grab_set()

        ctk.CTkLabel(modal, text="Channel Name:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=20, pady=(15, 2)
        )
        name_entry = ctk.CTkEntry(
            modal,
            placeholder_text="e.g. My Channel (optional if URL provided)",
            width=460,
        )
        name_entry.insert(0, target.name)
        name_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Channel URL:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=20, pady=(5, 2)
        )
        url_entry = ctk.CTkEntry(modal, placeholder_text="https://www.youtube.com/@handle", width=460)
        url_entry.insert(0, target.url)
        url_entry.pack(padx=20, pady=(0, 10))

        fetch_var = ctk.BooleanVar(value=True)
        limit_var = ctk.StringVar(value="25")
        custom_entry = None
        if not is_editing:
            fetch_frame = ctk.CTkFrame(modal, fg_color="transparent")
            fetch_frame.pack(padx=20, pady=(0, 10), fill="x")

            ctk.CTkCheckBox(
                fetch_frame,
                text="Fetch videos automatically",
                variable=fetch_var,
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(side="left")

            custom_entry = ctk.CTkEntry(
                fetch_frame,
                width=55,
                height=26,
                placeholder_text="#",
            )

            def _on_limit_change(choice: str) -> None:
                if custom_entry is None:
                    return
                if choice == "Custom...":
                    custom_entry.pack(side="right", padx=(4, 0))
                    custom_entry.focus_set()
                else:
                    custom_entry.pack_forget()

            limit_menu = ctk.CTkOptionMenu(
                fetch_frame,
                values=FETCH_LIMIT_OPTIONS,
                variable=limit_var,
                command=_on_limit_change,
                width=90,
                height=26,
            )
            limit_menu.pack(side="right")
            ctk.CTkLabel(fetch_frame, text="Limit:", font=ctk.CTkFont(size=12)).pack(
                side="right", padx=(0, 5)
            )

        def on_submit() -> None:
            raw_name = name_entry.get().strip()
            raw_url = url_entry.get().strip()

            if not raw_name and not raw_url:
                messagebox.showerror("Error", "Please provide a Channel Name or Channel URL.", parent=modal)
                return

            candidates = [ch for i, ch in enumerate(self.channels) if not is_editing or i != index]

            # Check duplicate URL early
            if raw_url:
                canon_input = canonical_channel_url(raw_url)
                if canon_input:
                    for ch in candidates:
                        if canonical_channel_url(ch.url) == canon_input:
                            messagebox.showerror(
                                "Duplicate Channel",
                                f"A channel with this URL already exists: '{ch.name}'.",
                                parent=modal,
                            )
                            return

            limit_str = limit_var.get()
            if limit_str == "All":
                max_fetch = 0
            elif limit_str == "Custom...":
                custom_val = custom_entry.get().strip() if custom_entry else ""
                if not custom_val.isdigit() or int(custom_val) <= 0:
                    messagebox.showerror(
                        "Invalid Limit",
                        "Please enter a valid positive number for custom limit.",
                        parent=modal,
                    )
                    return
                max_fetch = int(custom_val)
            else:
                try:
                    max_fetch = int(limit_str)
                except ValueError:
                    max_fetch = 25

            should_fetch = not is_editing and fetch_var.get() and bool(raw_url)

            # Case: Channel Name left empty, URL provided, and auto-fetch enabled
            if not raw_name and should_fetch:
                modal.destroy()
                self._fetch_and_create_channel(
                    raw_url=raw_url,
                    max_fetch=max_fetch,
                    on_saved=on_saved,
                )
                return

            # Case: Channel Name left empty, but URL provided without auto-fetch
            if not raw_name and raw_url:
                raw_name = extract_fallback_channel_name(raw_url)

            try:
                channel = Channel.parse(
                    raw_name, raw_url, existing=candidates, enabled=target.enabled
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

            if should_fetch:
                self.fetch_videos_for_channel(channel, max_results=max_fetch)

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

    def fetch_videos_for_channel(
        self,
        channel: Channel,
        max_results: int = 50,
        on_imported: Optional[Callable[[], None]] = None,
    ) -> None:
        """Fetch videos for a channel in a background thread and show selection modal."""
        if not channel.url:
            messagebox.showwarning(
                "Missing URL",
                f"Channel '{channel.name}' does not have a URL configured.",
                parent=self,
            )
            return

        loading = ctk.CTkToplevel(self)
        loading.title("Fetching Videos...")
        loading.geometry("380x140")
        loading.grab_set()

        ctk.CTkLabel(
            loading,
            text=f"Fetching videos from YouTube for\n'{channel.name}'...",
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="center",
        ).pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(loading, mode="indeterminate", width=280)
        prog.pack(pady=(0, 15))
        prog.start()

        def _bg_fetch():
            res = fetch_channel_videos(channel.url, max_results=max_results)
            self.after(0, lambda: _on_fetch_done(res))

        def _on_fetch_done(res: ChannelFetchResult):
            try:
                prog.stop()
                loading.destroy()
            except Exception:
                pass

            if res.error:
                messagebox.showerror(
                    "Fetch Error",
                    f"Failed to fetch videos from channel:\n{res.error}",
                    parent=self,
                )
                return

            if not res.videos:
                messagebox.showinfo(
                    "No Videos Found",
                    f"No public videos were found for '{channel.name}'.",
                    parent=self,
                )
                return

            tasks: List[VideoTask] = getattr(self.app_controller, "queued_video_tasks", []) or []
            existing_ids: Set[str] = set()
            for t in tasks:
                vid = extract_video_id(t.url)
                if vid:
                    existing_ids.add(vid)

            def _handle_import(selected_tasks: List[VideoTask]):
                if not selected_tasks:
                    return
                tasks.extend(selected_tasks)
                self.app_controller.queued_video_tasks = tasks
                self.app_controller.config_mgr.set_video_queue([t.to_dict() for t in tasks])

                self.refresh_list()
                queue = self._queue_view()
                if queue is not None:
                    queue.refresh_list()
                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()
                if on_imported:
                    on_imported()

                messagebox.showinfo(
                    "Import Complete",
                    f"Successfully imported {len(selected_tasks)} video(s) for channel '{channel.name}'.",
                    parent=self,
                )

            ChannelImportModal(
                parent=self,
                channel_name=channel.name,
                channel_url=channel.url,
                fetched_videos=res.videos,
                existing_video_ids=existing_ids,
                on_import=_handle_import,
            )

        threading.Thread(target=_bg_fetch, daemon=True).start()

    def _fetch_and_create_channel(
        self,
        raw_url: str,
        max_fetch: int = 50,
        on_saved: Optional[OnChannelSaved] = None,
    ) -> None:
        """Fetch channel name and videos when channel name was omitted, then create channel and prompt for import."""
        canon_input = canonical_channel_url(raw_url)
        if canon_input:
            for ch in self.channels:
                if canonical_channel_url(ch.url) == canon_input:
                    messagebox.showerror(
                        "Duplicate Channel",
                        f"A channel with this URL already exists: '{ch.name}'.",
                        parent=self,
                    )
                    return

        loading = ctk.CTkToplevel(self)
        loading.title("Fetching Channel...")
        loading.geometry("400x140")
        loading.grab_set()

        ctk.CTkLabel(
            loading,
            text=f"Resolving channel & fetching videos for\n'{raw_url}'...",
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="center",
        ).pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(loading, mode="indeterminate", width=300)
        prog.pack(pady=(0, 15))
        prog.start()

        def _bg_fetch():
            res = fetch_channel_videos(raw_url, max_results=max_fetch)
            self.after(0, lambda: _on_fetch_done(res))

        def _on_fetch_done(res: ChannelFetchResult):
            try:
                prog.stop()
                loading.destroy()
            except Exception:
                pass

            if res.error:
                messagebox.showerror(
                    "Fetch Error",
                    f"Failed to fetch channel details:\n{res.error}",
                    parent=self,
                )
                return

            resolved_name = (res.channel_title or "").strip() or extract_fallback_channel_name(raw_url)
            # Ensure unique name among existing channels
            unique_name = resolved_name
            counter = 2
            while any(ch.name.lower() == unique_name.lower() for ch in self.channels):
                unique_name = f"{resolved_name} ({counter})"
                counter += 1

            target_url = clean_channel_url(res.channel_url or raw_url)
            canon_target = canonical_channel_url(target_url)
            if canon_target:
                for ch in self.channels:
                    if canonical_channel_url(ch.url) == canon_target:
                        messagebox.showerror(
                            "Duplicate Channel",
                            f"A channel with this URL already exists: '{ch.name}'.",
                            parent=self,
                        )
                        return

            channel = Channel(
                name=unique_name,
                url=target_url,
                enabled=True,
            )
            self.channels.append(channel)
            self.save_channels()

            queue = self._queue_view()
            if queue is not None:
                queue.refresh_list()

            if on_saved:
                on_saved(channel)

            if not res.videos:
                messagebox.showinfo(
                    "Channel Added",
                    f"Created channel '{channel.name}', but no public videos were found.",
                    parent=self,
                )
                return

            tasks: List[VideoTask] = getattr(self.app_controller, "queued_video_tasks", []) or []
            existing_ids: Set[str] = set()
            for t in tasks:
                vid = extract_video_id(t.url)
                if vid:
                    existing_ids.add(vid)

            def _handle_import(selected_tasks: List[VideoTask]):
                if not selected_tasks:
                    return
                tasks.extend(selected_tasks)
                self.app_controller.queued_video_tasks = tasks
                self.app_controller.config_mgr.set_video_queue([t.to_dict() for t in tasks])

                self.refresh_list()
                q = self._queue_view()
                if q is not None:
                    q.refresh_list()
                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()

                messagebox.showinfo(
                    "Import Complete",
                    f"Successfully imported {len(selected_tasks)} video(s) for channel '{channel.name}'.",
                    parent=self,
                )

            ChannelImportModal(
                parent=self,
                channel_name=channel.name,
                channel_url=channel.url,
                fetched_videos=res.videos,
                existing_video_ids=existing_ids,
                on_import=_handle_import,
            )

        threading.Thread(target=_bg_fetch, daemon=True).start()

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

        if channel.url:
            ctk.CTkButton(
                modal_actions,
                text="🔄 Fetch Videos",
                font=ctk.CTkFont(weight="bold"),
                fg_color="#0D9488",
                hover_color="#0F766E",
                command=lambda: self.fetch_videos_for_channel(
                    channel,
                    on_imported=lambda: _refresh_modal_videos(),
                ),
            ).pack(side="left", padx=(8, 0))

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

                store = getattr(self.app_controller, "stats_store", None)
                rec = store.find_for_task(task) if store else None
                v_watch_sec = float(rec.watch_seconds) if rec else 0.0
                label_parts.append(f"Total Watch Time: {format_hours(v_watch_sec)}")

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

            chk = ctk.CTkCheckBox(
                row,
                text="",
                width=24,
            )
            if channel.enabled:
                chk.select()
            else:
                chk.deselect()

            def _toggle_channel(ch=channel, checkbox=chk):
                new_enabled = bool(checkbox.get())
                ch.enabled = new_enabled
                if not new_enabled:
                    # Move unchecked channel to the bottom of the list
                    if ch in self.channels:
                        self.channels.remove(ch)
                        self.channels.append(ch)
                    queue = self._queue_view()
                    if queue is not None:
                        ch_name = ch.name.lower()
                        non_ch = [t for t in queue.video_tasks if (t.channel or "").strip().lower() != ch_name]
                        ch_vids = [t for t in queue.video_tasks if (t.channel or "").strip().lower() == ch_name]
                        queue.video_tasks = non_ch + ch_vids
                        queue.save_queue()
                    else:
                        ch_name = ch.name.lower()
                        raw_queue = self.app_controller.config_mgr.get_video_queue()
                        non_ch = [t for t in raw_queue if (t.get("channel") or "").strip().lower() != ch_name]
                        ch_vids = [t for t in raw_queue if (t.get("channel") or "").strip().lower() == ch_name]
                        self.app_controller.config_mgr.set_video_queue(non_ch + ch_vids)
                self.save_channels()
                self.refresh_list()
                queue = self._queue_view()
                if queue is not None:
                    queue.refresh_list()
                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()

            chk.configure(command=_toggle_channel)
            chk.grid(row=0, column=0, padx=(12, 4), pady=8, sticky="w")

            raw_name = (channel.name or "").strip()
            clean_name = raw_name.lstrip("@").strip() or raw_name or "Channel"
            status_tag = ""
            if channel.archived:
                status_tag = " [ARCHIVED]"
            elif not channel.enabled:
                status_tag = " [DISABLED]"

            line1 = f"{pos} - {clean_name}{status_tag}"
            line2 = self.get_channel_handle(channel)
            watch_time_str = self.get_channel_watch_time(channel)
            line3 = f"Total Watch Time: {watch_time_str}"

            info_text = f"{line1}\n{line2}\n{line3}"

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

            if channel.url:
                ctk.CTkButton(
                    action_frame,
                    text="🔄 Fetch",
                    width=60,
                    fg_color="#0D9488",
                    hover_color="#0F766E",
                    command=lambda ch=channel: self.fetch_videos_for_channel(ch),
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
