import math
from tkinter import messagebox
from typing import Any, Dict

import customtkinter as ctk

from ...models.video_task import DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS
from ...utils.scroll_helper import setup_smooth_scrolling

APPEARANCE_OPTIONS = ["Dark", "Light", "System"]
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
        self.micro_pause_switch.pack(anchor="w", padx=30, pady=(2, 15))

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

        # 12. Appearance mode
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
