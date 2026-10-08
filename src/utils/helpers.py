import random
import re
import time
from typing import Any, Optional


def human_like_delay(min_seconds: float = 0.5, max_seconds: float = 1.2) -> None:
    """Randomized sleep interval to mimic human behavior."""
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def slow_type(element: Any, text: str, min_delay: float = 0.02, max_delay: float = 0.06) -> None:
    """Type text character by character into selenium element with variable delays."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))


def extract_video_id(url: Optional[str]) -> Optional[str]:
    """Extract YouTube video ID from URL string."""
    if not url:
        return None
    patterns = [
        r"(?:v=|\/vi\/|youtu\.be\/|\/v\/|\/embed\/|\/shorts\/)([A-Za-z0-9_\-]{11})",
        r"^([A-Za-z0-9_\-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def sanitize_text(text: str) -> str:
    """Sanitize strings for ChromeDriver or terminal outputs."""
    if not text:
        return ""
    # Filter out non-BMP characters that chromedriver send_keys can fail on
    return "".join(c for c in text if ord(c) <= 0xFFFF)
