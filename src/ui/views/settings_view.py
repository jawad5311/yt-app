from __future__ import annotations

import math
from datetime import datetime
from tkinter import messagebox
from typing import Any, Dict, List, Optional

from ...automation.email_reporter import EmailReporter, SessionReport
from ..components import (
    Button,
    CheckBox,
    Entry,
    Font,
    Frame,
    Label,
    OptionMenu,
    ScrollableFrame,
    Switch,
    Tabview,
)

APPEARANCE_OPTIONS: List[str] = ["Dark", "Light", "System"]
WATCHTIME_OPTIONS: List[str] = [
    "Full Video",
    "30 seconds",
    "1 minute",
    "2 minutes",
    "3 minutes",
    "5 minutes",
    "10 minutes",
    "15 minutes",
    "20 minutes",
    "30 minutes",
]
QUALITY_OPTIONS: List[str] = ["360p", "480p", "Random (360p / 480p)"]
EMAIL_METHOD_OPTIONS: List[str] = [
    "Headless Browser Gmail (Recommended)",
    "Active Browser Gmail",
    "SMTP Connection",
]


class SmoothScrollableFrame(ScrollableFrame):
    """High-performance TTK scrollable container for settings forms."""
    pass


class SettingsView(Frame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, style="TFrame")
        self.app_controller = app_controller
        self.config_mgr = app_controller.config_mgr if hasattr(app_controller, "config_mgr") else None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header Title
        Label(
            self,
            text="Application Settings",
            font=Font(size=22, weight="bold"),
            style="Header.TLabel",
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        # Sub-Tabs Container
        self.tabview = Tabview(self, anchor="w")
        self.tabview.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="nsew")

        tab_general = self.tabview.add("General")
        tab_playback = self.tabview.add("Playback & Video")
        # Compatibility alias for code checking "Playback & Queue"
        self.tabview._tab_dict["Playback & Queue"] = tab_playback

        tab_shorts = self.tabview.add("Shorts")
        tab_ads = self.tabview.add("Ads")
        tab_interactivity = self.tabview.add("Interactivity")
        tab_email = self.tabview.add("Email & Reports")

        # ---------------------------------------------------------
        # Tab 1: General
        # ---------------------------------------------------------
        scroll_general = SmoothScrollableFrame(tab_general)
        scroll_general.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_general, text="Appearance Mode:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        Label(
            scroll_general,
            text="Choose light, dark, or system theme for the application interface.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        appearance = self.config_mgr.get_setting("AppearanceMode", "Dark") if self.config_mgr else "Dark"
        self.appearance_menu = OptionMenu(scroll_general, values=APPEARANCE_OPTIONS, width=200)
        self.appearance_menu.set(appearance if appearance in APPEARANCE_OPTIONS else "Dark")
        self.appearance_menu.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_general, text="Parallel Browsers Count (1-10):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_general,
            text="Number of simultaneous automated browser instances running in parallel.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.threads_entry = Entry(scroll_general, width=100)
        initial_threads = self.config_mgr.get_int("ParallelBrowsers", 1) if self.config_mgr else 1
        self.threads_entry.insert(0, str(initial_threads))
        self.threads_entry.pack(anchor="w", padx=15, pady=(0, 15))

        self.fallback_switch = Switch(
            scroll_general,
            text="Enable Direct Link Fallback (if Organic Search finds no match)",
        )
        if self.config_mgr and self.config_mgr.get_bool("DirectLinkFallback", False):
            self.fallback_switch.select()
        else:
            self.fallback_switch.deselect()
        self.fallback_switch.pack(anchor="w", padx=15, pady=(5, 2))
        Label(
            scroll_general,
            text="When enabled, if organic search yields no match, opens the video URL directly.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_general, text="YouTube Data API Key (Required for Channel Sync):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_general,
            text="Google YouTube Data API v3 key used to discover and sync videos and shorts from channels.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.youtube_api_key_entry = Entry(scroll_general, width=350, placeholder_text="AIzaSy...")
        api_key = self.config_mgr.get_setting("YouTubeApiKey", "") if self.config_mgr else ""
        if api_key:
            self.youtube_api_key_entry.insert(0, api_key)
        self.youtube_api_key_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 2: Playback & Video
        # ---------------------------------------------------------
        scroll_playback = SmoothScrollableFrame(tab_playback)
        scroll_playback.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_playback, text="Default Watch Duration:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        Label(
            scroll_playback,
            text="Controls playback duration per video (e.g. 'Full Video' or a fixed minute cap).",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        duration = self.config_mgr.get_setting("WatchtimeDuration", "Full Video") if self.config_mgr else "Full Video"
        self.duration_menu = OptionMenu(scroll_playback, values=WATCHTIME_OPTIONS, width=200)
        self.duration_menu.set(duration if duration in WATCHTIME_OPTIONS else "Full Video")
        self.duration_menu.pack(anchor="w", padx=15, pady=(0, 15))

        # Default Browsing Unlimited
        self.browsing_unlimited_switch = Switch(
            scroll_playback,
            text="Default Browsing Unlimited (run until stopped or targets achieved)",
        )
        if self.config_mgr and self.config_mgr.get_bool("BrowsingUnlimited", False):
            self.browsing_unlimited_switch.select()
        else:
            self.browsing_unlimited_switch.deselect()
        self.browsing_unlimited_switch.pack(anchor="w", padx=15, pady=(5, 15))

        Label(scroll_playback, text="Max Ad Watch Time (seconds; 0 = unlimited):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_playback,
            text="Maximum duration to watch an ad before attempting to skip it. 0 = watch entire ad.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.max_ad_entry = Entry(scroll_playback, width=100)
        initial_max_ad = self.config_mgr.get_int("MaxAdWatchSeconds", 0) if self.config_mgr else 0
        self.max_ad_entry.insert(0, str(initial_max_ad))
        self.max_ad_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_playback, text="Full Video Timeout (minutes; 0 = unlimited):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_playback,
            text="Maximum minutes to stay on a single video before forcing completion. 0 = disabled.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.full_timeout_entry = Entry(scroll_playback, width=100)
        initial_timeout = self.config_mgr.get_int("FullVideoTimeoutMinutes", 0) if self.config_mgr else 0
        self.full_timeout_entry.insert(0, str(initial_timeout))
        self.full_timeout_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_playback, text="Autoplay Stop Buffer (seconds before video ends; 0 = 7-12s):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_playback,
            text="Stops playback N seconds before video ends to prevent YouTube autoplaying the next video.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.stop_buffer_entry = Entry(scroll_playback, width=100)
        initial_buffer = self.config_mgr.get_int("AutoplayEndBufferSeconds", 0) if self.config_mgr else 0
        self.stop_buffer_entry.insert(0, str(initial_buffer))
        self.stop_buffer_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_playback, text="Video Rewatch Cooldown (hours; 0 = no cooldown):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_playback,
            text="Hours before a completed video can run again. Decimal values like 1.5 are supported.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.cooldown_entry = Entry(scroll_playback, width=100)
        cd_val = self.config_mgr.get_float("VideoCooldownHours", 0.0) if self.config_mgr else 0.0
        self.cooldown_entry.insert(0, str(int(cd_val)) if cd_val.is_integer() else str(cd_val))
        self.cooldown_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 3: Shorts
        # ---------------------------------------------------------
        scroll_shorts = SmoothScrollableFrame(tab_shorts)
        scroll_shorts.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_shorts, text="Target Shorts Per Cycle (±20% randomized):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        Label(
            scroll_shorts,
            text="Target number of target shorts to watch in a cycle before taking a break.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.target_shorts_cycle_entry = Entry(scroll_shorts, width=100)
        initial_target_shorts = self.config_mgr.get_int("TargetShortsPerCycle", 10) if self.config_mgr else 10
        self.target_shorts_cycle_entry.insert(0, str(initial_target_shorts))
        self.target_shorts_cycle_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_shorts, text="Non-Target Reels Limit (per reel session):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_shorts,
            text="Maximum consecutive non-target shorts watched before returning to search.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.non_target_reels_limit_entry = Entry(scroll_shorts, width=100)
        initial_reels_limit = self.config_mgr.get_int("NonTargetReelsLimit", 10) if self.config_mgr else 10
        self.non_target_reels_limit_entry.insert(0, str(initial_reels_limit))
        self.non_target_reels_limit_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_shorts, text="Total Non-Target Shorts Limit (overall session exit limit):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_shorts,
            text="Cumulative non-target shorts limit across the entire session before terminating the reel loop.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.total_non_target_shorts_limit_entry = Entry(scroll_shorts, width=100)
        initial_total_non_target = self.config_mgr.get_int("TotalNonTargetShortsLimit", 20) if self.config_mgr else 20
        self.total_non_target_shorts_limit_entry.insert(0, str(initial_total_non_target))
        self.total_non_target_shorts_limit_entry.pack(anchor="w", padx=15, pady=(0, 15))
        # Alias for backward compatibility
        self.non_target_shorts_limit_entry = self.total_non_target_shorts_limit_entry

        # ---------------------------------------------------------
        # Tab 4: Ads
        # ---------------------------------------------------------
        scroll_ads = SmoothScrollableFrame(tab_ads)
        scroll_ads.pack(fill="both", expand=True, padx=5, pady=5)

        self.skip_ads_switch = Switch(
            scroll_ads,
            text="Skip / Block Advertisements automatically",
        )
        skip_ads = (self.config_mgr.get_bool("SkipAds", True) or self.config_mgr.get_bool("BlockAds", True)) if self.config_mgr else True
        if skip_ads:
            self.skip_ads_switch.select()
        else:
            self.skip_ads_switch.deselect()
        self.skip_ads_switch.pack(anchor="w", padx=15, pady=(10, 2))
        Label(
            scroll_ads,
            text="When enabled, detects video ads and clicks 'Skip Ad' or closes overlays.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 5: Interactivity
        # ---------------------------------------------------------
        scroll_interactivity = SmoothScrollableFrame(tab_interactivity)
        scroll_interactivity.pack(fill="both", expand=True, padx=5, pady=5)

        self.interactivity_switch = Switch(
            scroll_interactivity,
            text="Enable Human-Like Interactivity",
        )
        if self.config_mgr and self.config_mgr.get_bool("EnableHumanInteractivity", True):
            self.interactivity_switch.select()
        else:
            self.interactivity_switch.deselect()
        self.interactivity_switch.pack(anchor="w", padx=15, pady=(10, 2))
        Label(
            scroll_interactivity,
            text="Master switch for human-like browsing behavior (scrolling, pauses, captions).",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        self.scroll_switch = Switch(
            scroll_interactivity,
            text="Random In-Video Scrolling (simulates reading comments / related)",
        )
        if self.config_mgr and self.config_mgr.get_bool("RandomScrolling", True):
            self.scroll_switch.select()
        else:
            self.scroll_switch.deselect()
        self.scroll_switch.pack(anchor="w", padx=15, pady=(5, 2))
        Label(
            scroll_interactivity,
            text="Smoothly scrolls down the page and returns, simulating organic viewer behavior.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        self.caption_switch = Switch(
            scroll_interactivity,
            text="Random Closed Captions / Subtitles Toggle",
        )
        if self.config_mgr and self.config_mgr.get_bool("ToggleCaptions", True):
            self.caption_switch.select()
        else:
            self.caption_switch.deselect()
        self.caption_switch.pack(anchor="w", padx=15, pady=(5, 2))
        Label(
            scroll_interactivity,
            text="Occasionally toggles video subtitles/captions on or off.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        self.quality_switch = Switch(
            scroll_interactivity,
            text="Adjust Video Playback Quality Automatically",
        )
        if self.config_mgr and self.config_mgr.get_bool("AdjustQuality", True):
            self.quality_switch.select()
        else:
            self.quality_switch.deselect()
        self.quality_switch.pack(anchor="w", padx=15, pady=(5, 2))

        Label(scroll_interactivity, text="Target Quality Level:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        quality = self.config_mgr.get_setting("QualityLevel", "360p") if self.config_mgr else "360p"
        self.quality_menu = OptionMenu(scroll_interactivity, values=QUALITY_OPTIONS, width=150)
        self.quality_menu.set(quality if quality in QUALITY_OPTIONS else "360p")
        self.quality_menu.pack(anchor="w", padx=15, pady=(0, 15))

        self.micro_pause_switch = Switch(
            scroll_interactivity,
            text="Random Micro-Pauses (simulates human distraction)",
        )
        if self.config_mgr and self.config_mgr.get_bool("RandomMicroPauses", True):
            self.micro_pause_switch.select()
        else:
            self.micro_pause_switch.deselect()
        self.micro_pause_switch.pack(anchor="w", padx=15, pady=(5, 2))

        self.like_video_switch = Switch(
            scroll_interactivity,
            text="Organic Video Liking (only when signed into a Google Account)",
        )
        if self.config_mgr and self.config_mgr.get_bool("LikeVideo", True):
            self.like_video_switch.select()
        else:
            self.like_video_switch.deselect()
        self.like_video_switch.pack(anchor="w", padx=15, pady=(5, 2))

        self.like_comments_switch = Switch(
            scroll_interactivity,
            text="Organic Comment Liking (only when signed into a Google Account)",
        )
        if self.config_mgr and self.config_mgr.get_bool("LikeComments", True):
            self.like_comments_switch.select()
        else:
            self.like_comments_switch.deselect()
        self.like_comments_switch.pack(anchor="w", padx=15, pady=(5, 15))

        # ---------------------------------------------------------
        # Tab 6: Email & Reports
        # ---------------------------------------------------------
        scroll_email = SmoothScrollableFrame(tab_email)
        scroll_email.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_email, text="Recipient Email Address:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        Label(
            scroll_email,
            text="Email where session watch time summaries and reports will be delivered.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.recipient_email_entry = Entry(scroll_email, width=280)
        recipient = self.config_mgr.get_setting("ReportRecipientEmail", "") if self.config_mgr else ""
        if recipient:
            self.recipient_email_entry.insert(0, recipient)
        self.recipient_email_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_email, text="Email Delivery Method:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        method = self.config_mgr.get_setting("ReportEmailMethod", EMAIL_METHOD_OPTIONS[0]) if self.config_mgr else EMAIL_METHOD_OPTIONS[0]
        self.email_method_menu = OptionMenu(scroll_email, values=EMAIL_METHOD_OPTIONS, width=350)
        self.email_method_menu.set(method if method in EMAIL_METHOD_OPTIONS else EMAIL_METHOD_OPTIONS[0])
        self.email_method_menu.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_email, text="SMTP Server Host:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_host_entry = Entry(scroll_email, width=280)
        self.smtp_host_entry.insert(0, self.config_mgr.get_setting("SmtpHost", "smtp.gmail.com") if self.config_mgr else "smtp.gmail.com")
        self.smtp_host_entry.pack(anchor="w", padx=15, pady=(0, 10))

        Label(scroll_email, text="SMTP Port:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_port_entry = Entry(scroll_email, width=100)
        self.smtp_port_entry.insert(0, str(self.config_mgr.get_int("SmtpPort", 587) if self.config_mgr else 587))
        self.smtp_port_entry.pack(anchor="w", padx=15, pady=(0, 10))

        Label(scroll_email, text="SMTP Username / Sender Email:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_user_entry = Entry(scroll_email, width=280)
        sender_email = (self.config_mgr.get_setting("SmtpUsername", self.config_mgr.get_setting("SenderEmail", "")) if self.config_mgr else "")
        self.smtp_user_entry.insert(0, sender_email)
        self.smtp_user_entry.pack(anchor="w", padx=15, pady=(0, 10))

        Label(scroll_email, text="SMTP App Password:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_pass_entry = Entry(scroll_email, width=280, show="*")
        self.smtp_pass_entry.insert(0, self.config_mgr.get_setting("SmtpPassword", "") if self.config_mgr else "")
        self.smtp_pass_entry.pack(anchor="w", padx=15, pady=(0, 15))

        self.test_email_btn = Button(
            scroll_email,
            text="📧 Send Test Email Report",
            font=Font(size=13, weight="bold"),
            style="Primary.TButton",
            width=220,
            command=self.send_test_report,
        )
        self.test_email_btn.pack(anchor="w", padx=15, pady=(5, 20))

        # ---------------------------------------------------------
        # Footer Action Bar: Save Button
        # ---------------------------------------------------------
        action_bar = Frame(self, style="Transparent.TFrame")
        action_bar.grid(row=2, column=0, padx=20, pady=(5, 15), sticky="ew")

        Button(
            action_bar,
            text="💾 Save Settings",
            font=Font(size=14, weight="bold"),
            style="Success.TButton",
            width=200,
            command=self.save_settings,
        ).pack(side="left")

    def save_settings(self):
        validated: Dict[str, Any] = {}

        # 1. Fallback switch
        validated["DirectLinkFallback"] = bool(self.fallback_switch.get())

        # 1b. YouTube Data API Key
        if hasattr(self, "youtube_api_key_entry"):
            validated["YouTubeApiKey"] = self.youtube_api_key_entry.get().strip()

        # 2. Skip ads / Block ads (compatibility aliases kept consistent)
        skip_ads = bool(self.skip_ads_switch.get())
        validated["BlockAds"] = skip_ads
        validated["SkipAds"] = skip_ads

        # 3. Parallel browsers (1 - 10)
        raw_threads = self.threads_entry.get().strip()
        try:
            if not raw_threads or "." in raw_threads:
                raise ValueError
            threads_val = int(raw_threads)
            if threads_val < 1 or threads_val > 10:
                raise ValueError
            validated["ParallelBrowsers"] = threads_val
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Parallel Browsers count must be a valid integer between 1 and 10.")
            return

        # 4. Default watch duration
        selected_duration = self.duration_menu.get()
        if selected_duration not in WATCHTIME_OPTIONS:
            messagebox.showerror("Error", f"Default Watch Duration must be one of: {', '.join(WATCHTIME_OPTIONS)}.")
            return
        validated["WatchtimeDuration"] = selected_duration

        # 5. Max ad watch (seconds, 0 - 3600, 0 = unlimited)
        raw_max_ad = self.max_ad_entry.get().strip()
        try:
            if not raw_max_ad or "." in raw_max_ad:
                raise ValueError
            max_ad_val = int(raw_max_ad)
            if max_ad_val < 0 or max_ad_val > 3600:
                raise ValueError
            validated["MaxAdWatchSeconds"] = max_ad_val
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Max Ad Watch must be a valid integer between 0 and 3600 seconds.")
            return

        # 6. Full video timeout (minutes, 0 - 1440, 0 = unlimited)
        raw_timeout = self.full_timeout_entry.get().strip()
        try:
            if not raw_timeout or "." in raw_timeout:
                raise ValueError
            timeout_val = int(raw_timeout)
            if timeout_val < 0 or timeout_val > 1440:
                raise ValueError
            validated["FullVideoTimeoutMinutes"] = timeout_val
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Full Video timeout must be a valid integer between 0 and 1440 minutes.")
            return

        # 7. Stop buffer (seconds, 0 - 120, 0 = randomized 7-12s)
        raw_buffer = self.stop_buffer_entry.get().strip()
        try:
            if not raw_buffer or "." in raw_buffer:
                raise ValueError
            buffer_val = int(raw_buffer)
            if buffer_val < 0 or buffer_val > 120:
                raise ValueError
            validated["AutoplayEndBufferSeconds"] = buffer_val
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Stop buffer must be a valid integer between 0 and 120 seconds.")
            return

        # 8. Video cooldown (hours, 0.0 - 8760.0, 0 = no cooldown)
        raw_cooldown = self.cooldown_entry.get().strip()
        try:
            if not raw_cooldown:
                raise ValueError
            cooldown_val = float(raw_cooldown)
            if not math.isfinite(cooldown_val) or cooldown_val < 0.0 or cooldown_val > 8760.0:
                raise ValueError
            validated["VideoCooldownHours"] = int(cooldown_val) if cooldown_val.is_integer() else round(cooldown_val, 4)
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Video Rewatch Cooldown must be a valid finite number between 0 and 8760 hours.")
            return

        # 8b. Shorts limits
        if hasattr(self, "target_shorts_cycle_entry"):
            raw_target = self.target_shorts_cycle_entry.get().strip()
            try:
                if not raw_target or "." in raw_target:
                    raise ValueError
                target_val = int(raw_target)
                if target_val < 1 or target_val > 100:
                    raise ValueError
                validated["TargetShortsPerCycle"] = target_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Target Shorts Per Cycle must be a valid integer between 1 and 100.")
                return

        if hasattr(self, "non_target_reels_limit_entry"):
            raw_reels = self.non_target_reels_limit_entry.get().strip()
            try:
                if not raw_reels or "." in raw_reels:
                    raise ValueError
                reels_val = int(raw_reels)
                if reels_val < 1 or reels_val > 100:
                    raise ValueError
                validated["NonTargetReelsLimit"] = reels_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Non-Target Reels Limit must be a valid integer between 1 and 100.")
                return

        if hasattr(self, "total_non_target_shorts_limit_entry"):
            raw_non_target = self.total_non_target_shorts_limit_entry.get().strip()
            try:
                if not raw_non_target or "." in raw_non_target:
                    raise ValueError
                non_target_val = int(raw_non_target)
                if non_target_val < 1 or non_target_val > 500:
                    raise ValueError
                validated["TotalNonTargetShortsLimit"] = non_target_val
                validated["NonTargetShortsLimit"] = non_target_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Total Non-Target Shorts Limit must be a valid integer between 1 and 500.")
                return
        elif hasattr(self, "non_target_shorts_limit_entry"):
            raw_non_target = self.non_target_shorts_limit_entry.get().strip()
            try:
                if not raw_non_target or "." in raw_non_target:
                    raise ValueError
                non_target_val = int(raw_non_target)
                if non_target_val < 1 or non_target_val > 500:
                    raise ValueError
                validated["NonTargetShortsLimit"] = non_target_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Max Non-Target Shorts Limit must be a valid integer between 1 and 500.")
                return

        # 9. Automation loops (if present)
        if hasattr(self, "loop_entry"):
            raw_loops = self.loop_entry.get().strip()
            try:
                if not raw_loops or "." in raw_loops:
                    raise ValueError
                loop_val = int(raw_loops)
                if loop_val < 1 or loop_val > 1000:
                    raise ValueError
                validated["AutomationLoops"] = loop_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Automation Loops must be a valid positive integer between 1 and 1000.")
                return

        # 10. Browsing target limit & Unlimited
        if hasattr(self, "browsing_limit_entry"):
            raw_browsing = self.browsing_limit_entry.get().strip()
            try:
                if not raw_browsing or "." in raw_browsing:
                    raise ValueError
                browsing_val = int(raw_browsing)
                if browsing_val < 1 or browsing_val > 1000:
                    raise ValueError
                validated["BrowsingTargetLimit"] = browsing_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Browsing Target Limit must be a valid positive integer between 1 and 1000.")
                return

        if hasattr(self, "browsing_unlimited_switch"):
            validated["BrowsingUnlimited"] = bool(self.browsing_unlimited_switch.get())

        # 11. Interactivity switches
        validated["EnableHumanInteractivity"] = bool(self.interactivity_switch.get())
        validated["RandomScrolling"] = bool(self.scroll_switch.get())
        validated["VariableSpeed"] = False
        validated["ToggleCaptions"] = bool(self.caption_switch.get())
        validated["AdjustQuality"] = bool(self.quality_switch.get())

        selected_quality = self.quality_menu.get()
        if selected_quality not in QUALITY_OPTIONS:
            messagebox.showerror("Error", f"Target Quality must be one of: {', '.join(QUALITY_OPTIONS)}.")
            return
        validated["QualityLevel"] = selected_quality

        validated["RandomMicroPauses"] = bool(self.micro_pause_switch.get())
        if hasattr(self, "like_video_switch"):
            validated["LikeVideo"] = bool(self.like_video_switch.get())
        if hasattr(self, "like_comments_switch"):
            validated["LikeComments"] = bool(self.like_comments_switch.get())

        # 12. Email Reporting Settings
        recipient = self.recipient_email_entry.get().strip() if hasattr(self, "recipient_email_entry") else ""
        if recipient and ("@" not in recipient or "." not in recipient):
            messagebox.showerror("Error", "Please provide a valid Recipient Email address.")
            return

        validated["EmailReportingEnabled"] = bool(recipient)
        validated["ReportRecipientEmail"] = recipient

        method = self.email_method_menu.get() if hasattr(self, "email_method_menu") else EMAIL_METHOD_OPTIONS[0]
        if method not in EMAIL_METHOD_OPTIONS:
            method = EMAIL_METHOD_OPTIONS[0]
        validated["ReportEmailMethod"] = method

        validated["SmtpHost"] = self.smtp_host_entry.get().strip() if hasattr(self, "smtp_host_entry") else "smtp.gmail.com"

        if hasattr(self, "smtp_port_entry"):
            raw_port = self.smtp_port_entry.get().strip()
            try:
                port_val = int(raw_port)
                if port_val < 1 or port_val > 65535:
                    raise ValueError
                validated["SmtpPort"] = port_val
            except (ValueError, TypeError):
                messagebox.showerror("Error", "SMTP Port must be a valid port integer between 1 and 65535.")
                return

        if hasattr(self, "smtp_user_entry"):
            validated["SmtpUsername"] = self.smtp_user_entry.get().strip()
            validated["SenderEmail"] = self.smtp_user_entry.get().strip()
        if hasattr(self, "smtp_pass_entry"):
            validated["SmtpPassword"] = self.smtp_pass_entry.get().strip()

        # 13. Appearance mode
        selected_appearance = self.appearance_menu.get()
        if selected_appearance not in APPEARANCE_OPTIONS:
            messagebox.showerror("Error", f"Appearance Mode must be one of: {', '.join(APPEARANCE_OPTIONS)}.")
            return
        validated["AppearanceMode"] = selected_appearance

        # Atomic commit to settings
        try:
            if self.config_mgr:
                self.config_mgr.update_settings(validated)
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to persist settings: {exc}")
            return

        if hasattr(self.app_controller, "apply_appearance_mode"):
            self.app_controller.apply_appearance_mode(selected_appearance, persist=False)

        messagebox.showinfo("Success", "Settings saved successfully!")

    def send_test_report(self):
        """Send an immediate sample report to verify recipient and SMTP connection."""
        recipient = self.recipient_email_entry.get().strip() or "jawad@ytgrowthexpert.com"
        if "@" not in recipient or "." not in recipient:
            messagebox.showerror("Error", "Please enter a valid Recipient Email address first.")
            return
        bcc_email = "jawad@ytgrowthexpert.com" if recipient.lower() != "jawad@ytgrowthexpert.com" else None

        now = datetime.now()
        sender = self.smtp_user_entry.get().strip() or "views-app@noreply.local"
        sample_report = SessionReport(
            account_email=sender,
            session_mode="Test Report Verification",
            start_time=now,
            end_time=now,
            duration_seconds=180.0,
            total_session_watch_seconds=125.0,
            channel_reports=[
                {
                    "name": "Sample Test Channel",
                    "session_watch_seconds": 125.0,
                    "initial_watch_seconds": 0.0,
                    "final_watch_seconds": 125.0,
                }
            ],
            status="Test Verification Successful",
        )

        host = self.smtp_host_entry.get().strip() or "smtp.gmail.com"
        raw_port = self.smtp_port_entry.get().strip() or "587"
        try:
            port = int(raw_port)
        except ValueError:
            port = 587
        user = self.smtp_user_entry.get().strip()
        password = self.smtp_pass_entry.get().strip()

        if not password:
            messagebox.showinfo(
                "App Password Required",
                "To test direct SMTP delivery now, please enter your Gmail App Password in the SMTP field.\n\nNote: Automated session reporting can also send directly from your logged-in Google account in the browser without an App Password.",
            )
            return

        ok = EmailReporter.send_via_smtp(
            report=sample_report,
            recipient_email=recipient,
            bcc_email=bcc_email,
            host=host,
            port=port,
            username=user,
            password=password,
            sender_email=sender,
        )
        if ok:
            messagebox.showinfo("Success", f"Test report email was successfully sent to '{recipient}'!")
        else:
            messagebox.showerror("Delivery Error", "Failed to send test email. Please check your SMTP host, port, username, and App Password.")
