import webbrowser
from datetime import datetime, timezone
import tkinter as tk
from tkinter import TclError, messagebox
from typing import Any, Callable, Dict, List, Optional

from .. import ttk_adapter as ctk
from ..components import Frame, AppleDropdownMenu

from ...models.channel import NO_CHANNEL_VALUE, Channel, VideoGroup, group_videos_by_channel
from ...models.video_task import (
    DEFAULT_MAX_RUNS,
    DEFAULT_SHORTS_MAX_RUNS,
    DEFAULT_TARGET_MULTIPLIER,
    DEFAULT_WATCHTIME_DURATION,
    WATCHTIME_OPTIONS,
    VideoTask,
)
from ...models.watch_stats import (
    format_totals_line,
    format_video_watch_ratio,
    format_watch_line,
    format_watch_multiplier,
    resolve_video_key,
)
from ...utils.helpers import clean_youtube_url
from ...utils.channel_fetcher import parse_duration_str
from ...utils.scroll_helper import setup_smooth_scrolling
from ..confirm_dialog import show_themed_confirm_dialog
from ..theme import get_font, get_theme_colors
from ..tooltip import add_tooltip, flash_button_feedback, with_button_feedback


def format_last_played(timestamp_str: Optional[str], now: Optional[datetime] = None) -> str:
    """Format an ISO timestamp into a user-friendly relative or calendar string."""
    if not timestamp_str or not str(timestamp_str).strip():
        return "Never"
    try:
        clean = str(timestamp_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        ref_now = now or (datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now())
        if dt.tzinfo is not None and ref_now.tzinfo is None:
            dt = dt.astimezone().replace(tzinfo=None)
        elif dt.tzinfo is None and ref_now.tzinfo is not None:
            ref_now = ref_now.astimezone().replace(tzinfo=None)

        diff = ref_now - dt
        seconds = diff.total_seconds()
        if seconds < 0:
            return "Just now"
        if seconds < 60:
            return "Just now"
        minutes = int(seconds // 60)
        if minutes < 60:
            return f"{minutes}m ago"
        hours = int(minutes // 60)
        if hours < 24:
            return f"{hours}h ago"
        days = int(hours // 24)
        if days < 7:
            return f"{days}d ago"
        return dt.strftime("%b %d, %Y")
    except Exception:
        return "Never"


def parse_published_datetime(pub_val: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 or common date string into a datetime object."""
    if not pub_val or not str(pub_val).strip():
        return None
    val = str(pub_val).strip()
    try:
        clean_val = val.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_val)
    except Exception:
        pass
    for fmt in (
        "%d %b %Y",
        "%d-%b-%Y",
        "%d %B %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(val[:19] if ("T" in val or " " in val) else val[:11], fmt)
        except Exception:
            pass
        try:
            return datetime.strptime(val, fmt)
        except Exception:
            pass
    return None


def format_published_date(
    pub_val: Optional[str],
    fallback: str = "",
    relative: bool = True,
    now: Optional[datetime] = None,
) -> str:
    """Format an ISO 8601 or date string into YouTube-style relative time (e.g. '1 day ago', '2 weeks ago').

    If relative is False, returns formatted calendar date '01 Jan 2026'.
    """
    if not pub_val or not str(pub_val).strip():
        return fallback
    val = str(pub_val).strip()

    # If it is already a relative string (e.g. '3 days ago'), return as is
    if "ago" in val.lower():
        return val

    dt = parse_published_datetime(val)
    if dt is None:
        return val if val else fallback

    if not relative:
        return dt.strftime("%d %b %Y")

    # Determine reference current time with matching timezone awareness
    if dt.tzinfo is not None:
        ref_now = now if now is not None else datetime.now(timezone.utc)
        if ref_now.tzinfo is None:
            ref_now = ref_now.replace(tzinfo=timezone.utc)
    else:
        ref_now = now if now is not None else datetime.now()
        if ref_now.tzinfo is not None:
            ref_now = ref_now.replace(tzinfo=None)

    diff = ref_now - dt
    total_seconds = int(diff.total_seconds())

    if total_seconds < 60:
        return "Just now"

    if total_seconds < 3600:
        mins = max(1, total_seconds // 60)
        return "1 minute ago" if mins == 1 else f"{mins} minutes ago"

    if total_seconds < 86400:
        hrs = max(1, total_seconds // 3600)
        return "1 hour ago" if hrs == 1 else f"{hrs} hours ago"

    days = total_seconds // 86400
    if days == 1:
        return "1 day ago"
    if 2 <= days <= 6:
        return f"{days} days ago"

    weeks = days // 7
    if weeks == 1:
        return "1 week ago"
    if 2 <= weeks <= 3:
        return f"{weeks} weeks ago"
    if weeks == 4 and days < 30:
        return "4 weeks ago"

    months = days // 30
    if months <= 1:
        return "1 month ago"
    if 2 <= months < 12:
        return f"{months} months ago"

    years = days // 365
    if years <= 1:
        return "1 year ago"
    return f"{years} years ago"


def format_exact_published_datetime(pub_val: Optional[str], fallback: str = "Date: N/A") -> str:
    """Format publication timestamp into exact date and time for tooltips (e.g. '26 Feb 2025, 01:49 PM')."""
    if not pub_val or not str(pub_val).strip():
        return fallback
    val = str(pub_val).strip()
    dt = parse_published_datetime(val)
    if dt is None:
        return val if val else fallback

    has_time = any(c in val for c in (":", "T", " "))
    if has_time and (dt.hour != 0 or dt.minute != 0 or dt.second != 0 or "T" in val):
        return dt.strftime("%d %b %Y, %I:%M %p")
    return dt.strftime("%d %b %Y")


def _get_task_sort_val(task: VideoTask, key: str, stats_store: Optional[Any], reverse: bool = True) -> float:
    """Extract numeric sort value for a task based on sort key and reverse direction."""
    record = stats_store.find_for_task(task) if stats_store is not None else None

    if key == "published_date":
        pub_str = getattr(task, "published_at", "") or ""
        dt = parse_published_datetime(pub_str)
        if dt is not None:
            try:
                return dt.timestamp()
            except Exception:
                pass
        # Unparsed/empty published dates stay at bottom regardless of sort direction
        return float("-inf") if reverse else float("inf")

    elif key == "watch_time":
        if record is not None:
            return float(record.watch_seconds or 0.0)
        return 0.0

    elif key == "last_played":
        ts_str = ""
        if record is not None:
            ts_str = record.last_run or record.last_completed or ""
        if ts_str:
            try:
                clean_ts = ts_str.strip().replace("Z", "+00:00")
                return datetime.fromisoformat(clean_ts).timestamp()
            except Exception:
                pass
        return float("-inf") if reverse else 0.0

    return 0.0


def _get_cfg_int(mgr: Any, key: str, default: int) -> int:
    if not mgr:
        return default
    try:
        val = mgr.get_int(key, default)
        return val if type(val) is int else default
    except Exception:
        return default


def _get_cfg_float(mgr: Any, key: str, default: float) -> float:
    if not mgr:
        return default
    try:
        val = mgr.get_float(key, default)
        return float(val) if isinstance(val, (int, float)) else default
    except Exception:
        return default


def get_task_status_badges(task: VideoTask, channel_enabled: bool = True, stats_store: Any = None) -> str:
    """Return status badge strings (e.g. '[DISABLED]', '[MAX VIEWS REACHED]') for a task."""
    badges: List[str] = []
    if not channel_enabled and not getattr(task, "enabled", True):
        badges.extend(["[CHANNEL & VIDEO DISABLED]", "[CHANNEL DISABLED]", "[DISABLED]"])
    elif not channel_enabled:
        badges.extend(["[CHANNEL DISABLED]", "[DISABLED]"])
    elif not getattr(task, "enabled", True):
        badges.append("[DISABLED]")

    try:
        record = stats_store.find_for_task(task) if (stats_store and hasattr(stats_store, "find_for_task")) else None
        current_runs = getattr(record, "runs", None)
        if current_runs is None:
            current_runs = getattr(task, "total_runs", getattr(task, "runs", 0)) or 0
        rec_dur = getattr(record, "video_duration", 0.0) if record else 0.0
        task_dur = getattr(task, "duration_seconds", 0.0) or 0.0
        eff_dur = float(rec_dur if rec_dur and rec_dur > 0 else (task_dur if isinstance(task_dur, (int, float)) else 0.0))
        watch_sec = getattr(record, "watch_seconds", None)
        if watch_sec is None:
            watch_sec = getattr(task, "total_watch_seconds", getattr(task, "watch_seconds", 0.0)) or 0.0
        if current_runs == 0 and eff_dur > 0 and watch_sec >= eff_dur:
            equiv = int(watch_sec // eff_dur)
            if equiv > 0:
                current_runs = equiv
        if watch_sec is None:
            watch_sec = getattr(task, "total_watch_seconds", getattr(task, "watch_seconds", 0.0)) or 0.0
        is_limit = False
        if hasattr(task, "is_target_reached"):
            is_limit = task.is_target_reached(
                current_runs=current_runs,
                watch_seconds=watch_sec,
                video_duration=eff_dur,
            )
        if is_limit:
            badges.append("[LIMIT REACHED]")
            if getattr(task, "is_short", False):
                badges.append("[MAX VIEWS REACHED]")
            else:
                mult = getattr(task, "target_multiplier", 0.0) or 0.0
                target_hit = mult > 0 and eff_dur > 0 and (watch_sec / eff_dur) >= mult
                max_runs = getattr(task, "max_runs", 0) or 0
                views_hit = max_runs > 0 and current_runs >= max_runs
                if target_hit and views_hit:
                    badges.extend(["[TARGET & MAX VIEWS REACHED]", "[TARGET MULTIPLIER REACHED]", "[MAX VIEWS REACHED]"])
                elif target_hit:
                    badges.append("[TARGET MULTIPLIER REACHED]")
                elif views_hit:
                    badges.append("[MAX VIEWS REACHED]")
                else:
                    badges.append("[MAX VIEWS REACHED]")
    except Exception:
        pass

    return " ".join(badges)


def _normalize_brackets(text: str) -> str:
    """Replace brackets, braces, and parentheses with spaces and normalize whitespace."""
    if not text:
        return ""
    t = text.replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ").replace("{", " ").replace("}", " ")
    return " ".join(t.split())


def compute_task_search_rank(task: VideoTask, query: str, channel_enabled: bool = True, stats_store: Any = None) -> Optional[int]:
    """Calculate search match rank for a video task against a search query.

    Matches against titles (title, title_2, title_3), keywords (keywords, keywords_2, keywords_3),
    channel name, URL, and status badges (e.g. [DISABLED], [MAX VIEWS REACHED]).
    Supports finding words inside square brackets `[]` whether queried with or without brackets.

    Ranking:
        0: Exact substring match in title, keywords, or status badges (highest priority)
        1: Exact substring match in channel name or URL
        2: Word match in title, keywords, or status badges (all query words present in any order)
        3: Word match across all fields (all query words present across title, channel, keywords, URL, status)
        None: No match (task filtered out)
    """
    clean_query = (query or "").strip().lower()
    if not clean_query:
        return 0

    title_text = f"{task.title or ''} {task.title_2 or ''} {task.title_3 or ''}".lower()
    keywords_text = f"{task.keywords or ''} {task.keywords_2 or ''} {task.keywords_3 or ''}".lower()
    channel_text = (task.channel or "").lower()
    url_text = (task.url or "").lower()
    status_text = get_task_status_badges(task, channel_enabled=channel_enabled, stats_store=stats_store).lower()

    title_and_kw = f"{title_text} {keywords_text} {status_text}".strip()
    full_text = f"{title_and_kw} {channel_text} {url_text}".strip()

    title_unbracketed = _normalize_brackets(f"{title_text} {keywords_text} {status_text}")
    full_unbracketed = _normalize_brackets(full_text)
    channel_unbracketed = _normalize_brackets(channel_text)
    url_unbracketed = _normalize_brackets(url_text)

    q_unbracketed = _normalize_brackets(clean_query)
    q_stripped = clean_query.strip("[](){}")

    # Tier 1: Exact Substring Matches (First priority)
    if (
        clean_query in title_and_kw
        or (q_unbracketed and q_unbracketed in title_unbracketed)
        or (q_stripped and (q_stripped in title_and_kw or q_stripped in title_unbracketed))
    ):
        return 0

    if (
        clean_query in channel_text
        or clean_query in url_text
        or (q_unbracketed and (q_unbracketed in channel_unbracketed or q_unbracketed in url_unbracketed))
        or (q_stripped and (q_stripped in channel_text or q_stripped in url_text))
    ):
        return 1

    # Tier 2: Word Matching (all query words present in any order)
    query_words = [w.strip("[](){},.") for w in q_unbracketed.split() if w.strip("[](){},.")]
    if not query_words:
        query_words = clean_query.split()

    if len(query_words) > 1:
        if all(w in title_and_kw or w in title_unbracketed for w in query_words):
            return 2
        if all(w in full_text or w in full_unbracketed for w in query_words):
            return 3
    elif len(query_words) == 1:
        w = query_words[0]
        if w in title_and_kw or w in title_unbracketed:
            return 0
        if w in full_text or w in full_unbracketed:
            return 1

    return None


class QueueView(Frame):
    shorts_only: bool = False
    current_sort_key: Optional[str] = "last_played"
    current_sort_reverse: bool = True

    @property
    def config_mgr(self) -> Any:
        return getattr(self.app_controller, "config_mgr", None)

    def __init__(self, parent, app_controller, shorts_only: bool = False):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.shorts_only = shorts_only
        self.video_tasks: List[VideoTask] = []
        self.displayed_tasks: List[VideoTask] = []
        self.collapsed_groups: set = set()
        self._pending_card_queue: List[Any] = []
        self._render_job: Optional[str] = None
        self._loading_indicator: Optional[Any] = None
        self.current_sort_key: Optional[str] = "last_played"
        self.current_sort_reverse: bool = True
        self._original_task_order: List[str] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header (macOS Large Title & Subtitle)
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        title_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_container.pack(side="left", anchor="w")

        header_title = "Shorts Automation Queue" if getattr(self, "shorts_only", False) else "Video Automation Queue"
        ctk.CTkLabel(
            title_container,
            text=header_title,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")

        sub_title = "Shorts Scheduling, Watch Time Metrics & Priority Queue" if getattr(self, "shorts_only", False) else "Task Scheduling, Watch Time Metrics & Priority Queue"
        ctk.CTkLabel(
            title_container,
            text=sub_title,
            font=ctk.CTkFont(size=9),
            text_color=("gray45", "gray65"),
        ).pack(anchor="w", pady=(2, 0))

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        is_shorts = getattr(self, "shorts_only", False)
        add_btn_text = "+ Add Target Short" if is_shorts else "+ Add Target Video"
        add_btn = ctk.CTkButton(
            btn_frame,
            text=add_btn_text,
        )
        add_btn.configure(
            command=with_button_feedback(add_btn, "Adding...", lambda: self.open_video_modal())
        )
        add_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            add_btn,
            "Add a new short task to the queue." if is_shorts else "Add a new video task to the queue.",
        )

        bulk_btn = ctk.CTkButton(
            btn_frame,
            text="⚡ Bulk Apply",
            fg_color="#1F6AA5",
            hover_color="#144870",
        )
        bulk_cmd = self.open_shorts_bulk_apply_modal if is_shorts else self.open_video_bulk_apply_modal
        bulk_btn.configure(
            command=with_button_feedback(bulk_btn, "Applying...", bulk_cmd)
        )
        bulk_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            bulk_btn,
            "Apply max views cap to multiple shorts at once." if is_shorts else "Apply target multipliers and view caps to multiple videos at once.",
        )

        self.sort_btn = ctk.CTkButton(
            btn_frame,
            text="↕ Sort ▾",
            width=135,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.open_sort_menu,
        )
        self.sort_btn.pack(side="left", padx=(0, 4))
        add_tooltip(
            self.sort_btn,
            "Sort queue by published date, watch time, or recently played.",
        )

        self.sort_dir_btn = ctk.CTkButton(
            btn_frame,
            text="↓",
            width=36,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.toggle_sort_direction,
        )
        self.sort_dir_btn.pack(side="left", padx=(0, 10))
        self.sort_dir_tooltip = add_tooltip(
            self.sort_dir_btn,
            "Sort Direction: Descending (Click to switch to Ascending)",
        )
        self._update_sort_buttons()

        # Real-time Search Bar
        self.search_frame = ctk.CTkFrame(btn_frame, fg_color="transparent")
        self.search_frame.pack(side="left", padx=(12, 10))

        search_icon = ctk.CTkLabel(self.search_frame, text="🔍", font=ctk.CTkFont(size=12))
        search_icon.pack(side="left", padx=(0, 4))

        placeholder = "Search shorts..." if is_shorts else "Search videos..."
        self.search_entry = ctk.CTkEntry(
            self.search_frame,
            placeholder_text=placeholder,
            width=240,
        )
        self.search_entry.pack(side="left", padx=(0, 4))

        self.search_clear_btn = ctk.CTkButton(
            self.search_frame,
            text="✕",
            width=28,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.clear_search,
        )
        self.search_clear_btn.pack(side="left")
        add_tooltip(
            self.search_clear_btn,
            "Clear search and show all queue tasks.",
        )
        add_tooltip(
            self.search_entry,
            "Real-time search across titles, channel names, keywords, and URLs.\nExact matches are prioritized, followed by multi-word matches.",
        )

        self._search_debounce_job: Optional[str] = None
        self._last_applied_search: str = ""
        self.search_entry.bind("<KeyRelease>", self._on_search_key_release)
        self.search_entry.bind("<Return>", lambda e: self._apply_search_filter())
        self.search_entry.bind("<Escape>", lambda e: self.clear_search())
        for key in ("<Control-BackSpace>", "<Control-Key-BackSpace>", "<Control-w>", "<Control-Key-w>"):
            self.search_entry.bind(key, self._on_search_ctrl_backspace)
            if hasattr(self.search_entry, "_entry") and self.search_entry._entry:
                self.search_entry._entry.bind(key, self._on_search_ctrl_backspace)

        reset_btn = ctk.CTkButton(
            btn_frame,
            text="🗑 Reset Stats",
            fg_color="#D97706",
            hover_color="#B45309",
            command=self.reset_stats,
        )
        reset_btn.pack(side="right", padx=(0, 10))
        add_tooltip(
            reset_btn,
            "Reset historical watch time and view counts for tasks in this queue.",
        )
        self.reset_btn = reset_btn

        clear_btn = ctk.CTkButton(
            btn_frame,
            text="🗑 Clear Queue",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_queue,
        )
        clear_btn.pack(side="right", padx=(0, 10))
        add_tooltip(
            clear_btn,
            "Clear all tasks from this queue.",
        )
        self.clear_btn = clear_btn

        # Scrollable Task List
        scroll_label = "Queued Target Shorts" if getattr(self, "shorts_only", False) else "Queued Target Videos"
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text=scroll_label)
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)
        self._setup_fast_scrolling()

        self.load_initial_queue()
        self.update_queue_header_stats()

    def _setup_fast_scrolling(self):
        """Install smooth, responsive, animated mouse wheel scrolling on the scroll frame."""
        setup_smooth_scrolling(self.scroll_frame, notch_pixels=65.0, damping=0.28)

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

    def get_queue_stats_summary(self) -> str:
        """Return the formatted stats string for total and per-channel queued videos/shorts.
        Format: '70 | 36:32 | Uncat: 2' (omits Uncat if 0, or just '0' if empty).
        Order of channels strictly follows the active display order in the queue.
        """
        is_shorts = bool(getattr(self, "shorts_only", False))

        tasks = getattr(self, "video_tasks", []) or []
        matching_tasks = [
            t for t in tasks
            if (t.is_short if is_shorts else not t.is_short)
        ]
        total_count = len(matching_tasks)

        query = self.get_search_query() if hasattr(self, "get_search_query") else ""
        stats_store = self._stats()
        if query:
            search_matched = [
                t for t in matching_tasks
                if compute_task_search_rank(t, query, stats_store=stats_store) is not None
            ]
            parts = [f"Matching: {len(search_matched)} of {total_count}"]
        else:
            parts = [str(total_count)]

        if total_count > 0:
            groups = group_videos_by_channel(
                tasks,
                self._get_display_channels(),
                include_archived=False,
            )
            channel_counts = []
            uncat_count = 0
            for group in groups:
                ch_enabled = group.channel.enabled if not group.is_uncategorized else True
                ch_matching = [
                    t for _, t in group.videos
                    if (t.is_short if is_shorts else not t.is_short) and (
                        not query or compute_task_search_rank(t, query, channel_enabled=ch_enabled, stats_store=stats_store) is not None
                    )
                ]
                count = len(ch_matching)
                if group.is_uncategorized or group.name == "Uncategorized":
                    uncat_count += count
                elif count > 0:
                    channel_counts.append(str(count))

            if channel_counts:
                parts.append(":".join(channel_counts))
            if uncat_count > 0:
                parts.append(f"Uncat: {uncat_count}")

        return " | ".join(parts)

    def get_queue_header_title(self) -> str:
        """Return the section header title with total and per-channel stats.
        Format: 'Queued Target Videos (70 | 36:32 | Uncat: 2)'
        """
        is_shorts = bool(getattr(self, "shorts_only", False))
        base_title = "Queued Target Shorts" if is_shorts else "Queued Target Videos"
        summary = self.get_queue_stats_summary()
        return f"{base_title} ({summary})"

    def update_queue_header_stats(self) -> None:
        """Update the scroll frame label with current queue stats."""
        title = self.get_queue_header_title()
        self.queue_stats_summary = self.get_queue_stats_summary()
        if hasattr(self, "scroll_frame") and self.scroll_frame is not None:
            try:
                self.scroll_frame.configure(label_text=title)
            except Exception:
                pass
            if hasattr(self.scroll_frame, "_label") and self.scroll_frame._label:
                try:
                    self.scroll_frame._label.configure(text=title)
                except Exception:
                    pass

    def _update_sort_buttons(self):
        is_desc = bool(self.current_sort_reverse)
        # Down arrow (↓) for Descending, Up arrow (↑) for Ascending
        dir_sym = "↓" if is_desc else "↑"
        dir_name = "Descending" if is_desc else "Ascending"
        next_dir = "Ascending" if is_desc else "Descending"

        if hasattr(self, "sort_dir_btn") and hasattr(self.sort_dir_btn, "configure"):
            try:
                self.sort_dir_btn.configure(text=dir_sym)
            except Exception:
                pass

        if hasattr(self, "sort_dir_tooltip") and self.sort_dir_tooltip is not None:
            try:
                self.sort_dir_tooltip.text = f"Sort Direction: {dir_name} (Click to switch to {next_dir})"
            except Exception:
                pass

        name_map = {
            "published_date": "Date",
            "watch_time": "Watch Time",
            "last_played": "Recently Played",
        }
        if hasattr(self, "sort_btn") and hasattr(self.sort_btn, "configure"):
            try:
                if self.current_sort_key in name_map:
                    label = f"Sort: {name_map[self.current_sort_key]} {dir_sym} ▾"
                    self.sort_btn.configure(text=label, fg_color="#2563EB")
                else:
                    self.sort_btn.configure(text="↕ Sort ▾", fg_color="#374151")
            except Exception:
                pass

    def get_search_query(self) -> str:
        """Return the current search query string from the search bar."""
        if hasattr(self, "search_entry") and self.search_entry:
            val = self.search_entry.get()
            return val.strip() if val else ""
        return ""

    def _on_search_ctrl_backspace(self, event=None):
        """Delete preceding word/bracket token on Ctrl+Backspace and trigger real-time search."""
        entry_widget = getattr(self.search_entry, "_entry", self.search_entry)
        try:
            # If text is selected, delete selection
            if hasattr(entry_widget, "select_present") and entry_widget.select_present():
                entry_widget.delete("sel.first", "sel.last")
                self._schedule_search_filter()
                return "break"

            cursor = entry_widget.index("insert")
            if cursor == 0:
                return "break"

            text = entry_widget.get()
            if not text:
                return "break"

            idx = cursor
            # 1. Skip trailing spaces
            while idx > 0 and text[idx - 1].isspace():
                idx -= 1
            if idx > 0:
                char = text[idx - 1]
                if char in "])}":
                    matching = {"]": "[", ")": "(", "}": "{"}[char]
                    open_pos = text.rfind(matching, 0, idx)
                    if open_pos != -1 and " " not in text[open_pos:idx]:
                        idx = open_pos
                    else:
                        idx -= 1
                        while idx > 0 and (text[idx - 1].isalnum() or text[idx - 1] in "_-"):
                            idx -= 1
                        if idx > 0 and text[idx - 1] in "([{\"'":
                            idx -= 1
                elif char.isalnum() or char in "_-":
                    while idx > 0 and (text[idx - 1].isalnum() or text[idx - 1] in "_-"):
                        idx -= 1
                    if idx > 0 and text[idx - 1] in "([{\"'":
                        idx -= 1
                else:
                    while idx > 0 and not (text[idx - 1].isalnum() or text[idx - 1] in "_-") and not text[idx - 1].isspace():
                        idx -= 1

            entry_widget.delete(idx, cursor)
            self._schedule_search_filter()
            return "break"
        except Exception:
            return "break"

    def _on_search_key_release(self, event=None):
        """Handle key release in search entry with real-time debouncing."""
        self._schedule_search_filter()

    def _on_search_changed(self):
        """Handle StringVar change with real-time debouncing."""
        self._schedule_search_filter()

    def _schedule_search_filter(self):
        """Debounce real-time keystrokes to ensure smooth, responsive filtering."""
        if hasattr(self, "_search_debounce_job") and self._search_debounce_job:
            try:
                self.after_cancel(self._search_debounce_job)
            except Exception:
                pass
            self._search_debounce_job = None

        query = self.get_search_query()
        if query == getattr(self, "_last_applied_search", None):
            return

        self._search_debounce_job = self.after(50, self._apply_search_filter)

    def _apply_search_filter(self):
        """Apply the search filter and re-render matching tasks in the queue."""
        self._search_debounce_job = None
        self._last_applied_search = self.get_search_query()
        self.refresh_list()

    def clear_search(self):
        """Clear the search input and restore all queue tasks."""
        if hasattr(self, "_search_debounce_job") and self._search_debounce_job:
            try:
                self.after_cancel(self._search_debounce_job)
            except Exception:
                pass
            self._search_debounce_job = None

        if hasattr(self, "search_entry") and self.search_entry:
            try:
                self.search_entry.delete(0, "end")
                if hasattr(self.search_entry, "_restore_placeholder"):
                    self.search_entry._restore_placeholder()
            except Exception:
                pass

        self._last_applied_search = ""
        self.refresh_list()
        if hasattr(self, "search_entry") and self.search_entry:
            try:
                self.search_entry.focus_set()
            except Exception:
                pass

    def destroy(self):
        if hasattr(self, "_search_debounce_job") and self._search_debounce_job:
            try:
                self.after_cancel(self._search_debounce_job)
            except Exception:
                pass
            self._search_debounce_job = None
        if hasattr(self, "_active_sort_menu") and self._active_sort_menu:
            try:
                self._active_sort_menu.destroy()
            except Exception:
                pass
            self._active_sort_menu = None
        super().destroy()

    def open_sort_menu(self):
        """Open popup dropdown menu to select sort criteria with Apple HIG styling."""
        if hasattr(self, "_active_sort_menu") and self._active_sort_menu:
            try:
                if self._active_sort_menu.is_open():
                    self._active_sort_menu.destroy()
                    self._active_sort_menu = None
                    return
            except Exception:
                pass

        menu = AppleDropdownMenu(self)
        self._active_sort_menu = menu

        def _label(key_id, name):
            if self.current_sort_key == key_id:
                dir_str = "↓ Descending" if self.current_sort_reverse else "↑ Ascending"
                return f"✓ {name} ({dir_str})"
            return f"   {name}"

        menu.add_command(
            label=_label("published_date", "Published Date"),
            command=lambda: self.apply_sort("published_date"),
        )
        menu.add_command(
            label=_label("watch_time", "Watch Time"),
            command=lambda: self.apply_sort("watch_time"),
        )
        menu.add_command(
            label=_label("last_played", "Recently Played"),
            command=lambda: self.apply_sort("last_played"),
        )
        menu.add_separator()
        menu.add_command(
            label="   Reset Sort Order",
            command=self.reset_sort_order,
        )

        try:
            x = self.sort_btn.winfo_rootx()
            y = self.sort_btn.winfo_rooty() + self.sort_btn.winfo_height() + 4
            menu.tk_popup(x, y)
        except Exception:
            pass
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def apply_sort(self, key_id: str):
        """Apply sort criterion or reverse order if clicked again."""
        if self.current_sort_key == key_id:
            self.current_sort_reverse = not self.current_sort_reverse
        else:
            self.current_sort_key = key_id
            self.current_sort_reverse = True
        self._apply_current_sort(save=True)

    def toggle_sort_direction(self):
        """Toggle ascending / descending direction for active sort."""
        if not self.current_sort_key:
            self.current_sort_key = "watch_time"
            self.current_sort_reverse = False  # Switch to ascending if clicked from default
        else:
            self.current_sort_reverse = not self.current_sort_reverse
        self._apply_current_sort(save=True)

    def reset_sort_order(self):
        """Reset sort order to initial unsorted state."""
        self.current_sort_key = None
        self.current_sort_reverse = True
        if getattr(self, "_original_task_order", None):
            order_map = {url: i for i, url in enumerate(self._original_task_order)}
            self.video_tasks.sort(key=lambda t: order_map.get(t.url or t.title, 999999))
            self.save_queue()
        self._update_sort_buttons()
        self.refresh_list()

    def _channel_watch_time(self, channel_name: str) -> float:
        """Calculate total watch time in seconds for a channel from stats store and current tasks."""
        if not channel_name or channel_name == NO_CHANNEL_VALUE:
            return -1.0
        store = self._stats()
        wt = 0.0
        if store is not None:
            try:
                tot = store.channel_totals(channel_name)
                wt = float(getattr(tot, "watch_seconds", 0.0) or 0.0)
            except Exception:
                wt = 0.0

        for ch in self._channels():
            if (ch.name or "").strip().lower() == channel_name.strip().lower():
                wt = max(wt, float(getattr(ch, "watch_seconds", 0.0) or 0.0))
                break

        tasks_wt = sum(
            _get_task_sort_val(t, "watch_time", store, reverse=True)
            for t in self.video_tasks
            if (t.channel or "").strip().lower() == channel_name.strip().lower()
        )
        return max(wt, tasks_wt)

    def _get_display_channels(self) -> List[Channel]:
        """Return channels in standard or watch-time sorted order based on active sort."""
        channels = list(self._channels())
        if getattr(self, "current_sort_key", None) == "watch_time":
            # The channel with the most watch time should be on the top, whether ascending or descending
            channels.sort(key=lambda ch: self._channel_watch_time(ch.name), reverse=True)
        return channels

    def _apply_current_sort(self, save: bool = True, render: bool = True):
        key = getattr(self, "current_sort_key", None)
        if not key or not self.video_tasks:
            self._update_sort_buttons()
            return

        store = self._stats()
        rev = getattr(self, "current_sort_reverse", True)

        ordered_channels = self._get_display_channels()
        by_channel: Dict[str, List[VideoTask]] = {}
        for t in self.video_tasks:
            ch_name = t.channel or ""
            by_channel.setdefault(ch_name, []).append(t)

        sorted_tasks: List[VideoTask] = []
        is_shorts = getattr(self, "shorts_only", False)

        def _sort_group(tasks: List[VideoTask]) -> List[VideoTask]:
            matching_indices = [i for i, t in enumerate(tasks) if (t.is_short if is_shorts else not t.is_short)]
            matching_tasks = [tasks[i] for i in matching_indices]
            matching_tasks.sort(key=lambda t: _get_task_sort_val(t, key, store, reverse=rev), reverse=rev)
            result = list(tasks)
            for idx, task in zip(matching_indices, matching_tasks):
                result[idx] = task
            return result

        for ch in ordered_channels:
            if ch.name in by_channel:
                sorted_tasks.extend(_sort_group(by_channel[ch.name]))
                del by_channel[ch.name]

        if by_channel:
            remaining_keys = list(by_channel.keys())
            if key == "watch_time":
                # Sort remaining channels by watch time descending as well, keeping uncategorized last
                remaining_keys.sort(
                    key=lambda k: (-1.0 if not k or k == NO_CHANNEL_VALUE else self._channel_watch_time(k)),
                    reverse=True,
                )
            for ch_name in remaining_keys:
                sorted_tasks.extend(_sort_group(by_channel[ch_name]))

        self.video_tasks = sorted_tasks
        if hasattr(self, "app_controller") and self.app_controller is not None:
            if hasattr(self.app_controller, "queued_video_tasks"):
                self.app_controller.queued_video_tasks = sorted_tasks
            views = getattr(self.app_controller, "views", {})
            if isinstance(views, dict):
                for v in views.values():
                    if isinstance(v, QueueView) and v is not self:
                        v.video_tasks = sorted_tasks
        if save:
            self.save_queue()
        self._update_sort_buttons()
        self.update_queue_header_stats()
        if render:
            self.refresh_list()

    def load_initial_queue(self):
        cur_view = getattr(self.app_controller, "current_view_name", "")
        should_render = (cur_view == "shorts_queue" if getattr(self, "shorts_only", False) else cur_view == "queue")

        if hasattr(self, "app_controller") and getattr(self.app_controller, "queued_video_tasks", None):
            self.video_tasks = self.app_controller.queued_video_tasks
            if not getattr(self, "_original_task_order", None):
                self._original_task_order = [t.url or t.title for t in self.video_tasks]
            if getattr(self, "current_sort_key", None) and self.video_tasks:
                self._apply_current_sort(save=False, render=should_render)
            elif should_render:
                self.refresh_list()
            self.update_queue_header_stats()
            return
        raw_list = self.app_controller.config_mgr.get_video_queue()
        self.video_tasks = [VideoTask.from_dict(v) for v in raw_list]
        if not getattr(self, "_original_task_order", None):
            self._original_task_order = [t.url or t.title for t in self.video_tasks]
        if getattr(self, "current_sort_key", None) and self.video_tasks:
            self._apply_current_sort(save=False, render=should_render)
        elif should_render:
            self.refresh_list()
        self.app_controller.on_queue_changed(self.video_tasks)
        self.update_queue_header_stats()

    def save_queue(self):
        dict_list = [v.to_dict() for v in self.video_tasks]
        self.app_controller.config_mgr.set_video_queue(dict_list)
        self.app_controller.on_queue_changed(self.video_tasks)
        self.update_queue_header_stats()

    def show_popup_notification(self, title: str, message: str) -> None:
        """Display an in-app floating bubble notification for user updates."""
        self._show_bubble_notification(message=message, title=title, success=True, duration_ms=3000)

    def show_bubble_notification(
        self,
        message: str,
        title: Optional[str] = None,
        success: bool = True,
        duration_ms: int = 3000,
    ) -> None:
        """Public method to display a floating in-app bubble notification."""
        self._show_bubble_notification(message=message, title=title, success=success, duration_ms=duration_ms)

    def _show_bubble_notification(
        self,
        message: str,
        title: Optional[str] = None,
        success: bool = True,
        duration_ms: int = 3000,
    ) -> None:
        """Display a floating in-app bubble notification at the middle-top of the queue view."""
        self.last_bubble_message = message
        self.last_bubble_title = title

        # Dismiss any active bubble notification first
        self.dismiss_bubble()

        if not hasattr(self, "_w") or not hasattr(self, "tk"):
            return

        try:
            mode = getattr(self.app_controller, "appearance_mode", "Dark")
            colors = get_theme_colors(mode)
            bg_color = colors.get("card_bg", "#202024")
            border_color = colors.get("card_border", "#323238")
            fg_color = colors.get("fg", "#ffffff")
            fg_dim = colors.get("fg_dim", "#d4d4d8")
            icon_color = colors.get("success", "#30d158") if success else colors.get("danger", "#ff453a")
            icon_text = "✓" if success else "✕"

            bubble = tk.Frame(
                self,
                background=bg_color,
                highlightbackground=border_color,
                highlightcolor=border_color,
                highlightthickness=1,
                padx=16,
                pady=10,
                cursor="hand2",
            )
            self._bubble_widget = bubble

            content_frame = tk.Frame(bubble, background=bg_color, cursor="hand2")
            content_frame.pack(fill="both", expand=True)

            lbl_icon = tk.Label(
                content_frame,
                text=icon_text,
                font=get_font(size=13, weight="bold"),
                foreground=icon_color,
                background=bg_color,
                cursor="hand2",
            )
            lbl_icon.pack(side="left", padx=(0, 10), anchor="center")

            text_block = tk.Frame(content_frame, background=bg_color, cursor="hand2")
            text_block.pack(side="left", fill="both", expand=True)

            if title:
                lbl_title = tk.Label(
                    text_block,
                    text=title,
                    font=get_font(size=10, weight="bold"),
                    foreground=fg_color,
                    background=bg_color,
                    cursor="hand2",
                    anchor="w",
                )
                lbl_title.pack(anchor="w")

            clean_msg = message.replace("\n", " ").strip()
            lbl_msg = tk.Label(
                text_block,
                text=clean_msg,
                font=get_font(size=9, weight="normal"),
                foreground=fg_dim if title else fg_color,
                background=bg_color,
                cursor="hand2",
                anchor="w",
            )
            lbl_msg.pack(anchor="w")

            lbl_close = tk.Label(
                content_frame,
                text="✕",
                font=get_font(size=9, weight="bold"),
                foreground=colors.get("fg_muted", "#888888"),
                background=bg_color,
                cursor="hand2",
            )
            lbl_close.pack(side="right", padx=(12, 0), anchor="center")

            if callable(getattr(bubble, "place", None)):
                bubble.place(relx=0.5, y=20, anchor="n")
            if callable(getattr(bubble, "lift", None)):
                bubble.lift()

            def _dismiss(event=None):
                self.dismiss_bubble()

            for ev in ("<ButtonPress>", "<Button-1>"):
                bubble.bind(ev, _dismiss)
                content_frame.bind(ev, _dismiss)
                lbl_icon.bind(ev, _dismiss)
                text_block.bind(ev, _dismiss)
                lbl_msg.bind(ev, _dismiss)
                lbl_close.bind(ev, _dismiss)
                if title:
                    lbl_title.bind(ev, _dismiss)

            if callable(getattr(self, "after", None)):
                self._bubble_timer = self.after(duration_ms, _dismiss)
        except Exception:
            pass

    def dismiss_bubble(self) -> None:
        """Dismiss active in-app bubble notification immediately."""
        if getattr(self, "_bubble_timer", None) is not None:
            try:
                if callable(getattr(self, "after_cancel", None)):
                    self.after_cancel(self._bubble_timer)
            except Exception:
                pass
            self._bubble_timer = None

        if getattr(self, "_bubble_widget", None) is not None:
            try:
                self._bubble_widget.destroy()
            except Exception:
                pass
            self._bubble_widget = None

    # -------------------------------------------------------------- channels

    def _channels(self) -> List[Channel]:
        return [Channel.from_dict(entry) for entry in self.app_controller.config_mgr.get_channels()]

    def _channel_values(self) -> List[str]:
        """Dropdown values for the task modal: every channel plus '(None)'."""
        return [NO_CHANNEL_VALUE] + [channel.name for channel in self._channels()]

    def open_video_bulk_apply_modal(self) -> None:
        """Open modal dialog to bulk configure multiplier and max views for video queue."""
        self.open_bulk_apply_modal(shorts_only=False)

    def open_shorts_bulk_apply_modal(self) -> None:
        """Open modal dialog to bulk configure max views cap only for shorts queue."""
        self.open_bulk_apply_modal(shorts_only=True)

    def open_bulk_apply_modal(self, shorts_only: Optional[bool] = None) -> None:
        """Open modal dialog to bulk configure multiplier and max views for queued videos or shorts."""
        is_shorts = self.shorts_only if shorts_only is None else bool(shorts_only)
        matching_tasks = [
            t for t in self.video_tasks
            if (getattr(t, "is_short", False) if is_shorts else not getattr(t, "is_short", False))
        ]
        if not matching_tasks:
            queue_name = "shorts" if is_shorts else "videos"
            messagebox.showinfo("Empty Queue", f"There are no {queue_name} in the queue to configure.", parent=self)
            return

        from .bulk_apply_modal import BulkApplyModal
        BulkApplyModal(
            self,
            tasks=matching_tasks,
            app_controller=self.app_controller,
            on_applied=self.refresh_list,
            shorts_only=is_shorts,
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
            shorts_only=getattr(self, "shorts_only", False),
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
            cfg = getattr(self.app_controller, "config_mgr", None)
            init_runs = (
                _get_cfg_int(cfg, "DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS)
                if getattr(self, "shorts_only", False)
                else _get_cfg_int(cfg, "DefaultVideoMaxViews", DEFAULT_MAX_RUNS)
            )
            init_mult = (
                0.0
                if getattr(self, "shorts_only", False)
                else _get_cfg_float(cfg, "DefaultVideoTargetViews", DEFAULT_TARGET_MULTIPLIER)
            )
            target_task = VideoTask(
                watchtime_duration=self._default_watch_duration(),
                channel=preselected_channel or "",
                target_multiplier=init_mult,
                max_runs=init_runs,
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

        is_shorts_view = getattr(self, "shorts_only", False)

        if not is_shorts_view:
            ctk.CTkLabel(modal, text="Target Multiplier (e.g. 3.0x, 0 or blank = unlimited, default 3x):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
            target_multiplier_entry = ctk.CTkEntry(modal, placeholder_text="e.g. 3.0 (0 or blank = unlimited)", width=460)
            if getattr(target_task, "target_multiplier", 0.0) > 0:
                mult_val = target_task.target_multiplier
                target_multiplier_entry.insert(0, str(int(mult_val)) if mult_val.is_integer() else f"{mult_val:.1f}")
            target_multiplier_entry.pack(padx=20, pady=(0, 10))
        else:
            target_multiplier_entry = None

        cfg = getattr(self.app_controller, "config_mgr", None)
        if is_shorts_view:
            configured_default_runs = _get_cfg_int(cfg, "DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS)
            views_label = f"Max Views Cap (0 or blank = unlimited, default {configured_default_runs}):"
            views_placeholder = f"e.g. {configured_default_runs} (0 or blank = unlimited)"
        else:
            configured_default_runs = _get_cfg_int(cfg, "DefaultVideoMaxViews", DEFAULT_MAX_RUNS)
            views_label = f"Max Views Cap (Full Runs Limit, 0 or blank = unlimited, default {configured_default_runs}):"
            views_placeholder = f"e.g. {configured_default_runs} (0 or blank = unlimited)"

        ctk.CTkLabel(modal, text=views_label, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        max_runs_entry = ctk.CTkEntry(modal, placeholder_text=views_placeholder, width=460)
        if target_task.max_runs > 0:
            max_runs_entry.insert(0, str(target_task.max_runs))
        elif not is_editing:
            max_runs_entry.insert(0, str(configured_default_runs))
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

            if is_shorts_view:
                target_mult_val = 0.0
            else:
                raw_mult = target_multiplier_entry.get().strip() if target_multiplier_entry else ""
                try:
                    target_mult_val = max(0.0, float(raw_mult)) if raw_mult else (DEFAULT_TARGET_MULTIPLIER if not is_editing else 0.0)
                except ValueError:
                    target_mult_val = DEFAULT_TARGET_MULTIPLIER if not is_editing else 0.0

            raw_limit = max_runs_entry.get().strip()
            default_runs_cap = configured_default_runs
            try:
                max_runs_val = max(0, int(raw_limit)) if raw_limit else (default_runs_cap if not is_editing else 0)
            except ValueError:
                max_runs_val = default_runs_cap if not is_editing else 0

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
                is_short_override=True if is_shorts_view else (getattr(target_task, "is_short_override", None) if is_editing else None),
                published_at=getattr(target_task, "published_at", ""),
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

    def clear_queue(self, confirm: bool = True):
        if confirm and hasattr(self, "_w") and hasattr(self, "tk"):
            queue_label = "shorts" if getattr(self, "shorts_only", False) else "videos"
            if not show_themed_confirm_dialog(
                self,
                title="Clear Queue",
                heading=f"Clear All {queue_label.capitalize()}?",
                message=f"Are you sure you want to remove all {queue_label} from the automation queue?\n\nThis action cannot be undone.",
                confirm_text="Clear All",
                cancel_text="Cancel",
                confirm_color="#D32F2F",
                confirm_hover="#9A0007",
                icon="🗑",
            ):
                return

        if getattr(self, "shorts_only", False):
            self.video_tasks = [t for t in self.video_tasks if not getattr(t, "is_short", False)]
        else:
            self.video_tasks = [t for t in self.video_tasks if getattr(t, "is_short", False)]
        self.save_queue()
        self.refresh_list()
        if hasattr(self, "clear_btn") and self.clear_btn:
            flash_button_feedback(self.clear_btn, "Cleared", duration_ms=2500)

    def reset_stats(self, confirm: bool = True):
        """Delete every recorded watch statistic after a confirmation."""
        if confirm and hasattr(self, "_w") and hasattr(self, "tk"):
            if not show_themed_confirm_dialog(
                self,
                title="Reset Statistics",
                heading="Reset All Statistics?",
                message="Are you sure you want to delete all recorded watch statistics?\n\nThis action cannot be undone.",
                confirm_text="Reset Stats",
                cancel_text="Cancel",
                confirm_color="#D97706",
                confirm_hover="#B45309",
                icon="⚠️",
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
        if hasattr(self, "reset_btn") and self.reset_btn:
            flash_button_feedback(self.reset_btn, "Reset", duration_ms=2500)

    def _cancel_pending_render(self):
        """Cancel any queued progressive card rendering jobs."""
        if getattr(self, "_render_job", None) is not None:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None
        if hasattr(self, "_pending_card_queue"):
            self._pending_card_queue.clear()
        if getattr(self, "_loading_indicator", None) is not None:
            try:
                self._loading_indicator.destroy()
            except Exception:
                pass
            self._loading_indicator = None

    def _process_card_render_batch(self, batch_size: int = 15):
        """Render a progressive chunk of cards and yield to the Tkinter event loop between batches."""
        self._render_job = None
        try:
            if hasattr(self, "winfo_exists") and not self.winfo_exists():
                if hasattr(self, "_pending_card_queue"):
                    self._pending_card_queue.clear()
                return
        except Exception:
            pass

        count = 0
        while getattr(self, "_pending_card_queue", None) and count < batch_size:
            (
                container,
                g_idx,
                task,
                real_index,
                group,
                can_move_up,
                can_move_down,
                on_move_up,
                on_move_down,
            ) = self._pending_card_queue.pop(0)

            try:
                if hasattr(container, "winfo_exists") and not container.winfo_exists():
                    continue
                card = self._build_task_card(
                    container,
                    g_idx + 1,
                    task,
                    real_index,
                    channel_enabled=group.channel.enabled if not group.is_uncategorized else True,
                    can_move_up=can_move_up,
                    can_move_down=can_move_down,
                    on_move_up=on_move_up,
                    on_move_down=on_move_down,
                    group=group,
                )
                card.grid(row=g_idx + 1, column=0, sticky="ew", pady=(0, 5))
            except Exception:
                pass
            count += 1

        if getattr(self, "_pending_card_queue", None):
            rem = len(self._pending_card_queue)
            if isinstance(getattr(self, "scroll_frame", None), ctk.CTkBaseClass):
                try:
                    if getattr(self, "_loading_indicator", None) is None:
                        self._loading_indicator = ctk.CTkLabel(
                            self.scroll_frame,
                            text=f"Loading items ({rem} remaining)...",
                            font=ctk.CTkFont(size=12, slant="italic"),
                            text_color=("gray50", "gray50"),
                        )
                        self._loading_indicator.pack(pady=10)
                    else:
                        self._loading_indicator.configure(text=f"Loading items ({rem} remaining)...")
                        self._loading_indicator.pack_forget()
                        self._loading_indicator.pack(pady=10)
                except Exception:
                    pass

            try:
                if hasattr(self, "after"):
                    self._render_job = self.after(10, lambda: self._process_card_render_batch(batch_size=25))
                else:
                    self._process_card_render_batch(batch_size=len(self._pending_card_queue))
            except Exception:
                while self._pending_card_queue:
                    (
                        container,
                        g_idx,
                        task,
                        real_index,
                        group,
                        can_move_up,
                        can_move_down,
                        on_move_up,
                        on_move_down,
                    ) = self._pending_card_queue.pop(0)
                    try:
                        if hasattr(container, "winfo_exists") and not container.winfo_exists():
                            continue
                        card = self._build_task_card(
                            container,
                            g_idx + 1,
                            task,
                            real_index,
                            channel_enabled=group.channel.enabled if not group.is_uncategorized else True,
                            can_move_up=can_move_up,
                            can_move_down=can_move_down,
                            on_move_up=on_move_up,
                            on_move_down=on_move_down,
                            group=group,
                        )
                        card.grid(row=g_idx + 1, column=0, sticky="ew", pady=(0, 5))
                    except Exception:
                        pass
        else:
            if getattr(self, "_loading_indicator", None) is not None:
                try:
                    self._loading_indicator.destroy()
                except Exception:
                    pass
            # Update canvas geometry once when complete so scrolling bounds are exact
            if hasattr(self, "scroll_frame") and hasattr(self.scroll_frame, "update_idletasks"):
                try:
                    self.scroll_frame.update_idletasks()
                except Exception:
                    pass
                self._loading_indicator = None

    def refresh_list(self):
        self._cancel_pending_render()
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        if hasattr(self, "app_controller") and getattr(self.app_controller, "queued_video_tasks", None) is not None:
            if self.app_controller.queued_video_tasks or not self.video_tasks:
                self.video_tasks = self.app_controller.queued_video_tasks

        self.update_queue_header_stats()

        matching_tasks = [
            t for t in self.video_tasks
            if (t.is_short if getattr(self, "shorts_only", False) else not t.is_short)
        ]

        if not matching_tasks:
            self.displayed_tasks = []
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

        query = self.get_search_query() if hasattr(self, "get_search_query") else ""
        channel_groups = group_videos_by_channel(self.video_tasks, self._get_display_channels(), include_archived=False)
        total_search_matches = 0
        prepared_groups = []

        for group in channel_groups:
            if getattr(self, "shorts_only", False):
                type_videos = [(idx, t) for (idx, t) in group.videos if t.is_short]
            else:
                type_videos = [(idx, t) for (idx, t) in group.videos if not t.is_short]

            if not type_videos:
                continue

            if query:
                # Score each task: 0 = exact title/kw match, 1 = exact ch/url match, 2 = word title match, 3 = word full match
                channel_enabled = group.channel.enabled if not group.is_uncategorized else True
                stats_store = self._stats()
                ranked_videos = []
                for idx, t in type_videos:
                    rank = compute_task_search_rank(t, query, channel_enabled=channel_enabled, stats_store=stats_store)
                    if rank is not None:
                        ranked_videos.append((rank, idx, t))

                if not ranked_videos:
                    continue

                # Sort matches within group: exact matches first
                ranked_videos.sort(key=lambda item: item[0])
                filtered_videos = [(idx, t) for _, idx, t in ranked_videos]
                best_group_rank = ranked_videos[0][0]
                total_search_matches += len(filtered_videos)
            else:
                filtered_videos = type_videos
                best_group_rank = 0

            prepared_groups.append((best_group_rank, VideoGroup(channel=group.channel, videos=filtered_videos)))

        # When searching, prioritize channel groups with higher relevance matches
        if query:
            prepared_groups.sort(key=lambda item: item[0])

        if query and total_search_matches == 0:
            self.displayed_tasks = []
            item_noun = "shorts" if getattr(self, "shorts_only", False) else "videos"
            no_match_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
            no_match_frame.pack(pady=40)
            ctk.CTkLabel(
                no_match_frame,
                text=f'🔍 No {item_noun} matching "{query}" found.',
                font=ctk.CTkFont(size=15, weight="bold"),
            ).pack(pady=(0, 6))
            ctk.CTkLabel(
                no_match_frame,
                text="Try searching with different keywords or click Clear Search to view all tasks.",
                font=ctk.CTkFont(size=12),
                text_color=("gray45", "gray65"),
            ).pack(pady=(0, 12))
            ctk.CTkButton(
                no_match_frame,
                text="✕ Clear Search",
                width=120,
                command=self.clear_search,
            ).pack()
            return

        self.displayed_tasks = [t for _, g in prepared_groups for _, t in g.videos]
        for _, filtered_group in prepared_groups:
            self._render_group(filtered_group)

        if hasattr(self, "_pending_card_queue") and self._pending_card_queue:
            self._process_card_render_batch(batch_size=15)

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
            font=ctk.CTkFont(size=11, weight="bold"),
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
                font=ctk.CTkFont(size=9),
                text_color=("gray30", "gray70"),
                justify="left",
                anchor="w",
                cursor="hand2",
            )
            url_lbl.grid(row=1, column=col_offset, padx=12, pady=(0, 6), sticky="ew")
            url_lbl.bind("<Button-1>", _toggle_collapse)

        header_btns = ctk.CTkFrame(header, fg_color="transparent")
        header_btns.grid(row=0, column=col_offset + 1, rowspan=2, padx=8, pady=6, sticky="e")

        # 1. Manage Button
        manage_btn = ctk.CTkButton(
            header_btns,
            text="⚙ Manage",
            width=85,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
        )
        manage_btn.configure(
            command=with_button_feedback(manage_btn, "Opening...", lambda g=group: self.open_manage_channel_modal(g))
        )
        has_more_btns = bool(group.url or (not group.is_uncategorized and group.channel and not getattr(group.channel, "archived", False)))
        manage_btn.pack(side="left", padx=(0, 4) if has_more_btns else 0)
        add_tooltip(
            manage_btn,
            "Configure target multipliers, max views caps, and watch goals for this channel.",
        )

        # 2. Open Channel Button
        if group.url:
            open_btn = ctk.CTkButton(
                header_btns,
                text="🔗 Open Channel",
                width=110,
                fg_color="#2E7D32",
                hover_color="#1B5E20",
            )
            open_btn.configure(
                command=with_button_feedback(open_btn, "Opening...", lambda u=group.url: webbrowser.open(u))
            )
            has_archive = not group.is_uncategorized and group.channel and not getattr(group.channel, "archived", False)
            open_btn.pack(side="left", padx=(0, 4) if has_archive else 0)
            add_tooltip(
                open_btn,
                "Open this channel's YouTube page in your web browser.",
            )

        # 3. Archive Button
        if not group.is_uncategorized and group.channel and not getattr(group.channel, "archived", False):
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

            arch_btn = ctk.CTkButton(
                header_btns,
                text="📁 Archive",
                width=80,
                fg_color="#D97706",
                hover_color="#B45309",
            )
            arch_btn.configure(
                command=with_button_feedback(arch_btn, "Archived", _archive_channel)
            )
            arch_btn.pack(side="left")
            add_tooltip(
                arch_btn,
                "Archive this channel and pause its automation.",
            )

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

            if hasattr(self, "_pending_card_queue"):
                self._pending_card_queue.append((
                    container,
                    g_idx,
                    task,
                    real_index,
                    group,
                    (not is_first and not bool(self.get_search_query() if hasattr(self, "get_search_query") else False)),
                    (not is_last and not bool(self.get_search_query() if hasattr(self, "get_search_query") else False)),
                    _move_up,
                    _move_down,
                ))
            else:
                card = self._build_task_card(
                    container,
                    g_idx + 1,
                    task,
                    real_index,
                    channel_enabled=group.channel.enabled if not group.is_uncategorized else True,
                    can_move_up=(not is_first and not bool(self.get_search_query() if hasattr(self, "get_search_query") else False)),
                    can_move_down=(not is_last and not bool(self.get_search_query() if hasattr(self, "get_search_query") else False)),
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

        noun = "short" if getattr(self, "shorts_only", False) else "video"

        btn_mv_up = ctk.CTkButton(
            reorder_frame,
            text="▲",
            width=24,
            height=20,
            corner_radius=4,
            font=ctk.CTkFont(size=11, weight="bold"),
            style="ArrowActive.TButton" if can_move_up else "ArrowDisabled.TButton",
            fg_color="#2563EB" if can_move_up else "gray25",
            hover_color="#1D4ED8" if can_move_up else "gray25",
            state="normal" if can_move_up else "disabled",
            cursor="hand2" if can_move_up else "arrow",
            command=on_move_up if can_move_up else None,
        )
        btn_mv_up.pack(side="top", pady=(0, 2))
        if can_move_up:
            add_tooltip(btn_mv_up, f"Move this {noun} up in queue priority.")

        btn_mv_down = ctk.CTkButton(
            reorder_frame,
            text="▼",
            width=24,
            height=20,
            corner_radius=4,
            font=ctk.CTkFont(size=11, weight="bold"),
            style="ArrowActive.TButton" if can_move_down else "ArrowDisabled.TButton",
            fg_color="#2563EB" if can_move_down else "gray25",
            hover_color="#1D4ED8" if can_move_down else "gray25",
            state="normal" if can_move_down else "disabled",
            cursor="hand2" if can_move_down else "arrow",
            command=on_move_down if can_move_down else None,
        )
        btn_mv_down.pack(side="bottom", pady=(2, 0))
        if can_move_down:
            add_tooltip(btn_mv_down, f"Move this {noun} down in queue priority.")

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
        rec_dur = getattr(record, "video_duration", 0.0) if record else 0.0
        task_dur = getattr(task, "duration_seconds", 0.0) or 0.0
        eff_dur = float(rec_dur if rec_dur and rec_dur > 0 else (task_dur if isinstance(task_dur, (int, float)) else 0.0))
        if current_runs == 0 and eff_dur > 0 and watch_sec >= eff_dur:
            equiv = int(watch_sec // eff_dur)
            if equiv > 0:
                current_runs = equiv

        rec_dur = getattr(record, "video_duration", 0.0) if record else 0.0
        if not isinstance(rec_dur, (int, float)):
            try:
                rec_dur = float(rec_dur)
            except Exception:
                rec_dur = 0.0
        task_dur = getattr(task, "duration_seconds", 0.0) or 0.0
        effective_duration = float(rec_dur if rec_dur > 0 else (task_dur if isinstance(task_dur, (int, float)) else 0.0))
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

        is_short_item = getattr(self, "shorts_only", False) or getattr(task, "is_short", False)

        status_prefix = ""
        if not channel_enabled and not task.enabled:
            status_prefix = "[CHANNEL & VIDEO DISABLED] "
        elif not channel_enabled:
            status_prefix = "[CHANNEL DISABLED] "
        elif not task.enabled:
            status_prefix = "[DISABLED] "
        elif is_limit_reached:
            if is_short_item:
                status_prefix = "[MAX VIEWS REACHED] "
            else:
                target_hit = task.target_multiplier > 0 and effective_duration > 0 and (
                    watch_sec >= task.target_multiplier * effective_duration
                    or round(watch_sec / effective_duration, 1) >= task.target_multiplier
                )
                views_hit = task.max_runs > 0 and current_runs >= task.max_runs
                if target_hit and views_hit:
                    status_prefix = "[TARGET & MAX VIEWS REACHED] "
                elif target_hit:
                    status_prefix = "[TARGET MULTIPLIER REACHED] "
                else:
                    status_prefix = "[MAX VIEWS REACHED] "

        mult = float(getattr(task, "target_multiplier", 0.0) or 0.0)
        target_sec = (effective_duration * mult) if (mult > 0 and not is_short_item) else 0.0

        if effective_duration <= 0.0 and getattr(task, "duration_text", ""):
            try:
                effective_duration = float(parse_duration_str(task.duration_text))
            except Exception:
                effective_duration = 0.0

        if effective_duration >= 60.0:
            length_part = f"Length: {max(1, int(round(effective_duration / 60.0)))} min"
        elif effective_duration > 0.0:
            length_part = f"Length: {int(effective_duration)}s"
        elif is_short_item:
            length_part = "Length: Short"
        else:
            length_part = "Length: Unknown"

        ratio_part = format_video_watch_ratio(watch_sec, 0.0 if is_short_item else target_sec)
        if not ratio_part or not str(ratio_part).strip():
            ratio_part = "0s"

        last_played_val = (record.last_run or record.last_completed) if record else ""
        last_played_text = format_last_played(last_played_val)
        raw_pub = getattr(task, "published_at", "")
        pub_date_text = format_published_date(raw_pub, fallback="Date: N/A")
        exact_pub_text = format_exact_published_datetime(raw_pub, fallback="Date: N/A")

        second_line_parts = [length_part, ratio_part, pub_date_text]
        stats_second_line = " | ".join(second_line_parts)

        info_text = f"#{position}. {status_prefix}{title_text}\n{stats_second_line}"
        lbl = ctk.CTkLabel(
            card,
            text=info_text,
            justify="left",
            font=ctk.CTkFont(size=10, weight="bold"),
            anchor="w",
            text_color=("gray50", "gray50") if (not task.enabled or not channel_enabled or is_limit_reached) else None,
        )
        lbl.grid(row=0, column=1, padx=10, pady=(10, 0), sticky="ew")

        tooltip_msg = f"Published: {exact_pub_text}" if exact_pub_text != "Date: N/A" else "Published date not available"
        add_tooltip(lbl, tooltip_msg)

        stats_lbl = ctk.CTkLabel(
            card,
            text=format_watch_line(
                record,
                max_runs=task.max_runs,
                use_hours=True,
                task_duration=effective_duration,
                show_multiplier=not is_short_item,
                unit="views" if is_short_item else "runs",
                last_played=last_played_text,
            ),
            justify="left",
            font=ctk.CTkFont(size=8),
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        stats_lbl.grid(row=1, column=1, padx=10, pady=(2, 6 if (task.max_runs > 0 or getattr(task, "target_multiplier", 0.0) > 0) else 10), sticky="ew")

        # Action Buttons Frame (Fixed to column 3 on the right edge)
        action_btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        action_btn_frame.grid(row=0, column=2, rowspan=2, padx=10, pady=10, sticky="e")

        target_entry = None
        if not is_short_item:
            ctk.CTkLabel(action_btn_frame, text="Target:", font=ctk.CTkFont(size=9, weight="bold")).pack(side="left", padx=(0, 2))
            target_entry = ctk.CTkEntry(
                action_btn_frame,
                width=34,
                height=24,
                placeholder_text="0",
                justify="center",
            )
            target_val = getattr(task, "target_multiplier", 0.0)
            target_entry.insert(0, str(int(target_val)) if target_val.is_integer() else f"{target_val:g}")
            target_entry.pack(side="left", padx=(0, 2))
            ctk.CTkLabel(action_btn_frame, text="x", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(action_btn_frame, text="Max Views:", font=ctk.CTkFont(size=9, weight="bold")).pack(side="left", padx=(0, 2))
        views_entry = ctk.CTkEntry(
            action_btn_frame,
            width=46,
            height=28,
            placeholder_text="10" if is_short_item else "0",
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

        if target_entry is not None:
            target_entry.bind("<FocusOut>", _revert_target)
            target_entry.bind("<Return>", _on_target_enter)
        views_entry.bind("<FocusOut>", _revert_views)
        views_entry.bind("<Return>", _on_views_enter)

        edit_task_btn = ctk.CTkButton(
            action_btn_frame,
            text="✏ Edit",
            width=52,
            font=ctk.CTkFont(size=9, weight="bold"),
            fg_color="#1F6AA5",
            hover_color="#144870",
        )
        edit_task_btn.configure(
            command=with_button_feedback(edit_task_btn, "Editing...", lambda i=index: self.open_video_modal(edit_index=i))
        )
        edit_task_btn.pack(side="left", padx=(0, 4))
        add_tooltip(edit_task_btn, "Edit this video's settings, URL, or target multiplier.")

        del_task_btn = ctk.CTkButton(
            action_btn_frame,
            text="🗑 Delete",
            width=58,
            font=ctk.CTkFont(size=9, weight="bold"),
            fg_color="#D32F2F",
            hover_color="#9A0007",
        )
        del_task_btn.configure(
            command=with_button_feedback(del_task_btn, "Deleted", lambda i=index: self.remove_task(i))
        )
        del_task_btn.pack(side="left")
        add_tooltip(del_task_btn, "Remove this video from the queue.")

        # Progress bar with dynamic color transition
        has_target = (not is_short_item) and getattr(task, "target_multiplier", 0.0) > 0 and effective_duration > 0
        has_views_cap = task.max_runs > 0
        if has_target or has_views_cap:
            r_target = (watch_sec / (task.target_multiplier * effective_duration)) if has_target else 0.0
            r_views = (current_runs / task.max_runs) if has_views_cap else 0.0
            ratio = min(1.0, max(0.0, max(r_target, r_views) if has_target else r_views))
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
