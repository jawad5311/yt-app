"""Selection Preview Modal for importing videos fetched from a YouTube channel."""

from tkinter import messagebox
from typing import Callable, Dict, List, Optional, Set

from .. import ttk_adapter as ctk
from ..components import Toplevel

from ...models.video_task import (
    DEFAULT_MAX_RUNS,
    DEFAULT_SHORTS_MAX_RUNS,
    DEFAULT_TARGET_MULTIPLIER,
    DEFAULT_WATCHTIME_DURATION,
    WATCHTIME_OPTIONS,
    VideoTask,
)
from ...utils.channel_fetcher import ChannelFetchResult, FetchedVideo
from ...utils.helpers import clean_youtube_url
from ..tooltip import add_tooltip


class ChannelImportModal(Toplevel):
    """Modal dialog allowing users to review and selectively import fetched channel videos."""

    def __init__(
        self,
        parent,
        channel_name: str,
        channel_url: str,
        fetched_videos: List[FetchedVideo],
        existing_video_ids: Set[str],
        on_import: Callable[[List[VideoTask]], None],
        fetch_result: Optional[ChannelFetchResult] = None,
    ):
        super().__init__(parent)
        self.channel_name = channel_name
        self.channel_url = channel_url
        self.fetched_videos = fetched_videos
        self.existing_video_ids = existing_video_ids
        self.on_import = on_import
        self.fetch_result = fetch_result

        self.title(f"Import Channel Videos — {channel_name}")
        self.geometry("780x620")
        self.minsize(650, 480)
        self.grab_set()

        # Selection state: map video_id -> BooleanVar
        self.selected_vars: Dict[str, ctk.BooleanVar] = {}

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. Header Frame
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(15, 8), sticky="ew")

        ctk.CTkLabel(
            header,
            text=f"📺 {self.channel_name}",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w")

        v_count = sum(1 for v in self.fetched_videos if not (getattr(v, "is_short", False) or "/shorts/" in (v.url or "")))
        s_count = len(self.fetched_videos) - v_count
        new_count = sum(1 for v in self.fetched_videos if v.video_id not in self.existing_video_ids)
        existing_count = len(self.fetched_videos) - new_count
        status_text = (
            f"Found {len(self.fetched_videos)} items ({v_count} videos, {s_count} shorts) · "
            f"{new_count} new, {existing_count} already in queue"
        )
        if self.fetch_result:
            if self.fetch_result.status == "initial_page_only":
                status_text += " (Initial page only)"
            elif self.fetch_result.status == "partial":
                status_text += " (Partial result)"

        ctk.CTkLabel(
            header,
            text=status_text,
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", pady=(2, 0))

        if self.fetch_result and self.fetch_result.warning:
            ctk.CTkLabel(
                header,
                text=f"ℹ️ {self.fetch_result.warning}",
                font=ctk.CTkFont(size=11),
                text_color=("#B7791F", "#F6E05E"),
                wraplength=700,
                justify="left",
            ).pack(anchor="w", pady=(3, 0))

        # 2. Action Toolbar (Filters & Bulk Selection)
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, padx=20, pady=(4, 8), sticky="ew")

        btn_select_all = ctk.CTkButton(
            toolbar,
            text="Select All",
            width=90,
            height=28,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self._select_all,
        )
        btn_select_all.pack(side="left", padx=(0, 6))
        add_tooltip(
            btn_select_all,
            "Select all discovered videos and shorts in the list.",
        )

        btn_deselect_all = ctk.CTkButton(
            toolbar,
            text="Deselect All",
            width=90,
            height=28,
            fg_color="#555555",
            hover_color="#333333",
            command=self._deselect_all,
        )
        btn_deselect_all.pack(side="left", padx=(0, 6))
        add_tooltip(
            btn_deselect_all,
            "Deselect all items currently checked in the list.",
        )

        btn_select_new = ctk.CTkButton(
            toolbar,
            text="Select New Only",
            width=110,
            height=28,
            fg_color="#2FA572",
            hover_color="#1E7B52",
            command=self._select_new_only,
        )
        btn_select_new.pack(side="left", padx=(0, 6))
        add_tooltip(
            btn_select_new,
            "Select only items that are not already present in your queue.",
        )

        btn_videos_only = ctk.CTkButton(
            toolbar,
            text="Videos Only",
            width=90,
            height=28,
            fg_color="#3B82F6",
            hover_color="#2563EB",
            command=self._select_videos_only,
        )
        btn_videos_only.pack(side="left", padx=(0, 6))
        add_tooltip(
            btn_videos_only,
            "Select all regular long-form videos and deselect shorts.",
        )

        btn_shorts_only = ctk.CTkButton(
            toolbar,
            text="Shorts Only",
            width=90,
            height=28,
            fg_color="#EC4899",
            hover_color="#DB2777",
            command=self._select_shorts_only,
        )
        btn_shorts_only.pack(side="left")
        add_tooltip(
            btn_shorts_only,
            "Select all YouTube Shorts and deselect long-form videos.",
        )

        # 3. Scrollable List of Videos
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Discovered Videos")
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self._populate_video_rows()

        # 4. Footer Frame
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(8, 15), sticky="ew")

        left_footer = ctk.CTkFrame(footer, fg_color="transparent")
        left_footer.pack(side="left")

        self.selected_label = ctk.CTkLabel(
            left_footer,
            text="Selected: 0",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.selected_label.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(
            left_footer,
            text="Watch Duration:",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 5))

        self.duration_option = ctk.CTkOptionMenu(
            left_footer,
            values=WATCHTIME_OPTIONS,
            width=140,
        )
        # Default to "Full Video" per requirement
        self.duration_option.set("Full Video" if "Full Video" in WATCHTIME_OPTIONS else DEFAULT_WATCHTIME_DURATION)
        self.duration_option.pack(side="left")

        right_footer = ctk.CTkFrame(footer, fg_color="transparent")
        right_footer.pack(side="right")

        cancel_btn = ctk.CTkButton(
            right_footer,
            text="Cancel",
            width=90,
            fg_color="#555555",
            hover_color="#333333",
            command=self.destroy,
        )
        cancel_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            cancel_btn,
            "Close this modal without importing any items.",
        )

        self.import_button = ctk.CTkButton(
            right_footer,
            text="Import Selected Videos",
            fg_color="#2FA572",
            hover_color="#1E7B52",
            command=self._submit_import,
        )
        self.import_button.pack(side="left")
        add_tooltip(
            self.import_button,
            "Import the selected videos and shorts into your playback queue.",
        )

        # Initial selection count update
        self._update_selection_counter()

    def _populate_video_rows(self):
        for idx, video in enumerate(self.fetched_videos):
            is_already_in_queue = video.video_id in self.existing_video_ids
            # Default: Select only new videos
            var = ctk.BooleanVar(value=not is_already_in_queue)
            self.selected_vars[video.video_id] = var

            row = ctk.CTkFrame(
                self.scroll_frame,
                fg_color=("gray85", "gray20") if idx % 2 == 0 else ("gray90", "gray17"),
                corner_radius=6,
            )
            row.pack(fill="x", padx=4, pady=3)
            row.grid_columnconfigure(1, weight=1)

            cb = ctk.CTkCheckBox(
                row,
                text="",
                variable=var,
                width=24,
                checkbox_width=20,
                checkbox_height=20,
                command=self._update_selection_counter,
            )
            cb.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=8, sticky="w")

            # Title
            title_label = ctk.CTkLabel(
                row,
                text=video.title,
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w",
                justify="left",
            )
            title_label.grid(row=0, column=1, padx=(0, 10), pady=(6, 0), sticky="ew")

            # Sub-info row (duration, ID, badge)
            meta_frame = ctk.CTkFrame(row, fg_color="transparent")
            meta_frame.grid(row=1, column=1, padx=(0, 10), pady=(0, 6), sticky="w")

            dur_display = f"⏱ {video.duration_text}" if video.duration_text else "⏱ Unknown"
            ctk.CTkLabel(
                meta_frame,
                text=dur_display,
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray60"),
            ).pack(side="left", padx=(0, 12))

            ctk.CTkLabel(
                meta_frame,
                text=f"ID: {video.video_id}",
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray60"),
            ).pack(side="left", padx=(0, 12))

            if is_already_in_queue:
                status_badge = ctk.CTkLabel(
                    meta_frame,
                    text="ALREADY IN QUEUE",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#F57C00",
                )
            else:
                status_badge = ctk.CTkLabel(
                    meta_frame,
                    text="NEW",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#2E7D32",
                )
            status_badge.pack(side="left")

            is_short = getattr(video, "is_short", False) or ("/shorts/" in (video.url or ""))
            if is_short:
                type_badge = ctk.CTkLabel(
                    meta_frame,
                    text="⚡ SHORT",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#EC4899",
                )
            else:
                type_badge = ctk.CTkLabel(
                    meta_frame,
                    text="🎬 VIDEO",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#3B82F6",
                )
            type_badge.pack(side="left", padx=(10, 0))

    def _select_all(self):
        for var in self.selected_vars.values():
            var.set(True)
        self._update_selection_counter()

    def _deselect_all(self):
        for var in self.selected_vars.values():
            var.set(False)
        self._update_selection_counter()

    def _select_new_only(self):
        for video in self.fetched_videos:
            var = self.selected_vars.get(video.video_id)
            if var is not None:
                var.set(video.video_id not in self.existing_video_ids)
        self._update_selection_counter()

    def _select_videos_only(self):
        for video in self.fetched_videos:
            var = self.selected_vars.get(video.video_id)
            if var is not None:
                is_short = getattr(video, "is_short", False) or ("/shorts/" in (video.url or ""))
                var.set(not is_short)
        self._update_selection_counter()

    def _select_shorts_only(self):
        for video in self.fetched_videos:
            var = self.selected_vars.get(video.video_id)
            if var is not None:
                is_short = getattr(video, "is_short", False) or ("/shorts/" in (video.url or ""))
                var.set(is_short)
        self._update_selection_counter()

    def _update_selection_counter(self):
        selected_count = sum(1 for var in self.selected_vars.values() if var.get())
        total_count = len(self.fetched_videos)
        self.selected_label.configure(text=f"Selected: {selected_count} of {total_count}")
        self.import_button.configure(
            text=f"Import ({selected_count}) Videos",
            state="normal" if selected_count > 0 else "disabled",
        )

    def _submit_import(self):
        selected_ids = {vid for vid, var in self.selected_vars.items() if var.get()}
        chosen_duration = self.duration_option.get()

        tasks_to_add: List[VideoTask] = []
        for video in self.fetched_videos:
            if video.video_id in selected_ids:
                is_s = getattr(video, "is_short", False) or ("/shorts/" in (video.url or ""))
                task_url = clean_youtube_url(video.url, as_short=is_s) if is_s else video.url
                task = VideoTask(
                    url=task_url,
                    title=video.title,
                    keywords=video.title,
                    channel=self.channel_name,
                    watchtime_duration=chosen_duration,
                    duration_seconds=video.duration_seconds,
                    duration_text=video.duration_text,
                    enabled=True,
                    target_multiplier=0.0 if is_s else DEFAULT_TARGET_MULTIPLIER,
                    max_runs=DEFAULT_SHORTS_MAX_RUNS if is_s else DEFAULT_MAX_RUNS,
                )
                tasks_to_add.append(task)

        # 1. Release modal grab so UI stays responsive and grab deadlock is prevented
        try:
            self.grab_release()
        except Exception:
            pass

        # 2. Deliver imported tasks to parent callback
        if callable(self.on_import):
            try:
                self.on_import(tasks_to_add)
            except Exception as exc:
                try:
                    self.grab_set()
                    if hasattr(self, "import_button") and self.import_button:
                        self.import_button.configure(text=f"Import Selected ({len(selected_ids)})", state="normal")
                except Exception:
                    pass
                messagebox.showerror(
                    "Import Failed",
                    f"Failed to import selected videos to queue:\n{exc}",
                )
                return

        # 3. Close dialog after successful import
        self.destroy()
