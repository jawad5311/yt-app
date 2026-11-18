from __future__ import annotations

import logging
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional, Tuple

from ...automation.account_auth import (
    AuthResult,
    AuthStatus,
    authenticate_account_profile,
    has_existing_profile_data,
)
from ...automation.driver import close_driver
from ...automation.profile_manager import resolve_profile_dir
from ...automation.warmup_manager import WarmupManager
from ...models.account import Account
from ..background_jobs import BackgroundJobs
from ..components import Button, Font, Frame, InputDialog, Label, ScrollableFrame
from ..tooltip import add_tooltip, flash_button_feedback, with_button_feedback
from ..warmup_dialog import WarmupDialog


class AccountsView(Frame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, style="TFrame")
        self.app_controller = app_controller
        self.accounts: List[Account] = []
        self._authenticating_accounts: Dict[str, str] = {}
        self._auth_states: Dict[str, AuthStatus] = {}
        self._auth_stop_events: Dict[str, threading.Event] = {}
        self._auth_drivers: Dict[str, Any] = {}
        self._auth_drivers_lock = threading.Lock()
        self._auth_buttons: Dict[str, Button] = {}
        self._auth_badges: Dict[str, tk.Label] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header (macOS Large Title & Subtitle)
        header_frame = Frame(self, style="Transparent.TFrame")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        title_container = Frame(header_frame, style="Transparent.TFrame")
        title_container.pack(side="left", anchor="w")

        Label(
            title_container,
            text="Google Accounts Manager",
            font=Font(size=22, weight="bold"),
            style="Header.TLabel",
        ).pack(anchor="w")

        Label(
            title_container,
            text="Browser Profiles, Cookies & Authentication Status",
            font=Font(size=9),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # Action Buttons
        btn_frame = Frame(self, style="Transparent.TFrame")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        add_acc_btn = Button(
            btn_frame,
            text="+ Add Single Account",
            style="Primary.TButton",
        )
        add_acc_btn.configure(
            command=with_button_feedback(add_acc_btn, "Adding Account...", self.add_account_dialog)
        )
        add_acc_btn.pack(side="left", padx=(0, 10))
        add_tooltip(
            add_acc_btn,
            "Add a new Google account manually (email and password).",
        )

        load_acc_btn = Button(
            btn_frame,
            text="📂 Load Accounts File (accounts.txt)",
            style="TButton",
        )
        load_acc_btn.configure(
            command=with_button_feedback(load_acc_btn, "Loading File...", self.import_accounts_file)
        )
        load_acc_btn.pack(side="left", padx=10)
        add_tooltip(
            load_acc_btn,
            "Import accounts from a local accounts.txt file.",
        )

        clear_acc_btn = Button(
            btn_frame,
            text="🗑 Clear All",
            style="Danger.TButton",
        )
        clear_acc_btn.configure(
            command=with_button_feedback(clear_acc_btn, "Clearing...", self.clear_accounts)
        )
        clear_acc_btn.pack(side="right")
        add_tooltip(
            clear_acc_btn,
            "Remove all accounts from the system.",
        )

        # Accounts List Scrollable Frame
        self.scroll_frame = ScrollableFrame(self, label_text="Loaded Accounts")
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.load_saved_accounts()

    def load_saved_accounts(self):
        try:
            raw_accounts = self.app_controller.config_mgr.get_accounts() if hasattr(self.app_controller, "config_mgr") else []
        except Exception as exc:
            logging.error(f"Failed to load accounts: {exc}")
            raw_accounts = []
        self.accounts = []
        for item in raw_accounts:
            if isinstance(item, dict) and "email" in item and "password" in item:
                self.accounts.append(Account(email=item["email"], password=item["password"]))
        self.refresh_list()
        if hasattr(self.app_controller, "on_accounts_changed"):
            self.app_controller.on_accounts_changed(self.accounts)

    def save_accounts(self):
        acc_dicts = [{"email": a.email, "password": a.password} for a in self.accounts]
        if hasattr(self.app_controller, "config_mgr"):
            self.app_controller.config_mgr.set_accounts(acc_dicts)
        if hasattr(self.app_controller, "on_accounts_changed"):
            self.app_controller.on_accounts_changed(self.accounts)

    def import_accounts_file(self, file_path: str = ""):
        if not file_path:
            file_path = filedialog.askopenfilename(
                title="Select Accounts File",
                filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            )
        if not file_path:
            return

        path = Path(file_path)
        if not path.exists():
            return

        imported: List[Account] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                acc = Account.parse_line(line)
                if acc:
                    imported.append(acc)

        if not imported:
            messagebox.showwarning("Warning", "No valid accounts found in the selected file.")
            return

        seen = {a.email: a for a in self.accounts}
        duplicates = []
        conflicts = []

        for acc in imported:
            if acc.email in seen:
                if seen[acc.email].password != acc.password:
                    conflicts.append(acc.email)
                else:
                    duplicates.append(acc.email)
            else:
                seen[acc.email] = acc

        if conflicts:
            messagebox.showerror(
                "Credential Conflict",
                f"Conflicting passwords found for account(s): {', '.join(set(conflicts))}. Import aborted without saving.",
            )
            return

        if duplicates:
            logging.info(f"Omitted {len(duplicates)} duplicate entry/entries during import.")

        unique_accounts = list(seen.values())
        if unique_accounts:
            self.accounts = unique_accounts
            self.save_accounts()
            self.refresh_list()

    def add_account_dialog(self):
        try:
            from .. import ttk_adapter as ctk
            dialog = ctk.CTkInputDialog(text="Enter account details as 'email:password':", title="Add Account")
        except Exception:
            dialog = InputDialog(text="Enter account details as 'email:password':", title="Add Account", master=self)
        input_text = dialog.get_input()
        if input_text:
            acc = Account.parse_line(input_text)
            if acc:
                existing = next((a for a in self.accounts if a.email == acc.email), None)
                if existing:
                    if existing.password != acc.password:
                        messagebox.showerror("Conflict", f"Account '{acc.email}' already exists with a different password.")
                    else:
                        messagebox.showwarning("Duplicate", f"Account '{acc.email}' is already added.")
                    return
                self.accounts.append(acc)
                self.save_accounts()
                self.refresh_list()
            else:
                messagebox.showerror("Error", "Invalid account format. Use 'email:password' or 'email,password'")

    def remove_account(self, index: int):
        if 0 <= index < len(self.accounts):
            email = self.accounts[index].email
            self._stop_account_authentication(email)
            self._auth_states.pop(email, None)
            del self.accounts[index]
            self.save_accounts()
            self.refresh_list()

    def clear_accounts(self):
        self.stop_all_authentications()
        self._auth_states.clear()
        self.accounts.clear()
        self.save_accounts()
        self.refresh_list()

    def _account_has_saved_profile(self, account: Account) -> bool:
        try:
            profile_dir = resolve_profile_dir(account, known_accounts=self.accounts)
            return has_existing_profile_data(profile_dir)
        except Exception:
            return False

    def _get_auth_badge_info(self, account: Account) -> Tuple[str, str, str]:
        """Return (label_text, bg_color, fg_color) for an account's auth/profile pill."""
        email = account.email
        auth_in_progress = self.__dict__.get("_authenticating_accounts", {})
        auth_states = self.__dict__.get("_auth_states", {})

        if email in auth_in_progress:
            return (f"⏳ {auth_in_progress[email]}", "#d97706", "#ffffff")

        status = auth_states.get(email)
        if status in (AuthStatus.ALREADY_LOGGED_IN, AuthStatus.AUTHENTICATED):
            return ("✓ Logged In", "#16a34a", "#ffffff")

        if self._account_has_saved_profile(account):
            return ("Profile Saved", "#334155", "#e2e8f0")

        return ("Not Logged In", "#4b5563", "#e5e7eb")

    def _update_account_auth_ui(self, email: str, *, stage: Optional[str] = None, busy: bool = False) -> None:
        """Update the Authenticate button and status badge for a specific account row."""
        auth_buttons = self.__dict__.get("_auth_buttons", {})
        auth_badges = self.__dict__.get("_auth_badges", {})

        btn = auth_buttons.get(email)
        if btn is not None:
            try:
                if btn.winfo_exists():
                    if busy:
                        btn.configure(state="disabled", text=f"⏳ {stage or 'Checking...'}")
                    else:
                        btn.configure(state="normal", text="🔐 Authenticate")
            except Exception:
                pass

        badge = auth_badges.get(email)
        if badge is not None:
            try:
                if badge.winfo_exists():
                    acc = next((a for a in self.accounts if a.email == email), None)
                    if acc is not None:
                        badge_text, badge_bg, badge_fg = self._get_auth_badge_info(acc)
                        badge.configure(text=f" {badge_text} ", bg=badge_bg, fg=badge_fg)
            except Exception:
                pass

    def is_authenticating(self) -> bool:
        """Return True if any account authentication check/login is currently running."""
        return bool(self.__dict__.get("_authenticating_accounts"))

    def refresh_auth_badges(self) -> None:
        """Refresh authentication/profile badges in-place without rebuilding rows."""
        for acc in list(self.accounts):
            is_busy = acc.email in self.__dict__.get("_authenticating_accounts", {})
            stage = self.__dict__.get("_authenticating_accounts", {}).get(acc.email)
            self._update_account_auth_ui(acc.email, stage=stage, busy=is_busy)

    def _stop_account_authentication(self, email: str) -> None:
        """Cancel an in-progress authentication for a single account."""
        stop_events = self.__dict__.get("_auth_stop_events", {})
        evt = stop_events.get(email)
        if evt is not None:
            evt.set()
        lock = self.__dict__.get("_auth_drivers_lock")
        drivers = self.__dict__.get("_auth_drivers", {})
        driver = None
        if lock is not None:
            with lock:
                driver = drivers.pop(email, None)
        else:
            driver = drivers.pop(email, None)
        if driver is not None:
            try:
                close_driver(driver)
            except Exception:
                pass

    def stop_all_authentications(self) -> None:
        """Signal all active account authentication jobs to stop and close their drivers."""
        stop_events = self.__dict__.get("_auth_stop_events", {})
        for evt in list(stop_events.values()):
            try:
                evt.set()
            except Exception:
                pass
        lock = self.__dict__.get("_auth_drivers_lock")
        drivers = self.__dict__.get("_auth_drivers", {})
        active_drivers: List[Any] = []
        if lock is not None:
            with lock:
                active_drivers = list(drivers.values())
                drivers.clear()
        else:
            active_drivers = list(drivers.values())
            drivers.clear()
        for drv in active_drivers:
            try:
                close_driver(drv)
            except Exception:
                pass

    def authenticate_account(self, account: Account) -> None:
        """Check if an account's Chrome profile is logged in, or open Chrome to sign in and store it."""
        email = account.email
        auth_in_progress = self.__dict__.setdefault("_authenticating_accounts", {})
        if email in auth_in_progress:
            return

        stop_evt = threading.Event()
        auth_in_progress[email] = "Checking..."
        self.__dict__.setdefault("_auth_stop_events", {})[email] = stop_evt
        self._update_account_auth_ui(email, stage="Checking...", busy=True)

        known_accounts = list(self.accounts)
        log_queue = getattr(self.app_controller, "log_queue", None)
        drivers_lock = self.__dict__.setdefault("_auth_drivers_lock", threading.Lock())
        drivers_map = self.__dict__.setdefault("_auth_drivers", {})

        def set_active_driver(drv: Optional[Any]) -> None:
            with drivers_lock:
                if drv is None:
                    drivers_map.pop(email, None)
                else:
                    drivers_map[email] = drv

        def work(report_progress):
            return authenticate_account_profile(
                account=account,
                known_accounts=known_accounts,
                log_queue=log_queue,
                stop_event=stop_evt,
                progress_callback=report_progress,
                active_driver_callback=set_active_driver,
            )

        def on_progress(stage: str) -> None:
            if email in auth_in_progress:
                auth_in_progress[email] = stage
                self._update_account_auth_ui(email, stage=stage, busy=True)

        def _cleanup_state() -> None:
            auth_in_progress.pop(email, None)
            self.__dict__.get("_auth_stop_events", {}).pop(email, None)
            with drivers_lock:
                drivers_map.pop(email, None)

        def on_done(result: AuthResult) -> None:
            _cleanup_state()
            auth_states = self.__dict__.setdefault("_auth_states", {})
            if result.is_logged_in:
                auth_states[email] = result.status
            elif result.status in (AuthStatus.WRONG_ACCOUNT, AuthStatus.FAILED):
                auth_states.pop(email, None)

            self._update_account_auth_ui(email, busy=False)

            if getattr(self.app_controller, "is_closing", False):
                return
            try:
                if not self.winfo_exists():
                    return
            except Exception:
                return

            if result.status == AuthStatus.ALREADY_LOGGED_IN:
                messagebox.showinfo("Already Logged In", result.message, parent=self)
            elif result.status == AuthStatus.AUTHENTICATED:
                messagebox.showinfo("Account Authenticated", result.message, parent=self)
            elif result.status == AuthStatus.PROFILE_BUSY:
                messagebox.showwarning("Profile In Use", result.message, parent=self)
            elif result.status == AuthStatus.WRONG_ACCOUNT:
                messagebox.showwarning("Wrong Account Signed In", result.message, parent=self)
            elif result.status == AuthStatus.CANCELLED:
                messagebox.showinfo("Authentication Cancelled", result.message, parent=self)
            else:
                messagebox.showerror("Authentication Failed", result.message, parent=self)

        def on_error(exc: Exception) -> None:
            _cleanup_state()
            self._update_account_auth_ui(email, busy=False)
            if getattr(self.app_controller, "is_closing", False):
                return
            err_text = str(exc)
            if account.password and account.password in err_text:
                err_text = err_text.replace(account.password, "[REDACTED_PASSWORD]")
            try:
                if self.winfo_exists():
                    messagebox.showerror(
                        "Authentication Error",
                        f"Failed to authenticate '{email}':\n{err_text}",
                        parent=self,
                    )
            except Exception:
                pass

        def is_current() -> bool:
            try:
                return bool(self.winfo_exists())
            except Exception:
                return False

        jobs = getattr(self.app_controller, "background_jobs", None)
        if isinstance(jobs, BackgroundJobs):
            jobs.submit(
                work,
                done=on_done,
                error=on_error,
                progress=on_progress,
                current=is_current,
                discard=_cleanup_state,
            )
        else:
            def _runner():
                try:
                    result = work(lambda stage: self.after(0, lambda s=stage: on_progress(s)))
                except Exception as exc:
                    self.after(0, lambda e=exc: on_error(e))
                else:
                    self.after(0, lambda r=result: on_done(r))

            threading.Thread(target=_runner, daemon=True, name=f"account-auth-{email}").start()

    def refresh_list(self):
        self.__dict__.setdefault("_auth_buttons", {}).clear()
        self.__dict__.setdefault("_auth_badges", {}).clear()

        for child in self.scroll_frame.winfo_children():
            child.destroy()

        if not self.accounts:
            Label(
                self.scroll_frame,
                text="No accounts loaded. Click 'Load Accounts File' or 'Add Single Account'.",
                font=Font(size=14, slant="italic"),
                style="Muted.TLabel",
            ).pack(pady=30)
            return

        warmup_mgr = WarmupManager(self.app_controller.config_mgr) if hasattr(self.app_controller, "config_mgr") else None
        auth_in_progress = self.__dict__.get("_authenticating_accounts", {})

        for idx, acc in enumerate(self.accounts):
            row = Frame(self.scroll_frame, style="Card.TFrame")
            row.pack(fill="x", padx=5, pady=4)

            Label(
                row,
                text=f"👤  {idx + 1}.  {acc.email}",
                font=Font(size=10, weight="bold"),
                style="Card.TLabel",
            ).pack(side="left", padx=(15, 8), pady=10)

            # Warm-up gradient status pill badge
            if warmup_mgr:
                w_cfg = warmup_mgr.get_account_config(acc.email)
                w_info = w_cfg.get_status_info()
                badge_col = w_info["color"]
                badge_fg = w_info["text_fg"]
                if not w_cfg.enabled:
                    badge_text = "Warm-Up Off"
                    badge_col = "#4b5563"
                    badge_fg = "#ffffff"
                elif w_cfg.is_warmed_up:
                    badge_text = "✅ Warmed Up"
                else:
                    badge_text = f"🔥 {w_info['stage_label']}"

                badge = tk.Label(
                    row,
                    text=f" {badge_text} ",
                    bg=badge_col,
                    fg=badge_fg,
                    font=("Segoe UI", 8, "bold"),
                    padx=6,
                    pady=2,
                    relief="flat",
                )
                badge.pack(side="left", padx=(0, 8))

            # Profile / authentication status pill badge
            auth_badge_text, auth_badge_bg, auth_badge_fg = self._get_auth_badge_info(acc)
            auth_badge = tk.Label(
                row,
                text=f" {auth_badge_text} ",
                bg=auth_badge_bg,
                fg=auth_badge_fg,
                font=("Segoe UI", 8, "bold"),
                padx=6,
                pady=2,
                relief="flat",
            )
            auth_badge.pack(side="left", padx=(0, 10))
            self._auth_badges[acc.email] = auth_badge

            rem_btn = Button(
                row,
                text="Remove",
                width=70,
                style="Danger.TButton",
            )
            rem_btn.configure(
                command=with_button_feedback(rem_btn, "Removed", lambda i=idx: self.remove_account(i))
            )
            rem_btn.pack(side="right", padx=(5, 10), pady=10)
            add_tooltip(rem_btn, f"Remove account {acc.email}.")

            warmup_btn = Button(
                row,
                text="🔥 Warm Up",
                width=85,
                style="Primary.TButton",
            )
            warmup_btn.configure(
                command=lambda a=acc: self.open_warmup_dialog(a)
            )
            warmup_btn.pack(side="right", padx=(5, 5), pady=10)
            add_tooltip(warmup_btn, f"Configure warm-up settings and progression for {acc.email}.")

            is_busy = acc.email in auth_in_progress
            auth_btn = Button(
                row,
                text=f"⏳ {auth_in_progress[acc.email]}" if is_busy else "🔐 Authenticate",
                width=115,
                style="Teal.TButton",
                state="disabled" if is_busy else "normal",
            )
            auth_btn.configure(
                command=lambda a=acc: self.authenticate_account(a)
            )
            auth_btn.pack(side="right", padx=(5, 5), pady=10)
            add_tooltip(
                auth_btn,
                f"Check if {acc.email} is already logged in, or open Chrome to sign in and store its profile for Start Browsing.",
            )
            self._auth_buttons[acc.email] = auth_btn

    def open_warmup_dialog(self, account: Account) -> None:
        """Open the Warm Up Settings pop-up window for an account."""
        cfg_mgr = getattr(self.app_controller, "config_mgr", None)
        mode = getattr(self.app_controller, "appearance_mode", "Dark")
        WarmupDialog(self, account=account, config_mgr=cfg_mgr, on_save_callback=self.refresh_list, mode=mode)
