"""Channel Watch Time & Analytics Email Reporter.

Generates and delivers comprehensive HTML and plain-text channel intelligence reports:
- All Channels overview report with multi-channel comparison, share, and multiplier targets.
- Single Channel deep-dive report with video-level watch time breakdown and goal progress.
- Dispatches via SMTP or logged-in browser Gmail to user-provided or default system recipient.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..utils.logger import emit
from .email_reporter import DEFAULT_ADMIN_RECIPIENT_EMAIL, EmailReporter, resolve_report_recipients


@dataclass
class ChannelStatsReport:
    """Holds structured channel statistics data for email reporting."""

    time_filter: str = "All Time"
    selected_channel: str = "All Channels"
    total_watch_seconds: float = 0.0
    total_watch_formatted: str = "0s"
    total_watch_hours_float: float = 0.0
    active_channels_count: int = 0
    total_channels_count: int = 0
    total_runs: int = 0
    total_regular_vids: int = 0
    total_shorts: int = 0
    total_goal_seconds: float = 0.0
    total_goal_formatted: str = "0s"
    total_remaining_seconds: float = 0.0
    total_remaining_formatted: str = "0s"
    total_goal_progress_pct: float = 0.0
    channels: List[Dict[str, Any]] = field(default_factory=list)
    single_channel_data: Optional[Dict[str, Any]] = None
    video_records: List[Dict[str, Any]] = field(default_factory=list)
    system_name: str = ""
    account_email: str = ""
    generated_at: datetime = field(default_factory=datetime.now)
    thread_message_id: Optional[str] = None
    thread_url: Optional[str] = None

    @property
    def is_single_channel(self) -> bool:
        return bool(self.selected_channel and self.selected_channel != "All Channels")

    def generate_subject(self) -> str:
        """Produce clear, distinctive subject line for Gmail inbox filtering."""
        sys_pfx = f"{self.system_name} - " if self.system_name else ""
        stamp = self.generated_at.strftime("%d %b %Y - %I:%M %p")
        if self.is_single_channel:
            ch_name = self.single_channel_data.get("name", self.selected_channel) if self.single_channel_data else self.selected_channel
            watch_str = self.single_channel_data.get("watch_formatted", "0s") if self.single_channel_data else "0s"
            return f"{sys_pfx}Channel Report: {ch_name} [{self.time_filter}] • {watch_str} Watch Time ({stamp})"
        return f"{sys_pfx}All Channels Stats Report [{self.time_filter}] • {self.total_watch_formatted} Watch Time ({stamp})"

    def generate_plain_text(self) -> str:
        """Produce cleanly formatted ASCII report for mobile/plain text preview."""
        stamp = self.generated_at.strftime("%Y-%m-%d %H:%M:%S")
        lines: List[str] = []
        border = "=" * 74
        subborder = "-" * 74

        lines.append(border)
        if self.is_single_channel:
            lines.append(f"     CHANNEL PERFORMANCE REPORT: {self.selected_channel.upper()}")
        else:
            lines.append("     CHANNEL ANALYTICS & WATCH TIME REPORT (ALL CHANNELS)")
        lines.append(border)

        if self.system_name:
            lines.append(f"System:         {self.system_name}")
        if self.is_single_channel and self.single_channel_data:
            ch_display = self.single_channel_data.get("name", self.selected_channel)
            lines.append(f"Channel:        {ch_display}")
        lines.append(f"Timeframe:      {self.time_filter}")
        lines.append(f"Generated:      {stamp}")

        if self.is_single_channel and self.single_channel_data:
            ch = self.single_channel_data
            if ch.get("url"):
                lines.append(f"Channel URL:    {ch['url']}")
            lines.append(subborder)
            lines.append("                         KEY CHANNEL METRICS")
            lines.append(subborder)
            lines.append(f"* Watch Time Delivered:     {ch.get('watch_formatted', '0s')} ({ch.get('percentage_share', 0.0)}% share of total)")
            lines.append(f"* Completed Plays:          {ch.get('runs', 0)} Runs ({ch.get('success_rate_pct', 100.0)}% success)")
            lines.append(f"* Content Format Split:     {ch.get('regular_vids_count', 0)} Videos · {ch.get('shorts_count', 0)} Shorts")
            if ch.get("goal_seconds", 0) > 0:
                lines.append(f"* Watch Time Goal:          {ch.get('goal_formatted', '0s')} (Remaining: {ch.get('remaining_formatted', '0s')}, {ch.get('goal_progress_pct', 0.0)}% reached)")
            elif ch.get("target_hours", 0) > 0:
                lines.append(f"* Target Hours:             {ch.get('target_hours', 0.0)}h ({ch.get('target_progress_pct', 0.0)}% reached)")

            if self.video_records:
                lines.append("")
                lines.append(subborder)
                lines.append(f"            WATCHED VIDEOS & SHORTS ({len(self.video_records)} ITEMS)")
                lines.append(subborder)
                for idx, v in enumerate(self.video_records, 1):
                    v_type = "Short" if v.get("is_short") else "Video"
                    lines.append(f"{idx}. {v.get('title', 'Unknown Video')}")
                    lines.append(f"   Watch Time: {v.get('watch_formatted', '0s')} · Plays: {v.get('runs', 0)} · Length: {v.get('duration_formatted', '—')} · Format: {v_type}")
                    if v.get("url"):
                        lines.append(f"   Link: {v['url']}")
                    lines.append("")
        else:
            lines.append(f"Total Channels: {self.total_channels_count} ({self.active_channels_count} Active)")
            lines.append(subborder)
            lines.append("                         KEY METRICS OVERVIEW")
            lines.append(subborder)
            lines.append(f"* Total Watch Time:         {self.total_watch_formatted} ({self.total_watch_hours_float} hours)")
            lines.append(f"* Active Channels:          {self.active_channels_count} of {self.total_channels_count} channels generating watch time")
            lines.append(f"* Completed Plays:          {self.total_runs} Runs ({self.total_regular_vids} Videos · {self.total_shorts} Shorts)")
            if self.total_goal_seconds > 0:
                lines.append(f"* Total Watch Time Goal:    {self.total_goal_formatted} (Remaining: {self.total_remaining_formatted}, {self.total_goal_progress_pct}% reached)")
            else:
                lines.append("* Total Watch Time Goal:    No goals configured")

            if self.channels:
                lines.append("")
                lines.append(subborder)
                lines.append("                     INDIVIDUAL CHANNELS BREAKDOWN")
                lines.append(subborder)
                for c in self.channels:
                    lines.append(f"#{c.get('rank', 1)} {c.get('name', 'Unknown')}")
                    lines.append(f"   Watch Time: {c.get('watch_formatted', '0s')} ({c.get('percentage_share', 0.0)}% share) · Plays: {c.get('runs', 0)} ({c.get('regular_vids_count', 0)} vids, {c.get('shorts_count', 0)} shorts)")
                    if c.get("goal_seconds", 0) > 0:
                        lines.append(f"   Goal: {c.get('goal_formatted', '0s')} · Remaining: {c.get('remaining_formatted', '0s')} ({c.get('goal_progress_pct', 0.0)}% reached)")
                    if c.get("url"):
                        lines.append(f"   URL: {c['url']}")
                    lines.append("")

        lines.append(border)
        lines.append("End of Channel Analytics Report • Generated by Views App")
        lines.append(border)
        return "\n".join(lines)

    def generate_html_body(self) -> str:
        """Produce modern, responsive executive HTML body suitable for web and mobile email."""
        stamp = self.generated_at.strftime("%d %b %Y - %I:%M %p")
        sys_badge = f'<span style="background-color: #e2e8f0; color: #334155; padding: 4px 10px; border-radius: 8px; font-weight: 700; font-size: 12px; margin-right: 8px;">🖥 {html.escape(self.system_name)}</span>' if self.system_name else ""
        period_badge = f'<span style="background-color: #dbeafe; color: #1e40af; padding: 4px 10px; border-radius: 8px; font-weight: 700; font-size: 12px;">⏱ {html.escape(self.time_filter)}</span>'

        if self.is_single_channel:
            ch_meta = self.single_channel_data or {}
            ch_name = html.escape(ch_meta.get("name", self.selected_channel))
            ch_url = ch_meta.get("url", "")
            ch_link_btn = (
                f'<a href="{html.escape(ch_url)}" target="_blank" style="background-color: #2563eb; color: #ffffff; text-decoration: none; padding: 6px 14px; border-radius: 6px; font-weight: 600; font-size: 12px; display: inline-block;">↗ Open Channel on YouTube</a>'
                if ch_url else ""
            )

            # 4 KPI cards for Single Channel
            kpi_html = f"""
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 20px 0;">
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">⏱ Channel Watch Time</div>
                    <div style="font-size: 22px; font-weight: 800; color: #2563eb; margin: 6px 0;">{html.escape(ch_meta.get('watch_formatted', '0s'))}</div>
                    <div style="font-size: 11px; color: #64748b;">{ch_meta.get('percentage_share', 0.0)}% share of all channels</div>
                </div>
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">▶ Completed Plays</div>
                    <div style="font-size: 22px; font-weight: 800; color: #10b981; margin: 6px 0;">{ch_meta.get('runs', 0)} Plays</div>
                    <div style="font-size: 11px; color: #64748b;">{ch_meta.get('success_rate_pct', 100.0)}% completion success</div>
                </div>
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">🎬 Content Breakdown</div>
                    <div style="font-size: 22px; font-weight: 800; color: #f59e0b; margin: 6px 0;">{ch_meta.get('regular_vids_count', 0)}V · {ch_meta.get('shorts_count', 0)}S</div>
                    <div style="font-size: 11px; color: #64748b;">Regular videos vs shorts</div>
                </div>
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">🎯 Watch Time Goal</div>
                    <div style="font-size: 22px; font-weight: 800; color: #8b5cf6; margin: 6px 0;">{html.escape(ch_meta.get('goal_formatted', '0s'))}</div>
                    <div style="font-size: 11px; color: #64748b;">Remaining: {html.escape(ch_meta.get('remaining_formatted', '0s'))} ({ch_meta.get('goal_progress_pct', 0.0)}%)</div>
                </div>
            </div>
            """

            # Videos Table
            vids_rows = []
            if self.video_records:
                for idx, v in enumerate(self.video_records, 1):
                    v_title = html.escape(v.get("title", f"Video #{idx}"))
                    v_url = v.get("url", "")
                    link_html = f'<a href="{html.escape(v_url)}" target="_blank" style="color: #2563eb; text-decoration: none; font-weight: 600;">{v_title}</a>' if v_url else v_title
                    is_s = v.get("is_short", False)
                    type_pill = '<span style="background-color: #fef3c7; color: #b45309; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700;">Short</span>' if is_s else '<span style="background-color: #e0e7ff; color: #4338ca; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700;">Video</span>'

                    vids_rows.append(f"""
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 12px 14px; vertical-align: middle;">{link_html}</td>
                        <td style="padding: 12px 14px; vertical-align: middle; text-align: center;">{type_pill}</td>
                        <td style="padding: 12px 14px; vertical-align: middle; color: #64748b; font-size: 12px;">{html.escape(v.get('duration_formatted', '—'))}</td>
                        <td style="padding: 12px 14px; vertical-align: middle; text-align: center; font-weight: 600;">{v.get('runs', 0)}</td>
                        <td style="padding: 12px 14px; vertical-align: middle; text-align: right; font-weight: 700; color: #2563eb;">{html.escape(v.get('watch_formatted', '0s'))}</td>
                    </tr>
                    """)
            else:
                vids_rows.append('<tr><td colspan="5" style="padding: 20px; text-align: center; color: #94a3b8;">No video playback records found in this timeframe.</td></tr>')

            content_html = f"""
            <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; margin-top: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="background-color: #f8fafc; padding: 14px 18px; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-weight: 700; font-size: 13px; color: #334155; text-transform: uppercase;">🎬 Watched Videos & Shorts ({len(self.video_records)} Recorded)</span>
                </div>
                <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                    <thead>
                        <tr style="background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; color: #64748b; font-size: 11px; text-transform: uppercase;">
                            <th style="padding: 10px 14px;">Title & Link</th>
                            <th style="padding: 10px 14px; text-align: center;">Format</th>
                            <th style="padding: 10px 14px;">Duration</th>
                            <th style="padding: 10px 14px; text-align: center;">Plays</th>
                            <th style="padding: 10px 14px; text-align: right;">Watch Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(vids_rows)}
                    </tbody>
                </table>
            </div>
            """
            title_text = f"📺 Channel Report: {ch_name}"
            sub_text = f"Single channel delivery intelligence • {ch_link_btn}"
        else:
            # 4 KPI cards for All Channels
            kpi_html = f"""
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 20px 0;">
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">⏱ Total Watch Time</div>
                    <div style="font-size: 22px; font-weight: 800; color: #2563eb; margin: 6px 0;">{html.escape(self.total_watch_formatted)}</div>
                    <div style="font-size: 11px; color: #64748b;">{self.total_watch_hours_float} hours in {html.escape(self.time_filter)}</div>
                </div>
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">📺 Active Channels</div>
                    <div style="font-size: 22px; font-weight: 800; color: #10b981; margin: 6px 0;">{self.active_channels_count} of {self.total_channels_count}</div>
                    <div style="font-size: 11px; color: #64748b;">Channels generating watch time</div>
                </div>
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">▶ Completed Plays</div>
                    <div style="font-size: 22px; font-weight: 800; color: #f59e0b; margin: 6px 0;">{self.total_runs} Runs</div>
                    <div style="font-size: 11px; color: #64748b;">{self.total_regular_vids} Videos · {self.total_shorts} Shorts</div>
                </div>
                <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">🎯 Watch Time Goal</div>
                    <div style="font-size: 22px; font-weight: 800; color: #8b5cf6; margin: 6px 0;">{html.escape(self.total_goal_formatted)}</div>
                    <div style="font-size: 11px; color: #64748b;">Remaining: {html.escape(self.total_remaining_formatted)} ({self.total_goal_progress_pct}%)</div>
                </div>
            </div>
            """

            # Channels Comparison Table
            chan_rows = []
            if self.channels:
                for c in self.channels:
                    c_name = html.escape(c.get("name", "Unknown"))
                    c_url = c.get("url", "")
                    link_html = f'<a href="{html.escape(c_url)}" target="_blank" style="color: #2563eb; text-decoration: none; font-weight: 600;">{c_name}</a>' if c_url else f"<strong>{c_name}</strong>"

                    pct = c.get("goal_progress_pct", 0.0)
                    g_sec = c.get("goal_seconds", 0.0)
                    if g_sec > 0:
                        prog_bar = f"""
                        <div style="font-size: 11px; color: #475569; margin-bottom: 2px;">{html.escape(c.get('goal_formatted', '0s'))} ({pct}%)</div>
                        <div style="background-color: #e2e8f0; border-radius: 6px; height: 6px; width: 100px; overflow: hidden;">
                            <div style="background-color: #8b5cf6; width: {min(100.0, pct):.1f}%; height: 100%;"></div>
                        </div>
                        """
                    else:
                        prog_bar = '<span style="color: #94a3b8; font-size: 11px;">No Goal Set</span>'

                    chan_rows.append(f"""
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 12px 14px; vertical-align: middle; font-weight: 700; color: #64748b;">#{c.get('rank', 1)}</td>
                        <td style="padding: 12px 14px; vertical-align: middle;">{link_html}</td>
                        <td style="padding: 12px 14px; vertical-align: middle; font-weight: 700; color: #2563eb;">{html.escape(c.get('watch_formatted', '0s'))} <span style="font-size: 11px; color: #64748b; font-weight: normal;">({c.get('percentage_share', 0.0)}%)</span></td>
                        <td style="padding: 12px 14px; vertical-align: middle;">{prog_bar}</td>
                        <td style="padding: 12px 14px; vertical-align: middle; color: #334155; font-size: 12px;">{c.get('runs', 0)} plays <span style="font-size: 11px; color: #64748b;">({c.get('regular_vids_count', 0)}v, {c.get('shorts_count', 0)}s)</span></td>
                        <td style="padding: 12px 14px; vertical-align: middle; text-align: right; font-weight: 600; color: #10b981;">{c.get('success_rate_pct', 100.0)}%</td>
                    </tr>
                    """)
            else:
                chan_rows.append('<tr><td colspan="6" style="padding: 20px; text-align: center; color: #94a3b8;">No channel data available.</td></tr>')

            content_html = f"""
            <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; margin-top: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="background-color: #f8fafc; padding: 14px 18px; border-bottom: 1px solid #e2e8f0;">
                    <span style="font-weight: 700; font-size: 13px; color: #334155; text-transform: uppercase;">📋 Channel Performance & Delivery Breakdown ({len(self.channels)} Channels)</span>
                </div>
                <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                    <thead>
                        <tr style="background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; color: #64748b; font-size: 11px; text-transform: uppercase;">
                            <th style="padding: 10px 14px; width: 40px;">Rank</th>
                            <th style="padding: 10px 14px;">Channel</th>
                            <th style="padding: 10px 14px;">Watch Time</th>
                            <th style="padding: 10px 14px;">Goal Progress</th>
                            <th style="padding: 10px 14px;">Plays</th>
                            <th style="padding: 10px 14px; text-align: right;">Success</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(chan_rows)}
                    </tbody>
                </table>
            </div>
            """
            title_text = "📈 Channel Analytics & Watch Time Report"
            sub_text = "Multi-channel intelligence, comparison metrics & delivery reports"

        return f"""
        <div style="max-width: 820px; margin: 0 auto; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 16px; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
            <!-- Header Banner -->
            <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding: 26px 28px; color: #ffffff;">
                <div style="margin-bottom: 8px;">{sys_badge}{period_badge}</div>
                <h1 style="margin: 0 0 6px 0; font-size: 24px; font-weight: 800; letter-spacing: -0.5px;">{title_text}</h1>
                <div style="color: #94a3b8; font-size: 13px;">{sub_text} • Generated {stamp}</div>
            </div>

            <!-- Main Body -->
            <div style="padding: 24px 28px;">
                {kpi_html}
                {content_html}
            </div>

            <!-- Footer -->
            <div style="background-color: #f1f5f9; padding: 14px 28px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b; text-align: center;">
                Generated by <strong>Views App</strong> Channel Intelligence Engine • {stamp}
            </div>
        </div>
        """.strip()

    def generate_html(self) -> str:
        """Produce full email HTML document with CSS container."""
        body = self.generate_html_body()
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(self.generate_subject())}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px; color: #1e293b;">
{body}
</body>
</html>""".strip()


def build_channel_stats_report(
    stats: Dict[str, Any],
    selected_channel: str = "All Channels",
    time_filter: str = "All Time",
    system_name: str = "",
    account_email: str = "",
    video_records: Optional[List[Dict[str, Any]]] = None,
) -> ChannelStatsReport:
    """Construct a ChannelStatsReport dataclass from computed stats dict."""
    is_single = bool(selected_channel and selected_channel != "All Channels")
    clean_target = selected_channel.strip().lower().lstrip("@") if is_single else ""

    single_ch_meta: Optional[Dict[str, Any]] = None
    if is_single:
        single_ch_meta = next(
            (c for c in stats.get("channels", []) if c.get("key") == clean_target or c.get("name", "").strip().lower().lstrip("@") == clean_target),
            None,
        )
        if not single_ch_meta:
            single_ch_meta = {
                "name": selected_channel,
                "url": "",
                "watch_formatted": "0s",
                "watch_seconds": 0.0,
                "percentage_share": 0.0,
                "runs": 0,
                "success_rate_pct": 100.0,
                "regular_vids_count": 0,
                "shorts_count": 0,
                "goal_formatted": "0s",
                "goal_seconds": 0.0,
                "remaining_formatted": "0s",
                "goal_progress_pct": 0.0,
            }

    return ChannelStatsReport(
        time_filter=time_filter,
        selected_channel=selected_channel,
        total_watch_seconds=float(stats.get("total_watch_seconds", 0.0) or 0.0),
        total_watch_formatted=stats.get("total_watch_formatted", "0s"),
        total_watch_hours_float=float(stats.get("total_watch_hours_float", 0.0) or 0.0),
        active_channels_count=int(stats.get("active_channels_count", 0) or 0),
        total_channels_count=int(stats.get("total_channels_count", 0) or 0),
        total_runs=int(stats.get("total_runs", 0) or 0),
        total_regular_vids=int(stats.get("total_regular_vids", 0) or 0),
        total_shorts=int(stats.get("total_shorts", 0) or 0),
        total_goal_seconds=float(stats.get("total_goal_seconds", 0.0) or 0.0),
        total_goal_formatted=stats.get("total_goal_formatted", "0s"),
        total_remaining_seconds=float(stats.get("total_remaining_seconds", 0.0) or 0.0),
        total_remaining_formatted=stats.get("total_remaining_formatted", "0s"),
        total_goal_progress_pct=float(stats.get("total_goal_progress_pct", 0.0) or 0.0),
        channels=stats.get("channels", []),
        single_channel_data=single_ch_meta,
        video_records=video_records or [],
        system_name=system_name,
        account_email=account_email,
    )


def send_channel_stats_report(
    app_controller: Any,
    stats: Dict[str, Any],
    selected_channel: str = "All Channels",
    recipient_email: Optional[str] = None,
    time_filter: str = "All Time",
    queued_videos: Optional[List[Any]] = None,
    records: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Compile and dispatch channel statistics report to recipient via configured transports.

    Returns:
        (success: bool, status_message: str)
    """
    config_mgr = getattr(app_controller, "config_mgr", None)
    log_queue = getattr(app_controller, "log_queue", None)

    # Determine recipient
    target_recipient = (recipient_email or "").strip()
    if not target_recipient or "@" not in target_recipient:
        if config_mgr:
            target_recipient, _ = resolve_report_recipients(config_mgr)
        else:
            target_recipient = DEFAULT_ADMIN_RECIPIENT_EMAIL

    if not target_recipient or "@" not in target_recipient:
        return False, f"Invalid recipient email address: '{target_recipient}'."

    sys_name = (config_mgr.get_setting("SystemName", "") or "").strip() if config_mgr else ""
    loaded_accs = getattr(app_controller, "loaded_accounts", [])
    primary_acc = loaded_accs[0] if loaded_accs else None
    acc_email = getattr(primary_acc, "email", "") or ""

    # If single channel is selected, resolve video-level records
    from ..ui.views.stats_view import get_channel_video_records
    v_records: List[Dict[str, Any]] = []
    if selected_channel and selected_channel != "All Channels":
        v_records = get_channel_video_records(
            channel_name=selected_channel,
            records=records or {},
            queued_videos=queued_videos,
            time_filter=time_filter,
        )

    report = build_channel_stats_report(
        stats=stats,
        selected_channel=selected_channel,
        time_filter=time_filter,
        system_name=sys_name,
        account_email=acc_email,
        video_records=v_records,
    )

    if log_queue:
        emit(log_queue, f"Initiating channel stats report email to '{target_recipient}' (Scope: {selected_channel}, Timeframe: {time_filter})...")

    # 1. Try fast SMTP if credentials configured
    smtp_host = config_mgr.get_setting("SmtpHost", "smtp.gmail.com") if config_mgr else "smtp.gmail.com"
    smtp_port = config_mgr.get_int("SmtpPort", 587) if config_mgr else 587
    smtp_user = (config_mgr.get_setting("SmtpUsername", "") if config_mgr else "") or acc_email
    smtp_pass = (config_mgr.get_setting("SmtpPassword", "") if config_mgr else "") or getattr(primary_acc, "password", "")
    sender_email = (config_mgr.get_setting("SenderEmail", "") if config_mgr else "") or smtp_user

    has_smtp = bool(smtp_pass and (smtp_user or sender_email))

    if has_smtp:
        bcc_email = DEFAULT_ADMIN_RECIPIENT_EMAIL if target_recipient.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else None
        smtp_ok = EmailReporter.send_via_smtp(
            report=report,
            recipient_email=target_recipient,
            bcc_email=bcc_email,
            host=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_pass,
            sender_email=sender_email,
            log_queue=log_queue,
        )
        if smtp_ok:
            if log_queue:
                emit(log_queue, f"Channel stats report successfully emailed to '{target_recipient}' via SMTP.")
            return True, f"Report successfully sent to {target_recipient} via SMTP!"

    # 2. Try browser Gmail if an account profile is available
    if primary_acc is not None:
        bcc_email = DEFAULT_ADMIN_RECIPIENT_EMAIL if target_recipient.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else None
        try:
            browser_ok = EmailReporter.send_via_headless_browser_gmail(
                account=primary_acc,
                subject=report.generate_subject(),
                body_text=report.generate_plain_text(),
                html_content=report.generate_html_body(),
                recipient_email=target_recipient,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description="channel stats report",
                headless=False,
            )
            if browser_ok:
                if log_queue:
                    emit(log_queue, f"Channel stats report successfully emailed to '{target_recipient}' via Gmail browser.")
                return True, f"Report successfully sent to {target_recipient} via Gmail browser!"
        except Exception as exc:
            if log_queue:
                emit(log_queue, f"Browser Gmail delivery attempt failed: {exc}", logging.WARNING)

    # 3. Fallback to dispatch_report
    try:
        ok = EmailReporter.dispatch_report(
            report=report,
            config_mgr=config_mgr,
            account=primary_acc,
            log_queue=log_queue,
            recipient_override=target_recipient,
        )
        if ok:
            return True, f"Report successfully sent to {target_recipient}!"
    except Exception as exc:
        logging.error(f"Failed to dispatch channel stats report: {exc}")

    return False, "Failed to send report. Please verify SMTP credentials in Settings or sign in to an account profile."
