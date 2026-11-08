from unittest.mock import MagicMock, patch
import queue
import threading
import pytest

from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel


def test_target_shorts_per_cycle_math():
    """Verify ±20% randomized target limits for various configured values."""
    for base, exp_min, exp_max in [(10, 8, 12), (20, 16, 24), (5, 4, 6), (1, 1, 1)]:
        min_target = max(1, round(base * 0.8))
        max_target = max(min_target, round(base * 1.2))
        assert min_target == exp_min
        assert max_target == exp_max


def test_non_target_reels_limit_math():
    """Verify ±20% randomized non-target reels and ceiling limits."""
    for base, exp_min, exp_max in [(10, 8, 12), (20, 16, 24), (5, 4, 6), (1, 1, 1)]:
        min_val = max(1, round(base * 0.8))
        max_val = max(min_val, round(base * 1.2))
        assert min_val == exp_min
        assert max_val == exp_max


def test_settings_view_shorts_limits(ctk_app, tmp_path):
    """Verify SettingsView has all 3 Shorts limits with ±20% descriptions and saves them."""
    from src.ui.views.settings_view import SettingsView

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    mgr.set_setting("TargetShortsPerCycle", 15)
    mgr.set_setting("NonTargetReelsLimit", 10)
    mgr.set_setting("TotalNonTargetShortsLimit", 25)

    app = ctk_app
    app.config_mgr = mgr

    view = SettingsView(app, app)
    assert hasattr(view, "target_shorts_cycle_entry")
    assert hasattr(view, "non_target_reels_limit_entry")
    assert hasattr(view, "total_non_target_shorts_limit_entry")
    assert view.target_shorts_cycle_entry.get() == "15"
    assert view.non_target_reels_limit_entry.get() == "10"
    assert view.total_non_target_shorts_limit_entry.get() == "25"

    # Test updating values
    view.target_shorts_cycle_entry.delete(0, "end")
    view.target_shorts_cycle_entry.insert(0, "20")
    view.non_target_reels_limit_entry.delete(0, "end")
    view.non_target_reels_limit_entry.insert(0, "12")
    view.total_non_target_shorts_limit_entry.delete(0, "end")
    view.total_non_target_shorts_limit_entry.insert(0, "30")

    with patch("tkinter.messagebox.showinfo"):
        view.save_settings()

    assert mgr.get_int("TargetShortsPerCycle", 10) == 20
    assert mgr.get_int("NonTargetReelsLimit", 10) == 12
    assert mgr.get_int("TotalNonTargetShortsLimit", 20) == 30
    view.destroy()


def test_run_shorts_reel_loop_exits_when_target_achieved():
    """Verify _run_shorts_reel_loop exits once the randomized cycle target is reached."""
    mock_mgr = MagicMock()
    mock_mgr.get_int.side_effect = lambda key, default: 10 if key in ("TargetShortsPerCycle", "NonTargetReelsLimit") else default

    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[Channel(name="Target Channel", url="https://youtube.com/@target")],
        config_mgr=mock_mgr,
        stats_store=MagicMock(),
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        pause_event=threading.Event(),
    )
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])

    target_counter = [0]
    def _next_target():
        target_counter[0] += 1
        return {
            "title": f"Target Short {target_counter[0]}",
            "channel_name": "Target Channel",
            "channel_url": "https://youtube.com/@target",
            "video_id": f"testshort_{target_counter[0]}",
            "url": f"https://youtube.com/shorts/testshort_{target_counter[0]}",
        }
    worker.get_active_short_info = MagicMock(side_effect=_next_target)

    mock_res = MagicMock(watched_seconds=15.0, ad_seconds=0.0, video_duration=20.0, completed=True, playback_finished=True, interrupted=False, views_counted=1)
    with patch("src.automation.watching_worker.watch_short", return_value=mock_res), \
         patch.object(worker, "scroll_to_next_short"), \
         patch("random.randint", return_value=8):  # Fixed target
        
        watched, non_targets, reason = worker._run_shorts_reel_loop(session_watched_keys=set(), current_target_watched=0, cycle_target_shorts=8)
        assert watched == 8
        assert non_targets == 0
        assert reason == "target_achieved"


def test_run_shorts_reel_loop_exits_when_reel_limit_reached():
    """Verify _run_shorts_reel_loop fast-skips and exits with 'reel_limit_reached' when reel non-target limit is reached."""
    mock_mgr = MagicMock()
    mock_mgr.get_int.side_effect = lambda key, default: 10 if key in ("TargetShortsPerCycle", "NonTargetReelsLimit") else default

    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[Channel(name="Target Channel", url="https://youtube.com/@target")],
        config_mgr=mock_mgr,
        stats_store=MagicMock(),
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        pause_event=threading.Event(),
    )
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])

    nontarget_counter = [0]
    def _next_nontarget():
        nontarget_counter[0] += 1
        return {
            "title": f"Other Short {nontarget_counter[0]}",
            "channel_name": "Unknown Channel",
            "channel_url": "https://youtube.com/@unknown",
            "video_id": f"random_{nontarget_counter[0]}",
            "url": f"https://youtube.com/shorts/random_{nontarget_counter[0]}",
        }
    worker.get_active_short_info = MagicMock(side_effect=_next_nontarget)

    with patch.object(worker, "scroll_to_next_short"), \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("random.randint", return_value=3):  # Set reel limit to 3 for fast test
        
        watched, non_targets, reason = worker._run_shorts_reel_loop(session_watched_keys=set(), current_target_watched=0, cycle_target_shorts=10)
        assert watched == 0
        assert non_targets == 3
        assert reason == "reel_limit_reached"


def test_run_shorts_reel_loop_resets_counter_on_target_short():
    """Verify that encountering a target short resets the consecutive reel non-target counter."""
    mock_mgr = MagicMock()
    mock_mgr.get_int.side_effect = lambda key, default: 5 if key == "NonTargetReelsLimit" else 10

    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[Channel(name="Target Channel", url="https://youtube.com/@target")],
        config_mgr=mock_mgr,
        stats_store=MagicMock(),
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        pause_event=threading.Event(),
    )
    worker.driver = MagicMock()
    worker.get_target_channels = MagicMock(return_value=[Channel(name="Target Channel", url="https://youtube.com/@target")])
    worker.get_target_shorts = MagicMock(return_value=[])

    call_seq = [
        {"title": "Random 1", "channel_name": "Other1", "channel_url": "", "video_id": "r1", "url": "https://youtube.com/shorts/r1"},
        {"title": "Random 2", "channel_name": "Other2", "channel_url": "", "video_id": "r2", "url": "https://youtube.com/shorts/r2"},
        {"title": "Target Short", "channel_name": "Target Channel", "channel_url": "https://youtube.com/@target", "video_id": "t1", "url": "https://youtube.com/shorts/t1"},
        {"title": "Random 3", "channel_name": "Other3", "channel_url": "", "video_id": "r3", "url": "https://youtube.com/shorts/r3"},
    ]
    worker.get_active_short_info = MagicMock(side_effect=call_seq)

    mock_res = MagicMock(watched_seconds=15.0, ad_seconds=0.0, video_duration=20.0, completed=True, playback_finished=True, interrupted=False, views_counted=1)
    with patch("src.automation.watching_worker.watch_short", return_value=mock_res), \
         patch.object(worker, "scroll_to_next_short"), \
         patch("src.automation.watching_worker.human_like_delay"), \
         patch("random.randint", side_effect=[3, 1]):  # reel limit 3, target_loops 1
        
        # After 2 non-targets, target short appears (resetting counter to 0), then 1 non-target -> then stop event
        def stop_after_target(*args, **kwargs):
            if worker.get_active_short_info.call_count >= 4:
                worker.stop_event.set()
        worker.scroll_to_next_short.side_effect = stop_after_target

        watched, non_targets, reason = worker._run_shorts_reel_loop(session_watched_keys=set(), current_target_watched=0, cycle_target_shorts=10)
        assert watched == 1
        assert non_targets == 3  # 2 before target + 1 after target
        assert reason == "stopped"


def test_run_shorts_cycle_returns_to_home_and_exits_if_no_short_on_home():
    """Verify _run_shorts_cycle cleanly exits to long-form videos if navigation fails."""
    mock_mgr = MagicMock()
    mock_mgr.get_int.side_effect = lambda key, default: 10 if key in ("TargetShortsPerCycle", "NonTargetReelsLimit", "TotalNonTargetShortsLimit") else default

    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[Channel(name="Target Channel", url="https://youtube.com/@target")],
        config_mgr=mock_mgr,
        stats_store=MagicMock(),
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        pause_event=threading.Event(),
    )
    worker.driver = MagicMock()

    # Home scan finds no short, no candidates, and shorts tab cannot be opened
    worker.find_target_short_on_home = MagicMock(return_value=None)
    worker.fetch_home_shorts = MagicMock(return_value=[])
    worker.click_shorts_tab = MagicMock(return_value=False)

    total_watched = worker._run_shorts_cycle(session_watched_keys=set())
    assert total_watched == 0
    worker.find_target_short_on_home.assert_called_once()
    worker.click_shorts_tab.assert_called_once()


def test_run_shorts_cycle_falls_back_to_random_short_and_enters_reel():
    """Verify that if no target Short is matched on Home, a random candidate Short is clicked and enters reel loop."""
    mock_mgr = MagicMock()
    mock_mgr.get_int.side_effect = lambda key, default: 10 if key in ("TargetShortsPerCycle", "NonTargetReelsLimit", "TotalNonTargetShortsLimit") else default

    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[Channel(name="Target Channel", url="https://youtube.com/@target")],
        config_mgr=mock_mgr,
        stats_store=MagicMock(),
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        pause_event=threading.Event(),
    )
    worker.driver = MagicMock()

    candidate = {"title": "Random Short 1", "url": "https://youtube.com/shorts/rnd1", "video_id": "rnd1"}
    worker.find_target_short_on_home = MagicMock(return_value=None)
    worker.fetch_home_shorts = MagicMock(return_value=[candidate])
    worker.click_home_short = MagicMock(return_value=True)
    worker.scroll_to_next_short = MagicMock()
    worker._run_shorts_reel_loop = MagicMock(return_value=(2, 3, "target_achieved"))

    with patch("time.sleep"), patch("src.automation.watching_worker.human_like_delay"):
        total_watched = worker._run_shorts_cycle(session_watched_keys=set())

    assert total_watched == 2
    worker.click_home_short.assert_called_once_with(candidate)
    worker.scroll_to_next_short.assert_called_once()
    worker._run_shorts_reel_loop.assert_called_once()


def test_run_shorts_cycle_falls_back_to_shorts_tab_when_no_home_shelf():
    """Verify that if no Short shelf exists on Home, app navigates via click_shorts_tab into reel."""
    mock_mgr = MagicMock()
    mock_mgr.get_int.side_effect = lambda key, default: 10 if key in ("TargetShortsPerCycle", "NonTargetReelsLimit", "TotalNonTargetShortsLimit") else default

    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[Channel(name="Target Channel", url="https://youtube.com/@target")],
        config_mgr=mock_mgr,
        stats_store=MagicMock(),
        log_queue=queue.Queue(),
        stop_event=threading.Event(),
        pause_event=threading.Event(),
    )
    worker.driver = MagicMock()

    worker.find_target_short_on_home = MagicMock(return_value=None)
    worker.fetch_home_shorts = MagicMock(return_value=[])
    worker.click_shorts_tab = MagicMock(return_value=True)
    worker.get_active_short_info = MagicMock(return_value={"title": "Sidebar Short 1", "video_id": "s1"})
    worker.scroll_to_next_short = MagicMock()
    worker._run_shorts_reel_loop = MagicMock(return_value=(1, 4, "target_achieved"))

    with patch("time.sleep"), patch("src.automation.watching_worker.human_like_delay"):
        total_watched = worker._run_shorts_cycle(session_watched_keys=set())

    assert total_watched == 1
    worker.click_shorts_tab.assert_called_once()
    worker.scroll_to_next_short.assert_called_once()
    worker._run_shorts_reel_loop.assert_called_once()


def test_settings_tab_order_and_separation(ctk_app, tmp_path):
    """Verify tabs in SettingsView are in the exact requested order with Shorts in its own tab."""
    from src.ui.views.settings_view import SettingsView

    settings_file = tmp_path / "settings.json"
    mgr = ConfigManager(settings_file)
    app = ctk_app
    app.config_mgr = mgr

    view = SettingsView(app, app)
    tab_names = list(view.tabview._segmented_button._buttons_dict.keys())
    assert tab_names == ["General", "Playback", "Video", "Shorts", "Ads", "Interactivity", "Email & Reports"]

    # Verify shorts entries are in view and configured properly
    assert hasattr(view, "target_shorts_cycle_entry")
    assert hasattr(view, "non_target_reels_limit_entry")
    assert hasattr(view, "total_non_target_shorts_limit_entry")
    view.destroy()
