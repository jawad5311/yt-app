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

        # Logs Content Container (Allows floating scroll-down button overlay)
        self.logs_container = ctk.CTkFrame(self, fg_color="transparent")
        self.logs_container.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.logs_container.grid_columnconfigure(0, weight=1)
        self.logs_container.grid_rowconfigure(0, weight=1)

        # Text Box
        self.log_textbox = ctk.CTkTextbox(
            self.logs_container,
            font=ctk.CTkFont(family="Consolas", size=15),
            wrap="word",
        )
        self.log_textbox.grid(row=0, column=0, sticky="nsew")

        # Configure High-Contrast Color Tags for Readability
        self.log_textbox.tag_config("session_header", foreground="#38BDF8")  # Vibrant Sky Blue
        self.log_textbox.tag_config("watching_banner", foreground="#C084FC")  # Light Purple / Violet
        self.log_textbox.tag_config("label_channel", foreground="#94A3B8")    # Light Slate label
        self.log_textbox.tag_config("val_channel", foreground="#FBBF24")      # Warm Amber / Gold
        self.log_textbox.tag_config("label_url", foreground="#94A3B8")
        self.log_textbox.tag_config("val_url", foreground="#60A5FA", underline=True)  # Link Cyan-Blue
        self.log_textbox.tag_config("label_title", foreground="#94A3B8")
        self.log_textbox.tag_config("val_title", foreground="#4ADE80")        # Mint / Spring Green
        self.log_textbox.tag_config("action_success", foreground="#34D399")   # Emerald Green
        self.log_textbox.tag_config("skip", foreground="#FB923C")             # Orange / Coral
        self.log_textbox.tag_config("error", foreground="#EF4444")            # Vivid Red (Errors / Failures)
        self.log_textbox.tag_config("fallback_direct", foreground="#DC2626")  # Alert Red (Direct Navigation Fallback)
        self.log_textbox.tag_config("warning_orange", foreground="#F97316")   # Vibrant Orange (Warnings, Misses, Troubled flows)
        self.log_textbox.tag_config("ad", foreground="#FB923C")               # Orange
        self.log_textbox.tag_config("pause", foreground="#F59E0B")            # Amber
        self.log_textbox.tag_config("resume", foreground="#22C55E")           # Bright Green
        self.log_textbox.tag_config("progress", foreground="#94A3B8")         # Slate Gray
        self.log_textbox.tag_config("timeline", foreground="#818CF8")         # Soft Indigo
        self.log_textbox.tag_config("default", foreground="#E2E8F0")          # Crisp Off-White

        # Floating Circular Scroll-Down Button (bottom-center)
        self.scroll_btn = ctk.CTkButton(
            self.logs_container,
            text="↓",
            width=40,
            height=40,
            corner_radius=20,
            font=ctk.CTkFont(size=20, weight="bold"),
            fg_color=("#F1F3F5", "#2D3748"),
            hover_color=("#E2E8F0", "#374151"),
            border_color=("#CBD5E1", "#4B5563"),
            border_width=1,
            text_color=("#334155", "#F3F4F6"),
            command=self.scroll_to_bottom,
        )

        self.is_scroll_btn_visible = False
        self._anim_job = None
        self._scroll_job = None

        # Intercept scroll position to show/hide button with smooth transitions
        if hasattr(self.log_textbox, "_textbox") and hasattr(self.log_textbox, "_y_scrollbar"):
            self._orig_set = self.log_textbox._y_scrollbar.set
            self.log_textbox._textbox.configure(yscrollcommand=self._on_text_scroll)

    def _on_text_scroll(self, first, last):
        if hasattr(self, "_orig_set"):
            try:
                self._orig_set(first, last)
            except Exception:
                pass
        try:
            f, l = float(first), float(last)
            if (f, l) == (0.0, 1.0):
                # All content visible without scrollbar
                self.hide_scroll_button()
            elif l >= 0.985:
                # Reached the bottom of the log
                self.hide_scroll_button()
            elif l < 0.98:
                # User scrolled up towards top/middle
                self.show_scroll_button()
        except Exception:
            pass

    def show_scroll_button(self):
        """Trigger smooth slide-up appearance of the scroll-down button."""
        if getattr(self, "is_scroll_btn_visible", False):
            return
        self.is_scroll_btn_visible = True
        self._animate_scroll_button(show=True)

    def hide_scroll_button(self):
        """Trigger smooth slide-down disappearance of the scroll-down button."""
        if not getattr(self, "is_scroll_btn_visible", False):
            return
        self.is_scroll_btn_visible = False
        self._animate_scroll_button(show=False)

    def _animate_scroll_button(self, show: bool, step: int = 0, max_steps: int = 8):
        """Smoothly animate the button sliding into or out of view."""
        if not hasattr(self, "scroll_btn"):
            return
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

        start = 1.05 if show else 0.91
        target = 0.91 if show else 1.05
        t = step / max_steps
        ease = (1.0 - (1.0 - t) * (1.0 - t)) if show else (t * t)
        rely = start + (target - start) * ease

        try:
            self.scroll_btn.place(relx=0.5, rely=rely, anchor="center")
            self.scroll_btn.lift()
        except Exception:
            return

        if step < max_steps:
            self._anim_job = self.after(16, lambda: self._animate_scroll_button(show, step + 1, max_steps))
        else:
            self._anim_job = None
            if not show:
                try:
                    self.scroll_btn.place_forget()
                except Exception:
                    pass

    def scroll_to_bottom(self):
        """Smoothly scroll down to the latest log entry."""
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except Exception:
                pass
            self._scroll_job = None
        self._smooth_scroll_step(target=1.0)

    def _smooth_scroll_step(self, target: float = 1.0, steps_left: int = 8):
        try:
            cur_first, cur_last = self.log_textbox.yview()
            if cur_last >= 0.999 or steps_left <= 0:
                self.log_textbox.see("end")
                self.hide_scroll_button()
                self._scroll_job = None
                return

            diff = target - cur_last
            new_first = min(1.0, cur_first + diff * 0.45)
            self.log_textbox.yview_moveto(new_first)

            if new_first < 0.99 and steps_left > 1:
                self._scroll_job = self.after(16, lambda: self._smooth_scroll_step(target, steps_left - 1))
            else:
                self.log_textbox.see("end")
                self.hide_scroll_button()
                self._scroll_job = None
        except Exception:
            try:
                self.log_textbox.see("end")
            except Exception:
                pass
            self.hide_scroll_button()
            self._scroll_job = None

    def append_log(self, text: str):
        should_autoscroll = True
        try:
            if hasattr(self, "log_textbox") and hasattr(self.log_textbox, "yview"):
                cur_first, cur_last = self.log_textbox.yview()
                # If content overflows and user scrolled up away from bottom (or scroll button is visible)
                if (cur_first, cur_last) != (0.0, 1.0) and (
                    cur_last < 0.985 or getattr(self, "is_scroll_btn_visible", False)
                ):
                    should_autoscroll = False
        except Exception:
            should_autoscroll = True

        self.log_textbox.configure(state="normal")
        lines = (text or "").split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                self.log_textbox.insert("end", "\n")
                continue

            lower = stripped.lower()

            # 1. Direct navigation fallback (Traffic source risk - RED)
            if (
                "direct navigation fallback" in lower
                or "navigating directly" in lower
                or "direct fallback" in lower
                or "traffic source may register as direct" in lower
                or "navigation not detected after click" in lower
            ):
                self.log_textbox.insert("end", line + "\n", "fallback_direct")
            # 2. Critical Errors, Failures, Playback Mismatch, Sign-in errors (RED)
            elif (
                stripped.startswith("Error")
                or stripped.startswith("Critical error")
                or stripped.startswith("Failed")
                or "error:" in lower
                or "failed:" in lower
                or "identity verification failed" in lower
                or "playback identity verification failed" in lower
                or "aborting playback" in lower
                or "sign-in failed" in lower
                or "critical alert" in lower
                or "account suspended" in lower
                or "bot verification" in lower
            ):
                self.log_textbox.insert("end", line + "\n", "error")
            # 3. Warnings, Troubles, Search Fallbacks, Misses, Incomplete elements (ORANGE)
            elif (
                stripped.startswith("WARNING")
                or "[warning]" in lower
                or "warning:" in lower
                or "falling back to youtube search" in lower
                or "no target channel video found" in lower
                or "no video matching target" in lower
                or "unable to find candidate video" in lower
                or "not found in suggested section" in lower
                or "is an ad element; skipping" in lower
                or "miss attempt" in lower
                or "target channel videos not found" in lower
                or "not yet visible in home feed" in lower
                or "not in current view" in lower
                or "could not auto-detect" in lower
                or "network error" in lower
            ):
                self.log_textbox.insert("end", line + "\n", "warning_orange")
            # 4. Standard structural tags and labels
            elif stripped.startswith("=== ") or stripped.endswith(" ==="):
                self.log_textbox.insert("end", line + "\n", "session_header")
            elif stripped.startswith("--- Watching ") or (stripped.startswith("--- ") and stripped.endswith(" ---")):
                self.log_textbox.insert("end", line + "\n", "watching_banner")
            elif stripped.startswith("Channel: "):
                self.log_textbox.insert("end", "Channel: ", "label_channel")
                self.log_textbox.insert("end", line[len("Channel: "):] + "\n", "val_channel")
            elif stripped.startswith("URL: "):
                self.log_textbox.insert("end", "URL: ", "label_url")
                self.log_textbox.insert("end", line[len("URL: "):] + "\n", "val_url")
            elif stripped.startswith("Title: "):
                self.log_textbox.insert("end", "Title: ", "label_title")
                self.log_textbox.insert("end", line[len("Title: "):] + "\n", "val_title")
            elif stripped.startswith("Skipping ") or "skipping" in lower:
                self.log_textbox.insert("end", line + "\n", "skip")
            elif (
                stripped.startswith("Opening ")
                or stripped.startswith("Updated video time")
                or stripped.startswith("Added video to queue")
                or stripped.startswith("Found eligible")
                or stripped.startswith("Found video")
                or "liked comment" in lower
                or "liked the video" in lower
                or "liked a comment" in lower
            ):
                self.log_textbox.insert("end", line + "\n", "action_success")
            elif (
                stripped.startswith("Ad playing:")
                or stripped.startswith("Ad policy:")
                or stripped.startswith("Clicked Skip Ad")
                or stripped.startswith("Ad cap")
            ):
                self.log_textbox.insert("end", line + "\n", "ad")
            elif "paused" in lower:
                self.log_textbox.insert("end", line + "\n", "pause")
            elif "resumed" in lower:
                self.log_textbox.insert("end", line + "\n", "resume")
            elif (
                stripped.startswith("Scanning ")
                or stripped.startswith("Waiting ")
                or stripped.startswith("Smooth scrolling ")
                or stripped.startswith("Inspecting ")
            ):
                self.log_textbox.insert("end", line + "\n", "progress")
            elif (
                stripped.startswith("Playback timeline update:")
                or stripped.startswith("Noted video duration:")
                or stripped.startswith("Target stored channel(s):")
            ):
                self.log_textbox.insert("end", line + "\n", "timeline")
            else:
                self.log_textbox.insert("end", line + "\n", "default")

        self.log_textbox.insert("end", "\n")
        # Cap total lines to 3,000 to prevent Tkinter Text widget slowdowns during long sessions
        try:
            line_count = int(self.log_textbox.index("end-1c").split(".")[0])
            if line_count > 3000:
                self.log_textbox.delete("1.0", f"{line_count - 2500}.0")
        except Exception:
            pass

        # Auto-scroll only when user is at the bottom of the log; if scrolled up, leave view undisturbed
        if should_autoscroll:
            try:
                self.log_textbox.see("end")
            except Exception:
                pass
        self.log_textbox.configure(state="disabled")

    def clear_logs(self):
        if getattr(self, "is_scroll_btn_visible", False):
            self.hide_scroll_button()
        if hasattr(self, "scroll_btn"):
            try:
                self.scroll_btn.place_forget()
            except Exception:
                pass
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    def copy_logs(self):
        content = self.log_textbox.get("1.0", "end-1c")
        if content.strip():
            self.clipboard_clear()
            self.clipboard_append(content)
