"""Unit tests for the two-step Stop confirmation with 5-second countdown timer.

Rules verified:
1. When watching a long video from a targeted channel and Stop is clicked:
   - Does NOT stop immediately.
   - Updates Stop button text to "Click again to stop (5s)" with countdown timer.
   - If clicked again within 5s, stops the session and triggers report generation.
   - If 5s window is missed, reverts button to "⏹ Stop" and session continues uninterrupted.
   - Subsequent click restarts the confirmation check.
2. When watching a random channel video: stops immediately without countdown.
3. When watching a Short: stops immediately without countdown.
4. When between videos or idle: stops immediately without countdown.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.automation.watching_worker import WatchingWorker
from src.automation.worker import AutomationWorker
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.ui.app import YTBotApp
from src.ui.theme import setup_ttk_styles
from src.ui.views.dashboard_view import DashboardView


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


class TestDashboardStopConfirmationFlow:
    """Test UI Stop button interaction and countdown state transitions."""

    def test_target_long_video_shows_countdown_on_first_click(self, tk_root):
        """Clicking Stop while a target long video is playing initiates 5s countdown and does not stop immediately."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = True

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)

        # Trigger first Stop click
        view.stop_btn.invoke()

        # Stop automation must NOT be called immediately
        controller.stop_automation.assert_not_called()

        # Countdown should be active and showing initial 5s text
        assert view.stop_countdown_active is True
        assert view.stop_btn.cget("text") == "Click again to stop (5s)"

    def test_target_long_video_second_click_confirms_stop(self, tk_root):
        """Clicking Stop a second time within the 5s window cancels timer and triggers stop_automation."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = True

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)

        # First click: activates countdown
        view.stop_btn.invoke()
        assert view.stop_countdown_active is True
        controller.stop_automation.assert_not_called()

        # Second click: confirms stop
        view.stop_btn.invoke()
        controller.stop_automation.assert_called_once()
        assert view.stop_countdown_active is False

    def test_countdown_ticks_and_decrements_remaining_time(self, tk_root):
        """Each tick decrements remaining seconds and updates button text down to 1s."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = True

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)
        view.stop_btn.invoke()

        assert view.stop_btn.cget("text") == "Click again to stop (5s)"

        # Simulate 1s tick
        view._stop_countdown_tick()
        assert view.stop_btn.cget("text") == "Click again to stop (4s)"
        assert view.stop_countdown_active is True

        view._stop_countdown_tick()
        assert view.stop_btn.cget("text") == "Click again to stop (3s)"

        view._stop_countdown_tick()
        assert view.stop_btn.cget("text") == "Click again to stop (2s)"

        view._stop_countdown_tick()
        assert view.stop_btn.cget("text") == "Click again to stop (1s)"
        assert view.stop_countdown_active is True

    def test_countdown_expiration_reverts_button_and_continues_playback(self, tk_root):
        """When the 5s timer expires without a second click, reverts to '⏹ Stop' and session continues."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = True

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)
        view.stop_btn.invoke()

        # Advance through all 5 seconds to expiration (0s)
        for _ in range(5):
            view._stop_countdown_tick()

        # Timer expired: countdown active is reset, button reverts to Stop
        assert view.stop_countdown_active is False
        assert view.stop_btn.cget("text") == "⏹ Stop"
        controller.stop_automation.assert_not_called()

        # A new click restarts the confirmation timer afresh
        view.stop_btn.invoke()
        assert view.stop_countdown_active is True
        assert view.stop_btn.cget("text") == "Click again to stop (5s)"
        controller.stop_automation.assert_not_called()

    def test_random_channel_video_stops_immediately_without_countdown(self, tk_root):
        """Clicking Stop while a random/non-target video is playing stops immediately."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = False

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)

        view.stop_btn.invoke()

        controller.stop_automation.assert_called_once()
        assert view.stop_countdown_active is False

    def test_short_video_stops_immediately_without_countdown(self, tk_root):
        """Clicking Stop while watching a Short stops immediately."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = False

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)

        view.stop_btn.invoke()

        controller.stop_automation.assert_called_once()
        assert view.stop_countdown_active is False

    def test_session_stopped_externally_cancels_active_countdown(self, tk_root):
        """If session stops while countdown is ticking, countdown is safely cancelled."""
        controller = MagicMock()
        controller.is_running = True
        controller.is_playing_target_long_video.return_value = True

        view = DashboardView(tk_root, controller)
        view.set_running_state(True)
        view.stop_btn.invoke()
        assert view.stop_countdown_active is True

        view.set_running_state(False)
        assert view.stop_countdown_active is False
        assert view.stop_btn.cget("text") == "⏹ Stop"


class TestWorkerPlaybackStateDetection:
    """Test worker playback state reporting (target long video vs random vs shorts)."""

    def test_watching_worker_is_playing_target_long_video(self):
        account = Account("user@test.com", "pass")
        channel = Channel(name="TechChannel", enabled=True)
        config_mgr = MagicMock()
        config_mgr.get_channels.return_value = [{"name": "TechChannel", "enabled": True}]
        config_mgr.get_bool.return_value = True

        worker = WatchingWorker(
            account=account,
            channels=[channel],
            config_mgr=config_mgr,
        )

        # 1. Idle / between videos: returns False
        assert worker.is_playing_target_long_video() is False

        # 2. Watching Short: returns False
        worker.current_playback = {
            "is_playing": True,
            "is_short": True,
            "is_target_channel": True,
            "channel": "TechChannel",
            "title": "Short Clip",
            "url": "https://www.youtube.com/shorts/abc",
        }
        assert worker.is_playing_target_long_video() is False

        # 3. Watching random non-target video: returns False
        worker.current_playback = {
            "is_playing": True,
            "is_short": False,
            "is_target_channel": False,
            "channel": "RandomChannel",
            "title": "Random Video",
            "url": "https://www.youtube.com/watch?v=xyz",
        }
        assert worker.is_playing_target_long_video() is False

        # 4. Watching target long video: returns True
        worker.current_playback = {
            "is_playing": True,
            "is_short": False,
            "is_target_channel": True,
            "channel": "TechChannel",
            "title": "Long Tech Video",
            "url": "https://www.youtube.com/watch?v=target123",
        }
        assert worker.is_playing_target_long_video() is True

        # 5. Stop event set: returns False
        worker.stop_event.set()
        assert worker.is_playing_target_long_video() is False

    def test_automation_worker_is_playing_target_long_video(self):
        account = Account("user@test.com", "pass")
        task = VideoTask("https://www.youtube.com/watch?v=abc", "Target Video", channel="TargetChannel")
        config_mgr = MagicMock()
        config_mgr.get_channels.return_value = [{"name": "TargetChannel", "enabled": True}]

        worker = AutomationWorker(
            account=account,
            video_tasks=[task],
            config_mgr=config_mgr,
        )

        assert worker.is_playing_target_long_video() is False

        # Shorts -> False
        worker.current_playback = {
            "is_playing": True,
            "is_short": True,
            "is_target_channel": True,
            "channel": "TargetChannel",
        }
        assert worker.is_playing_target_long_video() is False

        # Target long video -> True
        worker.current_playback = {
            "is_playing": True,
            "is_short": False,
            "is_target_channel": True,
            "channel": "TargetChannel",
        }
        assert worker.is_playing_target_long_video() is True

        worker.stop()
        assert worker.is_playing_target_long_video() is False

    def test_app_controller_queries_active_workers(self):
        app = YTBotApp.__new__(YTBotApp)
        app.is_running = True
        app._close_button_enabled = True

        mock_worker = MagicMock()
        mock_worker.is_playing_target_long_video.return_value = True
        app.active_workers = [mock_worker]

        assert app.is_playing_target_long_video() is True

        mock_worker.is_playing_target_long_video.return_value = False
        assert app.is_playing_target_long_video() is False

        app.is_running = False
        assert app.is_playing_target_long_video() is False
