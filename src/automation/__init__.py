from .driver import setup_driver, close_driver
from .login import perform_google_login
from .navigation import navigate_to_video_organically
from .youtube_actions import watch_video
from .worker import AutomationWorker

__all__ = [
    "setup_driver",
    "close_driver",
    "perform_google_login",
    "navigate_to_video_organically",
    "watch_video",
    "AutomationWorker",
]
