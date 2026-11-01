from .browsing_worker import BrowsingWorker
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
from .navigation import (
    find_and_click_search_result,
    find_and_click_suggested_video,
    find_channel_video_on_home,
    navigate_to_video_organically,
    perform_youtube_search,
)
from .watching_worker import WatchingWorker, match_channel
from .worker import AutomationWorker
from .youtube_actions import ensure_video_playing, watch_short, watch_video

__all__ = [
    "setup_driver",
    "close_driver",
    "perform_google_login",
    "navigate_to_video_organically",
    "perform_youtube_search",
    "find_and_click_search_result",
    "find_channel_video_on_home",
    "find_and_click_suggested_video",
    "watch_video",
    "watch_short",
    "ensure_video_playing",
    "AutomationWorker",
    "BrowsingWorker",
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
