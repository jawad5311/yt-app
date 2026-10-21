from tkinter import messagebox

import customtkinter as ctk

from ...models.video_task import DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS

APPEARANCE_OPTIONS = ["Dark", "Light", "System"]


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
        container = ctk.CTkScrollableFrame(self)
        container.grid(row=1, column=0, padx=20, pady=(10, 20), sticky="nsew")

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

        # Block Ads toggle (formerly Skip Ads)
        self.skip_ads_switch = ctk.CTkSwitch(
            container,
            text="Block Ads entirely (block with uBlock Lite + skip fallback after 30-35s)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("BlockAds", self.config_mgr.get_bool("SkipAds", False)):
            self.skip_ads_switch.select()
        else:
            self.skip_ads_switch.deselect()
        self.skip_ads_switch.pack(anchor="w", padx=15, pady=(5, 2))

        ctk.CTkLabel(
            container,
            text="Off = watch every ad (monetisation) with the escape cap below.",
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
        self.config_mgr.set_setting("DirectLinkFallback", str(bool(self.fallback_switch.get())))
        block_ads_val = str(bool(self.skip_ads_switch.get()))
        self.config_mgr.set_setting("BlockAds", block_ads_val)
        self.config_mgr.set_setting("SkipAds", block_ads_val)

        try:
            max_ad = int(self.max_ad_entry.get().strip())
            max_ad = max(0, min(3600, max_ad))
            self.config_mgr.set_setting("MaxAdWatchSeconds", str(max_ad))
        except ValueError:
            messagebox.showerror("Error", "Max Ad Watch must be a valid integer (seconds).")
            return

        try:
            val = int(self.threads_entry.get().strip())
            val = max(1, min(10, val))
            self.config_mgr.set_setting("ParallelBrowsers", str(val))
        except ValueError:
            messagebox.showerror("Error", "Parallel Browsers count must be a valid integer.")
            return

        try:
            timeout = int(self.full_timeout_entry.get().strip())
            timeout = max(0, min(1440, timeout))
            self.config_mgr.set_setting("FullVideoTimeoutMinutes", str(timeout))
        except ValueError:
            messagebox.showerror("Error", "Full Video timeout must be a valid integer (minutes).")
            return

        try:
            buffer_val = int(self.stop_buffer_entry.get().strip())
            buffer_val = max(0, min(120, buffer_val))
            self.config_mgr.set_setting("AutoplayEndBufferSeconds", str(buffer_val))
        except ValueError:
            messagebox.showerror("Error", "Stop buffer must be a valid integer (seconds).")
            return

        try:
            loop_val = int(self.loop_entry.get().strip())
            loop_val = max(1, min(1000, loop_val))
            self.config_mgr.set_setting("AutomationLoops", str(loop_val))
        except ValueError:
            messagebox.showerror("Error", "Automation Loops must be a valid positive integer.")
            return

        self.config_mgr.set_setting("WatchtimeDuration", self.duration_menu.get())
        self.config_mgr.set_setting("AppearanceMode", self.appearance_menu.get())

        # Appearance applies immediately; other settings take effect from the next run.
        self.app_controller.apply_appearance_mode(self.appearance_menu.get())
        messagebox.showinfo("Success", "Settings saved successfully!")
