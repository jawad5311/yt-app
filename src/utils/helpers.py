import random
import time
from typing import Any


def human_like_delay(min_seconds: float = 0.5, max_seconds: float = 1.2) -> None:
    """Randomized sleep interval to mimic human behavior."""
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def slow_type(element: Any, text: str, min_delay: float = 0.02, max_delay: float = 0.06) -> None:
    """Type text character by character into selenium element with variable delays."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))


def sanitize_text(text: str) -> str:
    """Sanitize strings for ChromeDriver or terminal outputs."""
    if not text:
        return ""
    # Filter out non-BMP characters that chromedriver send_keys can fail on
    return "".join(c for c in text if ord(c) <= 0xFFFF)
