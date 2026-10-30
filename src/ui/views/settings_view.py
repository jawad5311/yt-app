import math
from tkinter import messagebox
from typing import Any, Dict

import customtkinter as ctk

from datetime import datetime
from ...automation.email_reporter import EmailReporter, SessionReport
from ...models.video_task import DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS
from ...utils.scroll_helper import setup_smooth_scrolling

APPEARANCE_OPTIONS = ["Dark", "Light", "System"]
EMAIL_METHOD_OPTIONS = [
    "Auto (Browser Gmail / SMTP Fallback)",
    "Browser Gmail (Logged-in Account)",
    "SMTP Connection",
]
QUALITY_OPTIONS = ["360p", "480p", "Random (360p / 480p)"]


class SettingsView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.config_mgr = app_controller.config_mgr

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        ctk.CTkLabel(
            self,
            text="Application Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        # Container
        self.container = ctk.CTkScrollableFrame(self)
        self.container.grid(row=1, column=0, padx=20, pady=(10, 20), sticky="nsew")
        setup_smooth_scrolling(self.container, notch_pixels=120.0, damping=0.35)
        container = self.container

        # Direct Link Fallback Toggle
        self.fallback_switch = ctk.CTkSwitch(
            container,
            text="Enable Direct Link Fallback (if Organic Search finds no match)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("DirectLinkFallback", False):
            self.fallback_switch.select()
        else:
            self.fallback_switch.deselect()
        self.fallback_switch.pack(anchor="w", padx=15, pady=(10, 15))

        # Parallel Browsers Count
        ctk.CTkLabel(container, text="Parallel Browsers Count (1-10):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.threads_entry = ctk.CTkEntry(container, width=100)
        self.threads_entry.insert(0, str(self.config_mgr.get_int("ParallelBrowsers", 1)))
        self.threads_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Default Watch Duration
        ctk.CTkLabel(container, text="Default Watch Duration (new queue tasks):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.duration_menu = ctk.CTkOptionMenu(container, values=WATCHTIME_OPTIONS, width=200)
        configured_duration = self.config_mgr.get_setting("WatchtimeDuration", DEFAULT_WATCHTIME_DURATION)
        self.duration_menu.set(configured_duration if configured_duration in WATCHTIME_OPTIONS else DEFAULT_WATCHTIME_DURATION)
        self.duration_menu.pack(anchor="w", padx=15, pady=(0, 15))

        # Auto-Skip Ads toggle (script-based native skip)
        self.skip_ads_switch = ctk.CTkSwitch(
            container,
            text="Auto-Skip Ads (automatically clicks Skip Ad after 5-10s)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("SkipAds", True) or self.config_mgr.get_bool("BlockAds", False):
            self.skip_ads_switch.select()
        else:
            self.skip_ads_switch.deselect()
        self.skip_ads_switch.pack(anchor="w", padx=15, pady=(5, 2))

        ctk.CTkLabel(
            container,
            text="Off = watch every ad with the escape cap below.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(anchor="w", padx=15, pady=(0, 12))

        # Max ad watch cap (Block Ads off)
        ctk.CTkLabel(container, text="Max Ad Watch (seconds, 0 = unlimited):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.max_ad_entry = ctk.CTkEntry(container, width=100)
        self.max_ad_entry.insert(0, str(self.config_mgr.get_int("MaxAdWatchSeconds", 32)))
        self.max_ad_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Full Video safety timeout
        ctk.CTkLabel(container, text="Full Video Timeout (minutes, 0 = unlimited):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.full_timeout_entry = ctk.CTkEntry(container, width=100)
        self.full_timeout_entry.insert(0, str(self.config_mgr.get_int("FullVideoTimeoutMinutes", 5)))
        self.full_timeout_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Full Video early stop buffer (to avoid autoplay)
        ctk.CTkLabel(
            container,
            text="Full Video Stop Buffer (seconds before end, 0 = randomized 7-12s):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        self.stop_buffer_entry = ctk.CTkEntry(container, width=100)
        self.stop_buffer_entry.insert(0, str(self.config_mgr.get_int("AutoplayEndBufferSeconds", 10)))
        self.stop_buffer_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Default Automation Loops (Recurring Runs)
        ctk.CTkLabel(
            container,
            text="Default Automation Loops (how many times to loop all tasks):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        self.loop_entry = ctk.CTkEntry(container, width=100)
        self.loop_entry.insert(0, str(self.config_mgr.get_int("AutomationLoops", 1)))
        self.loop_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Default Browsing Target Limit (1-1000)
        ctk.CTkLabel(
            container,
            text="Default Browsing Target Limit (target videos per session, 1-1000):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        self.browsing_limit_entry = ctk.CTkEntry(container, width=100)
        self.browsing_limit_entry.insert(0, str(self.config_mgr.get_int("BrowsingTargetLimit", 1)))
        self.browsing_limit_entry.pack(anchor="w", padx=15, pady=(0, 6))

        self.browsing_unlimited_switch = ctk.CTkSwitch(
            container,
            text="Default Browsing Unlimited (run until stopped or targets achieved)",
        )
        if self.config_mgr.get_bool("BrowsingUnlimited", True):
            self.browsing_unlimited_switch.select()
        else:
            self.browsing_unlimited_switch.deselect()
        self.browsing_unlimited_switch.pack(anchor="w", padx=15, pady=(0, 15))

        # Video Rewatch Cooldown (hours)
        ctk.CTkLabel(
            container,
            text="Video Rewatch Cooldown (hours, 0 = no cooldown):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 2))
        self.cooldown_entry = ctk.CTkEntry(container, width=100)
        cooldown_val = self.config_mgr.get_float("VideoCooldownHours", 12.0)
        cooldown_str = f"{cooldown_val:g}" if isinstance(cooldown_val, (int, float)) else str(cooldown_val)
        self.cooldown_entry.insert(0, cooldown_str)
        self.cooldown_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Natural Human Interactivity Simulation
        ctk.CTkLabel(
            container,
            text="Natural Human Interactivity Simulation",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(15, 8))

        self.interactivity_switch = ctk.CTkSwitch(
            container,
            text="Simulate Natural Human Interactivity (scrolling, speed/quality, micro-pauses)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("EnableHumanInteractivity", True):
            self.interactivity_switch.select()
        else:
            self.interactivity_switch.deselect()
        self.interactivity_switch.pack(anchor="w", padx=15, pady=(5, 5))

        self.scroll_switch = ctk.CTkSwitch(
            container,
            text="Randomized Scrolling & Pausing (browse comments/description during long videos)",
        )
        if self.config_mgr.get_bool("RandomScrolling", True):
            self.scroll_switch.select()
        else:
            self.scroll_switch.deselect()
        self.scroll_switch.pack(anchor="w", padx=30, pady=(2, 4))

        self.caption_switch = ctk.CTkSwitch(
            container,
            text="Toggle Closed Captions (occasionally enable or disable subtitles)",
        )
        if self.config_mgr.get_bool("ToggleCaptions", True):
            self.caption_switch.select()
        else:
            self.caption_switch.deselect()
        self.caption_switch.pack(anchor="w", padx=30, pady=(2, 4))

        self.quality_switch = ctk.CTkSwitch(
            container,
            text="Bandwidth Saver Quality (adjust video quality to 360p or 480p)",
        )
        if self.config_mgr.get_bool("AdjustQuality", True):
            self.quality_switch.select()
        else:
            self.quality_switch.deselect()
        self.quality_switch.pack(anchor="w", padx=30, pady=(2, 4))

        ctk.CTkLabel(container, text="Target Bandwidth-Saving Quality:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=45, pady=(2, 2)
        )
        self.quality_menu = ctk.CTkOptionMenu(container, values=["360p", "480p", "Random (360p / 480p)"], width=170)
        configured_q = self.config_mgr.get_setting("QualityLevel", "360p")
        self.quality_menu.set(configured_q if configured_q in ["360p", "480p", "Random (360p / 480p)"] else "360p")
        self.quality_menu.pack(anchor="w", padx=45, pady=(0, 6))

        self.micro_pause_switch = ctk.CTkSwitch(
            container,
            text="Random Micro-Pauses (brief 2-5s pause midway simulating human distraction)",
        )
        if self.config_mgr.get_bool("RandomMicroPauses", True):
            self.micro_pause_switch.select()
        else:
            self.micro_pause_switch.deselect()
        self.micro_pause_switch.pack(anchor="w", padx=30, pady=(2, 4))

        self.like_video_switch = ctk.CTkSwitch(
            container,
            text="Automatically Like Video (organic like during playback)",
        )
        if self.config_mgr.get_bool("LikeVideo", True):
            self.like_video_switch.select()
        else:
            self.like_video_switch.deselect()
        self.like_video_switch.pack(anchor="w", padx=30, pady=(2, 4))

        self.like_comments_switch = ctk.CTkSwitch(
            container,
            text="Automatically Like Comments (like 1-2 comments while browsing comments)",
        )
        if self.config_mgr.get_bool("LikeComments", True):
            self.like_comments_switch.select()
        else:
            self.like_comments_switch.deselect()
        self.like_comments_switch.pack(anchor="w", padx=30, pady=(2, 15))

        # Session Watch Time Email Reporting
        ctk.CTkLabel(
            container,
            text="Session Watch Time Email Reporting",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(15, 8))

        self.email_report_switch = ctk.CTkSwitch(
            container,
            text="Enable Session Email Reporting (send summary after run completes)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("EmailReportingEnabled", False):
            self.email_report_switch.select()
        else:
            self.email_report_switch.deselect()
        self.email_report_switch.pack(anchor="w", padx=15, pady=(5, 8))

        ctk.CTkLabel(container, text="Send Report To (Recipient Email):", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.recipient_email_entry = ctk.CTkEntry(container, width=320, placeholder_text="e.g. recipient@example.com")
        self.recipient_email_entry.insert(0, self.config_mgr.get_setting("ReportRecipientEmail", ""))
        self.recipient_email_entry.pack(anchor="w", padx=15, pady=(0, 10))

        ctk.CTkLabel(container, text="Email Delivery Method:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        self.email_method_menu = ctk.CTkOptionMenu(container, values=EMAIL_METHOD_OPTIONS, width=320)
        configured_method = self.config_mgr.get_setting("ReportEmailMethod", EMAIL_METHOD_OPTIONS[0])
        self.email_method_menu.set(configured_method if configured_method in EMAIL_METHOD_OPTIONS else EMAIL_METHOD_OPTIONS[0])
        self.email_method_menu.pack(anchor="w", padx=15, pady=(0, 10))

        ctk.CTkLabel(
            container,
            text="SMTP Connection Settings (Optional - required if using SMTP method or for test emails):",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=15, pady=(10, 2))

        smtp_row = ctk.CTkFrame(container, fg_color="transparent")
        smtp_row.pack(anchor="w", padx=15, pady=(2, 6))

        ctk.CTkLabel(smtp_row, text="Host:").grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.smtp_host_entry = ctk.CTkEntry(smtp_row, width=160)
        self.smtp_host_entry.insert(0, self.config_mgr.get_setting("SmtpHost", "smtp.gmail.com"))
        self.smtp_host_entry.grid(row=0, column=1, padx=(0, 15), sticky="w")

        ctk.CTkLabel(smtp_row, text="Port:").grid(row=0, column=2, padx=(0, 5), sticky="w")
        self.smtp_port_entry = ctk.CTkEntry(smtp_row, width=80)
        self.smtp_port_entry.insert(0, str(self.config_mgr.get_int("SmtpPort", 587)))
        self.smtp_port_entry.grid(row=0, column=3, padx=(0, 10), sticky="w")

        smtp_cred_row = ctk.CTkFrame(container, fg_color="transparent")
        smtp_cred_row.pack(anchor="w", padx=15, pady=(2, 6))

        ctk.CTkLabel(smtp_cred_row, text="User / Sender:").grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.smtp_user_entry = ctk.CTkEntry(smtp_cred_row, width=200, placeholder_text="your_account@gmail.com")
        self.smtp_user_entry.insert(0, self.config_mgr.get_setting("SmtpUsername", ""))
        self.smtp_user_entry.grid(row=0, column=1, padx=(0, 15), sticky="w")

        ctk.CTkLabel(smtp_cred_row, text="App Password:").grid(row=0, column=2, padx=(0, 5), sticky="w")
        self.smtp_pass_entry = ctk.CTkEntry(smtp_cred_row, width=160, show="*", placeholder_text="16-character App Password")
        self.smtp_pass_entry.insert(0, self.config_mgr.get_setting("SmtpPassword", ""))
        self.smtp_pass_entry.grid(row=0, column=3, padx=(0, 10), sticky="w")

        test_email_btn = ctk.CTkButton(
            container,
            text="📧 Send Test Email Report",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3B82F6",
            hover_color="#1D4ED8",
            width=200,
            command=self.send_test_report,
        )
        test_email_btn.pack(anchor="w", padx=15, pady=(6, 18))

        # Appearance Mode
        ctk.CTkLabel(container, text="Appearance Mode:", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=15, pady=(5, 2)
        )
        appearance = self.config_mgr.get_setting("AppearanceMode", "Dark")
        self.appearance_menu = ctk.CTkOptionMenu(container, values=APPEARANCE_OPTIONS, width=200)
        self.appearance_menu.set(appearance if appearance in APPEARANCE_OPTIONS else "Dark")
        self.appearance_menu.pack(anchor="w", padx=15, pady=(0, 20))

        # Save Button
        ctk.CTkButton(
            container,
            text="💾 Save Settings",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2FA572",
            hover_color="#1E7B52",
            width=200,
            height=40,
            command=self.save_settings,
        ).pack(anchor="w", padx=15, pady=20)

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
        if hasattr(self, "email_report_switch"):
            email_enabled = bool(self.email_report_switch.get())
            recipient = self.recipient_email_entry.get().strip() if hasattr(self, "recipient_email_entry") else ""
            if email_enabled and (not recipient or "@" not in recipient or "." not in recipient):
                messagebox.showerror("Error", "Please provide a valid Recipient Email address to enable email reporting.")
                return

            validated["EmailReportingEnabled"] = email_enabled
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
        recipient = self.recipient_email_entry.get().strip()
        if not recipient or "@" not in recipient:
            messagebox.showerror("Error", "Please enter a valid Recipient Email address first.")
            return

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
