from __future__ import annotations

import logging
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import List

from ...automation.warmup_manager import WarmupManager
from ...models.account import Account
from ..components import Button, Font, Frame, InputDialog, Label, ScrollableFrame
from ..tooltip import add_tooltip, flash_button_feedback, with_button_feedback
from ..warmup_dialog import WarmupDialog


class AccountsView(Frame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, style="TFrame")
        self.app_controller = app_controller
        self.accounts: List[Account] = []

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
            del self.accounts[index]
            self.save_accounts()
            self.refresh_list()

    def clear_accounts(self):
        self.accounts.clear()
        self.save_accounts()
        self.refresh_list()

    def refresh_list(self):
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
                badge.pack(side="left", padx=(0, 10))

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

    def open_warmup_dialog(self, account: Account) -> None:
        """Open the Warm Up Settings pop-up window for an account."""
        cfg_mgr = getattr(self.app_controller, "config_mgr", None)
        mode = getattr(self.app_controller, "appearance_mode", "Dark")
        WarmupDialog(self, account=account, config_mgr=cfg_mgr, on_save_callback=self.refresh_list, mode=mode)
