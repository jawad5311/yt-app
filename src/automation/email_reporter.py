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

    def generate_subject(self) -> str:
        """Produce a consistent subject line to ensure start and completion emails thread together in Gmail."""
        if self.custom_subject:
            return self.custom_subject
        stamp = self.start_time.strftime("%d %b %Y - %I:%M %p")
        return f"Entertainment Hub Session [{stamp}] - {self.account_email or 'Session'}"

    def generate_plain_text(self) -> str:
        """Produce a formatted plain-text report for terminal/log output and email."""
        start_str = self.start_time.strftime("%Y-%m-%d %H:%M:%S")
        end_str = self.end_time.strftime("%Y-%m-%d %H:%M:%S")
        session_elapsed = format_duration(self.duration_seconds)
        total_watch = format_duration(self.total_session_watch_seconds)
        total_hours = format_hours(self.total_session_watch_seconds)

        exit_reason = self.interruption_reason or ("Completed normally" if "complet" in self.status.lower() else self.status)

        lines = [
            "=" * 70,
            "                  ENTERTAINMENT HUB - SESSION REPORT                  ",
            "=" * 70,
            f"Account:              {self.account_email or 'N/A'}",
            f"Session Mode:         {self.session_mode}",
            f"Status:               {self.status}",
            f"Interruption Reason:  {exit_reason}",
            f"Videos Watched:       {self.total_videos_watched} ({self.regular_videos_count} regular, {self.shorts_count} Shorts)",
            f"  • Suggested Section:   {self.suggested_videos_count} video(s)",
            f"  • YouTube Search:      {self.search_videos_count} video(s) ({self.search_queries_count} searches performed)",
            f"  • Home Page Feed:      {self.home_videos_count} video(s)",
            f"  • YouTube Shorts:      {self.shorts_count} short(s)",
            f"  • Random / Other:      {self.random_videos_count} video(s) (non-target channels)",
        ]
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
            header = f"  {'Channel Name':<22} | {'This Session':<21} | {'Target Progress':<20} | {'Videos Watched':<16} | {'Lifetime Total'}"
            lines.append(header)
            lines.append("  " + "-" * 105)

            for ch in active_channels:
                name = ch.get("name", "Unknown")[:20]
                ses_sec = ch.get("session_watch_seconds", 0.0)
                tot_sec = ch.get("final_watch_seconds", 0.0)
                tgt_txt = ch.get("target_goal_text", "No target set")[:20]
                tot_vids = ch.get("total_videos_count")
                if tot_vids is None:
                    tot_vids = ch.get("regular_videos_count", 0) + ch.get("shorts_count", 0)
                vids_fmt = f"{tot_vids} video(s)"
                ses_fmt = f"{format_duration(ses_sec)} ({format_hours(ses_sec)})"
                tot_fmt = format_hours(tot_sec)
                lines.append(f"  {name:<22} | {ses_fmt:<21} | {tgt_txt:<20} | {vids_fmt:<16} | {tot_fmt}")

        lines.extend([
            "=" * 65,
            "Report automatically generated by Entertainment Hub.",
        ])
        return "\n".join(lines)

    def generate_html(self) -> str:
        """Produce a clean, modern HTML email report compatible with Gmail and mobile."""
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
            ses_sec = ch.get("session_watch_seconds", 0.0)
            init_sec = ch.get("initial_watch_seconds", 0.0)
            fin_sec = ch.get("final_watch_seconds", 0.0)
            tgt_text = html.escape(ch.get("target_goal_text", "No target set"))
            if ch.get("target_achieved"):
                tgt_badge = f'<span style="color: #059669; font-weight: 700;">{tgt_text}</span>'
            elif "No target" not in tgt_text:
                tgt_badge = f'<span style="color: #0284c7; font-weight: 600;">{tgt_text}</span>'
            else:
                tgt_badge = f'<span style="color: #94a3b8;">{tgt_text}</span>'
            tot_vids = ch.get("total_videos_count")
            if tot_vids is None:
                tot_vids = ch.get("regular_videos_count", 0) + ch.get("shorts_count", 0)
            reg_cnt = ch.get("regular_videos_count", 0)
            sh_cnt = ch.get("shorts_count", 0)
            if sh_cnt > 0:
                vids_html = f"<strong>{tot_vids}</strong> video(s) <span style=\"font-size: 11px; color: #64748b;\">({reg_cnt} vids, {sh_cnt} shorts)</span>"
            else:
                vids_html = f"<strong>{tot_vids}</strong> video(s)"

            rows_html.append(f"""
                <tr style="border-bottom: 1px solid #e2e8f0;">
                    <td style="padding: 12px 14px; font-weight: 600; color: #1e293b;">{name}</td>
                    <td style="padding: 12px 14px; color: #059669; font-weight: 700;">{format_duration(ses_sec)} <span style="font-size: 11px; color: #64748b; font-weight: normal;">({format_hours(ses_sec)})</span></td>
                    <td style="padding: 12px 14px; font-size: 12px;">{tgt_badge}</td>
                    <td style="padding: 12px 14px; color: #475569; font-size: 12px;">{vids_html}</td>
                    <td style="padding: 12px 14px; color: #0f172a; font-weight: 600;">{format_hours(fin_sec)}</td>
                </tr>
            """)

        if not rows_html:
            rows_html.append("""
                <tr>
                    <td colspan="5" style="padding: 16px; text-align: center; color: #94a3b8;">No channels logged entertainment in this session.</td>
                </tr>
            """)

        status_color = "#059669" if "complet" in self.status.lower() else "#d97706"
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

        html_template = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Entertainment Report</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
    <div style="max-width: 680px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 28px 24px; color: #ffffff;">
            <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: #38bdf8; font-weight: 700; margin-bottom: 6px;">Media Entertainment Activity</div>
            <h1 style="margin: 0; font-size: 22px; font-weight: 700; line-height: 1.3;">Session Entertainment Report</h1>
            <div style="margin-top: 8px; font-size: 13px; color: #94a3b8;">Account: <span style="color: #f1f5f9; font-weight: 600;">{html.escape(self.account_email or 'N/A')}</span> | Mode: <span style="color: #f1f5f9; font-weight: 600;">{html.escape(self.session_mode)}</span></div>
        </div>

        <div style="padding: 24px;">
            {error_box}

            <!-- KPI Cards -->
            <div style="display: table; width: 100%; margin-bottom: 16px;">
                <div style="display: table-cell; width: 48%; background-color: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 10px; padding: 16px; vertical-align: top;">
                    <div style="font-size: 11px; text-transform: uppercase; font-weight: 700; color: #047857; letter-spacing: 0.5px;">Combined Entertainment Time</div>
                    <div style="font-size: 24px; font-weight: 800; color: #065f46; margin-top: 4px;">{total_watch}</div>
                    <div style="font-size: 12px; color: #047857; margin-top: 2px;">{total_hours}</div>
                </div>
                <div style="display: table-cell; width: 4%;"></div>
                <div style="display: table-cell; width: 48%; background-color: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 10px; padding: 16px; vertical-align: top;">
                    <div style="font-size: 11px; text-transform: uppercase; font-weight: 700; color: #475569; letter-spacing: 0.5px;">Session Duration & Status</div>
                    <div style="font-size: 20px; font-weight: 700; color: #1e293b; margin-top: 4px;">{session_elapsed}</div>
                    <div style="font-size: 12px; color: {status_color}; font-weight: 600; margin-top: 2px;">Status: {html.escape(self.status)}</div>
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
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Channel</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">This Session</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Target Goal Progress</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Videos Watched</th>
                        <th style="padding: 10px 14px; font-weight: 700; color: #334155;">Lifetime Total</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>

            <!-- Footer -->
            <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; text-align: center;">
                Generated automatically by Entertainment Hub. All entertainment durations are measured from active playback.
            </div>
        </div>
    </div>
</body>
</html>
        """
        return html_template.strip()


class EmailReporter:
    """Manages report and notification delivery via headless Chrome Gmail, active browser, or SMTP."""

    last_thread_url: Optional[str] = None
    last_message_id: Optional[str] = None

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
        actual_thread_id = getattr(report, "thread_message_id", None) or cls.last_message_id
        if actual_thread_id:
            msg["In-Reply-To"] = actual_thread_id
            msg["References"] = actual_thread_id

        cls.last_message_id = msg_id
        if thread_info is not None and isinstance(thread_info, dict):
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
                emit(log_queue, f"Session watch time report successfully emailed to '{recipient_email}' via SMTP.")
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
    ) -> bool:
        """Core Gmail compose automation in Chrome (works for visible and headless Chrome)."""
        if not driver:
            if log_queue:
                emit(log_queue, f"Gmail {email_description} delivery failed: browser driver is unavailable.", logging.WARNING)
            return False

        is_start = "start" in email_description.lower()
        try:
            bcc_log = f" (BCC: {bcc_email})" if bcc_email else ""
            if log_queue and not is_start:
                emit(log_queue, f"Navigating to Gmail web compose to send {email_description} to '{recipient_email}'{bcc_log}...")

            compose_url = f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(recipient_email)}"
            if bcc_email and "@" in bcc_email and bcc_email.lower() != recipient_email.lower():
                compose_url += f"&bcc={quote(bcc_email)}"
            compose_url += f"&su={quote(subject)}"
            # Pre-populate body in URL query string when within safe URL limits
            if len(plain_text) < 7000:
                compose_url += f"&body={quote(plain_text)}"

            driver.get(compose_url)

            # Wait for Gmail to load or redirect
            start_wait = time.time()
            is_signed_in = True
            while time.time() - start_wait < 18:
                current_url = driver.current_url or ""
                if "accounts.google.com" in current_url:
                    is_signed_in = False
                    break
                # Check if compose box or any compose element has rendered
                if driver.find_elements(By.XPATH, "//div[@role='textbox'] | //input[@name='to' or @aria-label='To' or contains(@class, 'agP')]"):
                    break
                time.sleep(1.0)

            if not is_signed_in:
                if log_queue:
                    emit(log_queue, f"Gmail {email_description} delivery failed: Google account is not signed in for this profile.", logging.ERROR)
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
            if not curr_body or len(curr_body) < 20:
                try:
                    driver.execute_script(
                        """
                        var el = arguments[0];
                        var text = arguments[1];
                        el.focus();
                        if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, text);
                        } else {
                            el.innerText = text;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                        }
                        """,
                        body_element,
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
                if log_queue and is_valid_thread and not is_start:
                    emit(log_queue, f"Parked on session email thread: {captured_thread_url}")

            # Allow 3 seconds for background network sync before browser terminates
            time.sleep(3.0)
            if log_queue:
                if is_start:
                    emit(log_queue, "Notified successfully")
                else:
                    emit(log_queue, f"Successfully sent {email_description} to '{recipient_email}' via Gmail.")
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
                while time.time() - start_wait < 18:
                    cur = driver.current_url or ""
                    if "accounts.google.com" in cur:
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

            # Prepend report plain text at top of the reply body so the quoted message remains intact below
            try:
                driver.execute_script(
                    """
                    var el = arguments[0];
                    var text = arguments[1];
                    el.focus();
                    var sel = window.getSelection();
                    var range = document.createRange();
                    range.selectNodeContents(el);
                    range.collapse(true);
                    sel.removeAllRanges();
                    sel.addRange(range);
                    if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
                        document.execCommand('insertText', false, text + '\\n\\n');
                    } else {
                        var existing = el.innerHTML || '';
                        var formatted = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');
                        el.innerHTML = formatted + '<br><br>' + existing;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                    """,
                    body_element,
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
    ) -> bool:
        """Deliver report email using an active, visible Chrome browser session, threading into existing session if possible."""
        target_thread = report.thread_url or cls.last_thread_url
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
            )
            if reply_ok:
                return True

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
            )
            if reply_ok:
                return True

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
        )

    @classmethod
    def create_headless_reporting_driver(
        cls,
        account: Account,
        log_queue: Optional[queue.Queue] = None,
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
    ) -> bool:
        """Launch headless Chrome with account profile, send email via Gmail web, and close."""
        from .driver import close_driver, setup_driver

        if not account or not getattr(account, "email", None):
            if log_queue:
                emit(log_queue, "Headless Gmail delivery failed: no account specified.", logging.WARNING)
            return False

        if not recipient_email or "@" not in recipient_email:
            if log_queue:
                emit(log_queue, f"Headless Gmail delivery skipped: invalid recipient '{recipient_email}'.", logging.WARNING)
            return False

        if log_queue:
            emit(log_queue, f"Opening headless browser for account '{account.email}' to send {email_description} to '{recipient_email}'...")

        driver = None
        try:
            driver = setup_driver(
                account=account,
                log_queue=log_queue,
                headless=True,
            )
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
    ) -> bool:
        """Send an urgent alert notification email for bot challenges, disconnections, or crashes."""
        recipient, bcc_email = resolve_report_recipients(config_mgr)
        alert_dt = datetime.now()
        stamp_str = alert_dt.strftime("%d %b %Y - %I:%M:%S %p")
        email_addr = getattr(account, "email", "N/A")

        if not custom_subject:
            start_dt = start_time or alert_dt
            stamp = start_dt.strftime("%d %b %Y - %I:%M %p")
            subject = f"Entertainment Hub Session [{stamp}] - {email_addr}"
        else:
            subject = custom_subject

        plain_text = (
            f"URGENT ALERT: {alert_type.upper()}\n"
            f"{'=' * 50}\n\n"
            f"An issue requires attention during your session:\n\n"
            f"- Account:      {email_addr}\n"
            f"- Alert Type:   {alert_type}\n"
            f"- Timestamp:    {stamp_str}\n"
            f"- Details:      {message_details}\n\n"
            f"Session has been paused or stopped to protect the account.\n"
            f"A session entertainment report will follow in this email thread."
        )

        method = config_mgr.get_setting("ReportEmailMethod", "Auto").strip() if config_mgr else "Auto"
        smtp_pass = config_mgr.get_setting("SmtpPassword", "") if config_mgr else ""
        has_smtp = bool(smtp_pass)

        # 1. Prefer SMTP if configured (fast and independent of browser state)
        if has_smtp:
            ok = cls._send_plain_smtp(
                recipient=recipient,
                subject=subject,
                text=plain_text,
                config_mgr=config_mgr,
                bcc_email=bcc_email,
                account=account,
                log_queue=None,
                thread_message_id=thread_message_id or cls.last_message_id,
                thread_info=thread_info,
            )
            if ok:
                if log_queue:
                    emit(log_queue, f"Dispatched critical alert email to '{recipient}' via SMTP ({alert_type}).", logging.WARNING)
                return True

        # 2. If browser driver is active and visible
        if driver is not None and "active" in method.lower():
            return cls._deliver_via_gmail_compose(
                driver=driver,
                subject=subject,
                plain_text=plain_text,
                recipient_email=recipient,
                bcc_email=bcc_email,
                log_queue=None,
                email_description=f"alert ({alert_type})",
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
                thread_url=thread_url or cls.last_thread_url,
                thread_info=thread_info,
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
        recipient, bcc_email = resolve_report_recipients(config_mgr)

        start_dt = start_time or datetime.now()
        start_str = start_dt.strftime("%d %b %Y - %I:%M:%S %p")
        email_addr = getattr(account, "email", "N/A")

        if not custom_subject:
            stamp = start_dt.strftime("%d %b %Y - %I:%M %p")
            subject = f"Entertainment Hub Session [{stamp}] - {email_addr}"
        else:
            subject = custom_subject

        total_fmt = f"{format_hours(total_app_watch_seconds)} ({format_duration(total_app_watch_seconds)})"

        plain_text = (
            f"Entertainment session has started.\n\n"
            f"Session Details:\n"
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
            )

        # Start notification runs quietly without emitting logs to UI.
        # Only the completion report at the end of automation shows in the logs.
        if "smtp" in method.lower() and "browser" not in method.lower() and smtp_pass:
            ok = cls._send_plain_smtp(
                recipient=recipient,
                subject=subject,
                text=plain_text,
                config_mgr=config_mgr,
                bcc_email=bcc_email,
                account=account,
                log_queue=None,
                thread_info=thread_info,
            )
            if ok and log_queue:
                emit(log_queue, "Notified successfully")
            return ok

        # Default: Headless Browser Gmail
        ok = cls.send_via_headless_browser_gmail(
            account=account,
            subject=subject,
            body_text=plain_text,
            recipient_email=recipient,
            bcc_email=bcc_email,
            log_queue=None,
            email_description="start notification email",
            thread_info=thread_info,
        )
        if ok and log_queue:
            emit(log_queue, "Notified successfully")
        return ok

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
        actual_thread_id = thread_message_id or cls.last_message_id
        if actual_thread_id:
            msg["In-Reply-To"] = actual_thread_id
            msg["References"] = actual_thread_id

        cls.last_message_id = msg_id
        if thread_info is not None and isinstance(thread_info, dict):
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
        report: SessionReport,
        config_mgr: Any,
        driver: Optional[Any] = None,
        account: Optional[Account] = None,
        log_queue: Optional[queue.Queue] = None,
    ) -> bool:
        """Route report delivery based on configuration and available transports."""
        if not config_mgr:
            return False

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
            )

        # 2. If a browser driver is already supplied (persistent headless reporting browser or active browser):
        if driver is not None:
            return cls.send_via_browser_gmail(driver, report, recipient, bcc_email=bcc_email, log_queue=log_queue)

        # 3. Headless Browser Gmail (Default / Auto):
        # Uses headless browser with the account profile if account is available.
        if account is not None:
            headless_ok = cls.send_via_headless_browser_gmail(
                account=account,
                subject=report.generate_subject(),
                body_text=report.generate_plain_text(),
                recipient_email=recipient,
                bcc_email=bcc_email,
                log_queue=log_queue,
                email_description="session watch time report",
                thread_url=report.thread_url or cls.last_thread_url,
            )
            if headless_ok:
                return True

        # Fallback to driver if driver is available
        if driver is not None:
            return cls.send_via_browser_gmail(driver, report, recipient, bcc_email=bcc_email, log_queue=log_queue)

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
            )

        if log_queue:
            emit(
                log_queue,
                "Email reporting could not dispatch: no account profile or browser available, and no SMTP credentials configured.",
                logging.WARNING,
            )
        return False
