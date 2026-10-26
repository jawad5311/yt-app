import hashlib
from dataclasses import dataclass
from typing import Optional


def normalize_account_id(email: str) -> str:
    """Establish one account-identifier normalization policy:
    - Trim surrounding whitespace
    - Treat casing consistently (lowercase)
    - Do not remove dots, plus-address suffixes, or other meaningful characters
    """
    if not email:
        return ""
    return email.strip().lower()


@dataclass
class Account:
    """Represents a Google Account used for YouTube automation."""
    email: str
    password: str
    status: str = "Ready"

    def __post_init__(self) -> None:
        self.email = normalize_account_id(self.email)

    @property
    def normalized_email(self) -> str:
        return self.email

    def __repr__(self) -> str:
        return f"Account(email={self.email!r}, status={self.status!r})"

    def __str__(self) -> str:
        return f"Account({self.email})"

    def get_profile_dir_name(self) -> str:
        """Return a stable, collision-resistant folder name for Chrome user data.

        Distinct emails that previously produced the same sanitized prefix
        receive distinct profile folder names via deterministic hashing.
        """
        safe_email = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self.email)
        digest = hashlib.sha256(self.email.encode("utf-8")).hexdigest()[:8]
        return f"profile_{safe_email}_{digest}"

    def get_legacy_profile_dir_name(self) -> str:
        """Return legacy folder name for backwards compatibility."""
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
