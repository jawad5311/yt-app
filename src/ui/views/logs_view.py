import customtkinter as ctk


class LogsView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header Frame
        h_frame = ctk.CTkFrame(self, fg_color="transparent")
        h_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        ctk.CTkLabel(
            h_frame,
            text="Logs",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Action Buttons
        ctk.CTkButton(
            h_frame,
            text="Clear Logs",
            width=100,
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_logs,
        ).pack(side="right")

        ctk.CTkButton(
            h_frame,
            text="📋 Copy Logs",
            width=110,
            fg_color="#1F6AA5",
            command=self.copy_logs,
        ).pack(side="right", padx=(0, 10))

        # Text Box
        self.log_textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
        )
        self.log_textbox.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")

    def append_log(self, text: str):
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", text + "\n\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def clear_logs(self):
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    def copy_logs(self):
        content = self.log_textbox.get("1.0", "end-1c")
        if content.strip():
            self.clipboard_clear()
            self.clipboard_append(content)
            self.update()
