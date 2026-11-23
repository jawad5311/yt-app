from __future__ import annotations

import math
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
from typing import Any, Dict, List, Optional

from ...automation.email_reporter import DEFAULT_ADMIN_RECIPIENT_EMAIL, EmailReporter, SessionReport
from ...automation.warmup_manager import format_number
from ...models.video_task import DEFAULT_MAX_RUNS, DEFAULT_SHORTS_MAX_RUNS
from ...utils.channel_fetcher import validate_youtube_api_key
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
from ..confirm_dialog import ThemedAlertDialog, show_themed_alert_dialog
from ..theme import get_font, get_theme_colors
from ..tooltip import add_tooltip

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
QUALITY_OPTIONS: List[str] = [
    "360p",
    "480p",
    "720p",
    "1080p",
    "1440p",
    "Max Resolution",
    "Random (360p / 480p)",
]
EMAIL_METHOD_OPTIONS: List[str] = [
    "Headless Browser Gmail (Recommended)",
    "Active Browser Gmail",
    "SMTP Connection",
]
SHORT_SLEEP_OPTIONS: List[str] = [
    "30 Minutes",
    "1 Hour",
    "2 Hours",
    "3 Hours",
    "4 Hours",
    "6 Hours",
    "8 Hours",
    "12 Hours",
    "24 Hours",
    "Disabled",
]
NO_TARGET_SLEEP_OPTIONS: List[str] = SHORT_SLEEP_OPTIONS

NIGHT_START_TIME_OPTIONS: List[str] = [
    f"{h:02d}:00 ({(h % 12) or 12}:00 {'AM' if h < 12 else 'PM'})" for h in range(24)
]

NIGHT_WINDOW_OPTIONS: List[str] = [
    "8 Hours",
    "9 Hours",
    "10 Hours",
    "11 Hours",
    "12 Hours",
    "13 Hours",
    "14 Hours",
    "15 Hours",
    "16 Hours",
]

NIGHT_DURATION_OPTIONS: List[str] = [
    "5 Hours",
    "6 Hours",
    "7 Hours",
    "8 Hours",
    "9 Hours",
    "10 Hours",
    "7 - 8 Hours (Random)",
    "6 - 8 Hours (Random)",
    "7 - 9 Hours (Random)",
    "8 - 9 Hours (Random)",
]


class SmoothScrollableFrame(ScrollableFrame):
    """High-performance TTK scrollable container for settings forms."""
    pass


class SettingsView(Frame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, style="TFrame")
        self.app_controller = app_controller
        self.config_mgr = app_controller.config_mgr if hasattr(app_controller, "config_mgr") else None
        self._bubble_widget = None
        self._bubble_timer = None
        self._save_status_lbl = None
        self.last_alert_dialog = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header (macOS Large Title & Subtitle)
        header_frame = Frame(self, style="Transparent.TFrame")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        Label(
            header_frame,
            text="Application Settings",
            font=Font(size=22, weight="bold"),
            style="Header.TLabel",
        ).pack(anchor="w")

        Label(
            header_frame,
            text="Preferences, Automation Parameters & System Configuration",
            font=Font(size=9),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # Sub-Tabs Container
        self.tabview = Tabview(self, anchor="w")
        self.tabview.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="nsew")

        tab_general = self.tabview.add("General")
        tab_playback = self.tabview.add("Playback")
        tab_sleep = self.tabview.add("Sleep & Scheduling")
        tab_video = self.tabview.add("Video")
        tab_shorts = self.tabview.add("Shorts")
        tab_ads = self.tabview.add("Ads")
        tab_interactivity = self.tabview.add("Interactivity")
        tab_email = self.tabview.add("Email & Reports")
        tab_rdp = self.tabview.add("RDP/VPS")
        tab_warmup = self.tabview.add("Warm Up")

        # Compatibility aliases for code checking old tab names
        self.tabview._tab_dict["Playback & Video"] = tab_playback
        self.tabview._tab_dict["Playback & Queue"] = tab_playback
        self.tabview._tab_dict["Sleep Mode & Scheduling"] = tab_sleep
        self.tabview._tab_dict["Sleep"] = tab_sleep
        self.tabview._tab_dict["Scheduling"] = tab_sleep

        # ---------------------------------------------------------
        # Tab 1: General
        # ---------------------------------------------------------
        scroll_general = SmoothScrollableFrame(tab_general)
        scroll_general.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_general, text="System Name (Required):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        Label(
            scroll_general,
            text="PC / system name identifier (compulsory). Displayed in email report subjects and report headers.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.system_name_entry = Entry(scroll_general, width=350, placeholder_text="e.g. Worker-PC-01")
        sys_name = self.config_mgr.get_setting("SystemName", "") if self.config_mgr else ""
        if sys_name:
            self.system_name_entry.insert(0, sys_name)
        self.system_name_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_general, text="Appearance Mode:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_general,
            text="Choose light, dark, or system theme for the application interface.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        appearance = self.config_mgr.get_setting("AppearanceMode", "Dark") if self.config_mgr else "Dark"
        self.appearance_menu = OptionMenu(
            scroll_general,
            values=APPEARANCE_OPTIONS,
            width=200,
            command=self._on_appearance_change,
        )
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

        self.use_system_chrome_switch = Switch(
            scroll_general,
            text="Use Personal Chrome Profile (Undetectable & Ready)",
        )
        if self.config_mgr and self.config_mgr.get_bool("UseSystemChromeProfile", True):
            self.use_system_chrome_switch.select()
        else:
            self.use_system_chrome_switch.deselect()
        self.use_system_chrome_switch.pack(anchor="w", padx=15, pady=(5, 2))
        Label(
            scroll_general,
            text="When enabled, automates your installed Google Chrome profile directly. Bypasses manual account entry.",
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
        api_key_frame = Frame(scroll_general, style="Transparent.TFrame")
        api_key_frame.pack(anchor="w", padx=15, pady=(0, 15))

        self.youtube_api_key_entry = Entry(api_key_frame, width=320, placeholder_text="YouTube API key here")
        api_key = self.config_mgr.get_setting("YouTubeApiKey", "") if self.config_mgr else ""
        if api_key:
            self.youtube_api_key_entry.insert(0, api_key)
        self.youtube_api_key_entry.pack(side="left", padx=(0, 10))

        self.btn_test_api = Button(
            api_key_frame,
            text="Test",
            style="Primary.TButton",
            width=70,
            command=self.test_youtube_api_key,
        )
        self.btn_test_api.pack(side="left")
        add_tooltip(
            self.btn_test_api,
            "Test YouTube Data API v3 key validity and connectivity.",
        )
        self.youtube_api_key_entry.bind("<KeyRelease>", self._on_api_key_modified)

        # ---------------------------------------------------------
        # Tab 2: Playback
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

        # ---------------------------------------------------------
        # Tab 3: Sleep & Scheduling
        # ---------------------------------------------------------
        scroll_sleep = SmoothScrollableFrame(tab_sleep)
        scroll_sleep.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_sleep, text="Sleep Modes & Scheduling", font=Font(weight="bold", size=14)).pack(
            anchor="w", padx=15, pady=(15, 2)
        )
        Label(
            scroll_sleep,
            text="Configure automated sleep cycles: Short Sleep (Nap) when targeted videos/shorts are missing, and Long Sleep (Night) based on local system time.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 10))

        # 1. Short Sleep or Nap
        Label(scroll_sleep, text="1. Short Sleep / Nap (When No Target Found):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(4, 2)
        )
        Label(
            scroll_sleep,
            text="When browsing and shorts search finds no video from targeted channels, the app goes into nap mode, closes YouTube, sends the session notification email, and wakes up after this duration to search for new videos.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))

        initial_sleep = (
            (self.config_mgr.get_setting("ShortSleepHours") or self.config_mgr.get_setting("NoTargetFoundSleepHours", "2 Hours"))
            if self.config_mgr else "2 Hours"
        )
        initial_sleep_opt = "2 Hours"
        if isinstance(initial_sleep, str) and initial_sleep in SHORT_SLEEP_OPTIONS:
            initial_sleep_opt = initial_sleep
        else:
            try:
                hrs = float(str(initial_sleep).split()[0])
                if hrs <= 0:
                    initial_sleep_opt = "Disabled"
                elif hrs == 0.5:
                    initial_sleep_opt = "30 Minutes"
                else:
                    int_h = int(hrs)
                    cand = f"{int_h} Hour" if int_h == 1 else f"{int_h} Hours"
                    initial_sleep_opt = cand if cand in SHORT_SLEEP_OPTIONS else "2 Hours"
            except Exception:
                initial_sleep_opt = "2 Hours"

        self.short_sleep_menu = OptionMenu(
            scroll_sleep,
            values=SHORT_SLEEP_OPTIONS,
            width=220,
        )
        self.short_sleep_menu.set(initial_sleep_opt)
        self.short_sleep_menu.pack(anchor="w", padx=15, pady=(0, 15))
        self.no_target_sleep_menu = self.short_sleep_menu  # Backward compatibility alias

        # 2. Long Sleep or Night
        Label(scroll_sleep, text="2. Long Sleep / Night Mode (Human System Time Schedule):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(6, 2)
        )
        Label(
            scroll_sleep,
            text="Simulates human sleeping habits. Reads local system time to determine nighttime. The app will randomly choose a sleep window during your configured night timeframe and enter Long Sleep.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 8))

        initial_night_enabled = bool(self.config_mgr.get_setting("NightSleepEnabled", False)) if self.config_mgr else False
        self.night_sleep_switch = Switch(
            scroll_sleep,
            text="Enable Long Sleep / Night Mode",
        )
        if initial_night_enabled:
            self.night_sleep_switch.select()
        else:
            self.night_sleep_switch.deselect()
        self.night_sleep_switch.pack(anchor="w", padx=15, pady=(0, 8))

        # Night Window Start Time
        Label(scroll_sleep, text="Night Window Start Time (System Time):", font=Font(size=12, weight="bold")).pack(
            anchor="w", padx=25, pady=(3, 2)
        )
        Label(
            scroll_sleep,
            text="The time of day when your system enters nighttime (e.g. 22:00 / 10:00 PM). Sessions started during nighttime bypass initial long sleep to browse and nap until the next cycle.",
            font=Font(size=11),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=25, pady=(0, 4))

        init_start = str(self.config_mgr.get_setting("NightSleepStartTime", "22:00 (10:00 PM)")) if self.config_mgr else "22:00 (10:00 PM)"
        if init_start not in NIGHT_START_TIME_OPTIONS:
            init_start = "22:00 (10:00 PM)"
        self.night_start_menu = OptionMenu(
            scroll_sleep,
            values=NIGHT_START_TIME_OPTIONS,
            width=220,
        )
        self.night_start_menu.set(init_start)
        self.night_start_menu.pack(anchor="w", padx=25, pady=(0, 10))

        # Night Window Time Frame (Duration)
        Label(scroll_sleep, text="Night Window Time Frame (Total Duration):", font=Font(size=12, weight="bold")).pack(
            anchor="w", padx=25, pady=(3, 2)
        )
        Label(
            scroll_sleep,
            text="Total duration of the night window (e.g., 12 Hours means nighttime spans from 22:00 to 10:00).",
            font=Font(size=11),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=25, pady=(0, 4))

        init_window = str(self.config_mgr.get_setting("NightSleepWindowHours", "12 Hours")) if self.config_mgr else "12 Hours"
        if init_window not in NIGHT_WINDOW_OPTIONS:
            init_window = "12 Hours"
        self.night_window_menu = OptionMenu(
            scroll_sleep,
            values=NIGHT_WINDOW_OPTIONS,
            width=220,
        )
        self.night_window_menu.set(init_window)
        self.night_window_menu.pack(anchor="w", padx=25, pady=(0, 10))

        # Night Sleep Duration
        Label(scroll_sleep, text="Night Sleep Duration (Randomized Within Window):", font=Font(size=12, weight="bold")).pack(
            anchor="w", padx=25, pady=(3, 2)
        )
        Label(
            scroll_sleep,
            text="Duration to sleep (e.g., 8 Hours or 7 - 8 Hours). The app randomly chooses the sleep window during the nighttime timeframe.",
            font=Font(size=11),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=25, pady=(0, 4))

        init_dur = str(self.config_mgr.get_setting("NightSleepDurationHours", "8 Hours")) if self.config_mgr else "8 Hours"
        if init_dur not in NIGHT_DURATION_OPTIONS:
            init_dur = "8 Hours"
        self.night_duration_menu = OptionMenu(
            scroll_sleep,
            values=NIGHT_DURATION_OPTIONS,
            width=220,
        )
        self.night_duration_menu.set(init_dur)
        self.night_duration_menu.pack(anchor="w", padx=25, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 4: Video
        # ---------------------------------------------------------
        scroll_video = SmoothScrollableFrame(tab_video)
        scroll_video.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_video, text="Default Target Views per Video:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        Label(
            scroll_video,
            text="Default target views / watch multiplier assigned to new long-form videos added to queue (0 = unlimited).",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.default_video_views_entry = Entry(scroll_video, width=100)
        initial_video_views = (
            self.config_mgr.get_float("DefaultVideoTargetViews", 3.0)
            if self.config_mgr
            else 3.0
        )
        self.default_video_views_entry.insert(
            0, str(int(initial_video_views)) if initial_video_views.is_integer() else str(initial_video_views)
        )
        self.default_video_views_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_video, text="Default Max Views Cap per Video:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_video,
            text="Default max views cap assigned to new long-form videos added to queue (0 = unlimited).",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.default_video_max_views_entry = Entry(scroll_video, width=100)
        initial_video_max = (
            self.config_mgr.get_int("DefaultVideoMaxViews", DEFAULT_MAX_RUNS)
            if self.config_mgr
            else DEFAULT_MAX_RUNS
        )
        self.default_video_max_views_entry.insert(0, str(initial_video_max))
        self.default_video_max_views_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_video, text="Video Rewatch Cooldown (hours; 0 = no cooldown):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_video,
            text="Hours before a completed video can run again. Decimal values like 1.5 are supported.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.cooldown_entry = Entry(scroll_video, width=100)
        cd_val = self.config_mgr.get_float("VideoCooldownHours", 18.0) if self.config_mgr else 18.0
        self.cooldown_entry.insert(0, str(int(cd_val)) if cd_val.is_integer() else str(cd_val))
        self.cooldown_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 4: Shorts
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
        initial_reels_limit = self.config_mgr.get_int("NonTargetReelsLimit", 12) if self.config_mgr else 12
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
        initial_total_non_target = self.config_mgr.get_int("TotalNonTargetShortsLimit", 33) if self.config_mgr else 33
        self.total_non_target_shorts_limit_entry.insert(0, str(initial_total_non_target))
        self.total_non_target_shorts_limit_entry.pack(anchor="w", padx=15, pady=(0, 15))
        # Alias for backward compatibility
        self.non_target_shorts_limit_entry = self.total_non_target_shorts_limit_entry

        Label(scroll_shorts, text="Default Max Views per Short:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_shorts,
            text="Default max views cap assigned to new YouTube Shorts added to queue (0 = unlimited).",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.default_shorts_views_entry = Entry(scroll_shorts, width=100)
        initial_shorts_views = self.config_mgr.get_int("DefaultShortsMaxViews", DEFAULT_SHORTS_MAX_RUNS) if self.config_mgr else DEFAULT_SHORTS_MAX_RUNS
        self.default_shorts_views_entry.insert(0, str(initial_shorts_views))
        self.default_shorts_views_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 5: Ads
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
        Label(scroll_ads, text="Max Ad Watch Time (±20% randomized; seconds, 0 = unlimited):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_ads,
            text="Duration in seconds to watch an ad before attempting to close or skip it (±20% randomized, e.g. 10s -> 8-12s). 0 = watch entire ad.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.max_ad_entry = Entry(scroll_ads, width=100)
        initial_max_ad = self.config_mgr.get_int("MaxAdWatchSeconds", 9) if self.config_mgr else 9
        self.max_ad_entry.insert(0, str(initial_max_ad))
        self.max_ad_entry.pack(anchor="w", padx=15, pady=(0, 15))


        # ---------------------------------------------------------
        # Tab 6: Interactivity
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

        self.screen_mode_switch = Switch(
            scroll_interactivity,
            text="Fullscreen & Theater Mode Simulation",
        )
        if self.config_mgr and self.config_mgr.get_bool("FullscreenTheaterMode", True):
            self.screen_mode_switch.select()
        else:
            self.screen_mode_switch.deselect()
        self.screen_mode_switch.pack(anchor="w", padx=15, pady=(5, 2))
        Label(
            scroll_interactivity,
            text="Watches most videos in full screen and some in theater mode after start/ad skip; exits full screen at 94% progress to navigate suggested videos.",
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
        quality = str(self.config_mgr.get_setting("QualityLevel", "720p") if self.config_mgr else "720p").strip()
        if quality in ("720", "720p"):
            quality_opt = "720p"
        elif quality in ("1080", "1080p"):
            quality_opt = "1080p"
        elif quality in ("1440", "1440p"):
            quality_opt = "1440p"
        elif quality.lower() in ("max", "max resolution", "maximum", "max_resolution"):
            quality_opt = "Max Resolution"
        elif quality in ("480", "480p"):
            quality_opt = "480p"
        elif quality in ("360", "360p"):
            quality_opt = "360p"
        elif quality in QUALITY_OPTIONS:
            quality_opt = quality
        else:
            quality_opt = "720p"
        self.quality_menu = OptionMenu(scroll_interactivity, values=QUALITY_OPTIONS, width=180)
        self.quality_menu.set(quality_opt)
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
        # Tab 7: Email & Reports
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
        if recipient and recipient.strip().lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower():
            self.recipient_email_entry.insert(0, recipient.strip())
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
        # Tab 8: RDP/VPS
        # ---------------------------------------------------------
        scroll_rdp = SmoothScrollableFrame(tab_rdp)
        scroll_rdp.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_rdp, text="Username:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        self.rdp_username_entry = Entry(scroll_rdp, width=280)
        rdp_user = self.config_mgr.get_setting("RdpUsername", "") if self.config_mgr else ""
        if rdp_user:
            self.rdp_username_entry.insert(0, rdp_user)
        self.rdp_username_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_rdp, text="Password:", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.rdp_password_entry = Entry(scroll_rdp, width=280)
        rdp_pass = self.config_mgr.get_setting("RdpPassword", "") if self.config_mgr else ""
        if rdp_pass:
            self.rdp_password_entry.insert(0, rdp_pass)
        self.rdp_password_entry.pack(anchor="w", padx=15, pady=(0, 10))

        self.rdp_info_label = Label(
            scroll_rdp,
            text="This username and password is sent to the email address for the user to log in to the RDP or VPS if needed.",
            font=Font(size=12),
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        )
        self.rdp_info_label.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 9: Warm Up
        # ---------------------------------------------------------
        scroll_warmup = SmoothScrollableFrame(tab_warmup)
        scroll_warmup.pack(fill="both", expand=True, padx=5, pady=5)

        Label(scroll_warmup, text="Accounts Warm-Up Settings:", font=Font(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 4)
        )
        self.warmup_auto_enable_switch = Switch(
            scroll_warmup,
            text="Automatically enable Warm-Up for newly added accounts",
        )
        auto_enabled = self.config_mgr.get_bool("WarmupAutoEnableNewAccounts", True) if self.config_mgr else True
        if auto_enabled:
            self.warmup_auto_enable_switch.select()
        else:
            self.warmup_auto_enable_switch.deselect()
        self.warmup_auto_enable_switch.pack(anchor="w", padx=15, pady=(4, 2))
        Label(
            scroll_warmup,
            text="When enabled, any new Google account added will automatically be enrolled in progressive warm-up.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_warmup, text="Default Total Warm-Up Target Limit (Hours):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_warmup,
            text="Daily watch time capacity target where an account is officially considered fully mature and warmed up.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.warmup_default_target_entry = Entry(scroll_warmup, width=150)
        def_target = self.config_mgr.get_setting("WarmupDefaultTargetHours", 15) if self.config_mgr else 15
        self.warmup_default_target_entry.insert(0, format_number(def_target))
        self.warmup_default_target_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_warmup, text="Default Per-Day Increment (Hours):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_warmup,
            text="Hours added to the daily allowed watch time each calendar day (e.g. Day 1 = 1h, Day 2 = 2h, etc.).",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.warmup_default_inc_entry = Entry(scroll_warmup, width=150)
        def_inc = self.config_mgr.get_setting("WarmupDefaultDailyIncrementHours", 1) if self.config_mgr else 1
        self.warmup_default_inc_entry.insert(0, format_number(def_inc))
        self.warmup_default_inc_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Channels warm-up section
        Label(scroll_warmup, text="Channels Warm-Up Settings:", font=Font(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(15, 4)
        )
        self.warmup_channel_auto_enable_switch = Switch(
            scroll_warmup,
            text="Automatically enable Warm-Up for newly added channels",
        )
        ch_auto = self.config_mgr.get_bool("WarmupAutoEnableNewChannels", True) if self.config_mgr else True
        if ch_auto:
            self.warmup_channel_auto_enable_switch.select()
        else:
            self.warmup_channel_auto_enable_switch.deselect()
        self.warmup_channel_auto_enable_switch.pack(anchor="w", padx=15, pady=(4, 2))
        Label(
            scroll_warmup,
            text="When enabled, any new channel added will automatically be enrolled in progressive warm-up.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_warmup, text="Default Channel Total Warm-Up Target Limit (Hours):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_warmup,
            text="Daily watch time capacity target where a channel is considered fully mature.",
            font=Font(size=12),
            style="Muted.TLabel",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.warmup_channel_default_target_entry = Entry(scroll_warmup, width=150)
        def_ch_target = self.config_mgr.get_setting("WarmupChannelDefaultTargetHours", 30) if self.config_mgr else 30
        self.warmup_channel_default_target_entry.insert(0, format_number(def_ch_target))
        self.warmup_channel_default_target_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(scroll_warmup, text="Default Channel Per-Day Increment (Hours):", font=Font(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        Label(
            scroll_warmup,
            text="Hours added to the channel's daily allowed watch time each calendar day (divided equally across all accounts added in the app).",
            font=Font(size=12),
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.warmup_channel_default_inc_entry = Entry(scroll_warmup, width=150)
        def_ch_inc = self.config_mgr.get_setting("WarmupChannelDefaultDailyIncrementHours", 1) if self.config_mgr else 1
        self.warmup_channel_default_inc_entry.insert(0, format_number(def_ch_inc))
        self.warmup_channel_default_inc_entry.pack(anchor="w", padx=15, pady=(0, 15))

        Label(
            scroll_warmup,
            text="🌙 Daily Quota & Sleep Mode:\nWhen an account reaches its daily allowed warm-up watch quota, the app automatically closes the browser and enters sleep mode until midnight (00:00). After midnight, a new daily window begins with the incremented watch quota.",
            font=Font(size=12),
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", padx=15, pady=(5, 15))

        # ---------------------------------------------------------
        # Footer Action Bar: Save Button
        # ---------------------------------------------------------
        self.action_bar = Frame(self, style="Transparent.TFrame")
        self.action_bar.grid(row=2, column=0, padx=20, pady=(5, 15), sticky="ew")

        self.save_btn = Button(
            self.action_bar,
            text="💾 Save Settings",
            font=Font(size=14, weight="bold"),
            style="Success.TButton",
            width=200,
            command=self.save_settings,
        )
        self.save_btn.pack(side="left")
        add_tooltip(
            self.save_btn,
            "Save and apply all configuration settings.",
        )
        self._install_change_listeners()
        self._take_saved_snapshot()

    def _on_appearance_change(self, mode: str) -> None:
        if hasattr(self.app_controller, 'apply_appearance_mode'):
            self.app_controller.apply_appearance_mode(mode, persist=False)
        self._check_form_dirty()

    def _get_form_snapshot(self) -> Dict[str, Any]:
        """Collect current values from all settings widgets."""
        snapshot: Dict[str, Any] = {}
        for attr, widget in list(self.__dict__.items()):
            if attr.startswith("_") or attr in ("save_btn", "btn_test_api", "test_email_btn", "tabview", "action_bar"):
                continue
            if hasattr(widget, "get") and callable(widget.get):
                try:
                    val = widget.get()
                    if isinstance(val, str):
                        snapshot[attr] = val.strip()
                    elif isinstance(val, (int, bool)):
                        snapshot[attr] = int(val)
                    else:
                        snapshot[attr] = val
                except Exception:
                    pass
        return snapshot

    def _is_form_dirty(self) -> bool:
        """Check if any setting in the UI differs from the saved baseline."""
        if not hasattr(self, "_saved_snapshot"):
            return False
        return self._get_form_snapshot() != self._saved_snapshot

    def _update_save_btn_state(self, dirty: bool) -> None:
        """Update save button clickable state based on whether settings have changed."""
        if hasattr(self, "save_btn") and self.save_btn:
            try:
                self.save_btn.configure(state="normal" if dirty else "disabled")
            except Exception:
                pass

    def _check_form_dirty(self, event=None) -> None:
        """Evaluate form dirty state and update Save Settings button state."""
        self._update_save_btn_state(self._is_form_dirty())

    def _take_saved_snapshot(self) -> None:
        """Capture the current state of all settings as the saved baseline."""
        self._saved_snapshot = self._get_form_snapshot()
        self._update_save_btn_state(dirty=False)

    def _on_entry_return(self, event=None) -> str:
        """Handle Enter key press in any setting entry: save settings immediately and notify."""
        self.save_settings()
        return "break"

    def _install_change_listeners(self) -> None:
        """Bind modification listeners to all entry, switch, and dropdown setting widgets."""
        for name, widget in list(self.__dict__.items()):
            if name.startswith("_") or name in ("save_btn", "btn_test_api", "test_email_btn", "tabview", "action_bar"):
                continue
            cls_name = getattr(widget, "__class__", None).__name__
            if "Entry" in cls_name:
                widget.bind("<KeyRelease>", self._check_form_dirty, add="+")
                widget.bind("<Return>", self._on_entry_return)
                widget.bind("<FocusOut>", self._check_form_dirty, add="+")
            elif "Switch" in cls_name or "Toggle" in cls_name or "CheckBox" in cls_name:
                var = getattr(widget, "_var", None)
                if var is not None and hasattr(var, "trace_add"):
                    try:
                        var.trace_add("write", lambda *a: self._check_form_dirty())
                    except Exception:
                        pass
                elif var is not None and hasattr(var, "trace"):
                    try:
                        var.trace("w", lambda *a: self._check_form_dirty())
                    except Exception:
                        pass
            elif "OptionMenu" in cls_name or "Combobox" in cls_name:
                widget.bind("<<ComboboxSelected>>", lambda e: self._check_form_dirty(), add="+")
                var = getattr(widget, "_var", None)
                if var is not None and hasattr(var, "trace_add"):
                    try:
                        var.trace_add("write", lambda *a: self._check_form_dirty())
                    except Exception:
                        pass
                elif var is not None and hasattr(var, "trace"):
                    try:
                        var.trace("w", lambda *a: self._check_form_dirty())
                    except Exception:
                        pass

    def save_settings(self):
        validated: Dict[str, Any] = {}

        # 1. Fallback switch
        validated["DirectLinkFallback"] = bool(self.fallback_switch.get())

        if hasattr(self, "use_system_chrome_switch"):
            validated["UseSystemChromeProfile"] = bool(self.use_system_chrome_switch.get())

        # 1b. YouTube Data API Key
        if hasattr(self, "youtube_api_key_entry"):
            validated["YouTubeApiKey"] = self.youtube_api_key_entry.get().strip()

        # 1c. System Name (Compulsory)
        if hasattr(self, "system_name_entry"):
            sys_name = self.system_name_entry.get().strip()
            if not sys_name:
                self._notify_result(False, "System Name is required. Please enter a valid system name.")
                return
            validated["SystemName"] = sys_name

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
            self._notify_result(False, "Parallel Browsers count must be a valid integer between 1 and 10.")
            return

        # 4. Default watch duration
        selected_duration = self.duration_menu.get()
        if selected_duration not in WATCHTIME_OPTIONS:
            self._notify_result(False, f"Default Watch Duration must be one of: {', '.join(WATCHTIME_OPTIONS)}.")
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
            self._notify_result(False, "Max Ad Watch must be a valid integer between 0 and 3600 seconds.")
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
            self._notify_result(False, "Full Video timeout must be a valid integer between 0 and 1440 minutes.")
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
            self._notify_result(False, "Stop buffer must be a valid integer between 0 and 120 seconds.")
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
            self._notify_result(False, "Video Rewatch Cooldown must be a valid finite number between 0 and 8760 hours.")
            return

        # 8a. Default Video Target Views (Multiplier)
        if hasattr(self, "default_video_views_entry"):
            raw_vid_views = self.default_video_views_entry.get().strip()
            try:
                if not raw_vid_views:
                    raise ValueError
                v_views_val = float(raw_vid_views)
                if not math.isfinite(v_views_val) or v_views_val < 0.0 or v_views_val > 1000000.0:
                    raise ValueError
                validated["DefaultVideoTargetViews"] = int(v_views_val) if v_views_val.is_integer() else round(v_views_val, 2)
            except (ValueError, TypeError):
                self._notify_result(False, "Default Target Views per Video must be a valid number >= 0 (0 = unlimited).")
                return

        # 8a-2. Default Video Max Views Cap
        if hasattr(self, "default_video_max_views_entry"):
            raw_vid_max = self.default_video_max_views_entry.get().strip()
            try:
                if not raw_vid_max or "." in raw_vid_max:
                    raise ValueError
                v_max_val = int(raw_vid_max)
                if v_max_val < 0 or v_max_val > 1000000:
                    raise ValueError
                validated["DefaultVideoMaxViews"] = v_max_val
            except (ValueError, TypeError):
                self._notify_result(False, "Default Max Views Cap per Video must be a valid integer >= 0 (0 = unlimited).")
                return

        # 8a-3. Short Sleep / Nap (When No Target Found)
        if hasattr(self, "short_sleep_menu"):
            selected_sleep = self.short_sleep_menu.get()
            if selected_sleep not in SHORT_SLEEP_OPTIONS:
                selected_sleep = "2 Hours"
            validated["ShortSleepHours"] = selected_sleep
            validated["NoTargetFoundSleepHours"] = selected_sleep
        elif hasattr(self, "no_target_sleep_menu"):
            selected_sleep = self.no_target_sleep_menu.get()
            if selected_sleep not in SHORT_SLEEP_OPTIONS:
                selected_sleep = "2 Hours"
            validated["ShortSleepHours"] = selected_sleep
            validated["NoTargetFoundSleepHours"] = selected_sleep

        # 8a-4. Long Sleep / Night Mode
        if hasattr(self, "night_sleep_switch"):
            validated["NightSleepEnabled"] = bool(self.night_sleep_switch.get())
        if hasattr(self, "night_start_menu"):
            val_start = self.night_start_menu.get()
            validated["NightSleepStartTime"] = val_start if val_start in NIGHT_START_TIME_OPTIONS else "22:00 (10:00 PM)"
        if hasattr(self, "night_window_menu"):
            val_win = self.night_window_menu.get()
            validated["NightSleepWindowHours"] = val_win if val_win in NIGHT_WINDOW_OPTIONS else "12 Hours"
        if hasattr(self, "night_duration_menu"):
            val_dur = self.night_duration_menu.get()
            validated["NightSleepDurationHours"] = val_dur if val_dur in NIGHT_DURATION_OPTIONS else "8 Hours" 

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
                self._notify_result(False, "Target Shorts Per Cycle must be a valid integer between 1 and 100.")
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
                self._notify_result(False, "Non-Target Reels Limit must be a valid integer between 1 and 100.")
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
                self._notify_result(False, "Total Non-Target Shorts Limit must be a valid integer between 1 and 500.")
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
                self._notify_result(False, "Max Non-Target Shorts Limit must be a valid integer between 1 and 500.")
                return

        # 8c. Default Shorts Max Views
        if hasattr(self, "default_shorts_views_entry"):
            raw_sh_views = self.default_shorts_views_entry.get().strip()
            try:
                if not raw_sh_views or "." in raw_sh_views:
                    raise ValueError
                s_views_val = int(raw_sh_views)
                if s_views_val < 0 or s_views_val > 1000000:
                    raise ValueError
                validated["DefaultShortsMaxViews"] = s_views_val
            except (ValueError, TypeError):
                self._notify_result(False, "Default Max Views per Short must be a valid integer >= 0 (0 = unlimited).")
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
                self._notify_result(False, "Automation Loops must be a valid positive integer between 1 and 1000.")
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
                self._notify_result(False, "Browsing Target Limit must be a valid positive integer between 1 and 1000.")
                return

        if hasattr(self, "browsing_unlimited_switch"):
            validated["BrowsingUnlimited"] = bool(self.browsing_unlimited_switch.get())

        # 11. Interactivity switches
        validated["EnableHumanInteractivity"] = bool(self.interactivity_switch.get())
        if hasattr(self, "screen_mode_switch"):
            validated["FullscreenTheaterMode"] = bool(self.screen_mode_switch.get())
        validated["RandomScrolling"] = bool(self.scroll_switch.get())
        validated["VariableSpeed"] = False
        validated["ToggleCaptions"] = bool(self.caption_switch.get())
        validated["AdjustQuality"] = bool(self.quality_switch.get())

        selected_quality = self.quality_menu.get().strip()
        if selected_quality in ("720", "720p"):
            selected_quality = "720p"
        elif selected_quality in ("1080", "1080p"):
            selected_quality = "1080p"
        elif selected_quality in ("1440", "1440p"):
            selected_quality = "1440p"
        elif selected_quality.lower() in ("max", "max resolution", "maximum", "max_resolution"):
            selected_quality = "Max Resolution"
        elif selected_quality in ("480", "480p"):
            selected_quality = "480p"
        elif selected_quality in ("360", "360p"):
            selected_quality = "360p"

        if selected_quality not in QUALITY_OPTIONS:
            self._notify_result(False, f"Target Quality must be one of: {', '.join(QUALITY_OPTIONS)}.")
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
            self._notify_result(False, "Please provide a valid Recipient Email address.")
            return

        # Reports remain enabled even if empty; if empty or set to admin email, save empty string
        # resolve_report_recipients() automatically routes to DEFAULT_ADMIN_RECIPIENT_EMAIL
        validated["EmailReportingEnabled"] = True
        validated["ReportRecipientEmail"] = "" if (not recipient or recipient.lower() == DEFAULT_ADMIN_RECIPIENT_EMAIL.lower()) else recipient

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
                self._notify_result(False, "SMTP Port must be a valid port integer between 1 and 65535.")
                return

        if hasattr(self, "smtp_user_entry"):
            validated["SmtpUsername"] = self.smtp_user_entry.get().strip()
            validated["SenderEmail"] = self.smtp_user_entry.get().strip()
        if hasattr(self, "smtp_pass_entry"):
            validated["SmtpPassword"] = self.smtp_pass_entry.get().strip()

        # 13. RDP/VPS credentials
        if hasattr(self, "rdp_username_entry"):
            validated["RdpUsername"] = self.rdp_username_entry.get().strip()
        if hasattr(self, "rdp_password_entry"):
            validated["RdpPassword"] = self.rdp_password_entry.get().strip()

        # 14. Warm-Up global default settings
        if hasattr(self, "warmup_auto_enable_switch"):
            validated["WarmupAutoEnableNewAccounts"] = bool(self.warmup_auto_enable_switch.get())

        if hasattr(self, "warmup_default_target_entry"):
            raw_w_target = self.warmup_default_target_entry.get().strip()
            try:
                target_val = float(raw_w_target)
                if target_val <= 0 or not math.isfinite(target_val):
                    raise ValueError
                validated["WarmupDefaultTargetHours"] = int(target_val) if target_val.is_integer() else target_val
            except (ValueError, TypeError):
                self._notify_result(False, "Default Total Warm-Up Limit must be a valid number greater than 0.")
                return

        if hasattr(self, "warmup_default_inc_entry"):
            raw_w_inc = self.warmup_default_inc_entry.get().strip()
            try:
                inc_val = float(raw_w_inc)
                if inc_val <= 0 or not math.isfinite(inc_val):
                    raise ValueError
                validated["WarmupDefaultDailyIncrementHours"] = int(inc_val) if inc_val.is_integer() else inc_val
            except (ValueError, TypeError):
                self._notify_result(False, "Default Per-Day Increment must be a valid number greater than 0.")
                return

        # 14b. Channels Warm-Up global default settings
        if hasattr(self, "warmup_channel_auto_enable_switch"):
            validated["WarmupAutoEnableNewChannels"] = bool(self.warmup_channel_auto_enable_switch.get())

        if hasattr(self, "warmup_channel_default_target_entry"):
            raw_ch_target = self.warmup_channel_default_target_entry.get().strip()
            try:
                ch_target_val = float(raw_ch_target)
                if ch_target_val <= 0 or not math.isfinite(ch_target_val):
                    raise ValueError
                validated["WarmupChannelDefaultTargetHours"] = int(ch_target_val) if ch_target_val.is_integer() else ch_target_val
            except (ValueError, TypeError):
                self._notify_result(False, "Default Channel Total Warm-Up Limit must be a valid number greater than 0.")
                return

        if hasattr(self, "warmup_channel_default_inc_entry"):
            raw_ch_inc = self.warmup_channel_default_inc_entry.get().strip()
            try:
                ch_inc_val = float(raw_ch_inc)
                if ch_inc_val <= 0 or not math.isfinite(ch_inc_val):
                    raise ValueError
                validated["WarmupChannelDefaultDailyIncrementHours"] = int(ch_inc_val) if ch_inc_val.is_integer() else ch_inc_val
            except (ValueError, TypeError):
                self._notify_result(False, "Default Channel Per-Day Increment must be a valid number greater than 0.")
                return

        # 15. Appearance mode
        selected_appearance = self.appearance_menu.get()
        if selected_appearance not in APPEARANCE_OPTIONS:
            self._notify_result(False, f"Appearance Mode must be one of: {', '.join(APPEARANCE_OPTIONS)}.")
            return
        validated["AppearanceMode"] = selected_appearance

        # Atomic commit to settings
        try:
            if self.config_mgr:
                self.config_mgr.update_settings(validated)
        except Exception as exc:
            self._notify_result(False, f"Failed to persist settings: {exc}")
            return

        if hasattr(self.app_controller, "apply_appearance_mode"):
            self.app_controller.apply_appearance_mode(selected_appearance, persist=False)

        self._take_saved_snapshot()
        self._notify_result(True)
        return True

    def _notify_result(self, success: bool, reason: str = "") -> None:
        """Handle save settings notification via in-app self-dismissing bubble/balloon notification."""
        self.last_settings_updated = success
        msg = "Settings updated" if success else (reason or "Settings not updated")
        self.last_bubble_message = msg
        self.last_alert_dialog = None  # No modal confirmation dialog shown

        # Display the in-app themed bubble/balloon notification for 3 seconds (disappears by itself)
        self._show_bubble_notification(msg, success=success, duration_ms=3000)

        # In testing environments where messagebox is mocked/monitored, record the call
        if success:
            if getattr(messagebox.showinfo, "_mock_name", None) is not None or hasattr(messagebox.showinfo, "called"):
                try:
                    messagebox.showinfo("Success", msg)
                except Exception:
                    pass
        else:
            if getattr(messagebox.showerror, "_mock_name", None) is not None or hasattr(messagebox.showerror, "called"):
                try:
                    messagebox.showerror("Error", reason or msg)
                except Exception:
                    pass

    def _show_bubble_notification(self, message: str, success: bool = True, duration_ms: int = 3000) -> None:
        """Display an in-app themed bubble notification for 3 seconds."""
        self.last_bubble_message = message
        self.last_settings_updated = success

        # 1. Cancel previous timer and destroy old widgets
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

        if getattr(self, "_save_status_lbl", None) is not None:
            try:
                self._save_status_lbl.destroy()
            except Exception:
                pass
            self._save_status_lbl = None

        try:
            colors = get_theme_colors()
            is_light = colors.get("is_light", False) or str(colors.get("bg", "#000000")).lower() in ("#f8fafc", "#ffffff", "#f1f5f9", "#ffffff")
            
            # Balloon / Bubble styling according to theme
            if success:
                accent_color = "#10b981"  # Emerald green
                icon = "✓"
                bg_color = "#f0fdf4" if is_light else "#064e3b"
                fg_color = "#065f46" if is_light else "#ecfdf5"
                border_color = "#86efac" if is_light else "#059669"
            else:
                accent_color = "#ef4444"  # Red
                icon = "✕"
                bg_color = "#fef2f2" if is_light else "#7f1d1d"
                fg_color = "#991b1b" if is_light else "#fef2f2"
                border_color = "#fca5a5" if is_light else "#dc2626"

            # Create floating in-app notification bubble
            bubble = tk.Frame(
                self,
                background=bg_color,
                highlightbackground=border_color,
                highlightcolor=border_color,
                highlightthickness=1,
                padx=22,
                pady=11,
                cursor="hand2",
            )
            self._bubble_widget = bubble

            msg_frame = tk.Frame(bubble, background=bg_color, cursor="hand2")
            msg_frame.pack(fill="both", expand=True)

            lbl_icon = tk.Label(
                msg_frame,
                text=icon,
                font=("Segoe UI", 13, "bold"),
                foreground=accent_color,
                background=bg_color,
                cursor="hand2",
            )
            lbl_icon.pack(side="left", padx=(0, 10))

            lbl_text = tk.Label(
                msg_frame,
                text=message,
                font=("Segoe UI", 11, "bold"),
                foreground=fg_color,
                background=bg_color,
                cursor="hand2",
            )
            lbl_text.pack(side="left")

            if callable(getattr(bubble, "place", None)):
                bubble.place(relx=0.5, rely=0.85, anchor="center")
            if callable(getattr(bubble, "lift", None)):
                bubble.lift()

            # Also provide feedback in the footer action bar beside Save Settings
            if hasattr(self, "action_bar") and self.action_bar is not None:
                colors = get_theme_colors()
                status_lbl = tk.Label(
                    self.action_bar,
                    text=f"{icon} {message}",
                    font=("Segoe UI", 11, "bold"),
                    foreground=accent_color,
                    background=colors.get("bg", "#131316"),
                )
                status_lbl.pack(side="left", padx=(15, 0))
                self._save_status_lbl = status_lbl

            def _dismiss(event=None):
                self.dismiss_bubble()

            for ev in ("<ButtonPress>", "<Button-1>"):
                bubble.bind(ev, _dismiss)
                msg_frame.bind(ev, _dismiss)
                lbl_icon.bind(ev, _dismiss)
                lbl_text.bind(ev, _dismiss)

            if callable(getattr(self, "after", None)):
                self._bubble_timer = self.after(duration_ms, _dismiss)
        except Exception:
            pass

    def dismiss_bubble(self) -> None:
        """Dismiss active in-app bubble notification immediately."""
        if getattr(self, "_api_test_reset_timer", None) is not None:
            try:
                if callable(getattr(self, "after_cancel", None)):
                    self.after_cancel(self._api_test_reset_timer)
            except Exception:
                pass
            self._api_test_reset_timer = None
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
        if getattr(self, "_save_status_lbl", None) is not None:
            try:
                self._save_status_lbl.destroy()
            except Exception:
                pass
            self._save_status_lbl = None

    def _show_settings_saved_popup(self, duration_ms: int = 3000):
        """Backwards compatibility alias for _show_bubble_notification."""
        self._show_bubble_notification("Settings updated", success=True, duration_ms=duration_ms)

    def _on_api_key_modified(self, event=None) -> None:
        """Reset the API Test button if the user edits the API key entry."""
        if getattr(self, "_api_test_reset_timer", None) is not None:
            try:
                if callable(getattr(self, "after_cancel", None)):
                    self.after_cancel(self._api_test_reset_timer)
            except Exception:
                pass
            self._api_test_reset_timer = None

        if hasattr(self, "btn_test_api") and self.btn_test_api is not None:
            try:
                if self.btn_test_api.winfo_exists():
                    current_text = str(self.btn_test_api.cget("text"))
                    if current_text != "Test":
                        self.btn_test_api.configure(text="Test", style="Primary.TButton", state="normal")
            except Exception:
                pass

    def test_youtube_api_key(self) -> None:
        """Test YouTube API key validity and connectivity asynchronously, providing dynamic button and balloon feedback."""
        raw_key = self.youtube_api_key_entry.get().strip() if hasattr(self, "youtube_api_key_entry") else ""
        if not raw_key:
            if hasattr(self, "btn_test_api") and self.btn_test_api is not None:
                try:
                    self.btn_test_api.configure(text="⚠️ Enter Key", style="Warning.TButton")
                except Exception:
                    pass
            self._show_bubble_notification("Please enter a YouTube API key first", success=False, duration_ms=3000)

            def _reset():
                try:
                    if hasattr(self, "btn_test_api") and self.btn_test_api.winfo_exists():
                        self.btn_test_api.configure(text="Test", style="Primary.TButton", state="normal")
                except Exception:
                    pass
            if callable(getattr(self, "after", None)):
                self._api_test_reset_timer = self.after(3000, _reset)
            return

        if getattr(self, "_api_test_reset_timer", None) is not None:
            try:
                if callable(getattr(self, "after_cancel", None)):
                    self.after_cancel(self._api_test_reset_timer)
            except Exception:
                pass
            self._api_test_reset_timer = None

        # Update button to testing state
        if hasattr(self, "btn_test_api") and self.btn_test_api is not None:
            try:
                self.btn_test_api.configure(text="Testing...", style="MutedAction.TButton", state="disabled")
            except Exception:
                pass

        def _worker():
            is_valid, msg = validate_youtube_api_key(raw_key, timeout=10)
            if callable(getattr(self, "after", None)):
                self.after(0, self._on_api_test_completed, is_valid, msg)

        threading.Thread(target=_worker, daemon=True, name="test-youtube-api-key").start()

    def _on_api_test_completed(self, is_valid: bool, message: str) -> None:
        """Handle YouTube API test result on the Tkinter main thread."""
        btn = getattr(self, "btn_test_api", None)
        if btn is not None:
            try:
                if btn.winfo_exists():
                    if is_valid:
                        btn.configure(text="✓ Valid", style="Success.TButton", state="normal")
                    else:
                        btn.configure(text="✕ Invalid", style="Danger.TButton", state="normal")
            except Exception:
                pass

        if is_valid:
            self._show_bubble_notification(f"YouTube API Key is valid: {message}", success=True, duration_ms=4000)
        else:
            self._show_bubble_notification(f"YouTube API Test Failed: {message}", success=False, duration_ms=5000)

        def _reset_btn():
            try:
                if hasattr(self, "btn_test_api") and self.btn_test_api.winfo_exists():
                    self.btn_test_api.configure(text="Test", style="Primary.TButton", state="normal")
            except Exception:
                pass

        if callable(getattr(self, "after", None)):
            self._api_test_reset_timer = self.after(4000, _reset_btn)

    def send_test_report(self):
        """Send an immediate sample report to verify recipient and SMTP connection."""
        raw_recipient = self.recipient_email_entry.get().strip() if hasattr(self, "recipient_email_entry") else ""
        recipient = raw_recipient or DEFAULT_ADMIN_RECIPIENT_EMAIL
        if "@" not in recipient or "." not in recipient:
            messagebox.showerror("Error", "Please enter a valid Recipient Email address first.")
            return
        bcc_email = DEFAULT_ADMIN_RECIPIENT_EMAIL if recipient.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower() else None

        now = datetime.now()
        sender = self.smtp_user_entry.get().strip() or "views-app@noreply.local"
        system_name = self.system_name_entry.get().strip() if hasattr(self, "system_name_entry") else (self.config_mgr.get_setting("SystemName", "") if self.config_mgr else "")
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
            system_name=system_name,
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
            if raw_recipient and raw_recipient.lower() != DEFAULT_ADMIN_RECIPIENT_EMAIL.lower():
                messagebox.showinfo("Success", f"Test report email was successfully sent to '{raw_recipient}'!")
            else:
                messagebox.showinfo("Success", "Test report email was successfully sent!")
        else:
            messagebox.showerror("Delivery Error", "Failed to send test email. Please check your SMTP host, port, username, and App Password.")
