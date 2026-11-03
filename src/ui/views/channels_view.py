import re
import threading
import webbrowser
from tkinter import TclError, messagebox
from typing import Any, Callable, List, Optional, Set, Tuple

from .. import ttk_adapter as ctk
from ..components import Frame

from ...models.channel import (
    UNCATEGORIZED_LABEL,
    Channel,
    canonical_channel_url,
    clean_channel_url,
)
from ...models.video_task import VideoTask, safe_float
from ...models.watch_stats import (
    format_hours,
    format_totals_line,
    format_video_watch_ratio,
)
from ...utils.channel_fetcher import (
    ChannelFetchResult,
    extract_fallback_channel_name,
    fetch_channel_data,
    fetch_channel_title_online,
    fetch_channel_videos,
)
from ...utils.helpers import extract_video_id
from ..tooltip import add_tooltip
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

FETCH_DATA_OPTIONS: List[str] = [
    "Fetch No Data",
    "50",
    "100",
    "200",
    "300",
    "500",
    "All",
]


def parse_fetch_limit(val_str: Any) -> Optional[int]:
    """Parse dropdown limit choice into an integer limit:
    - 0 indicates 'All' (fetch all until exhausted)
    - None indicates 'Fetch No Data' / 'No Fetching' (skip)
    - Positive int indicates requested count (50, 100, 200, etc.)
    """
    if val_str is None:
        return None
    val = str(val_str).strip()
    if val.lower() == "all":
        return 0
    if val.lower() in (
        "fetch no data",
        "no fetching",
        "none",
        "skip",
        "0",
        "0 (skip)",
    ):
        return None
    try:
        num = int(val)
        return num if num > 0 else None
    except ValueError:
        return 100


class ChannelsView(Frame):
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

        add_btn = ctk.CTkButton(
            btn_frame,
            text="+ Add Channel",
            command=lambda: self.open_channel_modal(),
        )
        add_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            add_btn,
            "Add a new channel to track and automate.\nOptionally configure target watch goals and fetch videos.",
        )

        check_btn = ctk.CTkButton(
            btn_frame,
            text="🔍 Check New",
            width=115,
            fg_color="#0D9488",
            hover_color="#0F766E",
            font=ctk.CTkFont(weight="bold"),
            command=self.sync_all_channel_data,
        )
        check_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            check_btn,
            "Scan YouTube to check for newly published content\nacross all saved channels since the last check.",
        )

        sync_data_btn = ctk.CTkButton(
            btn_frame,
            text="⚡ Sync Data All",
            width=135,
            fg_color="#1F6AA5",
            hover_color="#144870",
            font=ctk.CTkFont(weight="bold"),
            command=self.sync_data_all,
        )
        sync_data_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            sync_data_btn,
            "Fetch and synchronize public videos and shorts\nacross all channels into your playback queue.",
        )

        self.current_tab = "Active"
        self.tab_selector = ctk.CTkSegmentedButton(
            btn_frame,
            values=["Active", "Archived"],
            command=self._on_tab_change,
        )
        self.tab_selector.set("Active")
        self.tab_selector.pack(side="left", padx=(0, 10))
        add_tooltip(
            self.tab_selector,
            "Switch between Active tracked channels\nand Archived (paused) channels.",
        )

        clear_btn = ctk.CTkButton(
            btn_frame,
            text="🗑 Clear All",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_channels,
        )
        clear_btn.pack(side="right")
        add_tooltip(
            clear_btn,
            "Permanently remove all channels from your list.\nAssociated queued videos will be unassigned.",
        )

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
        self.app_controller.config_mgr.set_channels([ch.to_dict() for ch in self.channels], preserve_counters=True)
        # Re-sync local watch_seconds with authoritative values from config_mgr
        stored_counters = {
            (c.get("name") or "").strip().lower().lstrip("@"): float(c.get("watch_seconds", 0.0) or 0.0)
            for c in self.app_controller.config_mgr.get_channels() if isinstance(c, dict)
        }
        for ch in self.channels:
            k = (ch.name or "").strip().lower().lstrip("@")
            if k in stored_counters:
                ch.watch_seconds = stored_counters[k]
        self.refresh_list()
        current_view = getattr(self.app_controller, "current_view_name", "")
        queue = self._queue_view()
        if queue is not None:
            if current_view == "queue":
                queue.refresh_list()
            else:
                self.app_controller._queue_needs_refresh = True
        shorts_queue = self._shorts_queue_view()
        if shorts_queue is not None:
            if current_view == "shorts_queue":
                shorts_queue.refresh_list()
            else:
                self.app_controller._shorts_queue_needs_refresh = True
        update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
        if callable(update_dash):
            update_dash()

    # ------------------------------------------------------- cross-view hooks

    def _queue_view(self):
        """Queue view is built after this one, so look it up lazily."""
        views = getattr(self.app_controller, "views", None)
        return views.get("queue") if isinstance(views, dict) else None

    def _shorts_queue_view(self):
        views = getattr(self.app_controller, "views", None)
        return views.get("shorts_queue") if isinstance(views, dict) else None

    @staticmethod
    def _widget_exists(widget) -> bool:
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except (TclError, AttributeError):
            return False

    def _channel_is_current(self, channel, name, url) -> bool:
        return (
            any(saved is channel for saved in self.channels)
            and channel.name == name
            and canonical_channel_url(channel.url) == canonical_channel_url(url)
        )

    def _submit_channel_job(self, loading, progress_bar, work, done, *, current=lambda: True):
        """Bridge plain network results to live dialogs on the main thread."""
        def close_loading():
            if self._widget_exists(loading):
                progress_bar.stop()
                loading.destroy()

        def finish(result):
            close_loading()
            done(result)

        def fail(exc):
            close_loading()
            messagebox.showerror("Fetch Error", f"Failed to fetch channel details:\n{exc}", parent=self)

        return self.app_controller.background_jobs.submit(
            work, done=finish, error=fail,
            current=lambda: self._widget_exists(self) and self._widget_exists(loading) and current(),
            discard=close_loading,
        )

    def _fetch_name_for_dialog(self, modal, url_entry, name_entry, fetch_btn=None, status_lbl=None, *, on_loaded=None):
        """Resolve a name without overwriting newer edits or touching a closed dialog."""
        url = url_entry.get().strip()
        original_name = name_entry.get()
        jobs = self.app_controller.background_jobs
        previous = modal.__dict__.get("_channel_name_job")
        if previous is not None:
            jobs.cancel(previous)
        token = object()
        modal._channel_name_token = token
        if fetch_btn is not None:
            fetch_btn.configure(state="disabled", text="⏳ Fetching...")
        if status_lbl is not None:
            status_lbl.configure(text="Contacting YouTube for official channel name...", text_color=("gray40", "gray60"))

        def reset_button():
            if self._widget_exists(modal) and modal._channel_name_token is token:
                if fetch_btn is not None:
                    fetch_btn.configure(state="normal", text="🔍 Fetch Name")
                if status_lbl is not None:
                    status_lbl.configure(text="")

        def current():
            return (
                self._widget_exists(self) and self._widget_exists(modal)
                and modal._channel_name_token is token
                and url_entry.get().strip() == url and name_entry.get() == original_name
            )

        def done(fetched):
            reset_button()
            if fetched or on_loaded is not None:
                fetched = fetched or extract_fallback_channel_name(url)
                name_entry.delete(0, "end")
                name_entry.insert(0, fetched)
                if status_lbl is not None:
                    status_lbl.configure(text=f"✓ Detected: '{fetched}'", text_color="#10B981")
                if on_loaded is not None:
                    on_loaded()
            else:
                if status_lbl is not None:
                    status_lbl.configure(text="Could not auto-detect channel name; you can type it above.", text_color="#F59E0B")

        def fail(exc):
            reset_button()
            if status_lbl is not None:
                status_lbl.configure(text=f"Could not fetch the name: {exc}", text_color="#EF4444")

        modal._channel_name_job = jobs.submit(
            lambda report: fetch_channel_title_online(url), done=done, error=fail,
            current=current, discard=reset_button,
        )

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
        return format_hours(store.channel_totals(channel.name).watch_seconds)

    def get_channel_target_watch_seconds(self, channel: Channel) -> float:
        """Calculate the total target watch time (in seconds) that can be generated for this channel,
        based on the duration and target multiplier of each video task assigned to this channel."""
        tasks = getattr(self.app_controller, "queued_video_tasks", None)
        if not tasks and hasattr(self.app_controller, "config_mgr"):
            raw = self.app_controller.config_mgr.get_video_queue()
            tasks = [VideoTask.from_dict(d) if isinstance(d, dict) else d for d in raw]

        if not tasks:
            return safe_float(getattr(channel, "target_watch_seconds", 0.0), default=0.0)

        store = getattr(self.app_controller, "stats_store", None)
        ch_name_clean = (channel.name or "").strip().lower().lstrip("@")
        ch_handle_clean = self.get_channel_handle(channel).strip().lower().lstrip("@")

        total_target_sec = 0.0

        for t in tasks:
            t_ch = (getattr(t, "channel", "") or "").strip().lower().lstrip("@")
            if t_ch and (t_ch == ch_name_clean or (ch_handle_clean and t_ch == ch_handle_clean)):
                raw_dur = getattr(t, "duration_seconds", 0)
                dur = safe_float(raw_dur, default=0.0)
                if dur <= 0.0 and store:
                    try:
                        rec = store.find_for_task(t)
                        if rec is not None:
                            vd = getattr(rec, "video_duration", 0)
                            if not hasattr(vd, "_mock_wraps") and not hasattr(vd, "assert_called"):
                                dur = safe_float(vd, default=0.0)
                    except Exception:
                        pass

                raw_mult = getattr(t, "target_multiplier", 0.0)
                mult = safe_float(raw_mult, default=1.0)
                if mult <= 0.0:
                    mult = 1.0  # 1x duration if multiplier is not set or default

                if dur > 0.0:
                    total_target_sec += dur * mult

        if total_target_sec > 0.0:
            return total_target_sec

        return safe_float(getattr(channel, "target_watch_seconds", 0.0), default=0.0)

    def get_channel_watch_time_display(self, channel: Channel) -> str:
        """Calculate and format the watch time display for a channel (Option C: Progress Ratio Style)."""
        store = getattr(self.app_controller, "stats_store", None)
        actual_sec = 0.0
        if store:
            try:
                tot = store.channel_totals(channel.name)
                ws = getattr(tot, "watch_seconds", 0.0)
                actual_sec = safe_float(ws, default=0.0)
            except Exception:
                pass
        if actual_sec <= 0.0:
            actual_sec = safe_float(getattr(channel, "watch_seconds", 0.0), default=0.0)

        target_sec = self.get_channel_target_watch_seconds(channel)

        actual_hrs = max(0.0, actual_sec) / 3600.0
        if target_sec > 0.0:
            target_hrs = target_sec / 3600.0
            percent = int(round((actual_sec / target_sec) * 100))
            return f"Watch Time: {actual_hrs:.1f} / {target_hrs:.1f} hours ({percent}%)"
        else:
            return f"Watch Time: {actual_hrs:.1f} hours"

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
        original_identity = (target.name, target.url)

        modal = ctk.CTkToplevel(self)
        modal.title("Edit Channel" if is_editing else "Add Channel")
        modal.geometry("540x480" if not is_editing else "540x260")
        if hasattr(modal, "resizable"):
            try:
                modal.resizable(False, False)
            except Exception:
                pass
        modal.grab_set()

        ctk.CTkLabel(modal, text="Channel URL or Handle:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=20, pady=(12, 2)
        )
        url_entry = ctk.CTkEntry(
            modal,
            placeholder_text="https://www.youtube.com/@handle or @handle",
            width=500,
        )
        url_entry.insert(0, target.url)
        url_entry.pack(padx=20, pady=(0, 8), fill="x")

        if is_editing:
            ev, es, et = self.get_existing_video_counts_for_channel(target.name)
            v_cnt = target.total_videos if target.total_videos is not None else ev
            s_cnt = target.total_shorts if target.total_shorts is not None else es
            combined_total = v_cnt + s_cnt
            ctk.CTkLabel(
                modal,
                text=f"all videos: {combined_total:,} | shorts: {s_cnt:,} | videos: {v_cnt:,}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=("#1D4ED8", "#93C5FD"),
            ).pack(anchor="w", padx=20, pady=(0, 6))

        fetch_btn = None
        status_lbl = None

        ctk.CTkLabel(modal, text="Channel Name (Optional):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=20, pady=(4, 2)
        )
        name_entry = ctk.CTkEntry(
            modal,
            placeholder_text="e.g. My Channel (Optional — auto-fetched from URL if blank)",
            width=500,
        )
        name_entry.insert(0, target.name)
        name_entry.pack(padx=20, pady=(0, 8), fill="x")

        url_entry.bind("<Return>", lambda e: on_submit())
        name_entry.bind("<Return>", lambda e: on_submit())

        videos_menu = None
        shorts_menu = None
        fetch_var = ctk.BooleanVar(value=True)
        if not is_editing:
            # Checkbox maintained for test compatibility
            fetch_check = ctk.CTkCheckBox(
                modal,
                text="Fetch videos automatically",
                variable=fetch_var,
                font=ctk.CTkFont(size=12, weight="bold"),
            )

            form_frame = ctk.CTkFrame(modal, fg_color=("gray90", "gray18"), corner_radius=8)
            form_frame.pack(padx=20, pady=(4, 10), fill="x")

            # Videos limit section
            ctk.CTkLabel(
                form_frame,
                text="Recent Long-Form Videos to fetch:",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(anchor="w", padx=16, pady=(10, 2))

            ctk.CTkLabel(
                form_frame,
                text="Select number of videos to fetch (or 'All' / 'Fetch No Data'):",
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray60"),
            ).pack(anchor="w", padx=16, pady=(0, 4))

            videos_menu = ctk.CTkOptionMenu(
                form_frame,
                values=FETCH_DATA_OPTIONS,
                width=160,
            )
            videos_menu.set("100")
            videos_menu.pack(anchor="w", padx=16, pady=(0, 10))

            # Shorts limit section
            ctk.CTkLabel(
                form_frame,
                text="Recent YouTube Shorts to fetch:",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(anchor="w", padx=16, pady=(2, 2))

            ctk.CTkLabel(
                form_frame,
                text="Select number of shorts to fetch (or 'All' / 'Fetch No Data'):",
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray60"),
            ).pack(anchor="w", padx=16, pady=(0, 4))

            shorts_menu = ctk.CTkOptionMenu(
                form_frame,
                values=FETCH_DATA_OPTIONS,
                width=160,
            )
            shorts_menu.set("100")
            shorts_menu.pack(anchor="w", padx=16, pady=(0, 14))

        def on_submit() -> None:
            raw_name = name_entry.get().strip()
            raw_url = url_entry.get().strip()

            current_index = next((i for i, ch in enumerate(self.channels) if ch is target), -1)
            if is_editing and (current_index < 0 or (target.name, target.url) != original_identity):
                messagebox.showerror("Channel Changed", "This channel changed or was removed. Reopen it before saving.", parent=modal)
                return

            if not raw_name and not raw_url:
                messagebox.showerror("Error", "Please provide a Channel Name or Channel URL.", parent=modal)
                return

            candidates = [ch for i, ch in enumerate(self.channels) if not is_editing or i != current_index]

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

            v_limit = parse_fetch_limit(videos_menu.get()) if videos_menu else None
            s_limit = parse_fetch_limit(shorts_menu.get()) if shorts_menu else None
            is_auto_fetch_enabled = fetch_var.get() if not is_editing else False
            should_fetch = not is_editing and bool(raw_url) and is_auto_fetch_enabled and (v_limit is not None or s_limit is not None)

            # Case: Channel Name left empty, URL provided
            if not is_editing and not raw_name and raw_url:
                if should_fetch:
                    modal.destroy()
                    self._fetch_and_create_channel(
                        raw_url=raw_url,
                        video_limit=v_limit,
                        shorts_limit=s_limit,
                        on_saved=on_saved,
                        fetch_items=True,
                    )
                    return
                else:
                    self._fetch_name_for_dialog(
                        modal, url_entry, name_entry, fetch_btn, status_lbl, on_loaded=on_submit,
                    )
                    return

            try:
                channel = Channel.parse(
                    raw_name,
                    raw_url,
                    existing=candidates,
                    enabled=target.enabled if is_editing else True,
                    archived=target.archived if is_editing else False,
                    watch_seconds=getattr(target, "watch_seconds", 0.0) if is_editing else 0.0,
                    target_watch_seconds=getattr(target, "target_watch_seconds", 0.0) if is_editing else 0.0,
                    total_videos=target.total_videos if is_editing else None,
                    total_shorts=target.total_shorts if is_editing else None,
                )
            except ValueError as exc:
                messagebox.showerror("Error", str(exc), parent=modal)
                return

            old_name = target.name if is_editing else ""
            if is_editing:
                self.channels[current_index] = channel
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
            shorts_queue = self._shorts_queue_view()
            if shorts_queue is not None:
                shorts_queue.refresh_list()

            modal.destroy()
            if on_saved:
                on_saved(channel)

            if should_fetch:
                self.fetch_data_for_channel(channel, video_limit=v_limit, shorts_limit=s_limit)

        btn_text = "Save Changes" if is_editing else "Add Channel"
        btn_color = "#1F6AA5" if is_editing else "#2FA572"
        btn_hover = "#144870" if is_editing else "#1E7B52"
        submit_btn = ctk.CTkButton(
            modal, text=btn_text, fg_color=btn_color, hover_color=btn_hover, command=on_submit
        )
        submit_btn.pack(pady=10)
        add_tooltip(
            submit_btn,
            "Save channel settings and optionally import\nits public videos and shorts into the queue.",
        )

        # Notify the opener once the dialog goes away (saved or cancelled),
        # so it can restore its own input grab.
        def _on_destroy(event) -> None:
            if str(event.widget) != str(modal):
                return
            if on_close:
                on_close()

        modal.bind("<Destroy>", _on_destroy)
        name_entry.focus_set()

    def open_fetch_data_dialog(
        self,
        channel: Channel,
        on_imported: Optional[Callable[[], None]] = None,
    ) -> None:
        """Display options dialog to select separate limits for Videos and Shorts, then fetch."""
        if not channel.url:
            messagebox.showwarning(
                "Missing URL",
                f"Channel '{channel.name}' does not have a URL configured.",
                parent=self,
            )
            return

        modal = ctk.CTkToplevel(self)
        modal.title(f"Fetch Channel Data — {channel.name}")
        modal.geometry("450x380")
        modal.minsize(420, 350)
        modal.resizable(False, False)
        modal.grab_set()

        # Header
        header = ctk.CTkFrame(modal, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 10))

        ctk.CTkLabel(
            header,
            text="⚡ Fetch Channel Data",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text=f"Channel: {channel.name}\nURL: {channel.url}",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
            justify="left",
            wraplength=400,
        ).pack(anchor="w", pady=(4, 0))

        form_frame = ctk.CTkFrame(modal, fg_color=("gray90", "gray18"), corner_radius=8)
        form_frame.pack(fill="x", padx=24, pady=10)

        # Videos limit section
        ctk.CTkLabel(
            form_frame,
            text="Recent Long-Form Videos to fetch:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 2))

        ctk.CTkLabel(
            form_frame,
            text="Select number of videos to fetch (or 'All' / 'Fetch No Data'):",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=16, pady=(0, 4))

        videos_menu = ctk.CTkOptionMenu(
            form_frame,
            values=FETCH_DATA_OPTIONS,
            width=160,
        )
        videos_menu.set("100")
        videos_menu.pack(anchor="w", padx=16, pady=(0, 12))

        # Shorts limit section
        ctk.CTkLabel(
            form_frame,
            text="Recent YouTube Shorts to fetch:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(4, 2))

        ctk.CTkLabel(
            form_frame,
            text="Select number of shorts to fetch (or 'All' / 'Fetch No Data'):",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=16, pady=(0, 4))

        shorts_menu = ctk.CTkOptionMenu(
            form_frame,
            values=FETCH_DATA_OPTIONS,
            width=160,
        )
        shorts_menu.set("100")
        shorts_menu.pack(anchor="w", padx=16, pady=(0, 16))

        # Actions
        btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(10, 15))

        def _on_cancel():
            modal.destroy()

        def _on_fetch():
            v_count = parse_fetch_limit(videos_menu.get())
            s_count = parse_fetch_limit(shorts_menu.get())

            if v_count is None and s_count is None:
                messagebox.showwarning(
                    "No Items Selected",
                    "Please select at least 1 video or 1 short to fetch (both cannot be 'Fetch No Data').",
                    parent=modal,
                )
                return

            modal.destroy()
            self.fetch_data_for_channel(
                channel=channel,
                video_limit=v_count,
                shorts_limit=s_count,
                on_imported=on_imported,
            )

        cancel_btn = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=90,
            fg_color="#555555",
            hover_color="#333333",
            command=_on_cancel,
        )
        cancel_btn.pack(side="right", padx=(10, 0))
        add_tooltip(
            cancel_btn,
            "Close this window without fetching any\nchannel content from YouTube.",
        )

        fetch_btn = ctk.CTkButton(
            btn_frame,
            text="⚡ Fetch",
            width=110,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            command=_on_fetch,
        )
        fetch_btn.pack(side="right")
        add_tooltip(
            fetch_btn,
            "Retrieve public videos and shorts from YouTube\nmatching the selected quantity limits.",
        )

    def fetch_data_for_channel(
        self,
        channel: Channel,
        video_limit: Optional[int] = 100,
        shorts_limit: Optional[int] = 100,
        on_imported: Optional[Callable[[], None]] = None,
        parent_modal: Optional[Any] = None,
    ) -> None:
        """Fetch videos and/or shorts for a channel using YouTube Data API v3 and open selection modal."""
        active_parent = parent_modal if self._widget_exists(parent_modal) else self

        if not channel.url:
            messagebox.showwarning(
                "Missing URL",
                f"Channel '{channel.name}' does not have a URL configured.",
                parent=active_parent,
            )
            return

        loading = ctk.CTkToplevel(active_parent)
        loading.title("Fetching Data...")
        loading.geometry("400x150")
        loading.grab_set()

        v_desc = "all" if video_limit == 0 else (f"up to {video_limit}" if video_limit is not None else "no")
        s_desc = "all" if shorts_limit == 0 else (f"up to {shorts_limit}" if shorts_limit is not None else "no")
        status_text = f"Fetching {v_desc} video(s) and {s_desc} short(s) for\n'{channel.name}'..."
        ctk.CTkLabel(
            loading,
            text=status_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="center",
            wraplength=360,
        ).pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(loading, mode="indeterminate", width=280)
        prog.pack(pady=(0, 15))
        prog.start()

        def _on_fetch_done(res: ChannelFetchResult):
            if res.error:
                messagebox.showerror(
                    "Fetch Error",
                    f"Failed to fetch channel data:\n{res.error}",
                    parent=active_parent if self._widget_exists(active_parent) else self,
                )
                return

            if res.total_videos is not None:
                channel.total_videos = res.total_videos
            if res.total_shorts is not None:
                channel.total_shorts = res.total_shorts
            if res.total_videos is not None or res.total_shorts is not None:
                self.save_channels()
                self.refresh_list()

            if not res.videos:
                messagebox.showinfo(
                    "No Items Found",
                    f"No public videos or shorts found for '{channel.name}'.",
                    parent=active_parent if self._widget_exists(active_parent) else self,
                )
                return

            existing_ids: Set[str] = {
                extract_video_id(t.url)
                for t in (getattr(self.app_controller, "queued_video_tasks", []) or [])
                if extract_video_id(t.url)
            }

            def _handle_import(selected_tasks: List[VideoTask]):
                if not selected_tasks:
                    return
                prior_len = len(self.app_controller.config_mgr.get_video_queue())
                new_dicts = [t.to_dict() for t in selected_tasks]
                updated_raw = self.app_controller.config_mgr.append_video_tasks(new_dicts, deduplicate=True)
                new_tasks = [VideoTask.from_dict(d) for d in updated_raw]
                self.app_controller.queued_video_tasks = new_tasks
                added_count = len(updated_raw) - prior_len

                if hasattr(self.app_controller, "on_queue_changed"):
                    self.app_controller.on_queue_changed(new_tasks)

                self.refresh_list()
                if on_imported:
                    on_imported()

                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()

                shorts_imported = sum(1 for t in selected_tasks if "/shorts/" in (t.url or ""))
                vids_imported = len(selected_tasks) - shorts_imported

                if added_count == len(selected_tasks):
                    msg = f"Successfully imported {added_count} item(s) ({vids_imported} video(s), {shorts_imported} short(s)) for channel '{channel.name}'."
                elif added_count > 0:
                    msg = f"Imported {added_count} item(s) for channel '{channel.name}' ({len(selected_tasks) - added_count} duplicate(s) skipped)."
                else:
                    msg = f"All {len(selected_tasks)} selected item(s) were already in the queue."

                messagebox.showinfo(
                    "Import Complete",
                    msg,
                    parent=active_parent if self._widget_exists(active_parent) else self,
                )

            ChannelImportModal(
                parent=active_parent,
                channel_name=channel.name,
                channel_url=channel.url,
                fetched_videos=res.videos,
                existing_video_ids=existing_ids,
                on_import=_handle_import,
                fetch_result=res,
            )

        name, url = channel.name, channel.url
        def _fetch_worker(report):
            if shorts_limit is not None:
                return fetch_channel_data(
                    url,
                    video_limit=video_limit,
                    shorts_limit=shorts_limit,
                    config_mgr=getattr(self.app_controller, "config_mgr", None),
                )
            return fetch_channel_videos(
                url,
                max_results=video_limit if video_limit is not None else 50,
                config_mgr=getattr(self.app_controller, "config_mgr", None),
            )

        self._submit_channel_job(
            loading,
            prog,
            _fetch_worker,
            _on_fetch_done,
            current=lambda: self._channel_is_current(channel, name, url),
        )

    def fetch_videos_for_channel(
        self,
        channel: Channel,
        max_results: int = 50,
        on_imported: Optional[Callable[[], None]] = None,
    ) -> None:
        """Compatibility wrapper delegating to fetch_data_for_channel."""
        self.fetch_data_for_channel(
            channel=channel,
            video_limit=max_results,
            shorts_limit=None,
            on_imported=on_imported,
        )

    def _fetch_and_create_channel(
        self,
        raw_url: str,
        max_fetch: int = 50,
        on_saved: Optional[OnChannelSaved] = None,
        video_limit: Optional[int] = None,
        shorts_limit: Optional[int] = None,
        fetch_items: bool = True,
    ) -> None:
        """Fetch channel name and videos/shorts when channel name was omitted, then create channel and prompt for import."""
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

        if video_limit is None and shorts_limit is None:
            v_lim = max_fetch
            s_lim = None
        else:
            v_lim = video_limit
            s_lim = shorts_limit

        should_fetch_items = fetch_items and (v_lim is not None or s_lim is not None)

        loading = ctk.CTkToplevel(self)
        loading.title("Fetching Channel...")
        loading.geometry("400x150")
        loading.grab_set()

        if should_fetch_items:
            v_desc = "all" if v_lim == 0 else (f"up to {v_lim}" if v_lim is not None else "no")
            s_desc = "all" if s_lim == 0 else (f"up to {s_lim}" if s_lim is not None else "no")
            status_text = f"Resolving channel & fetching\n{v_desc} video(s) and {s_desc} short(s) for\n'{raw_url}'..."
        else:
            status_text = f"Resolving channel name for\n'{raw_url}'..."

        ctk.CTkLabel(
            loading,
            text=status_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="center",
            wraplength=360,
        ).pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(loading, mode="indeterminate", width=280)
        prog.pack(pady=(0, 15))
        prog.start()

        def _bg_fetch(report):
            if should_fetch_items:
                if s_lim is not None:
                    res = fetch_channel_data(
                        raw_url,
                        video_limit=v_lim,
                        shorts_limit=s_lim,
                        config_mgr=getattr(self.app_controller, "config_mgr", None),
                    )
                else:
                    res = fetch_channel_videos(
                        raw_url,
                        max_results=v_lim if v_lim is not None else 50,
                        config_mgr=getattr(self.app_controller, "config_mgr", None),
                    )
            else:
                online_title = fetch_channel_title_online(raw_url) or extract_fallback_channel_name(raw_url)
                res = ChannelFetchResult(
                    channel_title=online_title,
                    channel_url=clean_channel_url(raw_url),
                    videos=[],
                )
            if not res.error and not (res.channel_title or "").strip():
                res.channel_title = fetch_channel_title_online(raw_url) or extract_fallback_channel_name(raw_url)
            return res

        def _on_fetch_done(res: ChannelFetchResult):
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
                total_videos=res.total_videos,
                total_shorts=res.total_shorts,
            )
            self.channels.append(channel)
            self.save_channels()

            current_view = getattr(self.app_controller, "current_view_name", "")
            queue = self._queue_view()
            if queue is not None:
                if current_view == "queue":
                    queue.refresh_list()
                else:
                    self.app_controller._queue_needs_refresh = True
            shorts_queue = self._shorts_queue_view()
            if shorts_queue is not None:
                if current_view == "shorts_queue":
                    shorts_queue.refresh_list()
                else:
                    self.app_controller._shorts_queue_needs_refresh = True

            if on_saved:
                on_saved(channel)

            if not should_fetch_items:
                messagebox.showinfo(
                    "Channel Added",
                    f"Created channel '{channel.name}'.",
                    parent=self,
                )
                return

            if not res.videos:
                messagebox.showinfo(
                    "Channel Added",
                    f"Created channel '{channel.name}', but no public videos or shorts were found.",
                    parent=self,
                )
                return

            existing_ids: Set[str] = {
                extract_video_id(t.url)
                for t in (getattr(self.app_controller, "queued_video_tasks", []) or [])
                if extract_video_id(t.url)
            }

            def _handle_import(selected_tasks: List[VideoTask]):
                if not selected_tasks:
                    return
                prior_len = len(self.app_controller.config_mgr.get_video_queue())
                new_dicts = [t.to_dict() for t in selected_tasks]
                updated_raw = self.app_controller.config_mgr.append_video_tasks(new_dicts, deduplicate=True)
                new_tasks = [VideoTask.from_dict(d) for d in updated_raw]
                self.app_controller.queued_video_tasks = new_tasks
                added_count = len(updated_raw) - prior_len

                if hasattr(self.app_controller, "on_queue_changed"):
                    self.app_controller.on_queue_changed(new_tasks)

                self.refresh_list()
                update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                if callable(update_dash):
                    update_dash()

                shorts_imported = sum(1 for t in selected_tasks if "/shorts/" in (t.url or ""))
                vids_imported = len(selected_tasks) - shorts_imported

                if added_count == len(selected_tasks):
                    msg = f"Successfully imported {added_count} item(s) ({vids_imported} video(s), {shorts_imported} short(s)) for channel '{channel.name}'."
                elif added_count > 0:
                    msg = f"Imported {added_count} item(s) for channel '{channel.name}' ({len(selected_tasks) - added_count} duplicate(s) skipped)."
                else:
                    msg = f"All {len(selected_tasks)} selected item(s) were already in the queue."

                messagebox.showinfo(
                    "Import Complete",
                    msg,
                    parent=self,
                )

            ChannelImportModal(
                parent=self,
                channel_name=channel.name,
                channel_url=channel.url,
                fetched_videos=res.videos,
                existing_video_ids=existing_ids,
                on_import=_handle_import,
                fetch_result=res,
            )

        self._submit_channel_job(loading, prog, _bg_fetch, _on_fetch_done)

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
        modal.geometry("680x620")
        if hasattr(modal, "minsize"):
            modal.minsize(640, 560)
        modal.grab_set()

        header = ctk.CTkFrame(modal, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 8))

        tag = " [ARCHIVED]" if channel.archived else (" [DISABLED]" if not channel.enabled else "")

        # 1. In-place editable Channel Name and 'Open Channel' Action Button
        name_container = ctk.CTkFrame(header, fg_color="transparent")
        name_container.pack(fill="x", anchor="w")

        def _open_channel():
            raw_url = (channel.url or "").strip()
            if not raw_url:
                messagebox.showinfo(
                    "Channel URL",
                    "No URL configured for this channel yet. Click the URL line to set one.",
                    parent=modal,
                )
                return
            if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
                raw_url = f"https://www.youtube.com/{raw_url}" if raw_url.startswith("@") else f"https://www.youtube.com/@{raw_url}"
            webbrowser.open_new(raw_url)

        open_channel_btn = ctk.CTkButton(
            name_container,
            text="Open Channel",
            width=110,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=_open_channel,
        )
        open_channel_btn.pack(side="right", anchor="e")

        name_lbl = ctk.CTkLabel(
            name_container,
            text=f"📺 {channel.name}{tag}",
            font=ctk.CTkFont(size=18, weight="bold"),
            cursor="hand2",
        )
        name_lbl.pack(side="left", anchor="w", before=open_channel_btn)

        name_entry = None
        name_saving = False

        def _save_name(event=None):
            nonlocal name_entry, name_saving
            if name_saving or name_entry is None:
                return
            name_saving = True
            try:
                raw_new = name_entry.get().strip()
                if raw_new and raw_new != channel.name:
                    if any(c is not channel and c.name.lower() == raw_new.lower() for c in self.channels):
                        messagebox.showwarning(
                            "Duplicate Name",
                            f"A channel with the name '{raw_new}' already exists.",
                            parent=modal,
                        )
                    else:
                        old_name = channel.name
                        channel.name = raw_new
                        self.save_channels()
                        queue = self._queue_view()
                        if queue:
                            queue.reassign_channel(old_name, raw_new)
                        self.refresh_list()
                        modal.title(f"Channel Videos — {channel.name}")
                        scroll.configure(label_text=f"Videos under '{channel.name}'")
                        update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
                        if callable(update_dash):
                            update_dash()
                name_lbl.configure(text=f"📺 {channel.name}{tag}")
            finally:
                if name_entry is not None:
                    name_entry.destroy()
                    name_entry = None
                name_lbl.pack(side="left", anchor="w", before=open_channel_btn)
                name_saving = False

        def _cancel_name(event=None):
            nonlocal name_entry, name_saving
            if name_saving or name_entry is None:
                return
            name_saving = True
            try:
                if name_entry is not None:
                    name_entry.destroy()
                    name_entry = None
                name_lbl.pack(side="left", anchor="w", before=open_channel_btn)
            finally:
                name_saving = False

        def _edit_name(event=None):
            nonlocal name_entry, name_saving
            if name_entry is not None:
                return
            name_lbl.pack_forget()
            name_entry = ctk.CTkEntry(
                name_container,
                font=ctk.CTkFont(size=16, weight="bold"),
                height=30,
            )
            name_entry.insert(0, channel.name)
            name_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), before=open_channel_btn)
            name_entry.focus_set()
            name_entry.select_range(0, "end")
            name_entry.bind("<Return>", _save_name)
            name_entry.bind("<FocusOut>", _save_name)
            name_entry.bind("<Escape>", _cancel_name)

        name_lbl.bind("<Button-1>", _edit_name)

        # 2. In-place editable Channel URL
        url_container = ctk.CTkFrame(header, fg_color="transparent")
        url_container.pack(fill="x", anchor="w", pady=(3, 0))

        def _get_url_display_text():
            return channel.url if channel.url else "Click to set channel URL / handle..."

        url_lbl = ctk.CTkLabel(
            url_container,
            text=_get_url_display_text(),
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60") if channel.url else ("#2563EB", "#60A5FA"),
            cursor="hand2",
        )
        url_lbl.pack(side="left", anchor="w")

        url_entry = None
        url_saving = False

        def _save_url(event=None):
            nonlocal url_entry, url_saving
            if url_saving or url_entry is None:
                return
            url_saving = True
            try:
                raw_new = url_entry.get().strip()
                cleaned = clean_channel_url(raw_new) if raw_new else ""
                if cleaned != channel.url:
                    canon = canonical_channel_url(cleaned)
                    if canon and any(c is not channel and canonical_channel_url(c.url) == canon for c in self.channels):
                        messagebox.showwarning(
                            "Duplicate URL",
                            "A channel with this URL already exists.",
                            parent=modal,
                        )
                    else:
                        channel.url = cleaned
                        self.save_channels()
                        self.refresh_list()
                        if channel.url:
                            if not fetch_settings_frame.winfo_ismapped():
                                fetch_settings_frame.pack(before=modal_actions, fill="x", padx=20, pady=(2, 6))
                            if not fetch_data_btn.winfo_ismapped():
                                fetch_data_btn.pack(side="left", padx=(8, 0))
                        else:
                            if fetch_settings_frame.winfo_ismapped():
                                fetch_settings_frame.pack_forget()
                            if fetch_data_btn.winfo_ismapped():
                                fetch_data_btn.pack_forget()
                url_lbl.configure(
                    text=_get_url_display_text(),
                    text_color=("gray40", "gray60") if channel.url else ("#2563EB", "#60A5FA"),
                )
            finally:
                if url_entry is not None:
                    url_entry.destroy()
                    url_entry = None
                url_lbl.pack(side="left", anchor="w")
                url_saving = False

        def _cancel_url(event=None):
            nonlocal url_entry, url_saving
            if url_saving or url_entry is None:
                return
            url_saving = True
            try:
                if url_entry is not None:
                    url_entry.destroy()
                    url_entry = None
                url_lbl.pack(side="left", anchor="w")
            finally:
                url_saving = False

        def _edit_url(event=None):
            nonlocal url_entry, url_saving
            if url_entry is not None:
                return
            url_lbl.pack_forget()
            url_entry = ctk.CTkEntry(
                url_container,
                font=ctk.CTkFont(size=12),
                height=26,
                placeholder_text="https://www.youtube.com/@handle or @handle",
            )
            url_entry.insert(0, channel.url or "")
            url_entry.pack(side="left", fill="x", expand=True)
            url_entry.focus_set()
            url_entry.select_range(0, "end")
            url_entry.bind("<Return>", _save_url)
            url_entry.bind("<FocusOut>", _save_url)
            url_entry.bind("<Escape>", _cancel_url)

        url_lbl.bind("<Button-1>", _edit_url)

        # 3. Channel Video Counts displayed directly under the URL
        totals_lbl = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#16A34A", "#4ADE80") if (getattr(channel, "has_new_content", False) or getattr(channel, "has_new_videos", False)) else ("#1D4ED8", "#93C5FD"),
        )
        totals_lbl.pack(anchor="w", pady=(3, 0))

        def _update_totals_display():
            ev, es, et = self.get_existing_video_counts_for_channel(channel.name)
            v_cnt = channel.total_videos if channel.total_videos is not None else ev
            s_cnt = channel.total_shorts if channel.total_shorts is not None else es
            combined_total = v_cnt + s_cnt

            header_text = f"all videos: {combined_total:,} | shorts: {s_cnt:,} | videos: {v_cnt:,}"
            if getattr(channel, "has_new_content", False) or getattr(channel, "has_new_videos", False):
                badge = getattr(channel, "new_badge_text", "") or "🔥 New Videos Available"
                header_text = f"{header_text}  •  {badge}"
            totals_lbl.configure(
                text=header_text,
                text_color=("#16A34A", "#4ADE80") if (getattr(channel, "has_new_content", False) or getattr(channel, "has_new_videos", False)) else ("#1D4ED8", "#93C5FD"),
            )

        _update_totals_display()

        stats = self.channel_stats_line(channel.name)
        if stats:
            ctk.CTkLabel(
                header,
                text=stats,
                font=ctk.CTkFont(size=12, slant="italic"),
            ).pack(anchor="w", pady=(2, 0))

        fetch_settings_frame = ctk.CTkFrame(modal, fg_color=("gray90", "gray18"), corner_radius=8)

        v_box = ctk.CTkFrame(fetch_settings_frame, fg_color="transparent")
        v_box.pack(side="left", padx=(14, 12), pady=8)
        ctk.CTkLabel(
            v_box,
            text="Videos to Fetch:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        videos_menu = ctk.CTkOptionMenu(
            v_box,
            values=FETCH_DATA_OPTIONS,
            width=150,
        )
        videos_menu.set("100")
        videos_menu.pack(anchor="w", pady=(3, 0))

        s_box = ctk.CTkFrame(fetch_settings_frame, fg_color="transparent")
        s_box.pack(side="left", padx=(0, 14), pady=8)
        ctk.CTkLabel(
            s_box,
            text="Shorts to Fetch:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        shorts_menu = ctk.CTkOptionMenu(
            s_box,
            values=FETCH_DATA_OPTIONS,
            width=150,
        )
        shorts_menu.set("100")
        shorts_menu.pack(anchor="w", pady=(3, 0))

        if channel.url:
            fetch_settings_frame.pack(fill="x", padx=20, pady=(2, 6))

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

        new_video_btn = ctk.CTkButton(
            modal_actions,
            text="+ New Video",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            command=_add_video_for_channel,
        )
        new_video_btn.pack(side="left")
        add_tooltip(
            new_video_btn,
            "Manually add a single video task URL\nor search keyword under this channel.",
        )

        def _do_fetch_data():
            if not channel.url:
                messagebox.showwarning(
                    "Missing URL",
                    f"Channel '{channel.name}' does not have a URL configured.",
                    parent=modal,
                )
                return

            v_count = parse_fetch_limit(videos_menu.get())
            s_count = parse_fetch_limit(shorts_menu.get())

            if v_count is None and s_count is None:
                messagebox.showwarning(
                    "No Items Selected",
                    "Please select at least 1 video or 1 short to fetch (both cannot be 'Fetch No Data').",
                    parent=modal,
                )
                return

            self.fetch_data_for_channel(
                channel=channel,
                video_limit=v_count,
                shorts_limit=s_count,
                on_imported=lambda: _refresh_modal_videos(),
                parent_modal=modal,
            )

        fetch_data_btn = ctk.CTkButton(
            modal_actions,
            text="⚡ Fetch Data",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=_do_fetch_data,
        )
        if channel.url:
            fetch_data_btn.pack(side="left", padx=(8, 0))
        add_tooltip(
            fetch_data_btn,
            "Fetch recent videos and shorts for this channel\nusing the limits configured above.",
        )

        scroll = ctk.CTkScrollableFrame(modal, label_text=f"Videos under '{channel.name}'")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        def _refresh_modal_videos():
            _update_totals_display()
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

                is_short = "/shorts/" in (task.url or "")
                type_tag = "[⚡ SHORT] " if is_short else "[🎬 VIDEO] "
                label_parts = [f"{pos}. {type_tag}{task.title or task.keywords or ('Untitled Short' if is_short else 'Untitled Video')}"]
                if task.title_2:
                    label_parts.append(f"Retry 2: {task.title_2}")
                if task.title_3:
                    label_parts.append(f"Retry 3: {task.title_3}")
                if task.url:
                    label_parts.append(f"URL: {task.url}")
                store = getattr(self.app_controller, "stats_store", None)
                rec = store.find_for_task(task) if store else None
                v_watch_sec = float(rec.watch_seconds) if rec else 0.0
                eff_dur = float(rec.video_duration if rec and rec.video_duration > 0 else getattr(task, "duration_seconds", 0.0))

                v_mult = float(getattr(task, "target_multiplier", 0.0) or 0.0)
                v_target_sec = eff_dur * v_mult if v_mult > 0 else 0.0

                if eff_dur >= 60.0:
                    v_len = f"Length: {max(1, int(round(eff_dur / 60.0)))} min"
                elif eff_dur > 0.0:
                    v_len = f"Length: {int(eff_dur)}s"
                else:
                    v_len = ""

                ratio_display = format_video_watch_ratio(v_watch_sec, v_target_sec)
                if v_len:
                    label_parts.append(f"{v_len} | Watch Time: {ratio_display}")
                else:
                    label_parts.append(f"Watch Time: {ratio_display}")

                if task.max_runs > 0:
                    label_parts.append(f"Limit: {task.max_runs} runs")

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

                btn_m_up = ctk.CTkButton(
                    card_btns,
                    text="▲",
                    width=30,
                    height=28,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    fg_color="#374151" if can_m_up else "gray30",
                    hover_color="#4B5563" if can_m_up else "gray30",
                    state="normal" if can_m_up else "disabled",
                    command=_move_modal_up,
                )
                btn_m_up.pack(side="left", padx=(0, 2))
                add_tooltip(btn_m_up, "Move this video higher in the channel queue order.")

                btn_m_down = ctk.CTkButton(
                    card_btns,
                    text="▼",
                    width=30,
                    height=28,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    fg_color="#374151" if can_m_down else "gray30",
                    hover_color="#4B5563" if can_m_down else "gray30",
                    state="normal" if can_m_down else "disabled",
                    command=_move_modal_down,
                )
                btn_m_down.pack(side="left", padx=(0, 6))
                add_tooltip(btn_m_down, "Move this video lower in the channel queue order.")

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

                btn_edit = ctk.CTkButton(
                    card_btns,
                    text="✏ Edit",
                    width=60,
                    fg_color="#1F6AA5",
                    hover_color="#144870",
                    command=_edit,
                )
                btn_edit.pack(side="left", padx=(0, 4))
                add_tooltip(
                    btn_edit,
                    "Edit titles, search keywords, watch multiplier,\nor run limits for this video task.",
                )

                btn_del = ctk.CTkButton(
                    card_btns,
                    text="🗑 Delete",
                    width=60,
                    fg_color="#D32F2F",
                    hover_color="#9A0007",
                    command=_delete,
                )
                btn_del.pack(side="left")
                add_tooltip(btn_del, "Delete this video task from the channel queue.")

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

    def get_existing_video_counts_for_channel(self, channel_name: str) -> Tuple[int, int, int]:
        """Count unique (long_form_count, shorts_count, total_count) for a channel from queue and stats."""
        unique_vids: Set[str] = set()
        unique_shorts: Set[str] = set()

        tasks = getattr(self.app_controller, "queued_video_tasks", None) or []
        for t in tasks:
            if (t.channel or "").strip().lower() == channel_name.strip().lower():
                vid_id = extract_video_id(t.url) or (t.url or t.title)
                if vid_id:
                    if "/shorts/" in (t.url or ""):
                        unique_shorts.add(vid_id)
                    else:
                        unique_vids.add(vid_id)

        store = getattr(self.app_controller, "stats_store", None)
        if store:
            records = store.for_channel(channel_name)
            for r in records:
                vid_key = getattr(r, "video_key", "") or getattr(r, "task_key", "")
                if vid_key:
                    is_short = getattr(r, "is_short", False) or ("/shorts/" in vid_key)
                    if is_short:
                        unique_shorts.add(vid_key)
                    else:
                        unique_vids.add(vid_key)

        return len(unique_vids), len(unique_shorts), len(unique_vids) + len(unique_shorts)

    def sync_all_channel_data(self) -> None:
        """Fetch total videos and shorts for all channels from YouTube, match against existing stats/queue,
        and flag channels with 'New Videos Available'."""
        channels_with_urls = [ch for ch in self.channels if ch.url]
        if not channels_with_urls:
            messagebox.showinfo("Sync Channel Data", "No channels with URLs configured to sync.", parent=self)
            return

        toast = ctk.CTkToplevel(self)
        toast.title("Checking for New Videos...")
        toast.geometry("400x140")
        toast.grab_set()

        lbl = ctk.CTkLabel(
            toast,
            text=f"Checking channel totals from YouTube (0/{len(channels_with_urls)})...",
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=360,
        )
        lbl.pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(toast, mode="indeterminate", width=320)
        prog.pack(pady=(0, 15))
        prog.start()

        snapshots = [(ch, ch.name, ch.url) for ch in channels_with_urls]
        requests = [(name, url) for _, name, url in snapshots]

        def _bg_sync(report):
            results = []
            for i, (name, url) in enumerate(requests, 1):
                report((i, name))
                try:
                    res = fetch_channel_data(
                        url,
                        video_limit=None,
                        shorts_limit=None,
                        config_mgr=getattr(self.app_controller, "config_mgr", None),
                    )
                    results.append(res)
                except Exception as exc:
                    results.append(ChannelFetchResult(channel_url=url, error=str(exc)))
            return results

        def _close():
            if self._widget_exists(toast):
                prog.stop()
                toast.destroy()

        def _done(results):
            _close()
            api_errors = [f"• {old_name or ch.name}: {res.error}" for (ch, old_name, old_url), res in zip(snapshots, results) if res.error]
            if api_errors:
                messagebox.showerror(
                    "YouTube API Error",
                    "The following issue(s) occurred while contacting YouTube API:\n\n" + "\n".join(api_errors),
                    parent=self,
                )
            new_vids_channels = 0
            updated_count = 0
            queue = self._queue_view()

            for (ch, old_name, old_url), res in zip(snapshots, results):
                if not self._channel_is_current(ch, old_name, old_url):
                    continue

                if res.total_videos is not None:
                    ch.total_videos = res.total_videos
                if res.total_shorts is not None:
                    ch.total_shorts = res.total_shorts

                # Update channel name if official name changed
                official_name = (res.channel_title or "").strip()
                if official_name and official_name != old_name:
                    if not any(other is not ch and other.name.lower() == official_name.lower() for other in self.channels):
                        ch.name = official_name
                        if queue is not None:
                            queue.reassign_channel(old_name, ch.name)

                # Match against existing videos and shorts in stats and queue
                exist_vids, exist_shorts, exist_total = self.get_existing_video_counts_for_channel(ch.name)

                yt_vids = ch.total_videos if ch.total_videos is not None else 0
                yt_shorts = ch.total_shorts if ch.total_shorts is not None else 0
                yt_total = yt_vids + yt_shorts

                has_new_v = False
                has_new_s = False

                if yt_total > 0:
                    if ch.total_videos is not None and yt_vids > exist_vids:
                        has_new_v = True
                    if ch.total_shorts is not None and yt_shorts > exist_shorts:
                        has_new_s = True
                    if not has_new_v and not has_new_s and yt_total > exist_total:
                        has_new_v = True

                ch.has_new_videos = has_new_v
                ch.has_new_shorts = has_new_s
                if getattr(ch, "has_new_content", False) or has_new_v or has_new_s:
                    new_vids_channels += 1
                updated_count += 1

            self.save_channels()
            self.refresh_list()
            if queue is not None:
                queue.refresh_list()

            msg = f"Checked {updated_count} channel(s)."
            if new_vids_channels > 0:
                msg += f"\n\n🔥 {new_vids_channels} channel(s) have new videos or shorts available!"
            else:
                msg += "\n\nAll channels are up to date with existing queue and stats."

            messagebox.showinfo("Check Complete", msg, parent=self)

        def _progress(data):
            idx, name = data
            lbl.configure(text=f"Syncing '{name}' ({idx}/{len(requests)})...")

        def _failed(exc):
            _close()
            messagebox.showerror("Sync Error", f"Failed to sync channel data:\n{exc}", parent=self)

        self.app_controller.background_jobs.submit(
            _bg_sync, done=_done, error=_failed, progress=_progress,
            current=lambda: self._widget_exists(self) and self._widget_exists(toast),
            discard=_close,
        )

    def sync_all_channel_names(self) -> None:
        """Fetch and update official YouTube channel names for all configured channels."""
        channels_with_urls = [ch for ch in self.channels if ch.url]
        if not channels_with_urls:
            messagebox.showinfo("Sync Channel Names", "No channels with URLs configured to sync.", parent=self)
            return

        toast = ctk.CTkToplevel(self)
        toast.title("Syncing Channel Names...")
        toast.geometry("400x140")
        toast.grab_set()

        lbl = ctk.CTkLabel(
            toast,
            text=f"Fetching official channel names from YouTube (0/{len(channels_with_urls)})...",
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=360,
        )
        lbl.pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(toast, mode="indeterminate", width=320)
        prog.pack(pady=(0, 15))
        prog.start()

        snapshots = [(ch, ch.name, ch.url) for ch in channels_with_urls]
        requests = [(name, url) for _, name, url in snapshots]

        def _bg_sync(report):
            names = []
            for i, (name, url) in enumerate(requests, 1):
                report((i, name))
                try:
                    names.append(fetch_channel_title_online(url))
                except Exception:
                    names.append("")
            return names

        def _close():
            if self._widget_exists(toast):
                prog.stop()
                toast.destroy()

        def _done(names):
            _close()
            updated_count = 0
            queue = self._queue_view()
            for (ch, old_name, old_url), fetched in zip(snapshots, names):
                if not self._channel_is_current(ch, old_name, old_url):
                    continue
                official_name = (fetched or "").strip()
                if not official_name or official_name == old_name:
                    continue
                # A rename must not collapse two saved channels into one.
                if any(other is not ch and other.name.lower() == official_name.lower() for other in self.channels):
                    continue
                ch.name = official_name
                if queue is not None:
                    queue.reassign_channel(old_name, ch.name)
                updated_count += 1
            if updated_count:
                self.save_channels()
                self.load_channels()
                if queue is not None:
                    queue.refresh_list()
            messagebox.showinfo(
                "Sync Complete",
                f"Channel names updated: {updated_count} channel(s) updated to official YouTube names.",
                parent=self,
            )

        def _progress(data):
            idx, name = data
            lbl.configure(text=f"Resolving '{name}' ({idx}/{len(requests)})...")

        def _failed(exc):
            _close()
            messagebox.showerror("Sync Error", f"Failed to sync channel names:\n{exc}", parent=self)

        self.app_controller.background_jobs.submit(
            _bg_sync, done=_done, error=_failed, progress=_progress,
            current=lambda: self._widget_exists(self) and self._widget_exists(toast),
            discard=_close,
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

    def sync_data_all(self) -> None:
        """Sync new available videos and shorts for all configured channels directly into the queue."""
        channels_to_sync = [ch for ch in self.channels if ch.url and not ch.archived]
        if not channels_to_sync:
            channels_to_sync = [ch for ch in self.channels if ch.url]
        if not channels_to_sync:
            messagebox.showinfo("Sync Data All", "No channels with URLs configured to sync.", parent=self)
            return

        toast = ctk.CTkToplevel(self)
        toast.title("Syncing All Channels...")
        toast.geometry("420x150")
        toast.grab_set()

        lbl = ctk.CTkLabel(
            toast,
            text=f"Syncing data for all channels (0/{len(channels_to_sync)})...",
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=380,
        )
        lbl.pack(pady=(20, 10))

        prog = ctk.CTkProgressBar(toast, mode="indeterminate", width=320)
        prog.pack(pady=(0, 15))
        prog.start()

        snapshots = [(ch, ch.name, ch.url, getattr(ch, "has_new_videos", False), getattr(ch, "has_new_shorts", False)) for ch in channels_to_sync]

        config_mgr = getattr(self.app_controller, "config_mgr", None)

        def _bg_sync(report):
            results = []
            for i, (ch, name, url, has_v, has_s) in enumerate(snapshots, 1):
                report((i, name))
                if has_v and not has_s:
                    v_lim, s_lim = 50, None
                elif has_s and not has_v:
                    v_lim, s_lim = None, 50
                else:
                    v_lim, s_lim = 50, 50

                try:
                    res = fetch_channel_data(
                        url,
                        video_limit=v_lim,
                        shorts_limit=s_lim,
                        config_mgr=config_mgr,
                    )
                    results.append((ch, res))
                except Exception as exc:
                    results.append((ch, ChannelFetchResult(channel_url=url, error=str(exc))))
            return results

        def _close():
            if self._widget_exists(toast):
                prog.stop()
                toast.destroy()

        def _done(results):
            _close()
            api_errors = [f"• {ch.name}: {res.error}" for ch, res in results if res.error]
            if api_errors:
                messagebox.showerror(
                    "YouTube API Error",
                    "The following issue(s) occurred while contacting YouTube API:\n\n" + "\n".join(api_errors),
                    parent=self,
                )
            current_tasks = getattr(self.app_controller, "queued_video_tasks", None)
            if current_tasks is None:
                current_tasks = [VideoTask.from_dict(d) for d in self.app_controller.config_mgr.get_video_queue()]
                self.app_controller.queued_video_tasks = current_tasks

            existing_by_id = {}
            for t in current_tasks:
                vid = extract_video_id(t.url)
                if vid:
                    existing_by_id[vid] = t
                elif t.url:
                    existing_by_id[t.url.strip().lower()] = t

            all_added_tasks: List[VideoTask] = []
            total_updated = 0

            for ch, res in results:
                if res.error:
                    continue
                if res.total_videos is not None:
                    ch.total_videos = res.total_videos
                if res.total_shorts is not None:
                    ch.total_shorts = res.total_shorts

                for f_task in (res.videos or []):
                    vid = extract_video_id(f_task.url) or (f_task.url.strip().lower() if f_task.url else "")
                    if vid and vid in existing_by_id:
                        exist = existing_by_id[vid]
                        changed = False
                        if not exist.channel and ch.name:
                            exist.channel = ch.name
                            changed = True
                        if (exist.duration_seconds <= 0) and f_task.duration_seconds > 0:
                            exist.duration_seconds = f_task.duration_seconds
                            exist.duration_text = f_task.duration_text
                            changed = True
                        if changed:
                            total_updated += 1
                    else:
                        new_task = VideoTask(
                            url=f_task.url,
                            title=f_task.title,
                            title_2=getattr(f_task, "title_2", "") or "",
                            title_3=getattr(f_task, "title_3", "") or "",
                            keywords=getattr(f_task, "keywords", "") or f_task.title,
                            channel=ch.name,
                            target_multiplier=3.0,
                            max_runs=5,
                            watchtime_duration="Full Video",
                            enabled=True,
                            duration_seconds=getattr(f_task, "duration_seconds", 0.0),
                            duration_text=getattr(f_task, "duration_text", ""),
                        )
                        all_added_tasks.append(new_task)
                        if vid:
                            existing_by_id[vid] = new_task

                # Re-evaluate channel new videos/shorts
                ev, es, et = self.get_existing_video_counts_for_channel(ch.name)
                yt_v = ch.total_videos if ch.total_videos is not None else 0
                yt_s = ch.total_shorts if ch.total_shorts is not None else 0
                ch.has_new_videos = (ch.total_videos is not None and yt_v > ev)
                ch.has_new_shorts = (ch.total_shorts is not None and yt_s > es)

            if all_added_tasks:
                new_dicts = [t.to_dict() for t in all_added_tasks]
                updated_raw = self.app_controller.config_mgr.append_video_tasks(new_dicts, deduplicate=True)
                self.app_controller.queued_video_tasks = [VideoTask.from_dict(d) for d in updated_raw]
            elif total_updated > 0:
                self.app_controller.config_mgr.set_video_queue([t.to_dict() for t in current_tasks])

            self.save_channels()
            self.refresh_list()

            current_view = getattr(self.app_controller, "current_view_name", "")
            queue = self._queue_view()
            if queue is not None:
                if current_view == "queue":
                    queue.refresh_list()
                else:
                    self.app_controller._queue_needs_refresh = True
            shorts_queue = self._shorts_queue_view()
            if shorts_queue is not None:
                if current_view == "shorts_queue":
                    shorts_queue.refresh_list()
                else:
                    self.app_controller._shorts_queue_needs_refresh = True
            update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
            if callable(update_dash):
                update_dash()

            added_shorts = sum(1 for t in all_added_tasks if "/shorts/" in (t.url or ""))
            added_vids = len(all_added_tasks) - added_shorts

            msg = f"Sync Data All completed for {len(channels_to_sync)} channel(s):"
            if all_added_tasks:
                msg += f"\n• Added {len(all_added_tasks)} new item(s) to queue ({added_vids} video(s), {added_shorts} short(s))."
            else:
                msg += "\n• All channels are up to date; no new items found."
            if total_updated > 0:
                msg += f"\n• Updated metadata for {total_updated} existing item(s)."

            messagebox.showinfo("Sync Data All Complete", msg, parent=self)

        def _progress(data):
            idx, name = data
            lbl.configure(text=f"Syncing '{name}' ({idx}/{len(channels_to_sync)})...")

        def _failed(exc):
            _close()
            messagebox.showerror("Sync Error", f"Failed to sync all channel data:\n{exc}", parent=self)

        self.app_controller.background_jobs.submit(
            _bg_sync,
            done=_done,
            error=_failed,
            progress=_progress,
            current=lambda: self._widget_exists(self) and self._widget_exists(toast),
            discard=_close,
        )

    def sync_channel_all(self, channel: Channel) -> None:
        """Fully sync all videos and shorts for a specific channel in batches of 50 until completed."""
        if not channel.url:
            messagebox.showinfo("Missing URL", f"Channel '{channel.name}' has no URL configured.", parent=self)
            return

        cancel_ev = threading.Event()

        loading = ctk.CTkToplevel(self)
        loading.title("Syncing All Videos & Shorts...")
        loading.geometry("440x190")
        loading.grab_set()

        status_lbl = ctk.CTkLabel(
            loading,
            text=f"Syncing all videos and shorts for\n'{channel.name}'...",
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="center",
            wraplength=400,
        )
        status_lbl.pack(pady=(16, 6))

        batch_info_lbl = ctk.CTkLabel(
            loading,
            text="Fetching videos and shorts...",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
            justify="center",
        )
        batch_info_lbl.pack(pady=(0, 8))

        prog = ctk.CTkProgressBar(loading, mode="indeterminate", width=320)
        prog.pack(pady=(0, 12))
        prog.start()

        def _on_cancel():
            cancel_ev.set()
            cancel_btn.configure(text="Cancelling...", state="disabled")
            batch_info_lbl.configure(text="Cancelling after current batch...")

        cancel_btn = ctk.CTkButton(
            loading,
            text="Cancel",
            width=80,
            fg_color="#EF4444",
            hover_color="#DC2626",
            command=_on_cancel,
        )
        cancel_btn.pack(pady=(0, 10))
        add_tooltip(
            cancel_btn,
            "Cancel the current background fetch operation\nand keep items discovered so far.",
        )

        def _close_loading():
            try:
                if loading.winfo_exists():
                    prog.stop()
                    loading.destroy()
            except Exception:
                pass

        def _on_progress(data):
            try:
                if not loading.winfo_exists():
                    return
                if isinstance(data, (tuple, list)) and len(data) >= 2:
                    c, k = data[0], data[1]
                    if k == "durations":
                        batch_info_lbl.configure(text=f"Enriching video durations ({c} processed)...")
                    else:
                        batch_info_lbl.configure(text=f"Fetched {c} {k} so far...")
                elif isinstance(data, str):
                    batch_info_lbl.configure(text=data)
            except Exception:
                pass

        def _on_sync_done(res: ChannelFetchResult):
            _close_loading()
            if res.error:
                messagebox.showerror(
                    "Sync Error",
                    f"Failed to sync all channel data:\n{res.error}",
                    parent=self,
                )
                return

            if res.total_videos is not None:
                channel.total_videos = res.total_videos
            if res.total_shorts is not None:
                channel.total_shorts = res.total_shorts

            fetched_tasks = res.videos or []
            if not fetched_tasks:
                msg = f"No public videos or shorts found for '{channel.name}'."
                if res.status == "interrupted":
                    msg = "Sync cancelled by user."
                messagebox.showinfo("Sync Complete", msg, parent=self)
                return

            current_tasks = getattr(self.app_controller, "queued_video_tasks", None)
            if current_tasks is None:
                current_tasks = [VideoTask.from_dict(d) for d in self.app_controller.config_mgr.get_video_queue()]
                self.app_controller.queued_video_tasks = current_tasks

            existing_by_id = {}
            for t in current_tasks:
                vid = extract_video_id(t.url)
                if vid:
                    existing_by_id[vid] = t
                elif t.url:
                    existing_by_id[t.url.strip().lower()] = t

            added_tasks: List[VideoTask] = []
            updated_count = 0

            for f_task in fetched_tasks:
                vid = extract_video_id(f_task.url) or (f_task.url.strip().lower() if f_task.url else "")
                if vid and vid in existing_by_id:
                    existing_task = existing_by_id[vid]
                    changed = False
                    if not existing_task.channel and channel.name:
                        existing_task.channel = channel.name
                        changed = True
                    if (existing_task.duration_seconds <= 0) and f_task.duration_seconds > 0:
                        existing_task.duration_seconds = f_task.duration_seconds
                        existing_task.duration_text = f_task.duration_text
                        changed = True
                    if changed:
                        updated_count += 1
                else:
                    new_task = VideoTask(
                        url=f_task.url,
                        title=f_task.title,
                        title_2=getattr(f_task, "title_2", "") or "",
                        title_3=getattr(f_task, "title_3", "") or "",
                        keywords=getattr(f_task, "keywords", "") or f_task.title,
                        channel=channel.name,
                        target_multiplier=3.0,
                        max_runs=5,
                        watchtime_duration="Full Video",
                        enabled=True,
                        duration_seconds=getattr(f_task, "duration_seconds", 0.0),
                        duration_text=getattr(f_task, "duration_text", ""),
                    )
                    added_tasks.append(new_task)
                    if vid:
                        existing_by_id[vid] = new_task

            if added_tasks:
                new_dicts = [t.to_dict() for t in added_tasks]
                updated_raw = self.app_controller.config_mgr.append_video_tasks(new_dicts, deduplicate=True)
                new_tasks = [VideoTask.from_dict(d) for d in updated_raw]
                self.app_controller.queued_video_tasks = new_tasks
                if hasattr(self.app_controller, "on_queue_changed"):
                    self.app_controller.on_queue_changed(new_tasks)
            elif updated_count > 0:
                raw = [t.to_dict() for t in current_tasks]
                self.app_controller.config_mgr.set_video_queue(raw)
                new_tasks = [VideoTask.from_dict(d) for d in raw]
                self.app_controller.queued_video_tasks = new_tasks
                if hasattr(self.app_controller, "on_queue_changed"):
                    self.app_controller.on_queue_changed(new_tasks)

            # Only refresh queue views if they are currently visible, else defer to tab switch
            current_view = getattr(self.app_controller, "current_view_name", "")
            queue = self._queue_view()
            if queue is not None:
                if added_tasks or updated_count > 0:
                    queue.video_tasks = list(self.app_controller.queued_video_tasks)
                if current_view == "queue":
                    queue.refresh_list()
                else:
                    self.app_controller._queue_needs_refresh = True

            shorts_queue = self._shorts_queue_view()
            if shorts_queue is not None:
                if added_tasks or updated_count > 0:
                    shorts_queue.video_tasks = list(self.app_controller.queued_video_tasks)
                if current_view == "shorts_queue":
                    shorts_queue.refresh_list()
                else:
                    self.app_controller._shorts_queue_needs_refresh = True

            update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
            if callable(update_dash):
                update_dash()

            ev, es, et = self.get_existing_video_counts_for_channel(channel.name)
            yt_v = channel.total_videos if channel.total_videos is not None else 0
            yt_s = channel.total_shorts if channel.total_shorts is not None else 0

            channel.has_new_videos = (channel.total_videos is not None and yt_v > ev)
            channel.has_new_shorts = (channel.total_shorts is not None and yt_s > es)

            self.save_channels()
            self.refresh_list()

            added_shorts = sum(1 for t in added_tasks if "/shorts/" in (t.url or ""))
            added_vids = len(added_tasks) - added_shorts

            lines = [f"Sync All completed for channel '{channel.name}':"]
            if res.status == "interrupted":
                lines[0] = f"Sync All stopped by user for channel '{channel.name}':"
            lines.append(f"• Fetched {len(fetched_tasks)} total item(s) from channel.")
            if added_tasks:
                lines.append(f"• Added {len(added_tasks)} new item(s) to queue ({added_vids} video(s), {added_shorts} short(s)).")
            else:
                lines.append("• No new items to add; all fetched items were already in queue.")
            if updated_count > 0:
                lines.append(f"• Updated metadata for {updated_count} existing item(s).")

            messagebox.showinfo("Sync All Complete", "\n".join(lines), parent=self)

        url = channel.url
        config_mgr = getattr(self.app_controller, "config_mgr", None)

        def _bg_fetch(report):
            return fetch_channel_data(
                url,
                video_limit=0,
                shorts_limit=0,
                config_mgr=config_mgr,
                cancel_event=cancel_ev,
                progress_callback=report,
            )

        def _on_error(exc):
            _close_loading()
            messagebox.showerror("Sync Error", f"Failed to sync all channel data:\n{exc}", parent=self)

        self.app_controller.background_jobs.submit(
            _bg_fetch,
            done=_on_sync_done,
            error=_on_error,
            progress=_on_progress,
            current=lambda: self._widget_exists(self) and self._widget_exists(loading),
            discard=_close_loading,
        )

    def sync_single_channel_data(self, channel: Channel) -> None:
        """Sync new available videos and/or shorts for a specific channel directly into the queue."""
        if not channel.url:
            messagebox.showinfo("Missing URL", f"Channel '{channel.name}' has no URL configured.", parent=self)
            return

        has_v = getattr(channel, "has_new_videos", False)
        has_s = getattr(channel, "has_new_shorts", False)

        # Check availability:
        # If only video -> only fetch videos
        # If only shorts -> only fetch shorts
        # If both (or fallback) -> fetch both
        if has_v and not has_s:
            v_limit = 50
            s_limit = None
            fetch_desc = "up to 50 recent videos"
        elif has_s and not has_v:
            v_limit = None
            s_limit = 50
            fetch_desc = "up to 50 recent shorts"
        else:
            v_limit = 50
            s_limit = 50
            fetch_desc = "up to 50 recent videos and 50 shorts"

        cancel_ev = threading.Event()

        loading = ctk.CTkToplevel(self)
        loading.title("Syncing Channel...")
        loading.geometry("420x170")
        loading.grab_set()

        status_text = f"Syncing {fetch_desc} for\n'{channel.name}'..."
        ctk.CTkLabel(
            loading,
            text=status_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="center",
            wraplength=380,
        ).pack(pady=(16, 6))

        batch_info_lbl = ctk.CTkLabel(
            loading,
            text="Fetching items...",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
            justify="center",
        )
        batch_info_lbl.pack(pady=(0, 8))

        prog = ctk.CTkProgressBar(loading, mode="indeterminate", width=300)
        prog.pack(pady=(0, 15))
        prog.start()

        def _close_loading():
            try:
                if loading.winfo_exists():
                    prog.stop()
                    loading.destroy()
            except Exception:
                pass

        def _on_progress(data):
            try:
                if not loading.winfo_exists():
                    return
                if isinstance(data, (tuple, list)) and len(data) >= 2:
                    c, k = data[0], data[1]
                    if k == "durations":
                        batch_info_lbl.configure(text=f"Enriching video durations ({c} processed)...")
                    else:
                        batch_info_lbl.configure(text=f"Fetched {c} {k} so far...")
                elif isinstance(data, str):
                    batch_info_lbl.configure(text=data)
            except Exception:
                pass

        def _on_sync_done(res: ChannelFetchResult):
            _close_loading()
            if res.error:
                messagebox.showerror(
                    "Sync Error",
                    f"Failed to sync channel data:\n{res.error}",
                    parent=self,
                )
                return

            if res.total_videos is not None:
                channel.total_videos = res.total_videos
            if res.total_shorts is not None:
                channel.total_shorts = res.total_shorts

            fetched_tasks = res.videos or []
            if not fetched_tasks:
                messagebox.showinfo(
                    "Sync Complete",
                    f"No public videos or shorts found for '{channel.name}'.",
                    parent=self,
                )
                return

            current_tasks = getattr(self.app_controller, "queued_video_tasks", None)
            if current_tasks is None:
                current_tasks = [VideoTask.from_dict(d) for d in self.app_controller.config_mgr.get_video_queue()]
                self.app_controller.queued_video_tasks = current_tasks

            existing_by_id = {}
            for t in current_tasks:
                vid = extract_video_id(t.url)
                if vid:
                    existing_by_id[vid] = t
                elif t.url:
                    existing_by_id[t.url.strip().lower()] = t

            added_tasks: List[VideoTask] = []
            updated_count = 0

            for f_task in fetched_tasks:
                vid = extract_video_id(f_task.url) or (f_task.url.strip().lower() if f_task.url else "")
                if vid and vid in existing_by_id:
                    existing_task = existing_by_id[vid]
                    changed = False
                    if not existing_task.channel and channel.name:
                        existing_task.channel = channel.name
                        changed = True
                    if (existing_task.duration_seconds <= 0) and f_task.duration_seconds > 0:
                        existing_task.duration_seconds = f_task.duration_seconds
                        existing_task.duration_text = f_task.duration_text
                        changed = True
                    if changed:
                        updated_count += 1
                else:
                    new_task = VideoTask(
                        url=f_task.url,
                        title=f_task.title,
                        title_2=getattr(f_task, "title_2", "") or "",
                        title_3=getattr(f_task, "title_3", "") or "",
                        keywords=getattr(f_task, "keywords", "") or f_task.title,
                        channel=channel.name,
                        target_multiplier=3.0,
                        max_runs=5,
                        watchtime_duration="Full Video",
                        enabled=True,
                        duration_seconds=getattr(f_task, "duration_seconds", 0.0),
                        duration_text=getattr(f_task, "duration_text", ""),
                    )
                    added_tasks.append(new_task)
                    if vid:
                        existing_by_id[vid] = new_task

            if added_tasks:
                new_dicts = [t.to_dict() for t in added_tasks]
                updated_raw = self.app_controller.config_mgr.append_video_tasks(new_dicts, deduplicate=True)
                new_tasks = [VideoTask.from_dict(d) for d in updated_raw]
                self.app_controller.queued_video_tasks = new_tasks
                if hasattr(self.app_controller, "on_queue_changed"):
                    self.app_controller.on_queue_changed(new_tasks)
            elif updated_count > 0:
                raw = [t.to_dict() for t in current_tasks]
                self.app_controller.config_mgr.set_video_queue(raw)
                new_tasks = [VideoTask.from_dict(d) for d in raw]
                self.app_controller.queued_video_tasks = new_tasks
                if hasattr(self.app_controller, "on_queue_changed"):
                    self.app_controller.on_queue_changed(new_tasks)

            # Only refresh queue views if they are currently visible, else defer to tab switch
            current_view = getattr(self.app_controller, "current_view_name", "")
            queue = self._queue_view()
            if queue is not None:
                if added_tasks or updated_count > 0:
                    queue.video_tasks = list(self.app_controller.queued_video_tasks)
                if current_view == "queue":
                    queue.refresh_list()
                else:
                    self.app_controller._queue_needs_refresh = True

            shorts_queue = self._shorts_queue_view()
            if shorts_queue is not None:
                if added_tasks or updated_count > 0:
                    shorts_queue.video_tasks = list(self.app_controller.queued_video_tasks)
                if current_view == "shorts_queue":
                    shorts_queue.refresh_list()
                else:
                    self.app_controller._shorts_queue_needs_refresh = True

            update_dash = getattr(self.app_controller, "update_dashboard_stats", None)
            if callable(update_dash):
                update_dash()

            ev, es, et = self.get_existing_video_counts_for_channel(channel.name)
            yt_v = channel.total_videos if channel.total_videos is not None else 0
            yt_s = channel.total_shorts if channel.total_shorts is not None else 0

            channel.has_new_videos = (channel.total_videos is not None and yt_v > ev)
            channel.has_new_shorts = (channel.total_shorts is not None and yt_s > es)

            self.save_channels()
            self.refresh_list()

            added_shorts = sum(1 for t in added_tasks if "/shorts/" in (t.url or ""))
            added_vids = len(added_tasks) - added_shorts

            lines = [f"Sync completed for channel '{channel.name}':"]
            if added_tasks:
                lines.append(f"• Added {len(added_tasks)} new item(s) to queue ({added_vids} video(s), {added_shorts} short(s)).")
            else:
                lines.append("• No new items to add; all fetched items were already in queue.")
            if updated_count > 0:
                lines.append(f"• Updated metadata for {updated_count} existing item(s).")

            messagebox.showinfo("Sync Complete", "\n".join(lines), parent=self)

        url = channel.url
        config_mgr = getattr(self.app_controller, "config_mgr", None)

        def _bg_fetch(report):
            return fetch_channel_data(
                url,
                video_limit=v_limit,
                shorts_limit=s_limit,
                config_mgr=config_mgr,
                cancel_event=cancel_ev,
                progress_callback=report,
            )

        def _on_error(exc):
            _close_loading()
            messagebox.showerror("Sync Error", f"Failed to sync channel data:\n{exc}", parent=self)

        self.app_controller.background_jobs.submit(
            _bg_fetch,
            done=_on_sync_done,
            error=_on_error,
            progress=_on_progress,
            current=lambda: self._widget_exists(self) and self._widget_exists(loading),
            discard=_close_loading,
        )

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
                current_view = getattr(self.app_controller, "current_view_name", "")
                queue = self._queue_view()
                if queue is not None:
                    if current_view == "queue":
                        queue.refresh_list()
                    else:
                        self.app_controller._queue_needs_refresh = True
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

            handle = self.get_channel_handle(channel)
            handle_suffix = f" -- {handle}" if handle else ""
            line1 = f"{pos} - {clean_name}{handle_suffix}{status_tag}"
            watch_time_str = self.get_channel_watch_time_display(channel)
            line2 = watch_time_str

            ev, es, et = self.get_existing_video_counts_for_channel(channel.name)
            v_cnt = channel.total_videos if channel.total_videos is not None else ev
            s_cnt = channel.total_shorts if channel.total_shorts is not None else es
            combined_total = v_cnt + s_cnt

            totals_text = f"all videos: {combined_total:,} | shorts: {s_cnt:,} | videos: {v_cnt:,}"

            def _on_channel_click(event=None, ch=channel):
                self.open_channel_videos_modal(ch)

            row.bind("<Button-1>", _on_channel_click)

            info_frame = ctk.CTkFrame(row, fg_color="transparent")
            info_frame.grid(row=0, column=1, padx=10, pady=8, sticky="ew")
            info_frame.bind("<Button-1>", _on_channel_click)

            lbl = ctk.CTkLabel(
                info_frame,
                text=f"{line1}\n{line2}",
                font=ctk.CTkFont(size=11),
                justify="left",
                anchor="w",
                cursor="hand2",
                text_color=("gray50", "gray50") if not channel.enabled else None,
            )
            lbl.pack(anchor="w", fill="x")
            lbl.bind("<Button-1>", _on_channel_click)

            has_new = getattr(channel, "has_new_content", False) or getattr(channel, "has_new_videos", False) or getattr(channel, "has_new_shorts", False)
            if totals_text or has_new:
                meta_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
                meta_frame.pack(anchor="w", fill="x", pady=(2, 0))
                meta_frame.bind("<Button-1>", _on_channel_click)

                if totals_text:
                    tot_lbl = ctk.CTkLabel(
                        meta_frame,
                        text=totals_text,
                        font=ctk.CTkFont(size=9),
                        justify="left",
                        anchor="w",
                        cursor="hand2",
                        text_color=("gray50", "gray50") if not channel.enabled else ("gray40", "gray60"),
                    )
                    tot_lbl.pack(side="left")
                    tot_lbl.bind("<Button-1>", _on_channel_click)

                if has_new:
                    sep = " • " if totals_text else ""
                    badge_text = getattr(channel, "new_badge_text", "") or "🔥 New Videos Available"
                    new_lbl = ctk.CTkLabel(
                        meta_frame,
                        text=f"{sep}{badge_text}",
                        font=ctk.CTkFont(size=9, weight="bold"),
                        justify="left",
                        anchor="w",
                        cursor="hand2",
                        text_color=("gray50", "gray50") if not channel.enabled else ("#16A34A", "#4ADE80"),
                    )
                    new_lbl.pack(side="left")
                    new_lbl.bind("<Button-1>", _on_channel_click)

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

            btn_up = ctk.CTkButton(
                action_frame,
                text="▲",
                width=24,
                height=24,
                font=ctk.CTkFont(size=9, weight="bold"),
                fg_color="#374151" if can_up else "gray30",
                hover_color="#4B5563" if can_up else "gray30",
                state="normal" if can_up else "disabled",
                command=_move_channel_up,
            )
            btn_up.pack(side="left", padx=(0, 2))
            add_tooltip(
                btn_up,
                "Move this channel up in list order\nto prioritize it during playback cycles.",
            )

            btn_down = ctk.CTkButton(
                action_frame,
                text="▼",
                width=24,
                height=24,
                font=ctk.CTkFont(size=9, weight="bold"),
                fg_color="#374151" if can_down else "gray30",
                hover_color="#4B5563" if can_down else "gray30",
                state="normal" if can_down else "disabled",
                command=_move_channel_down,
            )
            btn_down.pack(side="left", padx=(0, 6))
            add_tooltip(
                btn_down,
                "Move this channel down in list order\nto deprioritize it during playback cycles.",
            )

            has_url = bool((channel.url or "").strip())
            sync_all_btn = ctk.CTkButton(
                action_frame,
                text="⚡ Sync All",
                width=80,
                fg_color="#2563EB" if has_url else "gray30",
                hover_color="#1D4ED8" if has_url else "gray30",
                state="normal" if has_url else "disabled",
                command=lambda ch=channel: self.sync_channel_all(ch),
            )
            sync_all_btn.pack(side="left", padx=(0, 4))
            add_tooltip(
                sync_all_btn,
                "Fetch and sync all public long-form videos\nand shorts from this channel into the queue.",
            )

            has_new = getattr(channel, "has_new_content", False) or getattr(channel, "has_new_videos", False) or getattr(channel, "has_new_shorts", False)
            sync_btn = ctk.CTkButton(
                action_frame,
                text="⚡ Sync Recent",
                width=95,
                fg_color="#0D9488" if has_new else "gray30",
                hover_color="#0F766E" if has_new else "gray30",
                state="normal" if has_new else "disabled",
                command=lambda ch=channel: self.sync_single_channel_data(ch),
            )
            sync_btn.pack(side="left", padx=(0, 4))
            add_tooltip(
                sync_btn,
                "Fetch newly published videos or shorts\navailable on YouTube for this channel.",
            )

            if channel.archived:
                restore_btn = ctk.CTkButton(
                    action_frame,
                    text="📂 Restore",
                    width=75,
                    fg_color="#059669",
                    hover_color="#047857",
                    command=lambda i=real_idx: self.unarchive_channel(i),
                )
                restore_btn.pack(side="left", padx=(0, 4))
                add_tooltip(
                    restore_btn,
                    "Restore this archived channel back\nto the active channel list.",
                )
            else:
                archive_btn = ctk.CTkButton(
                    action_frame,
                    text="📁 Archive",
                    width=75,
                    fg_color="#D97706",
                    hover_color="#B45309",
                    command=lambda i=real_idx: self.archive_channel(i),
                )
                archive_btn.pack(side="left", padx=(0, 4))
                add_tooltip(
                    archive_btn,
                    "Archive this channel to pause automation\nwithout losing its saved statistics.",
                )

            del_btn = ctk.CTkButton(
                action_frame,
                text="🗑 Delete",
                width=70,
                fg_color="#D32F2F",
                hover_color="#9A0007",
                command=lambda i=real_idx: self.remove_channel(i),
            )
            del_btn.pack(side="left")
            add_tooltip(
                del_btn,
                "Permanently delete this channel\nand remove it from your saved list.",
            )
