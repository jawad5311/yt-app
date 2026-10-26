from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List

import customtkinter as ctk

from ...models.account import Account


class AccountsView(ctk.CTkFrame):
    def __init__(self, parent, app_controller):
        super().__init__(parent, corner_radius=10)
        self.app_controller = app_controller
        self.accounts: List[Account] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        ctk.CTkLabel(
            header_frame,
            text="Google Accounts Manager",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="+ Add Single Account",
            command=self.add_account_dialog,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="📂 Load Accounts File (accounts.txt)",
            fg_color="#1F6AA5",
            command=self.import_accounts_file,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="🗑 Clear All",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=self.clear_accounts,
        ).pack(side="right")

        # Accounts List Scrollable Frame
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Loaded Accounts")
        self.scroll_frame.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.load_saved_accounts()

    def load_saved_accounts(self):
        try:
            raw_accounts = self.app_controller.config_mgr.get_accounts()
        except Exception as exc:
            logging.error(f"Failed to load accounts: {exc}")
            raw_accounts = []
        self.accounts = []
        for item in raw_accounts:
            if isinstance(item, dict) and "email" in item and "password" in item:
                self.accounts.append(Account(email=item["email"], password=item["password"]))
        self.refresh_list()
        self.app_controller.on_accounts_changed(self.accounts)

    def save_accounts(self):
        acc_dicts = [{"email": a.email, "password": a.password} for a in self.accounts]
        self.app_controller.config_mgr.set_accounts(acc_dicts)
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

        new_accounts = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                acc = Account.parse_line(line)
                if acc:
                    new_accounts.append(acc)

        if new_accounts:
            self.accounts = new_accounts
            self.save_accounts()
            self.refresh_list()

    def add_account_dialog(self):
        dialog = ctk.CTkInputDialog(text="Enter account details as 'email:password':", title="Add Account")
        input_text = dialog.get_input()
        if input_text:
            acc = Account.parse_line(input_text)
            if acc:
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
            ctk.CTkLabel(
                self.scroll_frame,
                text="No accounts loaded. Click 'Load Accounts File' or 'Add Single Account'.",
                font=ctk.CTkFont(size=14, slant="italic"),
            ).pack(pady=30)
            return

        for idx, acc in enumerate(self.accounts):
            row = ctk.CTkFrame(self.scroll_frame, corner_radius=6)
            row.pack(fill="x", padx=5, pady=4)

            ctk.CTkLabel(
                row,
                text=f"{idx + 1}. {acc.email}",
                font=ctk.CTkFont(weight="bold"),
            ).pack(side="left", padx=15, pady=10)

            ctk.CTkButton(
                row,
                text="Remove",
                width=70,
                fg_color="#D32F2F",
                hover_color="#9A0007",
                command=lambda i=idx: self.remove_account(i),
            ).pack(side="right", padx=10, pady=10)
