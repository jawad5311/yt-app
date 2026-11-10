from .browsing_worker import BrowsingWorker
from .dispatcher import TaskDispatcher
from .driver import close_driver, setup_driver
from .interactivity import (
    HumanInteractivitySimulator,
    enter_fullscreen_mode,
    enter_theater_mode,
    exit_fullscreen_mode,
    exit_theater_mode,
    get_playback_speed,
    is_in_fullscreen,
    is_in_theater,
    is_short_liked,
    like_active_short,
    set_playback_speed,
    set_video_quality,
    simulate_micro_pause,
    simulate_reading_comments_or_description,
)
from .login import check_account_logged_in, detect_manual_challenge, perform_google_login
from .navigation import (
    find_and_click_search_result,
    find_and_click_suggested_video,
    find_channel_video_on_home,
    navigate_to_video_organically,
    perform_youtube_search,
)
from .watching_worker import WatchingWorker, match_channel
from .worker import AutomationWorker
from .youtube_actions import ensure_video_at_start, ensure_video_playing, watch_short, watch_video

__all__ = [
    "setup_driver",
    "close_driver",
    "perform_google_login",
    "check_account_logged_in",
    "detect_manual_challenge",
    "navigate_to_video_organically",
    "perform_youtube_search",
    "find_and_click_search_result",
    "find_channel_video_on_home",
    "find_and_click_suggested_video",
    "watch_video",
    "watch_short",
    "like_active_short",
    "is_short_liked",
    "ensure_video_playing",
    "ensure_video_at_start",
    "AutomationWorker",
    "BrowsingWorker",
    "WatchingWorker",
    "match_channel",
    "TaskDispatcher",
    "HumanInteractivitySimulator",
    "enter_fullscreen_mode",
    "exit_fullscreen_mode",
    "is_in_fullscreen",
    "enter_theater_mode",
    "exit_theater_mode",
    "is_in_theater",
    "set_playback_speed",
    "get_playback_speed",
    "set_video_quality",
    "simulate_reading_comments_or_description",
    "simulate_micro_pause",
]
