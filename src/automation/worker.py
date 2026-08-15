import logging
import queue
import threading
from typing import List, Optional

from ..config.manager import ConfigManager
from ..models.account import Account
from ..models.video_task import VideoTask
from ..ai.base import BaseAIProvider
from ..ai.gemini_provider import GeminiAIProvider
from ..ai.openai_provider import OpenAIProvider
from ..ai.openrouter_provider import OpenRouterProvider
from .driver import setup_driver, close_driver
from .login import perform_google_login
from .navigation import navigate_to_video_organically
from .youtube_actions import perform_youtube_interactions
from ..utils.logger import emit


class AutomationWorker:
    """Worker thread manager to execute automation tasks across video tasks for an account."""

    def __init__(
        self,
        account: Account,
        video_tasks: List[VideoTask],
        config_mgr: ConfigManager,
        log_queue: Optional[queue.Queue] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.account = account
        self.video_tasks = video_tasks
        self.config_mgr = config_mgr
        self.log_queue = log_queue
        self.stop_event = stop_event or threading.Event()
        self.driver = None

    def _init_ai_provider(self) -> Optional[BaseAIProvider]:
        provider_type = self.config_mgr.get_setting("AIProvider", "Gemini")
        gemini_key = self.config_mgr.get_setting("GeminiKey", "")
        openai_key = self.config_mgr.get_setting("OpenAIKey", "")
        openrouter_key = self.config_mgr.get_setting("OpenRouterKey", "")

        if provider_type == "OpenRouter" and openrouter_key:
            return OpenRouterProvider(openrouter_key)
        elif provider_type == "Gemini" and gemini_key:
            return GeminiAIProvider(gemini_key)
        elif provider_type == "OpenAI" and openai_key:
            return OpenAIProvider(openai_key)
        elif openrouter_key:
            return OpenRouterProvider(openrouter_key)
        elif gemini_key:
            return GeminiAIProvider(gemini_key)
        elif openai_key:
            return OpenAIProvider(openai_key)
        return None

    def run(self) -> None:
        """Main execution flow for account thread."""
        emit(self.log_queue, f"Starting automation session for account: {self.account.email}")
        ai_provider = self._init_ai_provider()
        direct_link_fallback = self.config_mgr.get_bool("DirectLinkFallback", True)

        try:
            self.driver = setup_driver(self.account, log_queue=self.log_queue)

            if self.stop_event.is_set():
                return

            # Sign-in phase
            login_ok = perform_google_login(
                self.driver,
                self.account,
                log_queue=self.log_queue,
                stop_event=self.stop_event,
            )

            if not login_ok:
                emit(self.log_queue, f"Skipping video tasks for {self.account.email} due to failed login.", logging.ERROR)
                return

            # Iterate through video tasks
            for idx, task in enumerate(self.video_tasks, 1):
                if self.stop_event.is_set():
                    emit(self.log_queue, "Automation stopped by user.")
                    break

                emit(self.log_queue, f"\n--- Processing Video Task {idx}/{len(self.video_tasks)}: {task.title or task.keywords or task.url} ---")

                # Navigate organically first!
                nav_ok = navigate_to_video_organically(
                    self.driver,
                    task,
                    direct_link_fallback=direct_link_fallback,
                    log_queue=self.log_queue,
                )

                if not nav_ok:
                    emit(self.log_queue, f"Could not navigate to video task #{idx}. Skipping.", logging.WARNING)
                    continue

                # Interact
                perform_youtube_interactions(
                    self.driver,
                    task,
                    ai_provider=ai_provider,
                    log_queue=self.log_queue,
                    stop_event=self.stop_event,
                )

            emit(self.log_queue, f"Session completed for {self.account.email}!")

        except Exception as exc:
            emit(self.log_queue, f"Critical error during worker session for {self.account.email}: {exc}", logging.ERROR)
        finally:
            close_driver(self.driver, log_queue=self.log_queue)
