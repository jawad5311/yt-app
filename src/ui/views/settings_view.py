import customtkinter as ctk
from tkinter import messagebox


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

        # AI Provider Selection
        ctk.CTkLabel(container, text="AI Comment Provider:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(15, 2))
        self.provider_option = ctk.CTkOptionMenu(container, values=["Gemini", "OpenAI"], width=350)
        self.provider_option.set(self.config_mgr.get_setting("AIProvider", "Gemini"))
        self.provider_option.pack(anchor="w", padx=15, pady=(0, 15))

        # Gemini API Key
        ctk.CTkLabel(container, text="Google Gemini API Key:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.gemini_entry = ctk.CTkEntry(container, placeholder_text="AIzaSy...", width=450, show="*")
        self.gemini_entry.insert(0, self.config_mgr.get_setting("GeminiKey", ""))
        self.gemini_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # OpenAI API Key
        ctk.CTkLabel(container, text="OpenAI API Key:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.openai_entry = ctk.CTkEntry(container, placeholder_text="sk-...", width=450, show="*")
        self.openai_entry.insert(0, self.config_mgr.get_setting("OpenAIKey", ""))
        self.openai_entry.pack(anchor="w", padx=15, pady=(0, 15))

        # Direct Link Fallback Toggle
        self.fallback_switch = ctk.CTkSwitch(
            container,
            text="Enable Direct Link Fallback (if Organic Search finds no match)",
            font=ctk.CTkFont(weight="bold"),
        )
        if self.config_mgr.get_bool("DirectLinkFallback", True):
            self.fallback_switch.select()
        else:
            self.fallback_switch.deselect()
        self.fallback_switch.pack(anchor="w", padx=15, pady=(10, 15))

        # Parallel Browsers Count
        ctk.CTkLabel(container, text="Parallel Browsers Count:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.threads_entry = ctk.CTkEntry(container, width=100)
        self.threads_entry.insert(0, str(self.config_mgr.get_int("ParallelBrowsers", 1)))
        self.threads_entry.pack(anchor="w", padx=15, pady=(0, 20))

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
        self.config_mgr.set_setting("AIProvider", self.provider_option.get())
        self.config_mgr.set_setting("GeminiKey", self.gemini_entry.get().strip())
        self.config_mgr.set_setting("OpenAIKey", self.openai_entry.get().strip())
        self.config_mgr.set_setting("DirectLinkFallback", str(bool(self.fallback_switch.get())))

        try:
            val = int(self.threads_entry.get().strip())
            val = max(1, min(10, val))
            self.config_mgr.set_setting("ParallelBrowsers", str(val))
        except ValueError:
            messagebox.showerror("Error", "Parallel Browsers count must be a valid integer.")
            return

        messagebox.showinfo("Success", "Settings saved successfully!")
