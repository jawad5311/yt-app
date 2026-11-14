"""Session Watch Time Email Reporter.

Generates and delivers detailed session watch time reports and start notifications:
- Sends 'Automation Started' email notification in a headless browser when automation begins.
- Notes baseline channel watch times at session start.
- Calculates per-channel watch times and combined total session watch time.
- Supports delivery via the logged-in Google account in a headless Chrome browser
  profile (mail.google.com), the active browser, or via standard SMTP (smtp.gmail.com).
"""

import html
import logging
import queue
import re
import smtplib
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from ..models.account import Account
from ..models.watch_stats import format_duration, format_hours
from ..utils.logger import emit

DEFAULT_ADMIN_RECIPIENT_EMAIL = "jawad@ytgrowthexpert.com"


def resolve_report_recipients(config_mgr: Any) -> tuple:
    """Determine (to_email, bcc_email) based on user config and default admin recipient."""
    raw_user = (config_mgr.get_setting("ReportRecipientEmail", "") or "").strip() if config_mgr else ""
    if not raw_user or "@" not in raw_user:
        return DEFAULT_ADMIN_RECIPIENT_EMAIL, None

    if raw_user.lower() == DEFAULT_ADMIN_RECIPIENT_EMAIL.lower():
        return DEFAULT_ADMIN_RECIPIENT_EMAIL, None

    # User configured another recipient: send To raw_user, and BCC the default admin
    return raw_user, DEFAULT_ADMIN_RECIPIENT_EMAIL


def _clean_error_msg(exc: Exception) -> str:
    """Format WebDriver and general exceptions into concise single-line messages without raw C++ chromedriver stack traces."""
    raw = getattr(exc, "msg", None) or str(exc)
    if "Stacktrace:" in raw:
        raw = raw.split("Stacktrace:")[0]
    first_line = raw.strip().split("\n")[0].strip()
    return first_line or str(exc)


@dataclass
class SessionReport:
    """Holds structured watch time data for an automation or watching session."""
    account_email: str
    session_mode: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    total_session_watch_seconds: float
    channel_reports: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "Completed"
    interruption_reason: Optional[str] = None
    error_message: Optional[str] = None
    total_videos_watched: int = 0
    regular_videos_count: int = 0
    shorts_count: int = 0
    custom_subject: Optional[str] = None
    suggested_videos_count: int = 0
    search_videos_count: int = 0
    home_videos_count: int = 0
    random_videos_count: int = 0
    search_queries_count: int = 0
    thread_url: Optional[str] = None
    thread_message_id: Optional[str] = None
    thread_references: Optional[List[str]] = None
    root_message_id: Optional[str] = None
    system_name: str = ""
    sleep_cycle_number: Optional[int] = None
    sleep_duration_hours: Optional[float] = None
    wake_up_time: Optional[datetime] = None
    sleep_mode_type: Optional[str] = None

    def generate_subject(self) -> str:
        """Produce a consistent subject line to ensure start and completion emails thread together in Gmail."""
        if self.custom_subject:
            if self.system_name and not self.custom_subject.startswith(self.system_name):
                return f"{self.system_name} - {self.custom_subject}"
            return self.custom_subject
        stamp = self.start_time.strftime("%d %b %Y - %I:%M %p")
        base = f"Entertainment Hub Session [{stamp}] - {self.account_email or 'Session'}"
        if self.system_name:
            return f"{self.system_name} - {base}"
        return base

    def generate_plain_text(self) -> str:
        """Produce a formatted plain-text report for terminal/log output and email."""
        start_str = self.start_time.strftime("%Y-%m-%d %H:%M:%S")
        end_str = self.end_time.strftime("%Y-%m-%d %H:%M:%S")
        session_elapsed = format_duration(self.duration_seconds)
        total_watch = format_duration(self.total_session_watch_seconds)
        total_hours = format_hours(self.total_session_watch_seconds)

        exit_reason = self.interruption_reason or ("Completed normally" if "complet" in self.status.lower() else self.status)

        if self.sleep_mode_type == "night":
            title_header = "      ENTERTAINMENT HUB - LONG SLEEP / NIGHT NOTIFICATION      "
        elif self.sleep_mode_type == "night_resume":
            title_header = "    ENTERTAINMENT HUB - NIGHT MODE COMPLETED • MORNING RESUMPTION    "
        elif self.sleep_mode_type == "warmup":
            c_num = f" (DAY #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            title_header = f"   ENTERTAINMENT HUB - WARM-UP DAILY LIMIT REACHED • SLEEP{c_num}   "
        elif self.sleep_mode_type == "warmup_resume":
            c_num = f" (DAY #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            title_header = f"   ENTERTAINMENT HUB - WARM-UP RESUMED • NEW DAY UNLOCKED{c_num}   "
        elif self.sleep_mode_type == "nap_resume":
            c_num = f" (NAP CYCLE #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            title_header = f"   ENTERTAINMENT HUB - NAP FINISHED • RESUMING SESSION{c_num}   "
        elif self.sleep_cycle_number or self.sleep_mode_type == "nap":
            c_num = f" (NAP CYCLE #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            title_header = f"      ENTERTAINMENT HUB - SHORT SLEEP / NAP NOTIFICATION{c_num}      "
        else:
            title_header = "                  ENTERTAINMENT HUB - SESSION REPORT                  "

        lines = [
            "=" * 70,
            title_header,
            "=" * 70,
            f"System Name:          {self.system_name or 'N/A'}",
            f"Account:              {self.account_email or 'N/A'}",
            f"Session Mode:         {self.session_mode}",
            f"Status:               {self.status}",
        ]

        if self.sleep_mode_type == "night":
            dur_str = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else "N/A"
            wake_str = self.wake_up_time.strftime("%Y-%m-%d %H:%M:%S") if self.wake_up_time else "N/A"
            lines.extend([
                f"Sleep Mode:           Long Sleep / Night Mode (Human System Time Schedule)",
                f"Sleep Duration:       {dur_str}",
                f"Estimated Wake-up:    {wake_str}",
                f"Notice:               System nighttime schedule reached. Simulating natural human habits,",
                f"                      the bot has gone to sleep for the night.",
                f"                      Closed YouTube browser tab to save resources.",
                f"                      The bot will wake up automatically in the morning.",
            ])
        elif self.sleep_mode_type == "night_resume":
            lines.extend([
                f"Sleep Mode:           Long Sleep / Night Mode Completed (Morning Wake-up)",
                f"Notice:               Nighttime sleep window has concluded. The bot has successfully",
                f"                      woken up, refreshed session stats, verified account sign-in,",
                f"                      and resumed daytime browsing for targeted channels.",
            ])
        elif self.sleep_mode_type == "warmup":
            dur_str = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else "N/A"
            wake_str = self.wake_up_time.strftime("%Y-%m-%d %H:%M:%S") if self.wake_up_time else "N/A"
            cycle_str = f"Day #{self.sleep_cycle_number}" if self.sleep_cycle_number else "N/A"
            is_ch = "channel" in (self.status or "").lower()
            mode_title = "Channel Warm-Up Daily Limit Reached (Sleeping until Midnight)" if is_ch else "Account Warm-Up Daily Limit Reached (Sleeping until Midnight)"
            notice_msg = "All targeted channels reached their allowed warm-up watch quota for today." if is_ch else "The account reached its allowed warm-up watch quota for today."
            lines.extend([
                f"Sleep Mode:           {mode_title}",
                f"Warm-Up Stage:        {cycle_str}",
                f"Sleep Duration:       {dur_str}",
                f"Estimated Wake-up:    {wake_str}",
                f"Notice:               {notice_msg}",
                f"                      Closed YouTube browser tab to preserve profile resources.",
                f"                      The bot will wake up automatically after midnight to begin the next day.",
            ])
        elif self.sleep_mode_type == "warmup_resume":
            cycle_str = f"Day #{self.sleep_cycle_number}" if self.sleep_cycle_number else "N/A"
            is_ch = "channel" in (self.status or "").lower()
            notice_target = "All targeted channels have advanced" if is_ch else "The account has advanced"
            lines.extend([
                f"Sleep Mode:           Warm-Up Midnight Rollover • New Day Resumed",
                f"Warm-Up Stage:        {cycle_str}",
                f"Notice:               Midnight has passed! {notice_target} to the next warm-up",
                f"                      ladder step with an incremented daily watch quota. Browsing resumed.",
            ])
        elif self.sleep_mode_type == "nap_resume":
            cycle_str = f"#{self.sleep_cycle_number}" if self.sleep_cycle_number else "N/A"
            dur_str = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else ""
            dur_line = f" ({dur_str})" if dur_str else ""
            lines.extend([
                f"Sleep Mode:           Short Sleep / Nap Mode Completed{dur_line}",
                f"Sleep Cycle:          {cycle_str}",
                f"Notice:               Nap cycle has concluded. The bot has successfully woken up,",
                f"                      refreshed session stats, verified account sign-in, and resumed",
                f"                      continuous browsing for targeted channel videos and shorts.",
            ])
        elif self.sleep_cycle_number or self.sleep_mode_type == "nap":
            dur_str = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else "N/A"
            wake_str = self.wake_up_time.strftime("%Y-%m-%d %H:%M:%S") if self.wake_up_time else "N/A"
            cycle_str = f"#{self.sleep_cycle_number}" if self.sleep_cycle_number else "N/A"
            lines.extend([
                f"Sleep Mode:           Short Sleep / Nap Mode (No Target Content Found)",
                f"Sleep Cycle:          {cycle_str}",
                f"Sleep Duration:       {dur_str}",
                f"Estimated Wake-up:    {wake_str}",
                f"Notice:               No target channel content found in this pass.",
                f"                      Closed YouTube browser tab to save resources.",
                f"                      The bot is taking a nap and will wake up automatically.",
            ])

        lines.extend([
            f"Interruption Reason:  {exit_reason}",
            f"Videos Watched:       {self.total_videos_watched} ({self.regular_videos_count} regular, {self.shorts_count} Shorts)",
            f"  • Suggested Section:   {self.suggested_videos_count} video(s)",
            f"  • YouTube Search:      {self.search_videos_count} video(s) ({self.search_queries_count} searches performed)",
            f"  • Home Page Feed:      {self.home_videos_count} video(s)",
            f"  • YouTube Shorts:      {self.shorts_count} short(s)",
            f"  • Random / Other:      {self.random_videos_count} video(s) (non-target channels)",
        ])
        if self.error_message and self.error_message != self.interruption_reason:
            lines.append(f"Error Details:        {self.error_message}")

        lines.extend([
            f"Started At:           {start_str}",
            f"Completed At:         {end_str}",
            f"Total Elapsed Time:   {session_elapsed}",
            "-" * 70,
            f"COMBINED ENTERTAINMENT TIME: {total_watch} ({total_hours})",
            "-" * 70,
            "PER-CHANNEL ENTERTAINMENT & TARGET GOAL PROGRESS:",
        ])

        active_channels = sorted(
            [c for c in self.channel_reports if c.get("session_watch_seconds", 0.0) > 0],
            key=lambda c: c.get("session_watch_seconds", 0.0),
            reverse=True,
        )

        if not active_channels:
            lines.append("  (No entertainment was logged for any channel in this session)")
        else:
            header = f"  {'Channel Name':<22} | {'This Session':<21} | {'Target Progress':<25} | {'Videos Watched'}"
            lines.append(header)
            lines.append("  " + "-" * 91)

            for ch in active_channels:
                name = ch.get("name", "Unknown")[:20]
                ses_sec = ch.get("session_watch_seconds", 0.0)
                tgt_txt = ch.get("target_goal_text", "No target set")[:25]
                tot_vids = ch.get("total_videos_count")
                if tot_vids is None:
                    tot_vids = ch.get("regular_videos_count", 0) + ch.get("shorts_count", 0)
                vids_fmt = f"{tot_vids} video(s)"
                ses_fmt = f"{format_duration(ses_sec)} ({format_hours(ses_sec)})"
                lines.append(f"  {name:<22} | {ses_fmt:<21} | {tgt_txt:<25} | {vids_fmt}")

        lines.extend([
            "=" * 65,
            "Report automatically generated by Entertainment Hub.",
        ])
        return "\n".join(lines)

    def generate_html_body(self) -> str:
        """Produce the inner HTML dashboard body suitable for web composer injection and modern email."""
        start_str = self.start_time.strftime("%Y-%m-%d %H:%M:%S")
        end_str = self.end_time.strftime("%Y-%m-%d %H:%M:%S")
        session_elapsed = format_duration(self.duration_seconds)
        total_watch = format_duration(self.total_session_watch_seconds)
        total_hours = format_hours(self.total_session_watch_seconds)

        active_channels = sorted(
            [c for c in self.channel_reports if c.get("session_watch_seconds", 0.0) > 0],
            key=lambda c: c.get("session_watch_seconds", 0.0),
            reverse=True,
        )

        rows_html = []
        for ch in active_channels:
            name = html.escape(ch.get("name", "Unknown"))
            ch_url = ch.get("url", "")
            ses_sec = ch.get("session_watch_seconds", 0.0)
            init_sec = ch.get("initial_watch_seconds", 0.0)
            fin_sec = ch.get("final_watch_seconds", 0.0)
            tgt_sec = ch.get("target_watch_seconds", 0.0)
            tgt_text = html.escape(ch.get("target_goal_text", "No target set"))

            pct = 0.0
            if tgt_sec > 0:
                pct = min(100.0, max(0.0, (fin_sec / tgt_sec) * 100.0))

            if ch.get("target_achieved"):
                tgt_badge = f'<span style="display: inline-block; background-color: #d1fae5; color: #047857; padding: 3px 8px; border-radius: 12px; font-weight: 700; font-size: 11px; border: 1px solid #a7f3d0;">{tgt_text}</span>'
                bar_color = "#059669"
            elif "No target" not in tgt_text:
                tgt_badge = f'<span style="display: inline-block; background-color: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 12px; font-weight: 600; font-size: 11px; border: 1px solid #bae6fd;">{tgt_text}</span>'
                bar_color = "#0284c7"
            else:
                tgt_badge = f'<span style="color: #94a3b8; font-size: 11px;">{tgt_text}</span>'
                bar_color = "#94a3b8"

            progress_bar = f"""
                <div style="background-color: #e2e8f0; border-radius: 9999px; height: 6px; width: 110px; overflow: hidden; margin-top: 5px;">
                    <div style="background-color: {bar_color}; width: {pct:.1f}%; height: 100%; border-radius: 9999px;"></div>
                </div>
            """ if tgt_sec > 0 else ""

            tot_vids = ch.get("total_videos_count")
            if tot_vids is None:
                tot_vids = ch.get("regular_videos_count", 0) + ch.get("shorts_count", 0)
            reg_cnt = ch.get("regular_videos_count", 0)
            sh_cnt = ch.get("shorts_count", 0)
            if sh_cnt > 0:
                vids_html = f"<strong>{tot_vids}</strong> video(s)<br><span style=\"font-size: 11px; color: #64748b;\">({reg_cnt} vids, {sh_cnt} shorts)</span>"
            else:
                vids_html = f"<strong>{tot_vids}</strong> video(s)"

            link_html = f'<br><a href="{html.escape(ch_url)}" target="_blank" style="display: inline-block; font-size: 11px; color: #0284c7; text-decoration: none; font-weight: 600; margin-top: 3px;">↗ Visit Channel</a>' if ch_url else ""

            rows_html.append(f"""
                <tr style="border-bottom: 1px solid #e2e8f0;">
                    <td style="padding: 12px 14px; font-weight: 600; color: #1e293b; vertical-align: middle;">{name}{link_html}</td>
                    <td style="padding: 12px 14px; color: #059669; font-weight: 700; vertical-align: middle;">{format_duration(ses_sec)} <span style="font-size: 11px; color: #64748b; font-weight: normal;">({format_hours(ses_sec)})</span></td>
                    <td style="padding: 12px 14px; font-size: 12px; vertical-align: middle;">{tgt_badge}{progress_bar}</td>
                    <td style="padding: 12px 14px; color: #475569; font-size: 12px; vertical-align: middle;">{vids_html}</td>
                </tr>
            """)

        if not rows_html:
            rows_html.append("""
                <tr>
                    <td colspan="4" style="padding: 16px; text-align: center; color: #94a3b8;">No channels logged entertainment in this session.</td>
                </tr>
            """)

        is_night_sleep = (self.sleep_mode_type == "night")
        is_night_resume = (self.sleep_mode_type == "night_resume")
        is_warmup_sleep = (self.sleep_mode_type == "warmup")
        is_warmup_resume = (self.sleep_mode_type == "warmup_resume")
        is_nap_resume = (self.sleep_mode_type == "nap_resume")
        is_nap_sleep = (self.sleep_mode_type == "nap" or (self.sleep_cycle_number is not None and not is_night_sleep and not is_night_resume and not is_warmup_sleep and not is_warmup_resume and not is_nap_resume))

        if is_night_sleep:
            status_color = "#6366f1"
            report_title = "Long Sleep / Night Notification"
            activity_header = "Night Mode • Daytime Activity Summary"
            kpi_label_1 = "Combined Entertainment Time (Daytime Pass)"
            kpi_label_2 = "Pass Duration & Status"
            kpi_status_text = "Going to Long Sleep (Night Mode)"
        elif is_night_resume:
            status_color = "#059669"
            report_title = "Night Mode Completed • Daytime Session Resumed"
            activity_header = "Morning Wake-Up • Media Activity Resuming"
            kpi_label_1 = "Combined Entertainment Time (Fresh Pass)"
            kpi_label_2 = "Session Status"
            kpi_status_text = "☀️ Morning Wake-up: Daytime Browsing Resumed"
        elif is_warmup_sleep:
            status_color = "#ea580c"
            cycle_desc = f" (Day #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            is_ch = "channel" in (self.status or "").lower()
            prefix = "Channel " if is_ch else "Account "
            report_title = f"{prefix}Warm-Up Daily Quota Reached • Sleep Mode{cycle_desc}"
            activity_header = f"{prefix}Warm-Up Cycle{cycle_desc} • Sleeping until Midnight"
            kpi_label_1 = "Watch Time Completed Today"
            kpi_label_2 = "Warm-Up Status"
            kpi_status_text = f"🔥 {prefix}Daily Quota Reached: Sleeping until Midnight"
        elif is_warmup_resume:
            status_color = "#059669"
            cycle_desc = f" (Day #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            is_ch = "channel" in (self.status or "").lower()
            prefix = "Channel " if is_ch else ""
            report_title = f"{prefix}Warm-Up Resumed • Midnight Rollover{cycle_desc}"
            activity_header = f"{prefix}Warm-Up Day #{self.sleep_cycle_number} • New Quota Unlocked" if self.sleep_cycle_number else f"{prefix}Warm-Up New Day • Browsing Resumed"
            kpi_label_1 = "Combined Entertainment Time (Fresh Pass)"
            kpi_label_2 = "Session Status"
            kpi_status_text = "☀️ Midnight Passed: New Warm-Up Window Active"
        elif is_nap_resume:
            status_color = "#059669"
            cycle_desc = f" (Nap Cycle #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            report_title = f"Short Sleep / Nap Finished • Resuming Session{cycle_desc}"
            activity_header = f"Sleep Cycle #{self.sleep_cycle_number} Completed • Resuming Browsing" if self.sleep_cycle_number else "Nap Completed • Resuming Browsing"
            kpi_label_1 = "Combined Entertainment Time (Fresh Pass)"
            kpi_label_2 = "Session Status"
            kpi_status_text = f"Nap Finished: Resuming Session (Cycle #{self.sleep_cycle_number})" if self.sleep_cycle_number else "Nap Finished: Resuming Session"
        elif is_nap_sleep:
            status_color = "#2563eb"
            cycle_desc = f" (Nap Cycle #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            report_title = f"Short Sleep / Nap Notification{cycle_desc}"
            activity_header = f"Sleep Cycle #{self.sleep_cycle_number} • Media Activity" if self.sleep_cycle_number else "Short Sleep / Nap • Media Activity"
            kpi_label_1 = "Combined Entertainment Time (This Pass)"
            kpi_label_2 = "Pass Duration & Status"
            kpi_status_text = f"Going to Nap (Cycle #{self.sleep_cycle_number})" if self.sleep_cycle_number else "Going to Nap"
        else:
            status_color = "#059669" if "complet" in self.status.lower() else "#d97706"
            report_title = "Session Entertainment Report"
            activity_header = "Media Entertainment Activity"
            kpi_label_1 = "Combined Entertainment Time"
            kpi_label_2 = "Session Duration & Status"
            kpi_status_text = f"Status: {html.escape(self.status)}"

        sleep_banner = ""
        if is_warmup_sleep:
            cycle_desc = f" (Day #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            wake_str = self.wake_up_time.strftime("%d %b %Y - %I:%M %p") if self.wake_up_time else "midnight"
            dur_txt = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else "until midnight"
            is_ch = "channel" in (self.status or "").lower()
            banner_title = f"🔥 Channel Warm-Up Quota Reached — Sleeping until Midnight{cycle_desc}" if is_ch else f"🔥 Account Warm-Up Quota Reached — Sleeping until Midnight{cycle_desc}"
            banner_desc = "All targeted channels have successfully fulfilled their configured warm-up watch quotas for today." if is_ch else "The account has successfully fulfilled its configured warm-up watch quota for today."
            sleep_banner = f"""
            <div style="background: linear-gradient(135deg, #431407 0%, #7c2d12 100%); border: 1px solid #9a3412; border-left: 6px solid #f97316; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
                <div style="font-size: 16px; font-weight: 700; color: #ffedd5; margin-bottom: 5px;">{banner_title}</div>
                <div style="font-size: 13px; color: #fed7aa; line-height: 1.5;">
                    {banner_desc}<br>
                    The YouTube browser tab has been closed to save resources. The bot is sleeping for <strong>{dur_txt}</strong> and will automatically wake up after midnight at approximately <strong>{wake_str}</strong> to unlock the next warm-up increment.<br>
                    <span style="font-size: 12px; color: #fdba74; margin-top: 4px; display: inline-block;">Below are the stats of media watched today prior to sleeping.</span>
                </div>
            </div>
            """
        elif is_warmup_resume:
            cycle_desc = f" (Day #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            is_ch = "channel" in (self.status or "").lower()
            banner_title = f"⚡ Channel Warm-Up Resumed — Midnight Rollover Completed{cycle_desc}" if is_ch else f"⚡ Warm-Up Resumed — Midnight Rollover Completed{cycle_desc}"
            banner_desc = "Midnight has passed! All targeted channels have advanced to the next warm-up ladder day with increased daily watch quotas." if is_ch else "Midnight has passed! The account has advanced to the next warm-up ladder day with an increased daily watch quota."
            sleep_banner = f"""
            <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-left: 5px solid #16a34a; border-radius: 8px; padding: 14px 18px; margin-bottom: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
                <div style="font-size: 15px; font-weight: 700; color: #166534; margin-bottom: 4px;">{banner_title}</div>
                <div style="font-size: 13px; color: #15803d; line-height: 1.5;">
                    {banner_desc}<br>
                    The browser has been relaunched with fresh stats and browsing is active.<br>
                    <span style="font-size: 12px; color: #16a34a; margin-top: 4px; display: inline-block;">Warm-up progression continues.</span>
                </div>
            </div>
            """
        elif is_night_resume:
            sleep_banner = f"""
            <div style="background: linear-gradient(135deg, #064e3b 0%, #065f46 100%); border: 1px solid #047857; border-left: 6px solid #34d399; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
                <div style="font-size: 16px; font-weight: 700; color: #ecfdf5; margin-bottom: 5px;">☀️ Morning Wake-Up — Long Sleep / Night Mode Completed</div>
                <div style="font-size: 13px; color: #a7f3d0; line-height: 1.5;">
                    The scheduled nighttime sleep window has finished.<br>
                    The bot has automatically woken up, refreshed session stats, verified account sign-in, and resumed daytime browsing for targeted channels.<br>
                    <span style="font-size: 12px; color: #6ee7b7; margin-top: 4px; display: inline-block;">Daytime browsing session is now running normally.</span>
                </div>
            </div>
            """
        elif is_nap_resume:
            cycle_desc = f" (Nap Cycle #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            sleep_banner = f"""
            <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-left: 5px solid #16a34a; border-radius: 8px; padding: 14px 18px; margin-bottom: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
                <div style="font-size: 15px; font-weight: 700; color: #166534; margin-bottom: 4px;">⚡ Woke Up — Nap Mode Finished • Resuming Session{cycle_desc}</div>
                <div style="font-size: 13px; color: #15803d; line-height: 1.5;">
                    The nap sleep cycle has finished.<br>
                    The bot has automatically woken up, refreshed session stats, verified account sign-in, and resumed continuous browsing for new videos and shorts from targeted channels.<br>
                    <span style="font-size: 12px; color: #16a34a; margin-top: 4px; display: inline-block;">Session is now active and looking for target videos.</span>
                </div>
            </div>
            """
        elif is_night_sleep:
            wake_str = self.wake_up_time.strftime("%d %b %Y - %I:%M %p") if self.wake_up_time else f"in {self.sleep_duration_hours:.1f} hours"
            dur_txt = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else "configured duration"
            sleep_banner = f"""
            <div style="background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); border: 1px solid #4338ca; border-left: 6px solid #818cf8; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
                <div style="font-size: 16px; font-weight: 700; color: #e0e7ff; margin-bottom: 5px;">🌙 Going to Sleep — Long Sleep / Night Mode</div>
                <div style="font-size: 13px; color: #c7d2fe; line-height: 1.5;">
                    System nighttime window has been reached. Simulating natural human habits, the bot has gone to sleep for the night.<br>
                    The YouTube browser tab has been closed. The bot is sleeping for <strong>{dur_txt}</strong> and will automatically wake up at approximately <strong>{wake_str}</strong> to start fresh daytime browsing.<br>
                    <span style="font-size: 12px; color: #a5b4fc; margin-top: 4px; display: inline-block;">Below are the stats of what was completed during daytime browsing prior to sleeping.</span>
                </div>
            </div>
            """
        elif is_nap_sleep:
            wake_str = self.wake_up_time.strftime("%d %b %Y - %I:%M %p") if self.wake_up_time else f"in {self.sleep_duration_hours:.1f} hours"
            dur_txt = f"{self.sleep_duration_hours:.1f} hour(s)" if self.sleep_duration_hours else "configured duration"
            cycle_desc = f" (Nap Cycle #{self.sleep_cycle_number})" if self.sleep_cycle_number else ""
            sleep_banner = f"""
            <div style="background-color: #eff6ff; border: 1px solid #bfdbfe; border-left: 5px solid #2563eb; border-radius: 8px; padding: 14px 18px; margin-bottom: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
                <div style="font-size: 15px; font-weight: 700; color: #1e40af; margin-bottom: 4px;">💤 Going to Sleep — Short Sleep / Nap Mode{cycle_desc}</div>
                <div style="font-size: 13px; color: #1e3a8a; line-height: 1.5;">
                    No new videos or shorts from targeted channels were found during this pass.<br>
                    The YouTube browser tab has been closed. The bot is taking a nap for <strong>{dur_txt}</strong> and will automatically wake up at approximately <strong>{wake_str}</strong> to check for new videos and shorts with fresh stats.<br>
                    <span style="font-size: 12px; color: #2563eb; margin-top: 4px; display: inline-block;">Below are the stats of what was completed during this pass prior to sleeping.</span>
                </div>
            </div>
            """

        error_box = ""
        if self.interruption_reason and ("error" in self.status.lower() or "bot" in self.status.lower() or "disconnect" in self.status.lower() or "proxy" in self.status.lower() or "stop" in self.status.lower()):
            error_box = f"""
            <div style="background-color: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; color: #9a3412; font-size: 13px;">
                <strong style="color: #c2410c;">Interruption / Exit Reason:</strong> {html.escape(self.interruption_reason)}
            </div>
            """
        elif self.error_message:
            error_box = f"""
            <div style="background-color: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; color: #991b1b; font-size: 13px;">
                <strong>Notice:</strong> {html.escape(self.error_message)}
            </div>
            """

        return f"""
    <div style="max-width: 680px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1); border: 1px solid #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 26px 24px; color: #ffffff;">
            <div style="display: table; width: 100%;">
                <div style="display: table-cell; vertical-align: middle;">
                    <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #38bdf8; font-weight: 700; margin-bottom: 4px;">{activity_header}</div>
                    <h1 style="margin: 0; font-size: 22px; font-weight: 800; line-height: 1.3;">{report_title}</h1>
                    <div style="margin-top: 8px; font-size: 13px; color: #94a3b8;">
                        <div style="margin-bottom: 4px; font-size: 14px;">System Name: <span style="color: #38bdf8; font-weight: 700;">{html.escape(self.system_name or 'N/A')}</span></div>
                        <div>Account: <span style="color: #f1f5f9; font-weight: 600;">{html.escape(self.account_email or 'N/A')}</span> | Mode: <span style="color: #f1f5f9; font-weight: 600;">{html.escape(self.session_mode)}</span></div>
                    </div>
                </div>
            </div>
            </div>
        </div>

        <div style="padding: 24px;">
            {sleep_banner}
            {error_box}

            <!-- KPI Cards -->
            <div style="display: table; width: 100%; margin-bottom: 16px;">
                <div style="display: table-cell; width: 48%; background-color: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 10px; padding: 16px; vertical-align: top;">
                    <div style="font-size: 11px; text-transform: uppercase; font-weight: 700; color: #047857; letter-spacing: 0.5px;">{kpi_label_1}</div>
                    <div style="font-size: 24px; font-weight: 800; color: #065f46; margin-top: 4px;">{total_watch}</div>
                    <div style="font-size: 12px; color: #047857; margin-top: 2px;">{total_hours}</div>
                </div>
                <div style="display: table-cell; width: 4%;"></div>
                <div style="display: table-cell; width: 48%; background-color: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 10px; padding: 16px; vertical-align: top;">
                    <div style="font-size: 11px; text-transform: uppercase; font-weight: 700; color: #475569; letter-spacing: 0.5px;">{kpi_label_2}</div>
                    <div style="font-size: 20px; font-weight: 700; color: #1e293b; margin-top: 4px;">{session_elapsed}</div>
                    <div style="font-size: 12px; color: {status_color}; font-weight: 600; margin-top: 2px;">{kpi_status_text}</div>
                </div>
            </div>

            <!-- Videos Watched & Ordered Discovery Source Breakdown Card -->
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 24px; box-sizing: border-box;">
                <div style="margin-bottom: 12px;">
                    <span style="font-size: 11px; text-transform: uppercase; font-weight: 700; color: #475569; letter-spacing: 0.5px;">Total Videos Watched: </span>
                    <strong style="font-size: 18px; color: #0f172a; margin-left: 4px;">{self.total_videos_watched}</strong>
                    <span style="font-size: 12px; color: #64748b; margin-left: 8px;">({self.regular_videos_count} regular, {self.shorts_count} Shorts)</span>
                </div>
                <div style="border-top: 1px solid #e2e8f0; padding-top: 12px; display: table; width: 100%;">
                    <div style="display: table-cell; width: 20%; vertical-align: top; padding-right: 6px;">
                        <div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">🎬 Suggested Section</div>
                        <div style="font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 2px;">{self.suggested_videos_count} <span style="font-size: 11px; font-weight: normal; color: #64748b;">watched</span></div>
                    </div>
                    <div style="display: table-cell; width: 20%; vertical-align: top; padding-right: 6px;">
                        <div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">🔍 YouTube Search</div>
                        <div style="font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 2px;">{self.search_videos_count} <span style="font-size: 11px; font-weight: normal; color: #64748b;">watched</span></div>
                        <div style="font-size: 11px; color: #64748b; margin-top: 1px;">({self.search_queries_count} searches)</div>
                    </div>
                    <div style="display: table-cell; width: 20%; vertical-align: top; padding-right: 6px;">
                        <div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">🏠 Homepage Feed</div>
                        <div style="font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 2px;">{self.home_videos_count} <span style="font-size: 11px; font-weight: normal; color: #64748b;">watched</span></div>
                    </div>
                    <div style="display: table-cell; width: 20%; vertical-align: top; padding-right: 6px;">
                        <div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">⚡ YouTube Shorts</div>
                        <div style="font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 2px;">{self.shorts_count} <span style="font-size: 11px; font-weight: normal; color: #64748b;">watched</span></div>
                    </div>
                    <div style="display: table-cell; width: 20%; vertical-align: top;">
                        <div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">🎲 Random / Other</div>
                        <div style="font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 2px;">{self.random_videos_count} <span style="font-size: 11px; font-weight: normal; color: #64748b;">watched</span></div>
                        <div style="font-size: 11px; color: #64748b; margin-top: 1px;">(non-target)</div>
                    </div>
                </div>
            </div>

            <!-- Meta details -->
            <div style="background-color: #f8fafc; border-radius: 8px; padding: 12px 16px; font-size: 12px; color: #64748b; margin-bottom: 24px;">
                <span style="font-weight: 600; color: #475569;">Started:</span> {start_str} &nbsp;|&nbsp;
                <span style="font-weight: 600; color: #475569;">Ended:</span> {end_str} &nbsp;|&nbsp;
                <span style="font-weight: 600; color: #475569;">Channels Played:</span> {len(active_channels)}
            </div>

            <!-- Breakdown Table -->
            <h3 style="font-size: 15px; font-weight: 700; margin: 0 0 12px 0; color: #0f172a;">Per-Channel Entertainment Breakdown</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                <thead>
                    <tr style="background-color: #f1f5f9; border-bottom: 2px solid #cbd5e1;">
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Channel Name</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">This Session</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Target Progress</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Videos Watched</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>

            <!-- Action Buttons -->
            <div style="margin-top: 28px; padding-top: 20px; border-top: 1px solid #e2e8f0; text-align: center;">
                <a href="https://www.youtube.com" target="_blank" style="display: inline-block; background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); color: #ffffff; padding: 10px 22px; border-radius: 8px; font-weight: 700; text-decoration: none; font-size: 13px; box-shadow: 0 2px 4px rgba(239,68,68,0.25); margin: 0 6px;">▶ Open YouTube</a>
                <a href="https://studio.youtube.com" target="_blank" style="display: inline-block; background: #ffffff; color: #1e293b; border: 1px solid #cbd5e1; padding: 9px 20px; border-radius: 8px; font-weight: 600; text-decoration: none; font-size: 13px; margin: 0 6px;">📊 YouTube Studio</a>
            </div>

            <!-- Footer -->
            <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #f1f5f9; font-size: 11px; color: #94a3b8; text-align: center;">
                Generated automatically by Entertainment Hub. All entertainment durations are measured from active playback.
            </div>
        </div>
    </div>
        """

    def generate_html(self) -> str:
        """Produce a clean, modern HTML email report compatible with Gmail and mobile."""
        body_content = self.generate_html_body()
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Entertainment Report</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
{body_content}
</body>
</html>""".strip()


class EmailReporter:
    """Manages report and notification delivery via headless Chrome Gmail, active browser, or SMTP."""

    last_thread_url: Optional[str] = None
    last_message_id: Optional[str] = None
    _recent_alert_times: Dict[tuple, float] = {}

    @classmethod
    def reset_session_thread(cls) -> None:
        """Reset cached class-level thread IDs and URLs when a new session starts."""
        cls.last_message_id = None
        cls.last_thread_url = None
        cls._recent_alert_times.clear()

    @classmethod
    def send_via_smtp(
        cls,
        report: SessionReport,
        recipient_email: str,
        bcc_email: Optional[str] = None,
        host: str = "smtp.gmail.com",
        port: int = 587,
        username: str = "",
        password: str = "",
        sender_email: Optional[str] = None,
        log_queue: Optional[queue.Queue] = None,
        thread_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Deliver report email using SMTP with TLS or SSL."""
        if not recipient_email or "@" not in recipient_email:
            if log_queue:
                emit(log_queue, f"Email report skipped: invalid recipient '{recipient_email}'.", logging.WARNING)
            return False

        sender = (sender_email or username or report.account_email or "").strip()
        if not sender or "@" not in sender:
            if log_queue:
                emit(log_queue, "SMTP report skipped: sender email address is not configured.", logging.WARNING)
            return False

        if not password:
            if log_queue:
                emit(log_queue, "SMTP report skipped: password / app password is not configured.", logging.WARNING)
            return False

        subject = report.generate_subject()
        plain_text = report.generate_plain_text()
        html_content = report.generate_html()

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient_email

        domain = sender.split("@")[-1] if "@" in sender else "yttool.app"
        msg_id = make_msgid(domain=domain)
        msg["Message-ID"] = msg_id

        actual_thread_id = (
            getattr(report, "thread_message_id", None)
            or (thread_info.get("message_id") if isinstance(thread_info, dict) else None)
            or cls.last_message_id
        )

        refs_list: List[str] = []
        if isinstance(thread_info, dict) and thread_info.get("references"):
            refs_list = list(thread_info["references"])
        elif getattr(report, "thread_references", None):
            refs_list = list(report.thread_references)
        elif actual_thread_id:
            refs_list = [actual_thread_id]

        if actual_thread_id:
            msg["In-Reply-To"] = actual_thread_id
            if actual_thread_id not in refs_list:
                refs_list.append(actual_thread_id)
            msg["References"] = " ".join(refs_list)

        cls.last_message_id = msg_id
        if thread_info is not None and isinstance(thread_info, dict):
            if not thread_info.get("root_message_id"):
                thread_info["root_message_id"] = actual_thread_id or msg_id
            if "references" not in thread_info or not isinstance(thread_info["references"], list):
                thread_info["references"] = list(refs_list)
            if msg_id not in thread_info["references"]:
                thread_info["references"].append(msg_id)
            thread_info["message_id"] = msg_id
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        try:
            if log_queue:
                emit(log_queue, f"Connecting to SMTP server {host}:{port} to send session report...")

            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=25)
            else:
                server = smtplib.SMTP(host, port, timeout=25)
                server.ehlo()
                server.starttls()
                server.ehlo()

            user_login = (username or sender).strip()
            server.login(user_login, password)
            all_recipients = [recipient_email]
            if bcc_email and "@" in bcc_email and bcc_email.lower() != recipient_email.lower():
                all_recipients.append(bcc_email)
            server.sendmail(sender, all_recipients, msg.as_string())
            server.quit()

            if log_queue:
                log_recipient = recipient_email if recipient_email.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else 'recipient'
                emit(log_queue, f"Session watch time report successfully emailed to '{log_recipient}' via SMTP.")
            return True

        except Exception as exc:
            if log_queue:
                emit(log_queue, f"SMTP email delivery failed: {exc}", logging.ERROR)
            return False

    @classmethod
    def _deliver_via_gmail_compose(
        cls,
        driver: Any,
        subject: str,
        plain_text: str,
        recipient_email: str,
        bcc_email: Optional[str] = None,
        log_queue: Optional[queue.Queue] = None,
        email_description: str = "report",
        thread_info: Optional[Dict[str, Any]] = None,
        html_content: Optional[str] = None,
        account: Optional[Account] = None,
        config_mgr: Optional[Any] = None,
    ) -> bool:
        """Core Gmail compose automation in Chrome (works for visible and headless Chrome)."""
        if not driver:
            if log_queue:
                emit(log_queue, f"Gmail {email_description} delivery failed: browser driver is unavailable.", logging.WARNING)
            return False

        is_start = "start" in email_description.lower()
        try:
            bcc_log = f" (BCC: {bcc_email})" if bcc_email and bcc_email.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else ""
            if log_queue and not is_start:
                log_recipient = recipient_email if recipient_email.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else 'recipient'
                emit(log_queue, f"Navigating to Gmail web compose to send {email_description} to '{log_recipient}'{bcc_log}...")

            compose_url = f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(recipient_email)}"
            if bcc_email and "@" in bcc_email and bcc_email.lower() != recipient_email.lower():
                compose_url += f"&bcc={quote(bcc_email)}"
            compose_url += f"&su={quote(subject)}"
            # Pre-populate body in URL query string when within safe URL limits only if not injecting HTML
            if not html_content and len(plain_text) < 7000:
                compose_url += f"&body={quote(plain_text)}"

            driver.get(compose_url)

            # Wait for Gmail to load or redirect
            start_wait = time.time()
            is_signed_in = True
            while time.time() - start_wait < 20:
                current_url = getattr(driver, "current_url", "") or ""
                # Check if compose box or any compose element has rendered
                if driver.find_elements(By.XPATH, "//div[@role='textbox'] | //input[@name='to' or @aria-label='To' or contains(@class, 'agP')]"):
                    is_signed_in = True
                    break
                # Check if redirected to accounts.google.com
                if "accounts.google.com" in current_url:
                    if any(p in current_url.lower() for p in ("/signin", "/servicelogin", "/identifier", "/v2/", "/v3/")):
                        time.sleep(2.0)
                        cur_after = getattr(driver, "current_url", "") or ""
                        if any(p in cur_after.lower() for p in ("/signin", "/servicelogin", "/identifier", "/v2/", "/v3/")):
                            is_signed_in = False
                            break
                time.sleep(1.0)

            if not is_signed_in:
                # If account is provided, attempt automatic Google sign-in
                if account and getattr(account, "email", "").strip() and getattr(account, "password", "").strip():
                    if log_queue:
                        emit(
                            log_queue,
                            f"Google account is not signed in for reporting browser. Attempting automatic sign-in for {account.email}...",
                            logging.INFO,
                        )
                    try:
                        from .login import perform_google_login
                        login_ok = perform_google_login(
                            driver,
                            account,
                            log_queue=log_queue,
                            config_mgr=config_mgr,
                        )
                        if login_ok:
                            if log_queue:
                                emit(log_queue, f"Sign-in successful for {account.email}! Re-opening Gmail compose...", logging.INFO)
                            driver.get(compose_url)
                            start_wait2 = time.time()
                            is_signed_in = True
                            while time.time() - start_wait2 < 20:
                                cur2 = getattr(driver, "current_url", "") or ""
                                if driver.find_elements(By.XPATH, "//div[@role='textbox'] | //input[@name='to' or @aria-label='To' or contains(@class, 'agP')]"):
                                    is_signed_in = True
                                    break
                                if "accounts.google.com" in cur2 and any(p in cur2.lower() for p in ("/signin", "/servicelogin", "/identifier", "/v2/", "/v3/")):
                                    is_signed_in = False
                                time.sleep(1.0)
                    except Exception as e_login:
                        if log_queue:
                            emit(log_queue, f"Notice during automatic reporting sign-in attempt: {e_login}", logging.DEBUG)

            if not is_signed_in:
                if log_queue:
                    acc_email = getattr(account, "email", "this profile") if account else "this profile"
                    emit(log_queue, f"Gmail {email_description} delivery failed: Google account ({acc_email}) is not signed in for this profile.", logging.ERROR)
                return False

            # Ensure Recipient is populated and committed
            recipient_clean = recipient_email.lower().strip()
            has_recipient = False
            chip_selectors = [
                f"//span[contains(translate(@email, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{recipient_clean}')]",
                f"//div[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{recipient_clean}')]",
                f"//span[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{recipient_clean}')]",
            ]
            for c_sel in chip_selectors:
                try:
                    chips = driver.find_elements(By.XPATH, c_sel)
                    if any(c.is_displayed() for c in chips):
                        has_recipient = True
                        break
                except Exception:
                    pass

            if not has_recipient:
                to_selectors = [
                    "//input[@name='to' or @aria-label='To' or @aria-label='To recipients']",
                    "//input[contains(@class, 'agP')]",
                    "//div[@role='combobox']//input",
                    "//div[@name='to']//input",
                ]
                for sel in to_selectors:
                    try:
                        fields = driver.find_elements(By.XPATH, sel)
                        for f in fields:
                            if f.is_displayed():
                                f.click()
                                time.sleep(0.3)
                                f.send_keys(recipient_email)
                                time.sleep(0.3)
                                f.send_keys(Keys.ENTER)
                                time.sleep(0.2)
                                f.send_keys(Keys.TAB)
                                has_recipient = True
                                break
                        if has_recipient:
                            break
                    except Exception:
                        continue

            # Locate the compose message body (with wait retry for Gmail to render)
            body_element = None
            body_selectors = [
                "//div[@role='textbox' and (contains(@aria-label, 'Message Body') or contains(@aria-label, 'Body'))]",
                "//div[contains(@class, 'editable') and @role='textbox']",
                "//div[@aria-label='Message Body']",
                "//div[@role='textbox']",
            ]
            start_wait = time.time()
            while time.time() - start_wait < 15:
                for sel in body_selectors:
                    try:
                        elems = driver.find_elements(By.XPATH, sel)
                        for el in elems:
                            if el.is_displayed():
                                body_element = el
                                break
                        if body_element:
                            break
                    except Exception:
                        continue
                if body_element:
                    break
                time.sleep(1.0)

            if not body_element:
                if log_queue:
                    emit(log_queue, "Gmail delivery failed: could not locate Gmail compose text area.", logging.ERROR)
                return False

            # Focus and write the body content if not already pre-populated via URL
            try:
                body_element.click()
            except Exception:
                pass
            time.sleep(0.5)

            curr_body = (body_element.text or "").strip()
            if html_content or not curr_body or len(curr_body) < 20:
                try:
                    driver.execute_script(
                        """
                        var el = arguments[0];
                        var html = arguments[1];
                        var text = arguments[2];
                        el.focus();
                        var inserted = false;
                        if (html && document.queryCommandSupported && document.queryCommandSupported('insertHTML')) {
                            try {
                                document.execCommand('selectAll', false, null);
                                inserted = document.execCommand('insertHTML', false, html);
                            } catch (e) {
                                inserted = false;
                            }
                        }
                        if (!inserted && html) {
                            try {
                                el.innerHTML = html;
                                inserted = true;
                            } catch (e) {
                                inserted = false;
                            }
                        }
                        if (!inserted) {
                            if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
                                document.execCommand('selectAll', false, null);
                                document.execCommand('insertText', false, text);
                            } else {
                                el.innerText = text;
                            }
                        }
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        """,
                        body_element,
                        html_content or "",
                        plain_text,
                    )
                except Exception:
                    lines = plain_text.split("\n")
                    for line in lines:
                        body_element.send_keys(line)
                        body_element.send_keys(Keys.SHIFT, Keys.ENTER)

            time.sleep(1.0)

            # Trigger Send
            sent_triggered = False
            send_btn_selectors = [
                "//div[@role='button' and @data-tooltip and contains(@data-tooltip, 'Send')]",
                "//div[@role='button' and @aria-label and contains(@aria-label, 'Send')]",
                "//div[@role='button' and (text()='Send' or contains(., 'Send'))]",
                "//div[contains(@class, 'T-I') and contains(@class, 'aoO')]",
            ]
            for sel in send_btn_selectors:
                try:
                    buttons = driver.find_elements(By.XPATH, sel)
                    for btn in buttons:
                        if btn.is_displayed():
                            try:
                                btn.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", btn)
                                except Exception:
                                    pass
                            sent_triggered = True
                            break
                    if sent_triggered:
                        break
                except Exception:
                    continue

            if not sent_triggered:
                try:
                    body_element.send_keys(Keys.CONTROL, Keys.ENTER)
                    sent_triggered = True
                except Exception:
                    pass

            if not sent_triggered:
                if log_queue:
                    emit(log_queue, f"Gmail {email_description} delivery failed: could not trigger Send button.", logging.ERROR)
                return False

            # Wait for delivery confirmation and dismiss any modal prompts
            delivery_confirmed = False
            start_confirm_wait = time.time()
            while time.time() - start_confirm_wait < 20:
                # 1. Accept any native or in-page alert/modal (e.g. 'Send without body/subject')
                try:
                    alert = driver.switch_to.alert
                    alert.accept()
                except Exception:
                    pass

                try:
                    dialog_btns = driver.find_elements(
                        By.XPATH,
                        "//div[@role='dialog']//button[contains(., 'OK') or contains(., 'Send') or contains(., 'Yes') or @name='ok']",
                    )
                    for d_btn in dialog_btns:
                        if d_btn.is_displayed():
                            try:
                                d_btn.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", d_btn)
                                except Exception:
                                    pass
                            break
                except Exception:
                    pass

                # 2. Check for 'Message sent' toast or notification
                success_indicators = [
                    "//span[contains(text(), 'Message sent') or contains(text(), 'Your message has been sent')]",
                    "//div[contains(@class, 'bAq') and contains(text(), 'Message sent')]",
                    "//div[@aria-live][contains(., 'Message sent') or contains(., 'sent')]",
                ]
                for s_sel in success_indicators:
                    try:
                        toasts = driver.find_elements(By.XPATH, s_sel)
                        if any(t.is_displayed() for t in toasts):
                            delivery_confirmed = True
                            break
                    except Exception:
                        pass
                if delivery_confirmed:
                    break

                # 3. Check if compose body disappeared and page is not in 'Sending...' state
                try:
                    body_visible = body_element.is_displayed()
                except Exception:
                    body_visible = False

                if not body_visible:
                    try:
                        sending_elems = driver.find_elements(By.XPATH, "//*[contains(text(), 'Sending...')]")
                        if not any(e.is_displayed() for e in sending_elems):
                            delivery_confirmed = True
                            break
                    except Exception:
                        delivery_confirmed = True
                        break

                time.sleep(1.0)

            if not delivery_confirmed:
                if log_queue:
                    emit(
                        log_queue,
                        f"Gmail {email_description} delivery unconfirmed: 'Message sent' confirmation not received within timeout.",
                        logging.WARNING,
                    )
                return False

            # Capture persistent thread URL from confirmation toast or opened thread
            captured_thread_url = None
            try:
                view_msg_links = driver.find_elements(
                    By.XPATH,
                    "//a[@id='link_vmsg' or contains(text(), 'View message') or contains(@aria-label, 'View message')]",
                )
                for v_link in view_msg_links:
                    href = v_link.get_attribute("href") or ""
                    if href:
                        if not href.startswith("http"):
                            base = (driver.current_url or "https://mail.google.com/mail/u/0/").split("#")[0]
                            captured_thread_url = f"{base}#{href.lstrip('#')}"
                        else:
                            captured_thread_url = href
                        try:
                            v_link.click()
                            time.sleep(1.2)
                            cur_after = driver.current_url or ""
                            if cur_after and any(k in cur_after for k in ("#sent/", "#inbox/", "#all/")) and len(cur_after.split("/")[-1]) > 5:
                                captured_thread_url = cur_after
                        except Exception:
                            pass
                        break
            except Exception:
                pass

            if not captured_thread_url:
                cur_u = driver.current_url or ""
                if any(x in cur_u for x in ("#sent/", "#inbox/", "#all/", "#search/")):
                    captured_thread_url = cur_u

            # If sending a start notification or thread_info is tracked, ensure we locate and park on the real thread in Sent folder
            is_valid_thread = (
                captured_thread_url
                and any(k in captured_thread_url for k in ("#sent/", "#inbox/", "#all/"))
                and len(captured_thread_url.split("/")[-1]) > 5
                and not captured_thread_url.endswith("#sent")
                and not captured_thread_url.endswith("#inbox")
            )

            if not is_valid_thread and ("start" in email_description.lower() or thread_info is not None):
                try:
                    if log_queue and not is_start:
                        emit(log_queue, "Navigating to Gmail Sent folder to open and capture persistent session email thread...")
                    driver.get("https://mail.google.com/mail/u/0/#sent")
                    sent_wait_start = time.time()
                    while time.time() - sent_wait_start < 15:
                        sent_rows = driver.find_elements(By.XPATH, "//tr[contains(@class, 'zA')]")
                        if sent_rows:
                            sent_rows[0].click()
                            time.sleep(2.0)
                            cur_thread = driver.current_url or ""
                            if any(k in cur_thread for k in ("#sent/", "#all/", "#inbox/")) and len(cur_thread.split("/")[-1]) > 5:
                                captured_thread_url = cur_thread
                                is_valid_thread = True
                            elif driver.find_elements(By.XPATH, "//div[@role='main']//h2 | //div[@role='textbox'] | //div[contains(@class, 'adn ads')]"):
                                captured_thread_url = cur_thread
                                is_valid_thread = True
                            break
                        time.sleep(1.0)
                except Exception as ex_sent:
                    if log_queue and not is_start:
                        emit(log_queue, f"Could not navigate to Sent folder: {ex_sent}", logging.DEBUG)

            if captured_thread_url:
                cls.last_thread_url = captured_thread_url
                if thread_info is not None and isinstance(thread_info, dict):
                    thread_info["thread_url"] = captured_thread_url
                    if not thread_info.get("root_thread_url"):
                        thread_info["root_thread_url"] = captured_thread_url
                if log_queue and is_valid_thread and not is_start:
                    emit(log_queue, f"Parked on session email thread: {captured_thread_url}")

            # Allow 3 seconds for background network sync before browser terminates
            time.sleep(3.0)
            if log_queue:
                if is_start:
                    emit(log_queue, "Notified successfully")
                else:
                    log_recipient = recipient_email if recipient_email.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else 'recipient'
                    emit(log_queue, f"Successfully sent {email_description} to '{log_recipient}' via Gmail.")
            return True

        except Exception as exc:
            if log_queue:
                emit(log_queue, f"Gmail {email_description} delivery error: {_clean_error_msg(exc)}", logging.ERROR)
            return False

    @classmethod
    def _deliver_via_gmail_thread_reply(
        cls,
        driver: Any,
        plain_text: str,
        thread_url: Optional[str] = None,
        subject: Optional[str] = None,
        recipient_email: Optional[str] = None,
        bcc_email: Optional[str] = None,
        log_queue: Optional[queue.Queue] = None,
        email_description: str = "report",
        thread_info: Optional[Dict[str, Any]] = None,
        html_content: Optional[str] = None,
        account: Optional[Account] = None,
        config_mgr: Optional[Any] = None,
    ) -> bool:
        """Reply directly to an existing session email thread in Gmail."""
        if not driver:
            return False

        try:
            target_url = thread_url
            current_url = driver.current_url or ""

            already_on_thread = False
            has_thread_id = (
                target_url
                and any(k in target_url for k in ("#sent/", "#inbox/", "#all/"))
                and len(target_url.split("/")[-1]) > 5
                and not target_url.endswith("#sent")
                and not target_url.endswith("#inbox")
            )

            # If target_url with specific thread id is provided, navigate directly to it
            if has_thread_id and target_url:
                if log_queue:
                    emit(log_queue, f"Navigating to existing session email thread ({target_url}) to send {email_description} as a reply...")
                driver.get(target_url)
            elif not target_url and (
                any(k in current_url for k in ("#sent/", "#inbox/", "#all/"))
                and len(current_url.split("/")[-1]) > 5
                and not current_url.endswith("#sent")
                and not current_url.endswith("#inbox")
            ):
                # Persistent background driver is already parked on the target session thread
                already_on_thread = True
            elif not target_url and subject:
                clean_subj = subject.strip().replace('"', '')
                search_query = f'subject:"{clean_subj}"'
                target_url = f"https://mail.google.com/mail/u/0/#search/{quote(search_query)}"
                if log_queue:
                    emit(log_queue, f"Searching for session email thread in Gmail...")
                driver.get(target_url)
            elif target_url:
                driver.get(target_url)
            else:
                if log_queue:
                    emit(log_queue, "Navigating to Gmail Sent mail to locate session email thread...")
                driver.get("https://mail.google.com/mail/u/0/#sent")

            if not already_on_thread:
                # Wait for thread or search results to load
                start_wait = time.time()
                is_signed_in = True
                while time.time() - start_wait < 20:
                    cur = getattr(driver, "current_url", "") or ""
                    if "accounts.google.com" in cur:
                        if any(p in cur.lower() for p in ("/signin", "/servicelogin", "/identifier", "/v2/", "/v3/")):
                            time.sleep(2.0)
                            cur_after = getattr(driver, "current_url", "") or ""
                            if any(p in cur_after.lower() for p in ("/signin", "/servicelogin", "/identifier", "/v2/", "/v3/")):
                                is_signed_in = False
                                break
                    is_direct_thread = bool(re.search(r"#(?:sent|inbox|all)/[a-zA-Z0-9_-]{6,}", cur))
                    if not is_direct_thread and ("#search" in cur or cur.rstrip("/").endswith("#sent")):
                        rows = driver.find_elements(By.XPATH, "//tr[contains(@class, 'zA')]")
                        if rows:
                            try:
                                rows[0].click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", rows[0])
                                except Exception:
                                    pass
                            time.sleep(2.0)
                            break
                    if driver.find_elements(By.XPATH, "//div[@role='button'][@aria-label='Reply' or contains(., 'Reply')] | //div[@role='textbox'] | //div[contains(@class, 'adn ads')]"):
                        break
                    time.sleep(1.0)

                if not is_signed_in:
                    if account and getattr(account, "email", "").strip() and getattr(account, "password", "").strip():
                        if log_queue:
                            emit(
                                log_queue,
                                f"Google account not signed in for reporting thread reply. Attempting automatic sign-in for {account.email}...",
                                logging.INFO,
                            )
                        try:
                            from .login import perform_google_login
                            login_ok = perform_google_login(
                                driver,
                                account,
                                log_queue=log_queue,
                                config_mgr=config_mgr,
                            )
                            if login_ok and target_url:
                                driver.get(target_url)
                                time.sleep(3.0)
                        except Exception:
                            return False
                    else:
                        return False

            # Ensure we are scrolled to the bottom of the conversation where Reply button lives
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
            time.sleep(1.0)

            # Locate Reply button and click to expand composer
            reply_opened = False
            reply_selectors = [
                "//div[@role='button' and @data-tooltip and contains(@data-tooltip, 'Reply')]",
                "//div[@role='button' and @aria-label and contains(@aria-label, 'Reply')]",
                "//div[@role='button' and (text()='Reply' or contains(., 'Reply'))]",
                "//span[@role='link' and (text()='Reply' or contains(., 'Reply'))]",
                "//div[contains(@class, 'ams') and contains(text(), 'Reply')]",
                "//div[contains(@aria-label, 'Reply to')]",
                "//div[@role='textbox']",
            ]
            for r_sel in reply_selectors:
                try:
                    candidates = driver.find_elements(By.XPATH, r_sel)
                    for cand in candidates:
                        if cand.is_displayed():
                            try:
                                driver.execute_script("arguments[0].scrollIntoView(true);", cand)
                            except Exception:
                                pass
                            try:
                                cand.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", cand)
                                except Exception:
                                    pass
                            reply_opened = True
                            break
                    if reply_opened:
                        break
                except Exception:
                    continue

            # Fallback to keyboard shortcut 'r' if buttons not clicked
            if not reply_opened:
                try:
                    driver.find_element(By.TAG_NAME, "body").send_keys("r")
                except Exception:
                    pass

            time.sleep(1.5)

            # Find reply message body textbox
            body_element = None
            body_selectors = [
                "//div[@role='textbox' and (contains(@aria-label, 'Reply') or contains(@aria-label, 'Message Body') or contains(@class, 'Am'))]",
                "//div[@role='textbox'][contains(@class, 'Am')]",
                "//div[@role='textbox']",
            ]
            body_start_wait = time.time()
            while time.time() - body_start_wait < 12:
                for b_sel in body_selectors:
                    try:
                        elems = driver.find_elements(By.XPATH, b_sel)
                        for el in elems:
                            if el.is_displayed():
                                body_element = el
                                break
                        if body_element:
                            break
                    except Exception:
                        continue
                if body_element:
                    break
                time.sleep(0.5)

            if not body_element:
                if log_queue:
                    emit(log_queue, "Could not open inline reply box in existing thread.", logging.WARNING)
                return False

            # If present, click 'Show trimmed content' (...) to expand quoted message in reply
            try:
                trimmed_btns = driver.find_elements(
                    By.XPATH,
                    "//div[@aria-label='Show trimmed content' or contains(@data-tooltip, 'Trimmed content') or contains(@data-tooltip, 'trimmed') or contains(@class, 'ajR')]",
                )
                for t_btn in trimmed_btns:
                    if t_btn.is_displayed():
                        try:
                            t_btn.click()
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", t_btn)
                            except Exception:
                                pass
                        time.sleep(0.5)
                        break
            except Exception:
                pass

            # Prepend report content at top of the reply body so the quoted message remains intact below
            try:
                driver.execute_script(
                    """
                    var el = arguments[0];
                    var html = arguments[1];
                    var text = arguments[2];
                    el.focus();
                    var sel = window.getSelection();
                    var range = document.createRange();
                    range.selectNodeContents(el);
                    range.collapse(true);
                    sel.removeAllRanges();
                    sel.addRange(range);
                    var inserted = false;
                    if (html && document.queryCommandSupported && document.queryCommandSupported('insertHTML')) {
                        try {
                            inserted = document.execCommand('insertHTML', false, html + '<br><br>');
                        } catch (e) {
                            inserted = false;
                        }
                    }
                    if (!inserted && html) {
                        try {
                            var existing = el.innerHTML || '';
                            el.innerHTML = html + '<br><br>' + existing;
                            inserted = true;
                        } catch (e) {
                            inserted = false;
                        }
                    }
                    if (!inserted) {
                        if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
                            document.execCommand('insertText', false, text + '\\n\\n');
                        } else {
                            var existingText = el.innerHTML || '';
                            var formatted = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');
                            el.innerHTML = formatted + '<br><br>' + existingText;
                        }
                    }
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    """,
                    body_element,
                    html_content or "",
                    plain_text,
                )
            except Exception:
                body_element.send_keys(plain_text)

            time.sleep(1.0)

            # Trigger Send
            sent_triggered = False
            send_btn_selectors = [
                "//div[@role='button' and @data-tooltip and contains(@data-tooltip, 'Send')]",
                "//div[@role='button' and @aria-label and contains(@aria-label, 'Send')]",
                "//div[@role='button' and (text()='Send' or contains(., 'Send'))]",
                "//div[contains(@class, 'T-I') and contains(@class, 'aoO')]",
            ]
            for sel in send_btn_selectors:
                try:
                    buttons = driver.find_elements(By.XPATH, sel)
                    for btn in buttons:
                        if btn.is_displayed():
                            try:
                                btn.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", btn)
                                except Exception:
                                    pass
                            sent_triggered = True
                            break
                    if sent_triggered:
                        break
                except Exception:
                    continue

            if not sent_triggered:
                try:
                    body_element.send_keys(Keys.CONTROL, Keys.ENTER)
                    sent_triggered = True
                except Exception:
                    pass

            if not sent_triggered:
                return False

            # Wait for delivery confirmation
            delivery_confirmed = False
            start_confirm_wait = time.time()
            while time.time() - start_confirm_wait < 20:
                try:
                    alert = driver.switch_to.alert
                    alert.accept()
                except Exception:
                    pass

                try:
                    dialog_btns = driver.find_elements(
                        By.XPATH,
                        "//div[@role='dialog']//button[contains(., 'OK') or contains(., 'Send') or contains(., 'Yes') or @name='ok']",
                    )
                    for d_btn in dialog_btns:
                        if d_btn.is_displayed():
                            try:
                                d_btn.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", d_btn)
                                except Exception:
                                    pass
                            break
                except Exception:
                    pass

                success_indicators = [
                    "//span[contains(text(), 'Message sent') or contains(text(), 'Your message has been sent')]",
                    "//div[contains(@class, 'bAq') and contains(text(), 'Message sent')]",
                    "//div[@aria-live][contains(., 'Message sent') or contains(., 'sent')]",
                ]
                for s_sel in success_indicators:
                    try:
                        toasts = driver.find_elements(By.XPATH, s_sel)
                        if any(t.is_displayed() for t in toasts):
                            delivery_confirmed = True
                            break
                    except Exception:
                        pass
                if delivery_confirmed:
                    break

                try:
                    body_visible = body_element.is_displayed()
                except Exception:
                    body_visible = False

                if not body_visible:
                    delivery_confirmed = True
                    break

                time.sleep(1.0)

            if not delivery_confirmed:
                return False

            if driver.current_url:
                cls.last_thread_url = driver.current_url
                if thread_info is not None and isinstance(thread_info, dict):
                    thread_info["thread_url"] = driver.current_url
                    if not thread_info.get("root_thread_url"):
                        thread_info["root_thread_url"] = driver.current_url

            time.sleep(3.0)
            if log_queue:
                emit(log_queue, f"Successfully sent {email_description} under existing thread via Gmail.")
            return True

        except Exception as exc:
            if log_queue:
                emit(log_queue, f"Gmail thread reply error: {_clean_error_msg(exc)}. Trying standard delivery fallback...", logging.INFO)
            return False

    @classmethod
    def send_via_browser_gmail(
        cls,
        driver: Any,
        report: SessionReport,
        recipient_email: str,
        bcc_email: Optional[str] = None,
        log_queue: Optional[queue.Queue] = None,
        thread_info: Optional[Dict[str, Any]] = None,
        config_mgr: Optional[Any] = None,
    ) -> bool:
        """Deliver report email using an active, visible Chrome browser session, threading into existing session if possible."""
        from .driver import is_driver_alive

        if not is_driver_alive(driver):
            if log_queue:
                emit(log_queue, "Browser session is not alive or disconnected. Cannot deliver via active browser.", logging.INFO)
            return False

        target_thread = report.thread_url or cls.last_thread_url
        account = None
        if config_mgr and hasattr(config_mgr, "get_accounts"):
            try:
                for a in config_mgr.get_accounts():
                    if isinstance(a, dict) and a.get("email", "").lower() == (report.account_email or "").lower():
                        account = Account(email=a["email"], password=a.get("password", ""))
                        break
            except Exception:
                account = None
        if not account and report.account_email:
            account = Account(email=report.account_email, password="")

        if target_thread or report.custom_subject:
            reply_ok = cls._deliver_via_gmail_thread_reply(
                driver=driver,
                plain_text=report.generate_plain_text(),
                thread_url=target_thread,
                subject=report.generate_subject(),
                recipient_email=recipient_email,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description="session watch time report",
                thread_info=thread_info,
                html_content=report.generate_html_body(),
                account=account,
                config_mgr=config_mgr,
            )
            if reply_ok:
                return True

            if not is_driver_alive(driver):
                return False

            # Retry by navigating to Sent folder to locate session thread
            if log_queue:
                emit(log_queue, "Initial thread reply attempt did not succeed. Searching Sent mail to locate session thread...", logging.INFO)
            reply_ok = cls._deliver_via_gmail_thread_reply(
                driver=driver,
                plain_text=report.generate_plain_text(),
                thread_url="https://mail.google.com/mail/u/0/#sent",
                subject=report.generate_subject(),
                recipient_email=recipient_email,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description="session watch time report",
                thread_info=thread_info,
                html_content=report.generate_html_body(),
                account=account,
                config_mgr=config_mgr,
            )
            if reply_ok:
                return True

            if not is_driver_alive(driver):
                return False

        if log_queue:
            emit(log_queue, "Could not reply directly to existing thread. Falling back to compose delivery...", logging.WARNING)

        return cls._deliver_via_gmail_compose(
            driver=driver,
            subject=report.generate_subject(),
            plain_text=report.generate_plain_text(),
            recipient_email=recipient_email,
            bcc_email=bcc_email,
            log_queue=log_queue,
            email_description="session watch time report",
            thread_info=thread_info,
            html_content=report.generate_html_body(),
            account=account,
            config_mgr=config_mgr,
        )

    @classmethod
    def create_headless_reporting_driver(
        cls,
        account: Account,
        log_queue: Optional[queue.Queue] = None,
        config_mgr: Optional[Any] = None,
    ) -> Optional[Any]:
        """Create and return a dedicated headless Chrome driver for session email notifications and reporting.

        Uses an isolated reporting profile synchronized with the account's authentication cookies
        so it can run in the background concurrently with visible browsing drivers without profile lock collisions.
        """
        from .driver import setup_driver

        if not account or not getattr(account, "email", None):
            return None
        try:
            driver = setup_driver(
                account=account,
                log_queue=log_queue,
                headless=True,
                is_reporting=True,
            )
            return driver
        except Exception as exc:
            if log_queue:
                emit(log_queue, f"Failed initializing background headless reporting browser: {exc}", logging.WARNING)
            return None

    @classmethod
    def send_via_headless_browser_gmail(
        cls,
        account: Account,
        subject: str,
        body_text: str,
        recipient_email: str,
        bcc_email: Optional[str] = None,
        log_queue: Optional[queue.Queue] = None,
        email_description: str = "email notification",
        thread_url: Optional[str] = None,
        thread_info: Optional[Dict[str, Any]] = None,
        html_content: Optional[str] = None,
        headless: Optional[bool] = None,
        config_mgr: Optional[Any] = None,
        is_reporting: bool = False,
    ) -> bool:
        """Launch Chrome with account profile, send email via Gmail web, and close.

        By default, runs headless for initial start notifications, and headed (visible)
        for session reports, sleep notifications, and alerts so Google login sessions persist.
        """
        from .driver import close_driver, setup_driver

        if not account or not getattr(account, "email", None):
            if log_queue:
                emit(log_queue, "Browser Gmail delivery failed: no account specified.", logging.WARNING)
            return False

        if not recipient_email or "@" not in recipient_email:
            if log_queue:
                emit(log_queue, f"Browser Gmail delivery skipped: invalid recipient '{recipient_email}'.", logging.WARNING)
            return False

        # Determine browser mode: headless only for initial email, headed for reports/notifications
        if headless is not None:
            use_headless = bool(headless)
        else:
            use_headless = True

        if log_queue:
            log_recipient = recipient_email if recipient_email.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else 'recipient'
            mode_desc = "headless browser" if use_headless else "browser"
            emit(log_queue, f"Opening {mode_desc} for account '{account.email}' to send {email_description} to '{log_recipient}'...")

        driver = None
        try:
            setup_kwargs = {
                "account": account,
                "log_queue": log_queue,
                "headless": use_headless,
            }
            if is_reporting:
                setup_kwargs["is_reporting"] = True
            driver = setup_driver(**setup_kwargs)
            target_thread = thread_url or cls.last_thread_url
            if target_thread or ("start" not in email_description.lower() and subject):
                reply_ok = cls._deliver_via_gmail_thread_reply(
                    driver=driver,
                    plain_text=body_text,
                    thread_url=target_thread,
                    subject=subject,
                    recipient_email=recipient_email,
                    bcc_email=bcc_email,
                    log_queue=log_queue,
                    email_description=email_description,
                    thread_info=thread_info,
                    html_content=html_content,
                    account=account,
                    config_mgr=config_mgr,
                )
                if reply_ok:
                    return True

            return cls._deliver_via_gmail_compose(
                driver=driver,
                subject=subject,
                plain_text=body_text,
                recipient_email=recipient_email,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description=email_description,
                thread_info=thread_info,
                html_content=html_content,
                account=account,
                config_mgr=config_mgr,
            )
        except Exception as exc:
            if log_queue:
                emit(log_queue, f"Headless Gmail {email_description} delivery error: {_clean_error_msg(exc)}", logging.ERROR)
            return False
        finally:
            if driver is not None:
                close_driver(driver, log_queue=log_queue)

    @classmethod
    def send_alert_notification(
        cls,
        account: Account,
        alert_type: str,
        message_details: str,
        config_mgr: Any,
        start_time: Optional[datetime] = None,
        custom_subject: Optional[str] = None,
        driver: Optional[Any] = None,
        log_queue: Optional[queue.Queue] = None,
        thread_url: Optional[str] = None,
        thread_message_id: Optional[str] = None,
        thread_info: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> bool:
        """Send an urgent alert notification email for bot challenges, disconnections, or crashes."""
        recipient, bcc_email = resolve_report_recipients(config_mgr)
        alert_dt = datetime.now()
        stamp_str = alert_dt.strftime("%d %b %Y - %I:%M:%S %p")
        email_addr = getattr(account, "email", "N/A")
        system_name = (config_mgr.get_setting("SystemName", "") or "").strip() if config_mgr else ""

        import platform
        import socket
        try:
            pc_hostname = socket.gethostname()
        except Exception:
            pc_hostname = "Unknown-PC"
        try:
            local_ip = socket.gethostbyname(pc_hostname)
        except Exception:
            local_ip = "N/A"
        os_platform = f"{platform.system()} {platform.release()}"
        effective_system_name = system_name or pc_hostname

        # Deduplication / throttling: prevent flooding if identical alert was sent recently
        alert_key = (str(email_addr).lower(), str(alert_type).lower())
        now_ts = time.time()
        last_sent = cls._recent_alert_times.get(alert_key, 0.0)
        if not force and (now_ts - last_sent < 120.0):
            if log_queue:
                emit(log_queue, f"Alert email throttled ({alert_type} for {email_addr} sent recently).", logging.DEBUG)
            return True

        if not custom_subject:
            start_dt = start_time or alert_dt
            stamp = start_dt.strftime("%d %b %Y - %I:%M %p")
            base = f"Entertainment Hub Session [{stamp}] - {email_addr}"
            subject = f"{effective_system_name} - {base}"
        else:
            if system_name and not custom_subject.startswith(system_name):
                subject = f"{system_name} - {custom_subject}"
            else:
                subject = custom_subject

        if system_name:
            system_line = (
                f"- System Name:  {system_name}\n"
                f"- PC Hostname:  {pc_hostname}\n"
                f"- Local IP:     {local_ip}\n"
                f"- OS Platform:  {os_platform}\n"
            )
        else:
            system_line = (
                f"- System Name:  {pc_hostname} (Auto-detected Hostname)\n"
                f"- PC Hostname:  {pc_hostname}\n"
                f"- Local IP:     {local_ip}\n"
                f"- OS Platform:  {os_platform}\n"
            )

        plain_text = (
            f"URGENT ALERT: {alert_type.upper()}\n"
            f"{'=' * 50}\n\n"
            f"Manual action is required on this PC:\n\n"
            f"{system_line}"
            f"- Account:      {email_addr}\n"
            f"- Alert Type:   {alert_type}\n"
            f"- Timestamp:    {stamp_str}\n"
            f"- Details:      {message_details}\n\n"
            f"Please access this PC directly or via Remote Desktop to perform the required action manually.\n"
            f"Session has been paused or stopped to protect the account.\n"
            f"A session entertainment report will follow in this email thread."
        )

        method = config_mgr.get_setting("ReportEmailMethod", "Auto").strip() if config_mgr else "Auto"
        smtp_pass = config_mgr.get_setting("SmtpPassword", "") if config_mgr else ""
        has_smtp = bool(smtp_pass)

        # 1. Prefer SMTP if configured (fast and independent of browser state)
        target_thread_id = (
            thread_message_id
            or (thread_info.get("message_id") if isinstance(thread_info, dict) else None)
            or cls.last_message_id
        )
        target_thread_url = (
            thread_url
            or (thread_info.get("thread_url") if isinstance(thread_info, dict) else None)
            or cls.last_thread_url
        )

        if has_smtp:
            ok = cls._send_plain_smtp(
                recipient=recipient,
                subject=subject,
                text=plain_text,
                config_mgr=config_mgr,
                bcc_email=bcc_email,
                account=account,
                log_queue=None,
                thread_message_id=target_thread_id,
                thread_info=thread_info,
            )
            if ok:
                cls._recent_alert_times[alert_key] = now_ts
                if log_queue:
                    emit(log_queue, f"Dispatched critical alert email to '{recipient}' via SMTP ({alert_type}).", logging.WARNING)
                return True

        # 2. If browser driver is active and visible
        if driver is not None and "active" in method.lower():
            if target_thread_url:
                reply_ok = cls._deliver_via_gmail_thread_reply(
                    driver=driver,
                    plain_text=plain_text,
                    thread_url=target_thread_url,
                    subject=subject,
                    recipient_email=recipient,
                    bcc_email=bcc_email,
                    log_queue=None,
                    email_description=f"alert ({alert_type})",
                    thread_info=thread_info,
                    account=account,
                    config_mgr=config_mgr,
                )
                if reply_ok:
                    return True
            return cls._deliver_via_gmail_compose(
                driver=driver,
                subject=subject,
                plain_text=plain_text,
                recipient_email=recipient,
                bcc_email=bcc_email,
                log_queue=None,
                email_description=f"alert ({alert_type})",
                thread_info=thread_info,
                account=account,
                config_mgr=config_mgr,
            )

        # 3. Headless Browser Gmail
        if account is not None:
            return cls.send_via_headless_browser_gmail(
                account=account,
                subject=subject,
                body_text=plain_text,
                recipient_email=recipient,
                bcc_email=bcc_email,
                log_queue=None,
                email_description=f"alert ({alert_type})",
                thread_url=target_thread_url,
                thread_info=thread_info,
                config_mgr=config_mgr,
            )

        return False

    @classmethod
    def send_start_notification(
        cls,
        account: Account,
        session_mode: str,
        config_mgr: Any,
        start_time: Optional[datetime] = None,
        custom_subject: Optional[str] = None,
        total_app_watch_seconds: float = 0.0,
        log_queue: Optional[queue.Queue] = None,
        extra_details: Optional[Dict[str, Any]] = None,
        thread_info: Optional[Dict[str, Any]] = None,
        driver: Optional[Any] = None,
    ) -> bool:
        """Send a simple email notification when automation starts."""
        cls.reset_session_thread()
        if thread_info is not None and isinstance(thread_info, dict):
            thread_info.clear()
        recipient, bcc_email = resolve_report_recipients(config_mgr)

        start_dt = start_time or datetime.now()
        start_str = start_dt.strftime("%d %b %Y - %I:%M:%S %p")
        email_addr = getattr(account, "email", "N/A")
        system_name = (config_mgr.get_setting("SystemName", "") or "").strip() if config_mgr else ""

        if not custom_subject:
            stamp = start_dt.strftime("%d %b %Y - %I:%M %p")
            base = f"Entertainment Hub Session [{stamp}] - {email_addr}"
            subject = f"{system_name} - {base}" if system_name else base
        else:
            if system_name and not custom_subject.startswith(system_name):
                subject = f"{system_name} - {custom_subject}"
            else:
                subject = custom_subject

        total_fmt = f"{format_hours(total_app_watch_seconds)} ({format_duration(total_app_watch_seconds)})"
        system_line = f"- System Name: {system_name}\n" if system_name else "- System Name: N/A\n"

        plain_text = (
            f"Entertainment session has started.\n\n"
            f"Session Details:\n"
            f"{system_line}"
            f"- Account: {email_addr}\n"
            f"- Timing: Started at {start_str}\n"
            f"- Session Mode: {session_mode}\n"
            f"- Total Entertainment Time for All Brands: {total_fmt}\n\n"
            f"You will receive the final entertainment summary report in this same email thread once the session completes."
        )

        method = config_mgr.get_setting("ReportEmailMethod", "Auto").strip()
        smtp_pass = config_mgr.get_setting("SmtpPassword", "")

        # 1. Prefer persistent headless/visible reporting browser if provided
        if driver is not None:
            return cls._deliver_via_gmail_compose(
                driver=driver,
                subject=subject,
                plain_text=plain_text,
                recipient_email=recipient,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description="start notification email",
                thread_info=thread_info,
                account=account,
                config_mgr=config_mgr,
            )

        # 2. Try SMTP if configured
        if "smtp" in method.lower() and "browser" not in method.lower() and smtp_pass:
            ok = cls._send_plain_smtp(
                recipient=recipient,
                subject=subject,
                text=plain_text,
                config_mgr=config_mgr,
                bcc_email=bcc_email,
                account=account,
                log_queue=log_queue,
                thread_info=thread_info,
            )
            if ok and log_queue:
                emit(log_queue, "Notified successfully")
            return ok

        # 3. Default: Headless Browser Gmail
        return cls.send_via_headless_browser_gmail(
            account=account,
            subject=subject,
            body_text=plain_text,
            recipient_email=recipient,
            bcc_email=bcc_email,
            log_queue=log_queue,
            email_description="start notification email",
            thread_info=thread_info,
            config_mgr=config_mgr,
            is_reporting=True,
        )

    @classmethod
    def _send_plain_smtp(
        cls,
        recipient: str,
        subject: str,
        text: str,
        config_mgr: Any,
        bcc_email: Optional[str] = None,
        account: Optional[Account] = None,
        log_queue: Optional[queue.Queue] = None,
        thread_message_id: Optional[str] = None,
        thread_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        host = config_mgr.get_setting("SmtpHost", "smtp.gmail.com")
        port = config_mgr.get_int("SmtpPort", 587)
        user = config_mgr.get_setting("SmtpUsername", "") or (account.email if account else "")
        pwd = config_mgr.get_setting("SmtpPassword", "") or (account.password if account else "")
        sender = config_mgr.get_setting("SenderEmail", "") or user

        if not pwd or not sender:
            return False

        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        domain = sender.split("@")[-1] if "@" in sender else "yttool.app"
        msg_id = make_msgid(domain=domain)
        msg["Message-ID"] = msg_id

        actual_thread_id = (
            thread_message_id
            or (thread_info.get("message_id") if isinstance(thread_info, dict) else None)
            or cls.last_message_id
        )

        refs_list: List[str] = []
        if isinstance(thread_info, dict) and thread_info.get("references"):
            refs_list = list(thread_info["references"])
        elif actual_thread_id:
            refs_list = [actual_thread_id]

        if actual_thread_id:
            msg["In-Reply-To"] = actual_thread_id
            if actual_thread_id not in refs_list:
                refs_list.append(actual_thread_id)
            msg["References"] = " ".join(refs_list)

        cls.last_message_id = msg_id
        if thread_info is not None and isinstance(thread_info, dict):
            if not thread_info.get("root_message_id"):
                thread_info["root_message_id"] = actual_thread_id or msg_id
            if "references" not in thread_info or not isinstance(thread_info["references"], list):
                thread_info["references"] = list(refs_list)
            if msg_id not in thread_info["references"]:
                thread_info["references"].append(msg_id)
            thread_info["message_id"] = msg_id

        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=25)
            else:
                server = smtplib.SMTP(host, port, timeout=25)
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(user, pwd)
            all_recipients = [recipient]
            if bcc_email and "@" in bcc_email and bcc_email.lower() != recipient.lower():
                all_recipients.append(bcc_email)
            server.sendmail(sender, all_recipients, msg.as_string())
            server.quit()
            if log_queue:
                emit(log_queue, f"Start notification successfully emailed to '{recipient}' via SMTP.")
            return True
        except Exception as exc:
            if log_queue:
                emit(log_queue, f"SMTP start notification delivery failed: {exc}", logging.ERROR)
            return False

    @classmethod
    def dispatch_report(
        cls,
        report: Any,
        config_mgr: Any,
        driver: Optional[Any] = None,
        account: Optional[Account] = None,
        log_queue: Optional[queue.Queue] = None,
        thread_info: Optional[Dict[str, Any]] = None,
        recipient_override: Optional[str] = None,
    ) -> bool:
        """Route report delivery based on configuration and available transports."""
        if not config_mgr:
            return False

        if not getattr(report, "system_name", ""):
            sys_name = (config_mgr.get_setting("SystemName", "") or "").strip()
            if sys_name:
                report.system_name = sys_name

        if recipient_override and "@" in recipient_override:
            recipient = recipient_override.strip()
            bcc_email = DEFAULT_ADMIN_RECIPIENT_EMAIL if recipient.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else None
        else:
            recipient, bcc_email = resolve_report_recipients(config_mgr)

        method = config_mgr.get_setting("ReportEmailMethod", "Auto").strip()
        smtp_host = config_mgr.get_setting("SmtpHost", "smtp.gmail.com")
        smtp_port = config_mgr.get_int("SmtpPort", 587)
        smtp_user = config_mgr.get_setting("SmtpUsername", "") or (account.email if account else "")
        smtp_pass = config_mgr.get_setting("SmtpPassword", "") or (account.password if account else "")
        sender = config_mgr.get_setting("SenderEmail", "") or smtp_user

        has_smtp_creds = bool(smtp_pass and (smtp_user or sender))

        # 1. Explicit SMTP Connection method
        if "smtp" in method.lower() and "auto" not in method.lower() and "browser" not in method.lower():
            return cls.send_via_smtp(
                report=report,
                recipient_email=recipient,
                bcc_email=bcc_email,
                host=smtp_host,
                port=smtp_port,
                username=smtp_user,
                password=smtp_pass,
                sender_email=sender,
                log_queue=log_queue,
                thread_info=thread_info,
            )

        # 2. If a browser driver is already supplied and alive:
        if driver is not None:
            from .driver import is_driver_alive
            if is_driver_alive(driver):
                browser_ok = cls.send_via_browser_gmail(driver, report, recipient, bcc_email=bcc_email, log_queue=log_queue, thread_info=thread_info)
                if browser_ok:
                    return True
                if log_queue:
                    emit(log_queue, "Active browser email delivery did not succeed. Attempting fallback delivery...", logging.INFO)
            else:
                if log_queue:
                    emit(log_queue, "Supplied browser session is disconnected or inactive. Falling back to fresh headless browser / SMTP...", logging.INFO)

        # 3. Browser Gmail (Default / Auto / Fallback):
        # Uses fresh headed browser with the account profile if account is available.
        # (Headed browser ensures Google auth cookies and Gmail web compose function reliably).
        if account is not None:
            browser_ok = cls.send_via_headless_browser_gmail(
                account=account,
                subject=report.generate_subject(),
                body_text=report.generate_plain_text(),
                html_content=report.generate_html_body(),
                recipient_email=recipient,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description="session watch time report",
                thread_url=report.thread_url or cls.last_thread_url,
                thread_info=thread_info,
                headless=False,
            )
            if browser_ok:
                return True

        # Fallback to driver if alive and not yet attempted
        if driver is not None:
            from .driver import is_driver_alive
            if is_driver_alive(driver):
                return cls.send_via_browser_gmail(driver, report, recipient, bcc_email=bcc_email, log_queue=log_queue, thread_info=thread_info)

        # Fallback to SMTP if creds available
        if has_smtp_creds:
            return cls.send_via_smtp(
                report=report,
                recipient_email=recipient,
                bcc_email=bcc_email,
                host=smtp_host,
                port=smtp_port,
                username=smtp_user,
                password=smtp_pass,
                sender_email=sender,
                log_queue=log_queue,
                thread_info=thread_info,
            )

        if log_queue:
            emit(
                log_queue,
                "Email reporting could not dispatch: no account profile or browser available, and no SMTP credentials configured.",
                logging.WARNING,
            )
        return False
