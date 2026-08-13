from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Account:
    """Represents a Google Account used for YouTube automation."""
    email: str
    password: str
    status: str = "Ready"

    def get_profile_dir_name(self) -> str:
        """Sanitize email to get a safe folder name for Chrome user data."""
        safe_email = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self.email)
        return f"profile_{safe_email}"

    @classmethod
    def parse_line(cls, line: str) -> Optional["Account"]:
        """Parse account line in 'email,password' or 'email:password' format."""
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        parts = []
        if "," in line:
            parts = line.split(",", 1)
        elif ":" in line:
            parts = line.split(":", 1)
        else:
            return None

        email = parts[0].strip()
        password = parts[1].strip()
        if email and password:
            return cls(email=email, password=password)
        return None
