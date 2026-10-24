from .dispatcher import TaskDispatcher
from .driver import close_driver, setup_driver
from .interactivity import (
    HumanInteractivitySimulator,
    get_playback_speed,
    set_playback_speed,
    set_video_quality,
    simulate_micro_pause,
    simulate_reading_comments_or_description,
)
from .login import perform_google_login
from .navigation import navigate_to_video_organically
from .watching_worker import WatchingWorker, match_channel
from .worker import AutomationWorker
from .youtube_actions import watch_video

__all__ = [
    "setup_driver",
    "close_driver",
    "perform_google_login",
    "navigate_to_video_organically",
    "watch_video",
    "AutomationWorker",
    "WatchingWorker",
    "match_channel",
    "TaskDispatcher",
    "HumanInteractivitySimulator",
    "set_playback_speed",
    "get_playback_speed",
    "set_video_quality",
    "simulate_reading_comments_or_description",
    "simulate_micro_pause",
]
