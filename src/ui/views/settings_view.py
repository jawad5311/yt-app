import math
import sys
from datetime import datetime
from tkinter import messagebox
from typing import Any, Dict, Optional

import customtkinter as ctk

from ...automation.email_reporter import EmailReporter, SessionReport
from ...models.video_task import DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS

APPEARANCE_OPTIONS = ["Dark", "Light", "System"]
EMAIL_METHOD_OPTIONS = [
    "Headless Browser Gmail (Recommended)",
    "Active Browser Gmail",
    "SMTP Connection",
]
QUALITY_OPTIONS = ["360p", "480p", "Random (360p / 480p)"]


class SmoothScrollableFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame with accelerated velocity and 60fps exponential smooth easing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._target_scroll_y: Optional[float] = None
        self._scroll_anim_id: Optional[str] = None
        self._scroll_step: float = 85.0  # Pixels per notch (~4x faster than CTk default 20px)

        # Synchronize target position when scrollbar thumb is dragged directly
        if hasattr(self, "_scrollbar") and self._scrollbar is not None:
            orig_cmd = self._parent_canvas.yview

            def on_scrollbar(*c_args):
                if self._scroll_anim_id is not None:
                    try:
                        self.after_cancel(self._scroll_anim_id)
                    except Exception:
                        pass
                    self._scroll_anim_id = None
                self._target_scroll_y = None
                return orig_cmd(*c_args)

            self._scrollbar.configure(command=on_scrollbar)

    def destroy(self):
        if self._scroll_anim_id is not None:
            try:
                self.after_cancel(self._scroll_anim_id)
            except Exception:
                pass
            self._scroll_anim_id = None
        super().destroy()

    def _mouse_wheel_all(self, event):
        if not self.check_if_master_is_canvas(event.widget):
            return

        if self._shift_pressed:
            super()._mouse_wheel_all(event)
            return

        if self._parent_canvas.yview() == (0.0, 1.0):
            return

        if sys.platform.startswith("win"):
            delta = event.delta
        elif sys.platform == "darwin":
            delta = event.delta * 12
        else:
            delta = 120 if getattr(event, "num", None) == 4 else (-120 if getattr(event, "num", None) == 5 else getattr(event, "delta", 0) * 20)

        bbox = self._parent_canvas.bbox("all")
        if not bbox:
            return
        total_height = bbox[3] - bbox[1]
        canvas_height = self._parent_canvas.winfo_height()
        max_scroll_px = max(0, total_height - canvas_height)
        if max_scroll_px <= 0:
            return

        current_y_px = self._parent_canvas.yview()[0] * total_height
        if self._target_scroll_y is None:
            self._target_scroll_y = current_y_px

        step_px = - (delta / 120.0) * self._scroll_step
        self._target_scroll_y = max(0.0, min(float(max_scroll_px), self._target_scroll_y + step_px))

        if self._scroll_anim_id is None:
            self._animate_scroll()

    def _animate_scroll(self):
        if not self.winfo_exists():
            self._scroll_anim_id = None
            return
        try:
            bbox = self._parent_canvas.bbox("all")
            if not bbox or self._target_scroll_y is None:
                self._scroll_anim_id = None
                self._target_scroll_y = None
                return

            total_height = bbox[3] - bbox[1]
            canvas_height = self._parent_canvas.winfo_height()
            max_scroll_px = max(0, total_height - canvas_height)

            current_y_px = self._parent_canvas.yview()[0] * total_height
            diff = self._target_scroll_y - current_y_px

            if abs(diff) <= 1.0 or max_scroll_px <= 0:
                target_fraction = max(0.0, min(1.0, self._target_scroll_y / total_height)) if total_height > 0 else 0.0
                self._parent_canvas.yview_moveto(target_fraction)
                self._target_scroll_y = None
                self._scroll_anim_id = None
            else:
                next_y_px = current_y_px + diff * 0.28
                next_fraction = max(0.0, min(1.0, next_y_px / total_height)) if total_height > 0 else 0.0
                self._parent_canvas.yview_moveto(next_fraction)
                self._scroll_anim_id = self.after(16, self._animate_scroll)
        except Exception:
            self._scroll_anim_id = None
            self._target_scroll_y = None


class SettingsView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.config_mgr = app_controller.config_mgr

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        # Header
        ctk.CTkLabel(
            self,
            text="Application Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        # Sub-Tabs Container
        self.tabview = ctk.CTkTabview(self, anchor="w")
        self.tabview.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="nsew")

        tab_general = self.tabview.add("General")
        tab_playback = self.tabview.add("Playback & Queue")
        tab_ads = self.tabview.add("Ads")
        tab_interactivity = self.tabview.add("Interactivity")
        tab_email = self.tabview.add("Email & Reports")

        # Configure tab buttons to be larger, clear, and spacious
        self.tabview._segmented_button.configure(
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36,
        )
        self.tabview._segmented_button.update_idletasks()
        for btn in self.tabview._segmented_button._buttons_dict.values():
            btn.configure(width=max(80, btn.winfo_reqwidth() + 10))

        # ---------------------------------------------------------
        # Tab 1: General
        # ---------------------------------------------------------
        scroll_general = SmoothScrollableFrame(tab_general, fg_color="transparent")
        scroll_general.pack(fill="both", expand=True, padx=5, pady=5)

        # Appearance Mode
        ctk.CTkLabel(scroll_general, text="Appearance Mode:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        ctk.CTkLabel(
            scroll_general,
            text="Choose light, dark, or system theme for the application interface.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        appearance = self.config_mgr.get_setting("AppearanceMode", "Dark")
        self.appearance_menu = ctk.CTkOptionMenu(scroll_general, values=APPEARANCE_OPTIONS, width=200)
        self.appearance_menu.set(appearance if appearance in APPEARANCE_OPTIONS else "Dark")
        self.appearance_menu.pack(anchor="w", padx=15, pady=(0, 15))

        # Concurrency: Parallel Browsers Count
        ctk.CTkLabel(scroll_general, text="Parallel Browsers Count (1-10):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        ctk.CTkLabel(
            scroll_general,
            text="Number of simultaneous automated browser instances running in parallel.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.threads_entry = ctk.CTkEntry(scroll_general, width=100)
        self.threads_entry.insert(0, str(self.config_mgr.get_int("ParallelBrowsers", 1)))
        self.threads_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Search: Direct Link Fallback Toggle
        self.fallback_switch = ctk.CTkSwitch(
            scroll_general,
            text="Enable Direct Link Fallback (if Organic Search finds no match)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("DirectLinkFallback", False):
            self.fallback_switch.select()
        else:
            self.fallback_switch.deselect()
        self.fallback_switch.pack(anchor="w", padx=15, pady=(5, 2))
        ctk.CTkLabel(
            scroll_general,
            text="When enabled, if organic search yields no match, opens the video URL directly.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 2: Playback & Queue
        # ---------------------------------------------------------
        scroll_playback = SmoothScrollableFrame(tab_playback, fg_color="transparent")
        scroll_playback.pack(fill="both", expand=True, padx=5, pady=5)

        # Default Watch Duration
        ctk.CTkLabel(scroll_playback, text="Default Watch Duration (new queue tasks):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 2)
        )
        self.duration_menu = ctk.CTkOptionMenu(scroll_playback, values=WATCHTIME_OPTIONS, width=200)
        configured_duration = self.config_mgr.get_setting("WatchtimeDuration", DEFAULT_WATCHTIME_DURATION)
        self.duration_menu.set(configured_duration if configured_duration in WATCHTIME_OPTIONS else DEFAULT_WATCHTIME_DURATION)
        self.duration_menu.pack(anchor="w", padx=15, pady=(0, 15))

        # Default Automation Loops (Recurring Runs)
        ctk.CTkLabel(
            scroll_playback,
            text="Default Automation Loops (how many times to loop all tasks):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        ctk.CTkLabel(
            scroll_playback,
            text="Number of times to loop through all queued tasks during automation (1-1000).",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.loop_entry = ctk.CTkEntry(scroll_playback, width=100)
        self.loop_entry.insert(0, str(self.config_mgr.get_int("AutomationLoops", 1)))
        self.loop_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Default Browsing Target Limit (1-1000)
        ctk.CTkLabel(
            scroll_playback,
            text="Default Browsing Target Limit (target videos per session, 1-1000):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        ctk.CTkLabel(
            scroll_playback,
            text="Target number of videos to watch per browsing session.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.browsing_limit_entry = ctk.CTkEntry(scroll_playback, width=100)
        self.browsing_limit_entry.insert(0, str(self.config_mgr.get_int("BrowsingTargetLimit", 1)))
        self.browsing_limit_entry.pack(anchor="w", padx=15, pady=(0, 6))

        self.browsing_unlimited_switch = ctk.CTkSwitch(
            scroll_playback,
            text="Default Browsing Unlimited (run until stopped or targets achieved)",
        )
        if self.config_mgr.get_bool("BrowsingUnlimited", False):
            self.browsing_unlimited_switch.select()
        else:
            self.browsing_unlimited_switch.deselect()
        self.browsing_unlimited_switch.pack(anchor="w", padx=15, pady=(0, 15))

        # Full Video safety timeout
        ctk.CTkLabel(scroll_playback, text="Full Video Timeout (minutes, 0 = unlimited):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        ctk.CTkLabel(
            scroll_playback,
            text="Maximum watch duration before forcibly completing a full-video task.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.full_timeout_entry = ctk.CTkEntry(scroll_playback, width=100)
        self.full_timeout_entry.insert(0, str(self.config_mgr.get_int("FullVideoTimeoutMinutes", 5)))
        self.full_timeout_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Full Video early stop buffer (to avoid autoplay)
        ctk.CTkLabel(
            scroll_playback,
            text="Full Video Stop Buffer (seconds before end, 0 = randomized 7-12s):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        ctk.CTkLabel(
            scroll_playback,
            text="Stops playback slightly before video ends to avoid YouTube triggering autoplay.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.stop_buffer_entry = ctk.CTkEntry(scroll_playback, width=100)
        self.stop_buffer_entry.insert(0, str(self.config_mgr.get_int("AutoplayEndBufferSeconds", 10)))
        self.stop_buffer_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Video Rewatch Cooldown (hours)
        ctk.CTkLabel(
            scroll_playback,
            text="Video Rewatch Cooldown (hours, 0 = no cooldown):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        ctk.CTkLabel(
            scroll_playback,
            text="Minimum hours before a previously watched video can be scheduled/watched again.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.cooldown_entry = ctk.CTkEntry(scroll_playback, width=100)
        cooldown_val = self.config_mgr.get_float("VideoCooldownHours", 12.0)
        cooldown_str = f"{cooldown_val:g}" if isinstance(cooldown_val, (int, float)) else str(cooldown_val)
        self.cooldown_entry.insert(0, cooldown_str)
        self.cooldown_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 3: Ads
        # ---------------------------------------------------------
        scroll_ads = SmoothScrollableFrame(tab_ads, fg_color="transparent")
        scroll_ads.pack(fill="both", expand=True, padx=5, pady=5)

        # Auto-Skip Ads toggle (script-based native skip)
        self.skip_ads_switch = ctk.CTkSwitch(
            scroll_ads,
            text="Auto-Skip Ads (automatically clicks Skip Ad after 5-10s)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("SkipAds", True) or self.config_mgr.get_bool("BlockAds", False):
            self.skip_ads_switch.select()
        else:
            self.skip_ads_switch.deselect()
        self.skip_ads_switch.pack(anchor="w", padx=15, pady=(10, 2))

        ctk.CTkLabel(
            scroll_ads,
            text="Off = watch every ad with the escape cap below.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 12))

        # Max ad watch cap (Block Ads off)
        ctk.CTkLabel(scroll_ads, text="Max Ad Watch (seconds, 0 = unlimited):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        ctk.CTkLabel(
            scroll_ads,
            text="Maximum duration spent watching an ad before attempting to bypass or resume playback.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 6))
        self.max_ad_entry = ctk.CTkEntry(scroll_ads, width=100)
        self.max_ad_entry.insert(0, str(self.config_mgr.get_int("MaxAdWatchSeconds", 32)))
        self.max_ad_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # ---------------------------------------------------------
        # Tab 4: Interactivity
        # ---------------------------------------------------------
        scroll_interactivity = SmoothScrollableFrame(tab_interactivity, fg_color="transparent")
        scroll_interactivity.pack(fill="both", expand=True, padx=5, pady=5)

        # Natural Human Interactivity Simulation
        self.interactivity_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Simulate Natural Human Interactivity (scrolling, speed/quality, micro-pauses)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("EnableHumanInteractivity", True):
            self.interactivity_switch.select()
        else:
            self.interactivity_switch.deselect()
        self.interactivity_switch.pack(anchor="w", padx=15, pady=(10, 2))

        ctk.CTkLabel(
            scroll_interactivity,
            text="Simulates realistic human-like behaviors during video playback to mimic organic viewer activity.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 15))

        self.scroll_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Randomized Scrolling & Pausing (browse comments/description during long videos)",
        )
        if self.config_mgr.get_bool("RandomScrolling", True):
            self.scroll_switch.select()
        else:
            self.scroll_switch.deselect()
        self.scroll_switch.pack(anchor="w", padx=30, pady=(2, 8))

        self.caption_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Toggle Closed Captions (occasionally enable or disable subtitles)",
        )
        if self.config_mgr.get_bool("ToggleCaptions", True):
            self.caption_switch.select()
        else:
            self.caption_switch.deselect()
        self.caption_switch.pack(anchor="w", padx=30, pady=(2, 8))

        self.quality_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Bandwidth Saver Quality (adjust video quality to 360p or 480p)",
        )
        if self.config_mgr.get_bool("AdjustQuality", True):
            self.quality_switch.select()
        else:
            self.quality_switch.deselect()
        self.quality_switch.pack(anchor="w", padx=30, pady=(2, 4))

        ctk.CTkLabel(scroll_interactivity, text="Target Bandwidth-Saving Quality:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=45, pady=(2, 2)
        )
        self.quality_menu = ctk.CTkOptionMenu(scroll_interactivity, values=QUALITY_OPTIONS, width=170)
        configured_q = self.config_mgr.get_setting("QualityLevel", "360p")
        self.quality_menu.set(configured_q if configured_q in QUALITY_OPTIONS else "360p")
        self.quality_menu.pack(anchor="w", padx=45, pady=(0, 8))

        self.micro_pause_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Random Micro-Pauses (brief 2-5s pause midway simulating human distraction)",
        )
        if self.config_mgr.get_bool("RandomMicroPauses", True):
            self.micro_pause_switch.select()
        else:
            self.micro_pause_switch.deselect()
        self.micro_pause_switch.pack(anchor="w", padx=30, pady=(2, 4))

        self.like_video_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Automatically Like Video (organic like during playback)",
        )
        if self.config_mgr.get_bool("LikeVideo", True):
            self.like_video_switch.select()
        else:
            self.like_video_switch.deselect()
        self.like_video_switch.pack(anchor="w", padx=30, pady=(2, 4))

        self.like_comments_switch = ctk.CTkSwitch(
            scroll_interactivity,
            text="Automatically Like Comments (like 1-2 comments while browsing comments)",
        )
        if self.config_mgr.get_bool("LikeComments", True):
            self.like_comments_switch.select()
        else:
            self.like_comments_switch.deselect()
        self.like_comments_switch.pack(anchor="w", padx=30, pady=(2, 15))

        # ---------------------------------------------------------
        # Tab 5: Email & Reports
        # ---------------------------------------------------------
        scroll_email = SmoothScrollableFrame(tab_email, fg_color="transparent")
        scroll_email.pack(fill="both", expand=True, padx=5, pady=5)

        ctk.CTkLabel(
            scroll_email,
            text="Session Watch Time Email Reporting",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(15, 4))

        ctk.CTkLabel(
            scroll_email,
            text="Reports are automatically sent to the email below when automation starts and finishes.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 12))

        ctk.CTkLabel(scroll_email, text="Send Report To (Recipient Email):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.recipient_email_entry = ctk.CTkEntry(scroll_email, width=320, placeholder_text="jawad@ytgrowthexpert.com")
        self.recipient_email_entry.insert(0, self.config_mgr.get_setting("ReportRecipientEmail", "jawad@ytgrowthexpert.com"))
        self.recipient_email_entry.pack(anchor="w", padx=15, pady=(0, 15))

        ctk.CTkLabel(scroll_email, text="Email Delivery Method:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.email_method_menu = ctk.CTkOptionMenu(scroll_email, values=EMAIL_METHOD_OPTIONS, width=320)
        configured_method = self.config_mgr.get_setting("ReportEmailMethod", EMAIL_METHOD_OPTIONS[0])
        self.email_method_menu.set(configured_method if configured_method in EMAIL_METHOD_OPTIONS else EMAIL_METHOD_OPTIONS[0])
        self.email_method_menu.pack(anchor="w", padx=15, pady=(0, 15))

        # Direct SMTP Configuration (Secondary / Custom Relay)
        ctk.CTkLabel(
            scroll_email,
            text="Direct SMTP Relay Settings (used for SMTP Method or Direct Test Delivery):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(10, 4))

        ctk.CTkLabel(scroll_email, text="SMTP Host:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_host_entry = ctk.CTkEntry(scroll_email, width=280)
        self.smtp_host_entry.insert(0, self.config_mgr.get_setting("SmtpHost", "smtp.gmail.com"))
        self.smtp_host_entry.pack(anchor="w", padx=15, pady=(0, 10))

        ctk.CTkLabel(scroll_email, text="SMTP Port (e.g. 587 for TLS, 465 for SSL):").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_port_entry = ctk.CTkEntry(scroll_email, width=120)
        self.smtp_port_entry.insert(0, str(self.config_mgr.get_int("SmtpPort", 587)))
        self.smtp_port_entry.pack(anchor="w", padx=15, pady=(0, 10))

        ctk.CTkLabel(scroll_email, text="SMTP Username / Sender Email:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_user_entry = ctk.CTkEntry(scroll_email, width=280)
        self.smtp_user_entry.insert(0, self.config_mgr.get_setting("SmtpUsername", self.config_mgr.get_setting("SenderEmail", "")))
        self.smtp_user_entry.pack(anchor="w", padx=15, pady=(0, 10))

        ctk.CTkLabel(scroll_email, text="SMTP App Password:").pack(anchor="w", padx=15, pady=(2, 2))
        self.smtp_pass_entry = ctk.CTkEntry(scroll_email, width=280, show="*")
        self.smtp_pass_entry.insert(0, self.config_mgr.get_setting("SmtpPassword", ""))
        self.smtp_pass_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Send Test Report Button
        self.test_email_btn = ctk.CTkButton(
            scroll_email,
            text="📧 Send Test Email Report",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#3B8ED0",
            hover_color="#36719C",
            width=220,
            height=36,
            command=self.send_test_report,
        )
        self.test_email_btn.pack(anchor="w", padx=15, pady=(5, 20))

        # ---------------------------------------------------------
        # Footer Action Bar: Save Button
        # ---------------------------------------------------------
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.grid(row=2, column=0, padx=20, pady=(5, 15), sticky="ew")

        ctk.CTkButton(
            action_bar,
            text="💾 Save Settings",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            width=200,
            height=38,
            command=self.save_settings,
        ).pack(side="left")

    def save_settings(self):
        validated: Dict[str, Any] = {}

        # 1. Fallback switch
        validated["DirectLinkFallback"] = bool(self.fallback_switch.get())

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

        # 9. Automation loops (1 - 1000)
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

        # 10. Browsing target limit (1 - 1000) & Unlimited
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

        # 11. Quality level
        valid_quality = QUALITY_OPTIONS
        selected_quality = self.quality_menu.get()
        if selected_quality not in valid_quality:
            messagebox.showerror("Error", f"Target Quality must be one of: {', '.join(valid_quality)}.")
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
            self.config_mgr.update_settings(validated)
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to persist settings: {exc}")
            return

        # Apply appearance change only after persistence succeeds, avoiding a second hidden save
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
