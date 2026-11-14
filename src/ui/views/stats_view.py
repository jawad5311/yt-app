"""Channel Stats & Analytics View (Positioned between Dashboard and Channels).

Dedicated first-class analytics tab providing:
- Top Channel Selector dropdown with "All Channels" overview or single-channel deep-dive
- Timeframe filter dropdown (All Time, Per Month, Per Week, Per Day) aligned with app theme
- Interactive horizontal bar charts and donut audience share charts
- Detailed channel intelligence cards with one-click drill-down
- Single-channel deep-dive mode with video-level watch time breakdown and goal tracking
"""
from __future__ import annotations

import webbrowser
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ...models.watch_stats import format_duration
from ..components import AppleDropdownMenu, Button, Font, Frame, Label, ScrollableFrame
from ..tooltip import add_tooltip, flash_button_feedback
from .channel_stats_section import (
    ChannelBarChart,
    ChannelDonutChart,
    compute_channel_stats,
    normalize_filter_name,
)


def get_channel_video_records(
    channel_name: str,
    records: Dict[str, Any],
    queued_videos: Optional[List[Any]] = None,
    time_filter: str = "All Time",
    current_time: Optional[datetime] = None,
    session_start_time: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return individual video playback records belonging to a specific channel filtered by timeframe."""
    now = current_time or datetime.now()
    normalized_filter = normalize_filter_name(time_filter)

    cutoff: Optional[datetime] = None
    if normalized_filter == "Last 24 Hours":
        cutoff = now - timedelta(days=1)
    elif normalized_filter == "Last 7 Days":
        cutoff = now - timedelta(days=7)
    elif normalized_filter == "Last 38 Days":
        cutoff = now - timedelta(days=38)
    elif normalized_filter == "Last Session":
        cutoff = session_start_time or (now - timedelta(hours=4))

    clean_target = (channel_name or "").strip().lower().lstrip("@")
    if not clean_target:
        return []

    # Map video URLs from queued_videos for title and link resolution
    url_map: Dict[str, Any] = {}
    if queued_videos:
        for t in queued_videos:
            u = getattr(t, "url", "") or ""
            if u:
                url_map[u] = t
            from ...models.watch_stats import resolve_video_key
            k = resolve_video_key(t)
            if k:
                url_map[k] = t

    results: List[Dict[str, Any]] = []

    for v_key, rec in (records or {}).items():
        if v_key.startswith("channel::"):
            continue
        rec_ch = (getattr(rec, "channel", "") or "").strip().lower().lstrip("@")
        if rec_ch != clean_target:
            continue

        last_ts_str = getattr(rec, "last_completed", "") or getattr(rec, "last_run", "")
        if cutoff is not None:
            if not last_ts_str:
                continue
            try:
                rec_dt = datetime.fromisoformat(last_ts_str)
                if rec_dt.tzinfo is not None and now.tzinfo is None:
                    rec_dt = rec_dt.astimezone().replace(tzinfo=None)
                elif rec_dt.tzinfo is None and now.tzinfo is not None:
                    now = now.astimezone().replace(tzinfo=None)
                if rec_dt < cutoff:
                    continue
            except Exception:
                continue

        w_sec = float(getattr(rec, "watch_seconds", 0.0) or 0.0)
        lbl = getattr(rec, "label", "") or v_key
        dur = float(getattr(rec, "video_duration", 0.0) or 0.0)

        # Look up URL and short status
        matched_task = url_map.get(v_key)
        if not matched_task:
            for alias in getattr(rec, "task_keys", []):
                if alias in url_map:
                    matched_task = url_map[alias]
                    break

        url = getattr(matched_task, "url", "") if matched_task else (
            f"https://www.youtube.com/watch?v={v_key}" if not v_key.startswith("http") and not v_key.startswith("url:") and not v_key.startswith("task:") else ""
        )
        if v_key.startswith("url:"):
            url = v_key[4:]

        is_short = (
            (matched_task is not None and getattr(matched_task, "is_short", lambda: False)())
            or "/shorts/" in (url or "").lower()
            or "/shorts/" in lbl.lower()
            or (0 < dur <= 60.0)
        )

        results.append({
            "video_key": v_key,
            "title": lbl,
            "url": url,
            "is_short": is_short,
            "watch_seconds": w_sec,
            "watch_formatted": format_duration(w_sec),
            "runs": int(getattr(rec, "runs", 0) or 0),
            "successes": int(getattr(rec, "successes", 0) or 0),
            "video_duration": dur,
            "duration_formatted": format_duration(dur) if dur > 0 else "—",
            "last_completed": last_ts_str or "—",
        })

    # Sort videos by watch time descending
    results.sort(key=lambda x: (x["watch_seconds"], x["runs"]), reverse=True)
    return results


class StatsView(ScrollableFrame):
    """Channel Stats & Analytics View (positioned between Dashboard and Channels)."""

    def __init__(self, parent, app_controller):
        super().__init__(parent, style="TFrame")
        self.app_controller = app_controller

        self.selected_channel_filter = "All Channels"  # "All Channels" or specific channel name
        self.current_time_filter = "All Time"

        self._active_dropdown_menu: Optional[AppleDropdownMenu] = None

        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_kpi_cards()

        # Dynamic Content Container
        self.content_frame = Frame(self, style="Transparent.TFrame")
        self.content_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.content_frame.grid_columnconfigure(0, weight=1)

        self.refresh()

    # ------------------------------------------------------------- 1. Header
    def _build_header(self) -> None:
        self.header_card = Frame(self, style="AppleCard.TFrame")
        self.header_card.pack(fill="x", padx=16, pady=(16, 12))
        self.header_card.grid_columnconfigure(0, weight=1)

        inner = Frame(self.header_card, style="AppleCardInner.TFrame")
        inner.pack(fill="x", padx=20, pady=16)

        left_f = Frame(inner, style="Transparent.TFrame")
        left_f.pack(side="left", anchor="w")

        # Large Apple Title
        self.title_label = Label(
            left_f,
            text="Channel Stats & Analytics",
            font=Font(size=22, weight="bold"),
            style="AppleHeader.TLabel",
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = Label(
            left_f,
            text="Channel-Level Watch Time Intelligence, Comparison Graphs & Delivery Reports",
            font=Font(size=9),
            style="AppleSubtitle.TLabel",
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # Right Controls: Channel Selector + Period Filter + Refresh
        right_f = Frame(inner, style="Transparent.TFrame")
        right_f.pack(side="right", anchor="e")

        # 1. Channel Selector Pill Button
        chan_lbl = Label(right_f, text="Channel:", font=Font(size=9, weight="bold"), style="AppleCard.TLabel")
        chan_lbl.pack(side="left", padx=(0, 6))

        self.channel_btn = Button(
            right_f,
            text="📺  All Channels ▾",
            font=Font(size=9, weight="bold"),
            style="AppleInset.TButton",
            width=160,
            cursor="hand2",
            command=self._open_channel_dropdown,
        )
        self.channel_btn.pack(side="left", padx=(0, 12))
        add_tooltip(self.channel_btn, "Select a single channel for detailed video-level stats, or choose All Channels for comparison.")

        # 2. Timeframe Filter Pill Button
        period_lbl = Label(right_f, text="Period:", font=Font(size=9, weight="bold"), style="AppleCard.TLabel")
        period_lbl.pack(side="left", padx=(0, 6))

        self.period_btn = Button(
            right_f,
            text="⏱  All Time ▾",
            font=Font(size=9, weight="bold"),
            style="AppleInset.TButton",
            width=130,
            cursor="hand2",
            command=self._open_period_dropdown,
        )
        self.period_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            self.period_btn,
            "Filter stats timeframe:\n• All Time: Lifetime total watch hours\n• Last 38 Days: Activity in last 38 days\n• Last 7 Days: Activity in last 7 days\n• Last 24 Hours: Activity in last 24 hours / today\n• Last Session: Watch stats generated in the active or most recent session",
        )

        # 3. Send Report Button
        self.send_report_btn = Button(
            right_f,
            text="✉️  Send Report",
            font=Font(size=9, weight="bold"),
            style="AppleInset.TButton",
            width=115,
            cursor="hand2",
            command=self._on_send_report_click,
        )
        self.send_report_btn.pack(side="left", padx=(0, 10))
        add_tooltip(self.send_report_btn, "Email current channel stats report to configured or custom recipient.")

        # 4. Refresh Button
        self.refresh_btn = Button(
            right_f,
            text="🔄 Refresh",
            font=Font(size=9, weight="bold"),
            style="ApplePrimary.TButton",
            width=90,
            cursor="hand2",
            command=self._on_refresh_click,
        )
        self.refresh_btn.pack(side="left")
        add_tooltip(self.refresh_btn, "Recalculate channel watch time and refresh charts.")

    # ------------------------------------------------------------- 2. KPI Cards
    def _build_kpi_cards(self) -> None:
        self.kpi_grid = Frame(self, style="Transparent.TFrame")
        self.kpi_grid.pack(fill="x", padx=16, pady=(0, 12))
        self.kpi_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.card_slot1 = self._create_card("TOTAL WATCH TIME", "0s", 0, "⏱", "In selected period", "#3B82F6")
        self.card_slot2 = self._create_card("ACTIVE CHANNELS", "0", 1, "📺", "Generating watch time", "#10B981")
        self.card_slot3 = self._create_card("COMPLETED PLAYS", "0 Runs", 2, "▶", "0 Vids · 0 Shorts", "#F59E0B")
        self.card_slot4 = self._create_card("TOTAL WATCH TIME GOAL", "0s", 3, "🎯", "Combined channels target", "#8B5CF6")

    def _create_card(
        self,
        title: str,
        initial_val: str,
        col: int,
        icon: str,
        footer: str,
        accent: str,
    ) -> Label:
        card = Frame(self.kpi_grid, style="AppleCard.TFrame")
        card.grid(row=0, column=col, padx=4, pady=0, sticky="nsew")

        hdr = Frame(card, style="AppleCardInner.TFrame")
        hdr.pack(fill="x", padx=16, pady=(12, 2))

        Label(
            hdr,
            text=f"{icon}  {title}",
            font=Font(size=8, weight="bold"),
            style="AppleCategory.TLabel",
        ).pack(side="left")

        val_f = Frame(card, style="AppleCardInner.TFrame")
        val_f.pack(fill="x", padx=16, pady=(2, 2))

        val_lbl = Label(
            val_f,
            text=initial_val,
            font=Font(size=18, weight="bold"),
            style="AppleMetric.TLabel",
        )
        val_lbl.configure(text_color=accent)
        val_lbl.pack(side="left")

        foot_f = Frame(card, style="AppleCardInner.TFrame")
        foot_f.pack(fill="x", padx=16, pady=(0, 12))

        foot_lbl = Label(
            foot_f,
            text=footer,
            font=Font(size=8),
            style="AppleMetricFooter.TLabel",
        )
        foot_lbl.pack(side="left")

        val_lbl.footer_lbl = foot_lbl
        return val_lbl

    # ------------------------------------------------------------- Dropdown Menus (Theme-Aligned)
    def _open_channel_dropdown(self) -> None:
        """Open theme-aligned AppleDropdownMenu for selecting All Channels or a specific channel."""
        if self._active_dropdown_menu and self._active_dropdown_menu.is_open():
            self._active_dropdown_menu.close()
            return

        menu = AppleDropdownMenu(self)
        self._active_dropdown_menu = menu

        is_all = (self.selected_channel_filter == "All Channels")
        menu.add_command(
            label="✓ All Channels (Overview)" if is_all else "   All Channels (Overview)",
            command=lambda: self.select_channel("All Channels"),
            active=is_all,
        )
        menu.add_separator()

        config_mgr = getattr(self.app_controller, "config_mgr", None)
        raw_channels = config_mgr.get_channels() if config_mgr else []

        stats_store = getattr(self.app_controller, "stats_store", None)
        records = stats_store.all() if stats_store else {}
        stats = compute_channel_stats(raw_channels, records, time_filter=self.current_time_filter)

        for ch in stats["channels"]:
            ch_name = ch["name"]
            is_cur = (self.selected_channel_filter.strip().lower() == ch_name.strip().lower())
            dur_str = ch["watch_formatted"]
            item_lbl = f"{'✓' if is_cur else ' '} {ch_name} ({dur_str})"
            menu.add_command(
                label=item_lbl,
                command=lambda n=ch_name: self.select_channel(n),
                active=is_cur,
            )

        try:
            x = self.channel_btn.winfo_rootx()
            y = self.channel_btn.winfo_rooty() + self.channel_btn.winfo_height() + 4
            menu.tk_popup(x, y)
        except Exception:
            pass

    def _open_period_dropdown(self) -> None:
        """Open theme-aligned AppleDropdownMenu for selecting timeframe."""
        if self._active_dropdown_menu and self._active_dropdown_menu.is_open():
            self._active_dropdown_menu.close()
            return

        menu = AppleDropdownMenu(self)
        self._active_dropdown_menu = menu

        periods = [
            ("All Time", "All Time", "Lifetime"),
            ("Last 38 Days", "Last 38 Days", "Past 38 Days"),
            ("Last 7 Days", "Last 7 Days", "Past Week"),
            ("Last 24 Hours", "Last 24 Hours", "Today"),
            ("Last Session", "Last Session", "Latest Run"),
        ]

        for p_key, p_lbl, p_badge in periods:
            is_cur = (normalize_filter_name(self.current_time_filter) == p_key)
            prefix = "✓ " if is_cur else "   "
            menu.add_command(
                label=f"{prefix}{p_lbl}",
                command=lambda k=p_key: self.select_period(k),
                active=is_cur,
                badge=p_badge,
            )

        try:
            x = self.period_btn.winfo_rootx()
            y = self.period_btn.winfo_rooty() + self.period_btn.winfo_height() + 4
            menu.tk_popup(x, y)
        except Exception:
            pass

    def select_channel(self, channel_name: str) -> None:
        """Select All Channels or drill into a single channel."""
        self.selected_channel_filter = channel_name
        btn_text = "📺  All Channels ▾" if channel_name == "All Channels" else f"📺  {channel_name} ▾"
        self.channel_btn.configure(text=btn_text)
        self.refresh()

    def select_period(self, period_str: str) -> None:
        """Change the active time filter."""
        self.current_time_filter = period_str
        self.period_btn.configure(text=f"⏱  {period_str} ▾")
        self.refresh()

    def _on_refresh_click(self) -> None:
        flash_button_feedback(self.refresh_btn, "Updated!", duration_ms=1500, original_text="🔄 Refresh")
        self.refresh()

    def _on_send_report_click(self) -> None:
        """Open the Send Stats Report modal."""
        self.open_send_report_modal()

    def open_send_report_modal(self) -> Any:
        """Open modal prompting recipient email and delivering report."""
        from .stats_report_modal import SendStatsReportModal

        config_mgr = getattr(self.app_controller, "config_mgr", None)
        raw_channels = config_mgr.get_channels() if config_mgr else []
        stats_store = getattr(self.app_controller, "stats_store", None)
        records = stats_store.all() if stats_store else {}
        queued_videos = getattr(self.app_controller, "queued_video_tasks", [])
        session_tracker = getattr(self.app_controller, "session_tracker", None)
        if session_tracker is None and hasattr(self.app_controller, "get_last_session_stats"):
            session_tracker = self.app_controller.get_last_session_stats()

        stats = compute_channel_stats(
            channels=raw_channels,
            records=records,
            queued_videos=queued_videos,
            session_tracker=session_tracker,
            time_filter=self.current_time_filter,
            stats_store=stats_store,
        )

        modal = SendStatsReportModal(
            parent=self,
            app_controller=self.app_controller,
            stats=stats,
            selected_channel=self.selected_channel_filter,
            time_filter=self.current_time_filter,
            queued_videos=queued_videos,
            records=records,
            on_success_callback=lambda rec: flash_button_feedback(
                self.send_report_btn, "Report Sent!", duration_ms=2500, original_text="✉️  Send Report"
            ),
        )
        return modal

    def send_report(self, recipient_email: Optional[str] = None) -> Tuple[bool, str]:
        """Send channel stats report directly or open modal if recipient_email is None."""
        if recipient_email is None:
            self.open_send_report_modal()
            return True, "Opened report modal."

        from ...automation.stats_reporter import send_channel_stats_report

        config_mgr = getattr(self.app_controller, "config_mgr", None)
        raw_channels = config_mgr.get_channels() if config_mgr else []
        stats_store = getattr(self.app_controller, "stats_store", None)
        records = stats_store.all() if stats_store else {}
        queued_videos = getattr(self.app_controller, "queued_video_tasks", [])
        session_tracker = getattr(self.app_controller, "session_tracker", None)
        if session_tracker is None and hasattr(self.app_controller, "get_last_session_stats"):
            session_tracker = self.app_controller.get_last_session_stats()

        stats = compute_channel_stats(
            channels=raw_channels,
            records=records,
            queued_videos=queued_videos,
            session_tracker=session_tracker,
            time_filter=self.current_time_filter,
            stats_store=stats_store,
        )

        ok, msg = send_channel_stats_report(
            app_controller=self.app_controller,
            stats=stats,
            selected_channel=self.selected_channel_filter,
            recipient_email=recipient_email,
            time_filter=self.current_time_filter,
            queued_videos=queued_videos,
            records=records,
        )
        if ok:
            flash_button_feedback(self.send_report_btn, "Report Sent!", duration_ms=2500, original_text="✉️  Send Report")
        return ok, msg

    # ------------------------------------------------------------- Refresh & Rendering
    def refresh(self) -> None:
        """Recalculate channel stats and render either All Channels or Single Channel view."""
        config_mgr = getattr(self.app_controller, "config_mgr", None)
        raw_channels = config_mgr.get_channels() if config_mgr else []

        stats_store = getattr(self.app_controller, "stats_store", None)
        records = stats_store.all() if stats_store else {}
        queued_videos = getattr(self.app_controller, "queued_video_tasks", [])
        session_tracker = getattr(self.app_controller, "session_tracker", None)

        stats = compute_channel_stats(
            channels=raw_channels,
            records=records,
            queued_videos=queued_videos,
            session_tracker=session_tracker,
            time_filter=self.current_time_filter,
            stats_store=stats_store,
        )

        for child in list(self.content_frame.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

        if self.selected_channel_filter == "All Channels":
            self._render_all_channels_view(stats)
        else:
            self._render_single_channel_view(self.selected_channel_filter, stats, records, queued_videos)

    def _render_all_channels_view(self, stats: Dict[str, Any]) -> None:
        """Render multi-channel comparison graphs, audience share, and channel list."""
        # 1. Update KPI Cards for All Channels
        self.card_slot1.configure(text=stats["total_watch_formatted"])
        self.card_slot1.footer_lbl.configure(text=f"{stats['total_watch_hours_float']} hours in {stats['time_filter']}")

        self.card_slot2.configure(text=f"{stats['active_channels_count']} Channels")
        self.card_slot2.footer_lbl.configure(
            text=f"{stats['active_channels_count']} of {stats['total_channels_count']} channels generating time"
        )

        self.card_slot3.configure(text=f"{stats['total_runs']} Runs")
        self.card_slot3.footer_lbl.configure(
            text=f"{stats['total_regular_vids']} Vids · {stats['total_shorts']} Shorts"
        )

        self.card_slot4.configure(text=stats["total_goal_formatted"])
        if stats.get("total_goal_seconds", 0) > 0:
            if stats.get("total_remaining_seconds", 0) > 0:
                goal_footer = f"Remaining: {stats['total_remaining_formatted']} ({stats['total_goal_progress_pct']}% reached)"
            else:
                goal_footer = f"Target Reached! ({stats['total_goal_progress_pct']}%)"
        else:
            goal_footer = "No goals configured"
        self.card_slot4.footer_lbl.configure(text=goal_footer)

        # 2. Charts Container (Bar Chart + Donut Chart)
        charts_card = Frame(self.content_frame, style="AppleCard.TFrame")
        charts_card.pack(fill="x", pady=(0, 14))
        charts_card.grid_columnconfigure(0, weight=3)
        charts_card.grid_columnconfigure(1, weight=2)

        # Left: Horizontal Bar Chart
        bar_box = Frame(charts_card, style="AppleInset.TFrame")
        bar_box.grid(row=0, column=0, padx=(16, 8), pady=16, sticky="nsew")

        bar_hdr = Frame(bar_box, style="AppleCardInner.TFrame")
        bar_hdr.pack(fill="x", padx=14, pady=(10, 4))
        Label(
            bar_hdr,
            text="📊  WATCH TIME BY CHANNEL (HOURS / MINUTES)",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        bar_chart = ChannelBarChart(bar_box, height=220)
        bar_chart.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        bar_chart.set_data(stats["channels"])

        # Right: Donut Share Chart
        donut_box = Frame(charts_card, style="AppleInset.TFrame")
        donut_box.grid(row=0, column=1, padx=(8, 16), pady=16, sticky="nsew")

        donut_hdr = Frame(donut_box, style="AppleCardInner.TFrame")
        donut_hdr.pack(fill="x", padx=14, pady=(10, 4))
        Label(
            donut_hdr,
            text="🍩  SHARE OF TOTAL WATCH TIME",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        donut_chart = ChannelDonutChart(donut_box, size=220)
        donut_chart.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        donut_chart.set_data(
            channels=stats["channels"],
            total_watch_seconds=stats["total_watch_seconds"],
            total_watch_formatted=stats["total_watch_formatted"],
            regular_watch_seconds=stats["total_regular_watch_seconds"],
            shorts_watch_seconds=stats["total_shorts_watch_seconds"],
        )

        # 3. Channel Cards List
        list_card = Frame(self.content_frame, style="AppleCard.TFrame")
        list_card.pack(fill="x", pady=(0, 14))

        list_hdr = Frame(list_card, style="AppleCardInner.TFrame")
        list_hdr.pack(fill="x", padx=20, pady=(14, 6))

        Label(
            list_hdr,
            text="📋  CHANNEL INTELLIGENCE LIST (CLICK A CHANNEL FOR DETAILED STATS)",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        channels_list = stats["channels"]
        if not channels_list:
            empty_lbl = Label(
                list_card,
                text="No channels configured yet. Click '+ Add Channel' in the Channels view to get started.",
                font=Font(size=9),
                style="AppleCardMuted.TLabel",
            )
            empty_lbl.pack(padx=20, pady=16, anchor="w")
            return

        for ch in channels_list:
            self._render_channel_overview_row(list_card, ch)

    def _render_channel_overview_row(self, parent: Frame, ch: Dict[str, Any]) -> None:
        """Render a single interactive channel card with drill-down button."""
        row_card = Frame(parent, style="AppleInset.TFrame")
        row_card.pack(fill="x", padx=20, pady=4)
        row_card.grid_columnconfigure(1, weight=1)

        # Rank + Initial
        avatar_f = Frame(row_card, style="Transparent.TFrame", width=42)
        avatar_f.pack(side="left", padx=(12, 10), pady=10)

        rank_lbl = Label(
            avatar_f,
            text=f"#{ch['rank']}",
            font=Font(size=10, weight="bold"),
            style="AppleCategory.TLabel",
        )
        rank_lbl.configure(text_color=ch["color"])
        rank_lbl.pack(anchor="center")

        # Name and metrics
        info_f = Frame(row_card, style="Transparent.TFrame")
        info_f.pack(side="left", fill="both", expand=True, padx=4, pady=10)

        top_line = Frame(info_f, style="Transparent.TFrame")
        top_line.pack(fill="x", anchor="w")

        name_lbl = Label(
            top_line,
            text=ch["name"],
            font=Font(size=11, weight="bold"),
            style="AppleCard.TLabel",
            cursor="hand2",
        )
        name_lbl.pack(side="left", anchor="w")
        name_lbl.bind("<Button-1>", lambda e, n=ch["name"]: self.select_channel(n))

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
            url_btn.pack(side="left", padx=(8, 0))
            add_tooltip(url_btn, f"Open {ch['name']} on YouTube:\n{ch['url']}")

        sub_text = (
            f"▶ {ch['runs']} runs  ·  {ch['regular_vids_count']} videos  ·  "
            f"{ch['shorts_count']} shorts  ·  {ch['success_rate_pct']}% success"
        )
        if ch.get("goal_seconds", 0) > 0:
            sub_text += f"  ·  Goal: {ch['goal_formatted']} ({ch['goal_progress_pct']}% reached, {ch['remaining_formatted']} remaining)"
        elif ch.get("target_hours", 0) > 0:
            sub_text += f"  ·  {ch['target_progress_pct']}% of {ch['target_hours']}h target"

        sub_lbl = Label(
            info_f,
            text=sub_text,
            font=Font(size=8),
            style="AppleCardMuted.TLabel",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # Right: Watch Time + "View Details →" Button
        right_f = Frame(row_card, style="Transparent.TFrame")
        right_f.pack(side="right", padx=(10, 16), pady=10)

        time_lbl = Label(
            right_f,
            text=ch["watch_formatted"],
            font=Font(size=12, weight="bold"),
            style="AppleMetric.TLabel",
        )
        time_lbl.configure(text_color=ch["color"])
        time_lbl.pack(anchor="e")

        detail_btn = Button(
            right_f,
            text="View Stats →",
            font=Font(size=8, weight="bold"),
            style="ApplePrimary.TButton",
            width=85,
            cursor="hand2",
            command=lambda n=ch["name"]: self.select_channel(n),
        )
        detail_btn.pack(anchor="e", pady=(4, 0))
        add_tooltip(detail_btn, f"View deep-dive video-level stats for {ch['name']}.")

    # ------------------------------------------------------------- Single Channel Deep-Dive Mode
    def _render_single_channel_view(
        self,
        channel_name: str,
        stats: Dict[str, Any],
        records: Dict[str, Any],
        queued_videos: List[Any],
    ) -> None:
        """Render single-channel detailed intelligence with video-level playback breakdown."""
        clean_name = channel_name.strip().lower().lstrip("@")
        ch_meta = next(
            (c for c in stats["channels"] if c["key"] == clean_name),
            None,
        )

        if not ch_meta:
            # Channel fallback
            ch_meta = {
                "name": channel_name,
                "url": "",
                "color": "#3B82F6",
                "watch_formatted": "0s",
                "watch_seconds": 0.0,
                "runs": 0,
                "successes": 0,
                "regular_vids_count": 0,
                "shorts_count": 0,
                "target_hours": 0.0,
                "target_progress_pct": 0.0,
                "percentage_share": 0.0,
                "success_rate_pct": 100.0,
            }

        # 1. Update KPI Cards for Single Channel
        self.card_slot1.configure(text=ch_meta["watch_formatted"])
        self.card_slot1.footer_lbl.configure(text=f"{ch_meta['name']} in {stats['time_filter']}")

        self.card_slot2.configure(text=f"{ch_meta['runs']} Plays")
        self.card_slot2.footer_lbl.configure(text=f"{ch_meta['success_rate_pct']}% completion success")

        self.card_slot3.configure(text=f"{ch_meta['regular_vids_count']} Vids · {ch_meta['shorts_count']} Shorts")
        self.card_slot3.footer_lbl.configure(text=f"{ch_meta['percentage_share']}% of total all-channel watch time")

        if ch_meta.get("goal_seconds", 0) > 0:
            self.card_slot4.configure(text=ch_meta["goal_formatted"])
            if ch_meta.get("remaining_seconds", 0) > 0:
                single_goal_footer = f"Remaining: {ch_meta['remaining_formatted']} ({ch_meta['goal_progress_pct']}% reached)"
            else:
                single_goal_footer = f"Target Reached! ({ch_meta['goal_progress_pct']}%)"
            self.card_slot4.footer_lbl.configure(text=single_goal_footer)
        elif ch_meta.get("target_hours", 0) > 0:
            target_text = f"{ch_meta['target_progress_pct']}%"
            target_footer = f"Target: {ch_meta['target_hours']} hours"
            self.card_slot4.configure(text=target_text)
            self.card_slot4.footer_lbl.configure(text=target_footer)
        else:
            self.card_slot4.configure(text="No Goal Set")
            self.card_slot4.footer_lbl.configure(text="Configure multipliers in Video Queue")

        # 2. Channel Deep-Dive Card
        channel_card = Frame(self.content_frame, style="AppleCard.TFrame")
        channel_card.pack(fill="x", pady=(0, 14))

        hdr = Frame(channel_card, style="AppleCardInner.TFrame")
        hdr.pack(fill="x", padx=20, pady=(16, 12))

        # Back Button
        back_btn = Button(
            hdr,
            text="← Back to All Channels",
            font=Font(size=9, weight="bold"),
            style="AppleDefault.TButton",
            width=150,
            cursor="hand2",
            command=lambda: self.select_channel("All Channels"),
        )
        back_btn.pack(side="left")

        # Channel Title on Right
        title_box = Frame(hdr, style="Transparent.TFrame")
        title_box.pack(side="right", anchor="e")

        Label(
            title_box,
            text=f"📺  {ch_meta['name']}",
            font=Font(size=14, weight="bold"),
            style="AppleHeader.TLabel",
        ).pack(side="left", padx=(0, 8))

        if ch_meta.get("url"):
            open_btn = Button(
                title_box,
                text="🔗 Open on YouTube",
                font=Font(size=8, weight="bold"),
                style="ApplePrimary.TButton",
                width=130,
                cursor="hand2",
                command=lambda u=ch_meta["url"]: webbrowser.open(u),
            )
            open_btn.pack(side="left")

        # Video-Level Breakdown Table
        session_tracker = getattr(self.app_controller, "session_tracker", None)
        if session_tracker is None and hasattr(self.app_controller, "get_last_session_stats"):
            session_tracker = self.app_controller.get_last_session_stats()
        s_start = None
        if isinstance(session_tracker, dict):
            s_start = session_tracker.get("session_start_time")
        elif session_tracker:
            s_start = getattr(session_tracker, "session_start_time", None)

        videos = get_channel_video_records(
            channel_name=ch_meta["name"],
            records=records,
            queued_videos=queued_videos,
            time_filter=self.current_time_filter,
            session_start_time=s_start,
        )

        table_card = Frame(self.content_frame, style="AppleCard.TFrame")
        table_card.pack(fill="x", pady=(0, 14))

        table_hdr = Frame(table_card, style="AppleCardInner.TFrame")
        table_hdr.pack(fill="x", padx=20, pady=(14, 6))

        Label(
            table_hdr,
            text=f"🎬  WATCHED VIDEOS & SHORTS ({len(videos)} RECORDED IN {stats['time_filter'].upper()})",
            font=Font(size=8, weight="bold"),
            style="AppleSectionTitle.TLabel",
        ).pack(side="left")

        if not videos:
            empty_vids = Label(
                table_card,
                text="No playback records found for this channel in the selected timeframe.\nRun browsing tasks to accumulate video watch time.",
                font=Font(size=9),
                style="AppleCardMuted.TLabel",
            )
            empty_vids.pack(padx=20, pady=16, anchor="w")
            return

        for v in videos:
            self._render_video_item_row(table_card, v)

    def _render_video_item_row(self, parent: Frame, v: Dict[str, Any]) -> None:
        """Render a clean individual video record row."""
        row_card = Frame(parent, style="AppleInset.TFrame")
        row_card.pack(fill="x", padx=20, pady=3)
        row_card.grid_columnconfigure(1, weight=1)

        # Format Badge
        badge_text = "⚡ Short" if v["is_short"] else "🎥 Video"
        badge_color = "#EF4444" if v["is_short"] else "#3B82F6"

        badge_f = Frame(row_card, style="Transparent.TFrame", width=65)
        badge_f.pack(side="left", padx=(10, 6), pady=8)

        b_lbl = Label(
            badge_f,
            text=badge_text,
            font=Font(size=8, weight="bold"),
            style="AppleCategory.TLabel",
        )
        b_lbl.configure(text_color=badge_color)
        b_lbl.pack(anchor="w")

        # Video Title and URL
        info_f = Frame(row_card, style="Transparent.TFrame")
        info_f.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        top_line = Frame(info_f, style="Transparent.TFrame")
        top_line.pack(fill="x", anchor="w")

        v_title = v["title"]
        if len(v_title) > 55:
            v_title = v_title[:54] + "…"

        title_lbl = Label(
            top_line,
            text=v_title,
            font=Font(size=9, weight="bold"),
            style="AppleCard.TLabel",
        )
        title_lbl.pack(side="left", anchor="w")

        if v.get("url"):
            u_btn = Button(
                top_line,
                text="🔗",
                width=24,
                font=Font(size=8),
                style="AppleDefault.TButton",
                cursor="hand2",
                command=lambda u=v["url"]: webbrowser.open(u),
            )
            u_btn.pack(side="left", padx=(6, 0))
            add_tooltip(u_btn, f"Open video on YouTube:\n{v['url']}")

        # Sub line: length, runs, last played
        sub_text = f"Length: {v['duration_formatted']}  ·  ▶ {v['runs']} runs  ·  Last completed: {v['last_completed']}"
        Label(
            info_f,
            text=sub_text,
            font=Font(size=8),
            style="AppleCardMuted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # Right: Watch Time
        right_f = Frame(row_card, style="Transparent.TFrame")
        right_f.pack(side="right", padx=(8, 14), pady=8)

        time_lbl = Label(
            right_f,
            text=v["watch_formatted"],
            font=Font(size=11, weight="bold"),
            style="AppleMetric.TLabel",
        )
        time_lbl.configure(text_color="#3B82F6")
        time_lbl.pack(anchor="e")

    def apply_theme(self, mode: Optional[str] = None) -> None:
        """Update styles and charts matching current light/dark theme."""
        super().apply_theme(mode)
        self.refresh()
