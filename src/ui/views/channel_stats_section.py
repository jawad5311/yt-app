"""Channel Performance & Watch Time Analytics Section ($10,000 build).

Provides high-end, Apple/YouTube Studio styled channel statistics, interactive
bar graphs, donut breakdown charts, KPI metric cards, and time filtering
(Per Day, Per Week, Per Month, All Time).
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ...models.channel import calculate_channel_target_seconds
from ...models.watch_stats import format_duration
from ..components import Button, Font, Frame, Label, OptionMenu
from ..theme import get_font, get_theme_colors
from ..tooltip import add_tooltip, flash_button_feedback

# Curated high-vibrancy modern color palette for distinct channel graphs
CHANNEL_PALETTE: List[str] = [
    "#2563EB",  # Electric Sapphire Blue
    "#10B981",  # Emerald Teal
    "#8B5CF6",  # Royal Violet
    "#F59E0B",  # Vibrant Amber
    "#EC4899",  # Hot Rose
    "#06B6D4",  # Ocean Cyan
    "#6366F1",  # Indigo
    "#14B8A6",  # Seafoam Teal
    "#F97316",  # Sunset Orange
    "#84CC16",  # Lime Green
]

TIME_FILTER_OPTIONS: List[str] = [
    "All Time",
    "Last 38 Days",
    "Last 7 Days",
    "Last 24 Hours",
    "Last Session",
]


def normalize_filter_name(filter_str: str) -> str:
    """Normalize any user/code string into one of the 5 standard filter keys."""
    raw = (filter_str or "").strip().lower()
    if "session" in raw:
        return "Last Session"
    if "38" in raw or "month" in raw or "30" in raw or "28" in raw:
        return "Last 38 Days"
    if "week" in raw or "7" in raw:
        return "Last 7 Days"
    if "day" in raw or "24" in raw or "today" in raw or "hour" in raw:
        return "Last 24 Hours"
    return "All Time"


def compute_channel_stats(
    channels: List[Any],
    records: Dict[str, Any],
    queued_videos: Optional[List[Any]] = None,
    session_tracker: Optional[Any] = None,
    time_filter: str = "All Time",
    current_time: Optional[datetime] = None,
    stats_store: Optional[Any] = None,
) -> Dict[str, Any]:
    """Calculate aggregated channel watch metrics filtered by the selected time period.

    Args:
        channels: List of Channel objects or channel dicts from ConfigManager.
        records: Dict of StatRecord objects from StatsStore.records.
        queued_videos: Optional list of VideoTask objects to identify regular vs shorts and targets.
        session_tracker: Optional SessionChannelTracker or dict for live session stats.
        time_filter: "All Time", "Last 38 Days", "Last 7 Days", "Last 24 Hours", or "Last Session".
        current_time: Reference datetime for filtering (defaults to datetime.now()).
        stats_store: Optional StatsStore for video duration lookup in goal calculation.

    Returns:
        Structured dictionary containing aggregated KPI metrics and per-channel stats.
    """
    now = current_time or datetime.now()
    normalized_filter = normalize_filter_name(time_filter)

    cutoff: Optional[datetime] = None
    if normalized_filter == "Last 24 Hours":
        cutoff = now - timedelta(days=1)
    elif normalized_filter == "Last 7 Days":
        cutoff = now - timedelta(days=7)
    elif normalized_filter == "Last 38 Days":
        cutoff = now - timedelta(days=38)

    # Build quick lookup for video shorts identity
    shorts_keys: set = set()
    if queued_videos:
        for t in queued_videos:
            url = getattr(t, "url", "") or ""
            is_sh = getattr(t, "is_short", None)
            if (callable(is_sh) and is_sh()) or "/shorts/" in url.lower():
                from ...models.watch_stats import resolve_video_key
                k = resolve_video_key(t)
                if k:
                    shorts_keys.add(k)
                if url:
                    shorts_keys.add(url)

    # Build channel stats lookup
    channel_data: Dict[str, Dict[str, Any]] = {}
    channel_display_names: Dict[str, str] = {}
    channel_urls: Dict[str, str] = {}
    channel_targets: Dict[str, float] = {}

    for ch in channels:
        name = getattr(ch, "name", "") if hasattr(ch, "name") else ch.get("name", "")
        if not name:
            continue
        key = name.strip().lower().lstrip("@")
        channel_display_names[key] = name
        url = getattr(ch, "url", "") if hasattr(ch, "url") else ch.get("url", "")
        channel_urls[key] = url
        target_h = getattr(ch, "target_hours", 0.0) if hasattr(ch, "target_hours") else ch.get("target_hours", 0.0)
        channel_targets[key] = float(target_h or 0.0)

        channel_data[key] = {
            "key": key,
            "name": name,
            "url": url,
            "watch_seconds": 0.0,
            "ad_seconds": 0.0,
            "runs": 0,
            "successes": 0,
            "failures": 0,
            "attempts": 0,
            "regular_vids_count": 0,
            "shorts_count": 0,
            "regular_watch_seconds": 0.0,
            "shorts_watch_seconds": 0.0,
            "last_active": "",
            "target_hours": channel_targets[key],
        }

    # Process all video records from StatsStore (skipped in Last Session mode if purely session)
    if normalized_filter != "Last Session":
        for v_key, record in (records or {}).items():
            if v_key.startswith("channel::"):
                continue  # Skip legacy channel duplicate records

            rec_channel = getattr(record, "channel", "") or ""
            rec_ch_key = rec_channel.strip().lower().lstrip("@")
            if not rec_ch_key:
                continue

            # If channel is not yet in channel_data, register it dynamically
            if rec_ch_key not in channel_data:
                channel_display_names[rec_ch_key] = rec_channel
                channel_urls[rec_ch_key] = ""
                channel_targets[rec_ch_key] = 0.0
                channel_data[rec_ch_key] = {
                    "key": rec_ch_key,
                    "name": rec_channel,
                    "url": "",
                    "watch_seconds": 0.0,
                    "ad_seconds": 0.0,
                    "runs": 0,
                    "successes": 0,
                    "failures": 0,
                    "attempts": 0,
                    "regular_vids_count": 0,
                    "shorts_count": 0,
                    "regular_watch_seconds": 0.0,
                    "shorts_watch_seconds": 0.0,
                    "last_active": "",
                    "target_hours": 0.0,
                }

            # Check timestamp for time window filter
            last_ts_str = getattr(record, "last_completed", "") or getattr(record, "last_run", "")
            rec_dt: Optional[datetime] = None
            if last_ts_str:
                try:
                    rec_dt = datetime.fromisoformat(last_ts_str)
                    if rec_dt.tzinfo is not None and now.tzinfo is None:
                        rec_dt = rec_dt.astimezone().replace(tzinfo=None)
                    elif rec_dt.tzinfo is None and now.tzinfo is not None:
                        now = now.astimezone().replace(tzinfo=None)
                except Exception:
                    rec_dt = None

            if cutoff is not None:
                if rec_dt is None or rec_dt < cutoff:
                    continue

            entry = channel_data[rec_ch_key]
            w_sec = float(getattr(record, "watch_seconds", 0.0) or 0.0)
            ad_sec = float(getattr(record, "ad_seconds", 0.0) or 0.0)
            r_runs = int(getattr(record, "runs", 0) or 0)
            r_succ = int(getattr(record, "successes", 0) or 0)
            r_fail = int(getattr(record, "failures", 0) or 0)
            r_att = int(getattr(record, "attempts", 0) or 0)

            entry["watch_seconds"] += w_sec
            entry["ad_seconds"] += ad_sec
            entry["runs"] += r_runs
            entry["successes"] += r_succ
            entry["failures"] += r_fail
            entry["attempts"] += max(r_att, r_runs)

            # Check regular video vs shorts attribution
            is_short = (
                v_key in shorts_keys
                or any(alias in shorts_keys for alias in getattr(record, "task_keys", []))
                or (getattr(record, "video_duration", 0.0) > 0 and getattr(record, "video_duration", 0.0) <= 60.0)
                or "/shorts/" in (getattr(record, "label", "") or "").lower()
            )

            if is_short:
                entry["shorts_count"] += max(1, r_runs) if (r_runs > 0 or w_sec > 0) else 0
                entry["shorts_watch_seconds"] += w_sec
            else:
                entry["regular_vids_count"] += max(1, r_runs) if (r_runs > 0 or w_sec > 0) else 0
                entry["regular_watch_seconds"] += w_sec

            if last_ts_str and last_ts_str > entry["last_active"]:
                entry["last_active"] = last_ts_str

    # Extract session data
    session_watch_map: Dict[str, float] = {}
    session_reg_vids_map: Dict[str, int] = {}
    session_shorts_map: Dict[str, int] = {}
    session_start_t: Optional[datetime] = None

    if session_tracker is not None:
        if isinstance(session_tracker, dict):
            session_watch_map = session_tracker.get("session_watch_seconds", session_tracker.get("channel_seconds", {})) or {}
            session_reg_vids_map = session_tracker.get("channel_regular_videos", {}) or {}
            session_shorts_map = session_tracker.get("channel_shorts", {}) or {}
            session_start_t = session_tracker.get("session_start_time")
        else:
            session_watch_map = getattr(session_tracker, "session_watch_seconds", {}) or {}
            session_reg_vids_map = getattr(session_tracker, "channel_regular_videos", {}) or {}
            session_shorts_map = getattr(session_tracker, "channel_shorts", {}) or {}
            session_start_t = getattr(session_tracker, "session_start_time", None)

    if normalized_filter == "Last Session":
        # In Last Session mode, metrics reflect activity in the latest session
        for ch_key, entry in channel_data.items():
            s_sec = 0.0
            r_cnt = 0
            s_cnt = 0
            for k, val in session_watch_map.items():
                if k.strip().lower().lstrip("@") == ch_key:
                    s_sec += float(val or 0.0)
            for k, val in session_reg_vids_map.items():
                if k.strip().lower().lstrip("@") == ch_key:
                    r_cnt += int(val or 0)
            for k, val in session_shorts_map.items():
                if k.strip().lower().lstrip("@") == ch_key:
                    s_cnt += int(val or 0)

            # If session_start_t is known, also include any records saved in stats_store during this session
            if session_start_t is not None:
                for v_key, record in (records or {}).items():
                    if v_key.startswith("channel::"):
                        continue
                    rec_ch = getattr(record, "channel", "") or ""
                    if rec_ch.strip().lower().lstrip("@") != ch_key:
                        continue
                    last_ts_str = getattr(record, "last_completed", "") or getattr(record, "last_run", "")
                    if not last_ts_str:
                        continue
                    try:
                        rec_dt = datetime.fromisoformat(last_ts_str)
                        if rec_dt.tzinfo is not None and session_start_t.tzinfo is None:
                            rec_dt = rec_dt.astimezone().replace(tzinfo=None)
                        elif rec_dt.tzinfo is None and session_start_t.tzinfo is not None:
                            rec_dt = rec_dt.replace(tzinfo=session_start_t.tzinfo)
                        if rec_dt >= session_start_t:
                            if s_sec == 0:
                                s_sec += float(getattr(record, "watch_seconds", 0.0) or 0.0)
                                if v_key in shorts_keys or "/shorts/" in (getattr(record, "label", "") or "").lower():
                                    s_cnt += 1
                                else:
                                    r_cnt += 1
                    except Exception:
                        pass

            entry["watch_seconds"] = s_sec
            entry["regular_vids_count"] = r_cnt
            entry["shorts_count"] = s_cnt
            entry["regular_watch_seconds"] = s_sec if (r_cnt > 0 or s_cnt == 0) else 0.0
            entry["shorts_watch_seconds"] = s_sec if (s_cnt > 0 and r_cnt == 0) else 0.0
            entry["runs"] = r_cnt + s_cnt if (r_cnt + s_cnt) > 0 else (1 if s_sec > 0 else 0)
            entry["successes"] = entry["runs"]
            entry["attempts"] = entry["runs"]
            entry["failures"] = 0
    else:
        # Include live session tracker watch seconds for current session in non-session filters
        if session_watch_map:
            for ch_canon, s_sec in session_watch_map.items():
                if s_sec > 0:
                    clean_k = ch_canon.strip().lower().lstrip("@")
                    if clean_k in channel_data:
                        channel_data[clean_k]["watch_seconds"] += float(s_sec)
                        channel_data[clean_k]["regular_watch_seconds"] += float(s_sec)

    # Calculate percentages and totals
    total_watch_seconds = sum(c["watch_seconds"] for c in channel_data.values())
    total_runs = sum(c["runs"] for c in channel_data.values())
    total_successes = sum(c["successes"] for c in channel_data.values())
    total_regular_vids = sum(c["regular_vids_count"] for c in channel_data.values())
    total_shorts = sum(c["shorts_count"] for c in channel_data.values())
    total_regular_watch_seconds = sum(c["regular_watch_seconds"] for c in channel_data.values())
    total_shorts_watch_seconds = sum(c["shorts_watch_seconds"] for c in channel_data.values())

    channel_list: List[Dict[str, Any]] = list(channel_data.values())
    # Sort by watch time descending
    channel_list.sort(key=lambda x: (x["watch_seconds"], x["runs"]), reverse=True)

    # Assign color, percentage share, formatted strings, and watch time goals
    for idx, c in enumerate(channel_list):
        c["rank"] = idx + 1
        c["color"] = CHANNEL_PALETTE[idx % len(CHANNEL_PALETTE)]
        c["watch_formatted"] = format_duration(c["watch_seconds"])
        c["watch_hours_float"] = round(c["watch_seconds"] / 3600.0, 2)
        c["percentage_share"] = (
            round((c["watch_seconds"] / total_watch_seconds) * 100.0, 1) if total_watch_seconds > 0 else 0.0
        )
        c["success_rate_pct"] = (
            round((c["successes"] / c["runs"]) * 100.0, 1) if c["runs"] > 0 else (100.0 if c["successes"] > 0 else 0.0)
        )
        t_hours = c["target_hours"]
        c["target_progress_pct"] = (
            min(100.0, round((c["watch_seconds"] / (t_hours * 3600.0)) * 100.0, 1)) if t_hours > 0 else 0.0
        )

        # Watch Time Goal based on video duration and multipliers
        ch_raw_obj = next(
            (
                ch for ch in channels
                if (
                    getattr(ch, "name", "")
                    if hasattr(ch, "name")
                    else (ch.get("name", "") if isinstance(ch, dict) else "")
                ).strip().lower().lstrip("@") == c["key"]
            ),
            c["name"],
        )
        ch_url = c.get("url", "")
        c["goal_seconds"] = calculate_channel_target_seconds(
            channel_name_or_obj=ch_raw_obj,
            tasks=queued_videos,
            stats_store=stats_store,
            channel_url=ch_url,
        )
        c["goal_formatted"] = format_duration(c["goal_seconds"])
        c["remaining_seconds"] = max(0.0, c["goal_seconds"] - c["watch_seconds"])
        c["remaining_formatted"] = format_duration(c["remaining_seconds"])
        c["goal_progress_pct"] = (
            min(100.0, round((c["watch_seconds"] / c["goal_seconds"]) * 100.0, 1))
            if c["goal_seconds"] > 0
            else (100.0 if c["watch_seconds"] > 0 else 0.0)
        )

    top_channel_name = channel_list[0]["name"] if channel_list and channel_list[0]["watch_seconds"] > 0 else "—"
    top_channel_time = channel_list[0]["watch_formatted"] if channel_list and channel_list[0]["watch_seconds"] > 0 else "0s"
    top_channel_share = f"{channel_list[0]['percentage_share']}%" if channel_list and channel_list[0]["watch_seconds"] > 0 else "0%"

    active_channels_count = sum(1 for c in channel_list if c["watch_seconds"] > 0)

    total_goal_seconds = sum(c["goal_seconds"] for c in channel_list)
    total_remaining_seconds = max(0.0, total_goal_seconds - total_watch_seconds)
    total_goal_progress_pct = (
        min(100.0, round((total_watch_seconds / total_goal_seconds) * 100.0, 1))
        if total_goal_seconds > 0
        else (100.0 if total_watch_seconds > 0 else 0.0)
    )

    return {
        "time_filter": normalized_filter,
        "total_watch_seconds": total_watch_seconds,
        "total_watch_formatted": format_duration(total_watch_seconds),
        "total_watch_hours_float": round(total_watch_seconds / 3600.0, 2),
        "total_runs": total_runs,
        "total_successes": total_successes,
        "total_regular_vids": total_regular_vids,
        "total_shorts": total_shorts,
        "total_regular_watch_seconds": total_regular_watch_seconds,
        "total_shorts_watch_seconds": total_shorts_watch_seconds,
        "top_channel_name": top_channel_name,
        "top_channel_time": top_channel_time,
        "top_channel_share": top_channel_share,
        "active_channels_count": active_channels_count,
        "total_channels_count": len(channel_list),
        "total_goal_seconds": total_goal_seconds,
        "total_goal_formatted": format_duration(total_goal_seconds),
        "total_remaining_seconds": total_remaining_seconds,
        "total_remaining_formatted": format_duration(total_remaining_seconds),
        "total_goal_progress_pct": total_goal_progress_pct,
        "channels": channel_list,
    }


class ChannelBarChart(tk.Canvas):
    """High-DPI Canvas-based horizontal bar chart for channel watch time."""

    def __init__(self, parent, height: int = 240, **kwargs):
        self.colors = get_theme_colors()
        super().__init__(
            parent,
            height=height,
            background=self.colors.get("card_bg", "#202024"),
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        self.channel_items: List[Dict[str, Any]] = []
        self._tooltip_window: Optional[tk.Toplevel] = None
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event=None) -> None:
        self.redraw()

    def set_data(self, channels: List[Dict[str, Any]]) -> None:
        self.channel_items = list(channels)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        self.colors = get_theme_colors()
        bg_color = self.colors.get("card_bg", "#202024")
        self.configure(background=bg_color)

        width = self.winfo_width()
        if width <= 50:
            width = 580

        is_dark = self.colors.get("bg") != "#f5f5f7"
        text_color = "#FFFFFF" if is_dark else "#1C1C1E"
        muted_color = "#8E8E93"
        track_color = "#2A2A2E" if is_dark else "#E5E5EA"
        grid_color = "#2E2E34" if is_dark else "#F0F0F4"

        channels = self.channel_items
        if not channels:
            self.create_text(
                width / 2,
                110,
                text="No channels added yet.",
                fill=muted_color,
                font=get_font(size=11, weight="bold"),
            )
            self.create_text(
                width / 2,
                135,
                text="Add channels in the Channels tab to begin tracking watch time.",
                fill=muted_color,
                font=get_font(size=9),
            )
            return

        max_sec = max((c["watch_seconds"] for c in channels), default=0.0)
        # Ensure scale is non-zero
        scale_max = max_sec if max_sec > 0 else 3600.0

        left_margin = 170
        right_margin = 130
        bar_area_width = max(80, width - left_margin - right_margin)

        # Draw vertical scale grid lines & labels (0%, 25%, 50%, 75%, 100%)
        top_y = 20
        grid_bottom_y = top_y + len(channels) * 44 + 8

        for pct in (0.0, 0.25, 0.5, 0.75, 1.0):
            gx = left_margin + bar_area_width * pct
            self.create_line(gx, top_y - 2, gx, grid_bottom_y, fill=grid_color, dash=(2, 3), width=1)
            tick_val = scale_max * pct
            tick_text = format_duration(tick_val)
            self.create_text(
                gx,
                grid_bottom_y + 12,
                text=tick_text,
                fill=muted_color,
                font=get_font(size=8),
                anchor="center",
            )

        # Draw each channel bar
        for idx, ch in enumerate(channels):
            y_center = top_y + idx * 44 + 18
            bar_height = 20
            y0 = y_center - bar_height / 2
            y1 = y_center + bar_height / 2

            color = ch.get("color", CHANNEL_PALETTE[idx % len(CHANNEL_PALETTE)])

            # Channel Avatar Initial Circle
            initial = (ch["name"][:1] or "?").upper()
            avatar_x = 24
            self.create_oval(avatar_x - 11, y_center - 11, avatar_x + 11, y_center + 11, fill=color, outline="")
            self.create_text(
                avatar_x,
                y_center,
                text=initial,
                fill="#FFFFFF",
                font=get_font(size=8, weight="bold"),
                anchor="center",
            )

            # Channel Name (truncated if long)
            display_name = ch["name"]
            if len(display_name) > 17:
                display_name = display_name[:16] + "…"
            self.create_text(
                avatar_x + 18,
                y_center,
                text=display_name,
                fill=text_color,
                font=get_font(size=9, weight="bold"),
                anchor="w",
            )

            # Background Track Bar (Rounded)
            track_x0 = left_margin
            track_x1 = left_margin + bar_area_width
            self._draw_rounded_rect(track_x0, y0, track_x1, y1, radius=6, fill=track_color, outline="")

            # Active Progress Bar
            w_sec = ch["watch_seconds"]
            bar_w = (w_sec / scale_max) * bar_area_width if scale_max > 0 else 0
            if bar_w > 0:
                bar_x1 = track_x0 + max(8, bar_w)
                self._draw_rounded_rect(track_x0, y0, bar_x1, y1, radius=6, fill=color, outline="")

            # Right Value Text (e.g., "39m 06s · 59.8%")
            val_text = f"{ch['watch_formatted']}"
            if max_sec > 0 and ch["percentage_share"] > 0:
                val_text += f" ({ch['percentage_share']}%)"
            elif max_sec == 0:
                val_text = "0s"

            self.create_text(
                track_x1 + 10,
                y_center,
                text=val_text,
                fill=text_color,
                font=get_font(size=9, weight="bold"),
                anchor="w",
            )

    def _draw_rounded_rect(
        self, x0: float, y0: float, x1: float, y1: float, radius: float = 6, **kwargs
    ) -> int:
        """Draw a smooth rounded rectangle on canvas using polygon coordinates."""
        radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
        points = [
            x0 + radius, y0,
            x1 - radius, y0,
            x1, y0,
            x1, y0 + radius,
            x1, y1 - radius,
            x1, y1,
            x1 - radius, y1,
            x0 + radius, y1,
            x0, y1,
            x0, y1 - radius,
            x0, y0 + radius,
            x0, y0,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)


class ChannelDonutChart(tk.Canvas):
    """High-DPI Canvas-based circular Donut / Ring chart showing share of watch time."""

    def __init__(self, parent, size: int = 240, **kwargs):
        self.colors = get_theme_colors()
        super().__init__(
            parent,
            width=size,
            height=size,
            background=self.colors.get("card_bg", "#202024"),
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        self.channel_items: List[Dict[str, Any]] = []
        self.total_watch_seconds: float = 0.0
        self.total_watch_formatted: str = "0s"
        self.regular_ratio_pct: float = 0.0
        self.shorts_ratio_pct: float = 0.0
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event=None) -> None:
        self.redraw()

    def set_data(
        self,
        channels: List[Dict[str, Any]],
        total_watch_seconds: float,
        total_watch_formatted: str,
        regular_watch_seconds: float = 0.0,
        shorts_watch_seconds: float = 0.0,
    ) -> None:
        self.channel_items = list(channels)
        self.total_watch_seconds = total_watch_seconds
        self.total_watch_formatted = total_watch_formatted

        tot = regular_watch_seconds + shorts_watch_seconds
        if tot > 0:
            self.regular_ratio_pct = round((regular_watch_seconds / tot) * 100.0, 1)
            self.shorts_ratio_pct = round((shorts_watch_seconds / tot) * 100.0, 1)
        else:
            self.regular_ratio_pct = 0.0
            self.shorts_ratio_pct = 0.0

        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        self.colors = get_theme_colors()
        bg_color = self.colors.get("card_bg", "#202024")
        self.configure(background=bg_color)

        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 50:
            width = 240
        if height <= 50:
            height = 240

        is_dark = self.colors.get("bg") != "#f5f5f7"
        text_color = "#FFFFFF" if is_dark else "#1C1C1E"
        muted_color = "#8E8E93"
        track_color = "#2A2A2E" if is_dark else "#E5E5EA"

        cx = width / 2
        cy = (height / 2) - 10
        outer_r = min(width, height) * 0.36
        inner_r = outer_r * 0.65

        # Empty state or 0 watch time
        if not self.channel_items or self.total_watch_seconds <= 0:
            # Draw neutral ring
            self.create_oval(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                fill=track_color, outline=""
            )
            self.create_oval(
                cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
                fill=bg_color, outline=""
            )
            self.create_text(
                cx, cy - 4,
                text="0s",
                fill=text_color,
                font=get_font(size=14, weight="bold"),
                anchor="center",
            )
            self.create_text(
                cx, cy + 12,
                text="NO DATA",
                fill=muted_color,
                font=get_font(size=7, weight="bold"),
                anchor="center",
            )
            return

        # Draw segmented colored donut arcs
        current_angle = 90.0  # Start from top (12 o'clock)
        for idx, ch in enumerate(self.channel_items):
            pct = ch["watch_seconds"] / self.total_watch_seconds
            if pct <= 0.001:
                continue
            extent = pct * 360.0
            color = ch.get("color", CHANNEL_PALETTE[idx % len(CHANNEL_PALETTE)])

            # Draw outer arc slice
            # Gap of 1.5 degrees between slices for modern segmented aesthetic
            gap = 1.5 if len(self.channel_items) > 1 else 0.0
            self.create_arc(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                start=current_angle,
                extent=-(extent - gap),
                fill=color,
                outline="",
            )
            current_angle -= extent

        # Center cutout
        self.create_oval(
            cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
            fill=bg_color, outline=""
        )

        # Center text: Total watch time & "TOTAL"
        self.create_text(
            cx, cy - 6,
            text=self.total_watch_formatted,
            fill=text_color,
            font=get_font(size=13, weight="bold"),
            anchor="center",
        )
        self.create_text(
            cx, cy + 12,
            text="TOTAL DELIVERED",
            fill=muted_color,
            font=get_font(size=7, weight="bold"),
            anchor="center",
        )

        # Bottom split bar: Regular Videos vs Shorts
        bar_y = height - 20
        bar_w = width * 0.8
        bx0 = (width - bar_w) / 2
        bx1 = bx0 + bar_w
        if self.regular_ratio_pct > 0 or self.shorts_ratio_pct > 0:
            reg_w = (self.regular_ratio_pct / 100.0) * bar_w
            # Video part (Blue)
            self.create_rectangle(bx0, bar_y, bx0 + reg_w, bar_y + 6, fill="#3B82F6", outline="")
            # Shorts part (Red)
            self.create_rectangle(bx0 + reg_w, bar_y, bx1, bar_y + 6, fill="#EF4444", outline="")

            legend_text = f"🎥 Vids: {self.regular_ratio_pct}%   ⚡ Shorts: {self.shorts_ratio_pct}%"
            self.create_text(
                width / 2, bar_y - 8,
                text=legend_text,
                fill=muted_color,
                font=get_font(size=8, weight="bold"),
                anchor="center",
            )


class ChannelStatsSection(Frame):
    """Premium $10,000 Apple HIG / YouTube Studio styled Channel Analytics Section."""

    def __init__(self, parent, app_controller):
        super().__init__(parent, style="AppleCard.TFrame")
        self.app_controller = app_controller
        self.current_time_filter = "All Time"

        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_kpi_cards()
        self._build_charts_container()
        self._build_channel_details_list()

        # Initial data render
        self.refresh()

    # ------------------------------------------------------------- 1. Header
    def _build_header(self) -> None:
        self.header_card = Frame(self, style="AppleCardInner.TFrame")
        self.header_card.pack(fill="x", padx=20, pady=(16, 6))
        self.header_card.grid_columnconfigure(0, weight=1)

        left_header = Frame(self.header_card, style="Transparent.TFrame")
        left_header.pack(side="left", anchor="w")

        # Title
        self.section_title = Label(
            left_header,
            text="📈  CHANNEL PERFORMANCE & WATCH TIME",
            font=Font(size=9, weight="bold"),
            style="AppleSectionTitle.TLabel",
        )
        self.section_title.pack(anchor="w")

        # Subtitle
        self.section_subtitle = Label(
            left_header,
            text="Visual analytics, multi-period watch hour comparison, and channel delivery share",
            font=Font(size=8),
            style="AppleCardMuted.TLabel",
        )
        self.section_subtitle.pack(anchor="w", pady=(2, 0))

        # Right Controls: Period Dropdown Filter + Refresh Button
        controls_frame = Frame(self.header_card, style="Transparent.TFrame")
        controls_frame.pack(side="right", anchor="e")

        filter_lbl = Label(
            controls_frame,
            text="Period:",
            font=Font(size=9, weight="bold"),
            style="AppleCard.TLabel",
        )
        filter_lbl.pack(side="left", padx=(0, 6))

        # Dropdown OptionMenu for time filter
        self.period_dropdown = OptionMenu(
            controls_frame,
            values=TIME_FILTER_OPTIONS,
            command=self._on_period_selected,
            width=130,
        )
        self.period_dropdown.set("All Time")
        self.period_dropdown.pack(side="left", padx=(0, 10))
        add_tooltip(
            self.period_dropdown,
            "Filter channel watch metrics by timeframe:\n• All Time: Lifetime total watch hours\n• Last 38 Days: Activity in last 38 days\n• Last 7 Days: Activity in last 7 days\n• Last 24 Hours: Activity in last 24 hours / today\n• Last Session: Watch stats generated in the active or most recent session",
        )

        self.refresh_btn = Button(
            controls_frame,
            text="🔄 Refresh",
            font=Font(size=9, weight="bold"),
            style="ApplePrimary.TButton",
            width=90,
            command=self._on_manual_refresh,
            cursor="hand2",
        )
        self.refresh_btn.pack(side="left")
        add_tooltip(self.refresh_btn, "Recalculate channel watch time and refresh charts.")

        # Divider line
        self.divider = Frame(self, style="AppleDivider.TFrame", height=1)
        self.divider.pack(fill="x", padx=20, pady=(6, 12))

    # ------------------------------------------------------------- 2. KPI Cards
    def _build_kpi_cards(self) -> None:
        self.kpi_grid = Frame(self, style="Transparent.TFrame")
        self.kpi_grid.pack(fill="x", padx=16, pady=(0, 12))
        self.kpi_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.kpi_watch_time = self._create_mini_kpi(
            parent=self.kpi_grid,
            col=0,
            icon="⏱",
            title="PERIOD WATCH TIME",
            initial_val="0s",
            footer="Delivered in selected period",
            accent_color="#3B82F6",
        )
        self.kpi_active_channels = self._create_mini_kpi(
            parent=self.kpi_grid,
            col=1,
            icon="📺",
            title="ACTIVE CHANNELS",
            initial_val="0",
            footer="Generating watch time",
            accent_color="#10B981",
        )
        self.kpi_tasks = self._create_mini_kpi(
            parent=self.kpi_grid,
            col=2,
            icon="🎯",
            title="COMPLETED PLAYS",
            initial_val="0 Runs",
            footer="0 Vids · 0 Shorts",
            accent_color="#F59E0B",
        )
        self.kpi_top_channel = self._create_mini_kpi(
            parent=self.kpi_grid,
            col=3,
            icon="🎯",
            title="TOTAL WATCH TIME GOAL",
            initial_val="0s",
            footer="Combined channels target",
            accent_color="#8B5CF6",
        )

    def _create_mini_kpi(
        self,
        parent: Frame,
        col: int,
        icon: str,
        title: str,
        initial_val: str,
        footer: str,
        accent_color: str,
    ) -> Label:
        card = Frame(parent, style="AppleInset.TFrame")
        card.grid(row=0, column=col, padx=4, pady=0, sticky="nsew")

        # Header title
        header_f = Frame(card, style="AppleCardInner.TFrame")
        header_f.pack(fill="x", padx=12, pady=(10, 2))

        t_lbl = Label(
            header_f,
            text=f"{icon}  {title}",
            font=Font(size=7, weight="bold"),
            style="AppleCategory.TLabel",
        )
        t_lbl.pack(side="left", anchor="w")

        # Value
        val_f = Frame(card, style="AppleCardInner.TFrame")
        val_f.pack(fill="x", padx=12, pady=(2, 2))

        val_lbl = Label(
            val_f,
            text=initial_val,
            font=Font(size=14, weight="bold"),
            style="AppleMetric.TLabel",
        )
        val_lbl.configure(text_color=accent_color)
        val_lbl.pack(side="left", anchor="w")

        # Footer
        footer_f = Frame(card, style="AppleCardInner.TFrame")
        footer_f.pack(fill="x", padx=12, pady=(0, 10))

        foot_lbl = Label(
            footer_f,
            text=footer,
            font=Font(size=7),
            style="AppleMetricFooter.TLabel",
        )
        foot_lbl.pack(side="left", anchor="w")

        val_lbl.footer_lbl = foot_lbl
        return val_lbl

    # ------------------------------------------------------------- 3. Charts
    def _build_charts_container(self) -> None:
        self.charts_card = Frame(self, style="AppleCardInner.TFrame")
        self.charts_card.pack(fill="x", padx=20, pady=(0, 12))
        self.charts_card.grid_columnconfigure(0, weight=3)
        self.charts_card.grid_columnconfigure(1, weight=2)

        # Left Column: Bar Chart (Horizontal rounded bars for channels)
        self.bar_box = Frame(self.charts_card, style="AppleInset.TFrame")
        self.bar_box.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")

        bar_hdr = Frame(self.bar_box, style="AppleCardInner.TFrame")
        bar_hdr.pack(fill="x", padx=14, pady=(10, 4))

        Label(
            bar_hdr,
            text="📊  WATCH TIME BY CHANNEL",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        self.bar_chart = ChannelBarChart(self.bar_box, height=220)
        self.bar_chart.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Right Column: Donut Chart & Content Type Ratio
        self.donut_box = Frame(self.charts_card, style="AppleInset.TFrame")
        self.donut_box.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")

        donut_hdr = Frame(self.donut_box, style="AppleCardInner.TFrame")
        donut_hdr.pack(fill="x", padx=14, pady=(10, 4))

        Label(
            donut_hdr,
            text="🍩  SHARE OF WATCH TIME",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        self.donut_chart = ChannelDonutChart(self.donut_box, size=220)
        self.donut_chart.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ------------------------------------------------------------- 4. Details List
    def _build_channel_details_list(self) -> None:
        self.details_header_f = Frame(self, style="AppleCardInner.TFrame")
        self.details_header_f.pack(fill="x", padx=20, pady=(4, 6))

        Label(
            self.details_header_f,
            text="📋  CHANNEL BREAKDOWN & INTELLIGENCE",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        # Container for channel item rows
        self.channel_rows_container = Frame(self, style="AppleCardInner.TFrame")
        self.channel_rows_container.pack(fill="x", padx=20, pady=(0, 16))

    # ------------------------------------------------------------- Events & Refresh
    def _on_period_selected(self, chosen_period: str) -> None:
        self.current_time_filter = chosen_period
        self.refresh()

    def _on_manual_refresh(self) -> None:
        flash_button_feedback(self.refresh_btn, "Updated!", duration_ms=1500, original_text="🔄 Refresh")
        self.refresh()

    def refresh(self, time_filter: Optional[str] = None) -> None:
        """Query fresh channel models and video statistics to update all charts and cards."""
        if time_filter is not None:
            self.current_time_filter = time_filter
            if hasattr(self, "period_dropdown"):
                try:
                    self.period_dropdown.set(time_filter)
                except Exception:
                    pass

        # Fetch channel objects from config manager
        config_mgr = getattr(self.app_controller, "config_mgr", None)
        raw_channels = config_mgr.get_channels() if config_mgr else []

        # Fetch video records from stats store
        stats_store = getattr(self.app_controller, "stats_store", None)
        records = stats_store.all() if stats_store else {}

        # Fetch queued videos
        queued_videos = getattr(self.app_controller, "queued_video_tasks", [])

        # Fetch session tracker for live session watch time
        session_tracker = getattr(self.app_controller, "session_tracker", None)

        stats = compute_channel_stats(
            channels=raw_channels,
            records=records,
            queued_videos=queued_videos,
            session_tracker=session_tracker,
            time_filter=self.current_time_filter,
            stats_store=stats_store,
        )

        self._render_stats(stats)

    def _render_stats(self, stats: Dict[str, Any]) -> None:
        """Update KPI cards, Bar Chart, Donut Chart, and Channel row cards."""
        # 1. Update KPI Cards
        self.kpi_watch_time.configure(text=stats["total_watch_formatted"])
        self.kpi_watch_time.footer_lbl.configure(text=f"{stats['total_watch_hours_float']} hours in {stats['time_filter']}")

        self.kpi_active_channels.configure(text=f"{stats['active_channels_count']} Channels")
        self.kpi_active_channels.footer_lbl.configure(
            text=f"{stats['active_channels_count']} of {stats['total_channels_count']} channels generating time"
        )

        self.kpi_tasks.configure(text=f"{stats['total_runs']} Runs")
        self.kpi_tasks.footer_lbl.configure(
            text=f"{stats['total_regular_vids']} Vids · {stats['total_shorts']} Shorts"
        )

        self.kpi_top_channel.configure(text=stats["total_goal_formatted"])
        if stats.get("total_goal_seconds", 0) > 0:
            if stats.get("total_remaining_seconds", 0) > 0:
                goal_footer = f"Remaining: {stats['total_remaining_formatted']} ({stats['total_goal_progress_pct']}% reached)"
            else:
                goal_footer = f"Target Reached! ({stats['total_goal_progress_pct']}%)"
        else:
            goal_footer = "No goals configured"
        self.kpi_top_channel.footer_lbl.configure(text=goal_footer)

        # 2. Update Charts
        channels_list = stats["channels"]
        self.bar_chart.set_data(channels_list)
        self.donut_chart.set_data(
            channels=channels_list,
            total_watch_seconds=stats["total_watch_seconds"],
            total_watch_formatted=stats["total_watch_formatted"],
            regular_watch_seconds=stats["total_regular_watch_seconds"],
            shorts_watch_seconds=stats["total_shorts_watch_seconds"],
        )

        # 3. Update Channel Rows
        for child in list(self.channel_rows_container.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

        if not channels_list:
            empty_lbl = Label(
                self.channel_rows_container,
                text="No channels configured. Click '+ Add Channel' in the Channels view to get started.",
                font=Font(size=9),
                style="AppleCardMuted.TLabel",
            )
            empty_lbl.pack(anchor="w", pady=6)
            return

        for ch in channels_list:
            self._render_channel_row(self.channel_rows_container, ch)

    def _render_channel_row(self, parent: Frame, ch: Dict[str, Any]) -> None:
        """Render a sleek individual channel intelligence card."""
        row_frame = Frame(parent, style="AppleInset.TFrame")
        row_frame.pack(fill="x", pady=3)
        row_frame.grid_columnconfigure(1, weight=1)

        # Left avatar with rank
        avatar_f = Frame(row_frame, style="Transparent.TFrame", width=36)
        avatar_f.pack(side="left", padx=(10, 8), pady=8)

        rank_lbl = Label(
            avatar_f,
            text=f"#{ch['rank']}",
            font=Font(size=9, weight="bold"),
            style="AppleCategory.TLabel",
        )
        rank_lbl.configure(text_color=ch["color"])
        rank_lbl.pack(anchor="center")

        # Channel Name & Metrics
        info_f = Frame(row_frame, style="Transparent.TFrame")
        info_f.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        top_line = Frame(info_f, style="Transparent.TFrame")
        top_line.pack(fill="x", anchor="w")

        name_lbl = Label(
            top_line,
            text=ch["name"],
            font=Font(size=10, weight="bold"),
            style="AppleCard.TLabel",
        )
        name_lbl.pack(side="left", anchor="w")

        if ch.get("url"):
            url_btn = Button(
                top_line,
                text="🔗",
                width=24,
                font=Font(size=8),
                style="AppleDefault.TButton",
                cursor="hand2",
                command=lambda u=ch["url"]: webbrowser.open(u),
            )
            url_btn.pack(side="left", padx=(6, 0))
            add_tooltip(url_btn, f"Open {ch['name']} on YouTube:\n{ch['url']}")

        # Sub line
        sub_text = (
            f"▶ {ch['runs']} runs  ·  {ch['regular_vids_count']} vids  ·  "
            f"{ch['shorts_count']} shorts  ·  {ch['success_rate_pct']}% success"
        )
        sub_lbl = Label(
            info_f,
            text=sub_text,
            font=Font(size=8),
            style="AppleCardMuted.TLabel",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # Right watch time delivery pill
        right_f = Frame(row_frame, style="Transparent.TFrame")
        right_f.pack(side="right", padx=(8, 14), pady=8)

        time_lbl = Label(
            right_f,
            text=ch["watch_formatted"],
            font=Font(size=11, weight="bold"),
            style="AppleMetric.TLabel",
        )
        time_lbl.configure(text_color=ch["color"])
        time_lbl.pack(anchor="e")

        share_lbl = Label(
            right_f,
            text=f"{ch['percentage_share']}% share",
            font=Font(size=7),
            style="AppleMetricFooter.TLabel",
        )
        share_lbl.pack(anchor="e")

    def apply_theme(self, mode: Optional[str] = None) -> None:
        """Repaint graphs and styles matching active Dark or Light appearance mode."""
        if hasattr(self, "bar_chart") and self.bar_chart.winfo_exists():
            self.bar_chart.redraw()
        if hasattr(self, "donut_chart") and self.donut_chart.winfo_exists():
            self.donut_chart.redraw()
