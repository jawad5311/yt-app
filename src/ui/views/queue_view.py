import customtkinter as ctk
from tkinter import messagebox
from typing import List, Dict, Any

from ...models.video_task import VideoTask


class QueueView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.video_tasks: List[VideoTask] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        ctk.CTkLabel(
            header_frame,
            text="Video Automation Queue",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="+ Add Target Video",
            command=self.open_add_video_modal,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="🗑 Clear Queue",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_queue,
        ).pack(side="right")

        # Scrollable Task List
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Queued Target Videos")
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.load_initial_queue()

    def load_initial_queue(self):
        raw_list = self.app_controller.config_mgr.get_video_queue()
        self.video_tasks = [VideoTask.from_dict(v) for v in raw_list]
        self.refresh_list()
        self.app_controller.on_queue_changed(self.video_tasks)

    def save_queue(self):
        dict_list = [v.to_dict() for v in self.video_tasks]
        self.app_controller.config_mgr.set_video_queue(dict_list)
        self.app_controller.on_queue_changed(self.video_tasks)

    def open_add_video_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Add Target Video Task")
        modal.geometry("500x520")
        modal.grab_set()

        ctk.CTkLabel(modal, text="Video URL:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(15, 2))
        url_entry = ctk.CTkEntry(modal, placeholder_text="https://www.youtube.com/watch?v=...", width=440)
        url_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Search Keywords (for Organic Search):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        keywords_entry = ctk.CTkEntry(modal, placeholder_text="e.g., python automation tutorial", width=440)
        keywords_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Video Title (Optional):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        title_entry = ctk.CTkEntry(modal, placeholder_text="Exact or partial video title", width=440)
        title_entry.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Watch Duration:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        duration_option = ctk.CTkOptionMenu(modal, values=["30 Seconds", "1 Minute", "2 Minutes", "5 Minutes", "10 Minutes"], width=440)
        duration_option.set("5 Minutes")
        duration_option.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Comment Mode:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        comment_mode = ctk.CTkOptionMenu(modal, values=["AI", "Custom", "None"], width=440)
        comment_mode.set("AI")
        comment_mode.pack(padx=20, pady=(0, 10))

        ctk.CTkLabel(modal, text="Custom Comment (if Custom Mode selected):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=20, pady=(5, 2))
        custom_comment_entry = ctk.CTkEntry(modal, placeholder_text="Type custom comment here...", width=440)
        custom_comment_entry.pack(padx=20, pady=(0, 15))

        def on_add():
            url = url_entry.get().strip()
            keywords = keywords_entry.get().strip()
            title = title_entry.get().strip()
            if not url and not keywords and not title:
                messagebox.showerror("Error", "Please provide at least a URL or Search Keywords!")
                return

            task = VideoTask(
                url=url,
                keywords=keywords,
                title=title,
                watchtime_duration=duration_option.get(),
                comment_type=comment_mode.get(),
                custom_comment=custom_comment_entry.get().strip(),
            )
            self.video_tasks.append(task)
            self.save_queue()
            self.refresh_list()
            modal.destroy()

        ctk.CTkButton(modal, text="Add Task", fg_color="#2FA572", hover_color="#1E7B52", command=on_add).pack(pady=10)

    def remove_task(self, index: int):
        if 0 <= index < len(self.video_tasks):
            del self.video_tasks[index]
            self.save_queue()
            self.refresh_list()

    def clear_queue(self):
        self.video_tasks.clear()
        self.save_queue()
        self.refresh_list()

    def refresh_list(self):
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        if not self.video_tasks:
            ctk.CTkLabel(
                self.scroll_frame,
                text="Queue is empty. Click '+ Add Target Video' to add tasks.",
                font=ctk.CTkFont(size=14, slant="italic"),
            ).pack(pady=30)
            return

        for idx, task in enumerate(self.video_tasks):
            card = ctk.CTkFrame(self.scroll_frame, corner_radius=6)
            card.pack(fill="x", padx=5, pady=5)
            card.grid_columnconfigure(0, weight=1)

            t_text = task.title or task.keywords or task.url
            info_text = f"#{idx + 1}. {t_text}\nKeywords: {task.keywords or 'N/A'} | Watchtime: {task.watchtime_duration} | Comment: {task.comment_type}"
            ctk.CTkLabel(card, text=info_text, justify="left", font=ctk.CTkFont(size=13)).pack(side="left", padx=15, pady=10)

            ctk.CTkButton(
                card,
                text="Remove",
                width=70,
                fg_color="#D32F2F",
                hover_color="#9A0007",
                command=lambda i=idx: self.remove_task(i),
            ).pack(side="right", padx=10, pady=10)
