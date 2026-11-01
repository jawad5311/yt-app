"""Regression tests for Issue 14:
Verify playback identity before recording video statistics.
"""

import queue
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.automation.navigation import navigate_to_video_organically
from src.automation.watching_worker import WatchingWorker
from src.config.manager import ConfigManager
from src.config.stats_store import StatsStore
from src.models.account import Account
from src.models.channel import Channel
from src.models.video_task import VideoTask
from src.models.watch_stats import TaskOutcome, WatchResult, resolve_video_id, resolve_video_key


class MockWebElement:
    def __init__(self, href="", text="", title=""):
        self._href = href
        self._text = text
        self._title = title
        self.clicked = False

    def get_attribute(self, name):
        if name == "href":
            return self._href
        if name == "title":
            return self._title
        return None

    @property
    def text(self):
        return self._text

    def click(self):
        self.clicked = True


class MockDriver:
    def __init__(self, current_url="https://www.youtube.com", search_results=None):
        self.current_url = current_url
        self.search_results = search_results or []
        self.navigated_urls = []

    def execute_script(self, script, *args):
        return None

    def find_elements(self, by, value):
        return []

    def get(self, url):
        self.navigated_urls.append(url)
        self.current_url = url


def test_url_variants_resolve_same_video_id():
    """5. Valid supported URL variants resolve to the same video ID."""
    vid = "dQw4w9WgXcQ"
    variants = [
        f"https://www.youtube.com/watch?v={vid}",
        f"https://www.youtube.com/watch?v={vid}&feature=share",
        f"https://youtu.be/{vid}",
        f"https://youtu.be/{vid}?t=10",
        f"https://www.youtube.com/embed/{vid}",
        f"https://www.youtube.com/shorts/{vid}",
        f"https://www.youtube.com/v/{vid}",
        vid,
    ]
    for url in variants:
        task = VideoTask(url=url, title="Test Video")
        assert resolve_video_id(task) == vid
        assert resolve_video_key(task) == vid


def test_search_rejects_matching_title_on_different_video_when_id_requested():
    """1. Requested video A, matching title on video B: B is rejected."""
    task = VideoTask(
        url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        title="My Special Title",
        keywords="special keyword",
    )
    # Search results contain video B (different ID) which happens to have the exact title
    elem_b = MockWebElement(
        href="https://www.youtube.com/watch?v=bbbbbbbbbbb",
        text="My Special Title",
        title="My Special Title",
    )

    driver = MockDriver(
        current_url="https://www.youtube.com/results?search_query=special+keyword",
        search_results=[elem_b],
    )

    log_q = queue.Queue()

    with patch("src.automation.navigation.WebDriverWait") as mock_wait, \
         patch("src.automation.navigation.safe_get") as mock_safe_get, \
         patch("src.automation.navigation.slow_type"), \
         patch("src.automation.navigation.human_like_delay"), \
         patch("src.automation.navigation.dismiss_youtube_dialogs"):

        search_box = MagicMock()
        # mock_wait().until: first returns search_box, then search results
        mock_wait.return_value.until.side_effect = [search_box, [elem_b]]

        success = navigate_to_video_organically(
            driver,
            task,
            direct_link_fallback=False,
            log_queue=log_q,
        )

        assert not success
        assert not elem_b.clicked


def test_search_rejects_empty_result_title():
    """3. Empty result title: rejected."""
    task = VideoTask(
        url="",
        title="My Special Title",
        keywords="special keyword",
    )
    elem_empty = MockWebElement(
        href="https://www.youtube.com/watch?v=bbbbbbbbbbb",
        text="",
        title="",
    )

    driver = MockDriver(
        current_url="https://www.youtube.com/results?search_query=special+keyword",
        search_results=[elem_empty],
    )

    log_q = queue.Queue()

    with patch("src.automation.navigation.WebDriverWait") as mock_wait, \
         patch("src.automation.navigation.safe_get"), \
         patch("src.automation.navigation.slow_type"), \
         patch("src.automation.navigation.human_like_delay"), \
         patch("src.automation.navigation.dismiss_youtube_dialogs"):

        search_box = MagicMock()
        mock_wait.return_value.until.side_effect = [search_box, [elem_empty]]

        success = navigate_to_video_organically(
            driver,
            task,
            direct_link_fallback=False,
            log_queue=log_q,
        )

        assert not success
        assert not elem_empty.clicked


def test_param_other_than_video_id_is_not_matched_as_video_id():
    """4. A URL containing requested ID in param other than video ID: rejected."""
    task = VideoTask(
        url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        title="My Target",
    )
    # Target ID is aaaaaaaaaaa, but link is video ccccccccccc with param ref=aaaaaaaaaaa
    elem_param = MockWebElement(
        href="https://www.youtube.com/watch?v=ccccccccccc&ref=aaaaaaaaaaa",
        text="Other Video",
    )

    driver = MockDriver(
        current_url="https://www.youtube.com/results?search_query=My+Target",
        search_results=[elem_param],
    )

    log_q = queue.Queue()

    with patch("src.automation.navigation.WebDriverWait") as mock_wait, \
         patch("src.automation.navigation.safe_get"), \
         patch("src.automation.navigation.slow_type"), \
         patch("src.automation.navigation.human_like_delay"), \
         patch("src.automation.navigation.dismiss_youtube_dialogs"):

        search_box = MagicMock()
        mock_wait.return_value.until.side_effect = [search_box, [elem_param]]

        success = navigate_to_video_organically(
            driver,
            task,
            direct_link_fallback=False,
            log_queue=log_q,
        )

        assert not success
        assert not elem_param.clicked


def test_navigation_verification_fails_if_page_remains_on_wrong_video():
    """2. Requested video A, current page remains video B after clicking: navigation fails."""
    target_id = "aaaaaaaaaaa"
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={target_id}",
        title="Target Video",
    )
    elem_target = MockWebElement(
        href=f"https://www.youtube.com/watch?v={target_id}",
        text="Target Video",
    )

    driver = MockDriver(
        current_url="https://www.youtube.com/watch?v=bbbbbbbbbbb",
        search_results=[elem_target],
    )

    log_q = queue.Queue()

    t_val = [100.0]

    def fake_time():
        t_val[0] += 5.0
        return t_val[0]

    with patch("src.automation.navigation.WebDriverWait") as mock_wait, \
         patch("src.automation.navigation.safe_get"), \
         patch("src.automation.navigation.slow_type"), \
         patch("src.automation.navigation.human_like_delay"), \
         patch("src.automation.navigation.dismiss_youtube_dialogs"), \
         patch("src.automation.navigation.time.time", side_effect=fake_time), \
         patch("src.automation.navigation.time.sleep"):

        search_box = MagicMock()
        mock_wait.return_value.until.side_effect = [search_box, [elem_target]]

        # Even though elem_target matched and was clicked, driver.current_url remains video B
        success = navigate_to_video_organically(
            driver,
            task,
            direct_link_fallback=False,
            log_queue=log_q,
        )

        assert elem_target.clicked
        assert not success


def test_direct_navigation_fallback_fails_if_landing_on_wrong_video():
    """7. Direct navigation ending on wrong video is rejected."""
    target_id = "aaaaaaaaaaa"
    task = VideoTask(
        url=f"https://www.youtube.com/watch?v={target_id}",
        title="Target Video",
    )

    # Driver current_url does not update to target_id upon safe_get
    driver = MockDriver(current_url="https://www.youtube.com/watch?v=wrongvideoid")
    log_q = queue.Queue()

    with patch("src.automation.navigation.safe_get") as mock_safe_get, \
         patch("src.automation.navigation.human_like_delay"):

        def fake_safe_get(d, url, **kwargs):
            # simulate landing on wrong video
            d.current_url = "https://www.youtube.com/watch?v=wrongvideoid"

        mock_safe_get.side_effect = fake_safe_get

        success = navigate_to_video_organically(
            driver,
            task,
            direct_link_fallback=True,
            log_queue=log_q,
            search_query_override="",  # skips organic, goes straight to fallback
        )

        assert not success


def test_title_only_task_records_resolved_id_and_alias(tmp_path: Path):
    """6. Title-only task that opens video B records B and remains findable through its task alias."""
    stats_file = tmp_path / "stats.json"
    store = StatsStore(stats_file)

    task = VideoTask(
        url="",
        title="Ambient Study Music",
    )

    outcome = TaskOutcome(
        task_key=resolve_video_key(task),
        video_key="bbbbbbbbbbb",
        label="Ambient Study Music",
        channel="LoFi Beats",
        watched_seconds=120.0,
        video_duration=300.0,
        success=True,
        playback_finished=True,
        attempts=1,
    )

    rec = store.record(outcome)
    assert rec is not None
    assert "bbbbbbbbbbb" in store.records
    assert "task:Ambient Study Music" in rec.task_keys

    # Find using original task without URL
    found = store.find_for_task(task)
    assert found is not None
    assert found.video_duration == 300.0
    assert found.runs == 1
    assert found.watch_seconds == 120.0


def test_watching_worker_aborts_on_playback_identity_mismatch(tmp_path: Path):
    """8. Failed navigation with stale current URL does not record watch time."""
    stats_file = tmp_path / "stats.json"
    store = StatsStore(stats_file)

    account = Account(email="test@example.com", password="secret")
    cfg = ConfigManager(tmp_path / "config.json")
    channels = [Channel(name="Target Channel", url="https://www.youtube.com/@TargetChannel", enabled=True)]

    worker = WatchingWorker(
        account=account,
        channels=channels,
        config_mgr=cfg,
        stats_store=store,
        log_queue=queue.Queue(),
    )

    target_id = "aaaaaaaaaaa"
    card = {
        "url": f"https://www.youtube.com/watch?v={target_id}",
        "title": "Target Video",
        "channel": "Target Channel",
    }

    # Driver is stuck on a stale video bbbbbbbbbbb
    driver = MockDriver(current_url="https://www.youtube.com/watch?v=bbbbbbbbbbb")
    worker.driver = driver

    with patch.object(worker, "click_home_video", return_value=True), \
         patch("src.automation.watching_worker.safe_get") as mock_safe_get, \
         patch("src.automation.watching_worker.WebDriverWait"), \
         patch("src.automation.watching_worker.watch_video") as mock_watch:

        # safe_get does not fix the URL (stuck on bbbbbbbbbbb)
        res = worker._play_target_video(
            card_info=card,
            full_url=card["url"],
            title=card["title"],
            channel_display=card["channel"],
            session_watched_keys=set(),
            is_home=True,
        )

        assert not res.completed
        assert res.interrupted
        assert res.reason == "navigation_identity_mismatch"
        # Playback must NOT have started
        mock_watch.assert_not_called()
        # Stats must NOT have been recorded
        assert store.lifetime_totals().watch_seconds == 0.0
        assert store.lifetime_totals().runs == 0


def test_watching_worker_records_watch_time_on_valid_match(tmp_path: Path):
    """9. Valid match records watch time once."""
    stats_file = tmp_path / "stats.json"
    store = StatsStore(stats_file)

    account = Account(email="test@example.com", password="secret")
    cfg = ConfigManager(tmp_path / "config.json")
    channels = [Channel(name="Target Channel", url="https://www.youtube.com/@TargetChannel", enabled=True)]

    worker = WatchingWorker(
        account=account,
        channels=channels,
        config_mgr=cfg,
        stats_store=store,
        log_queue=queue.Queue(),
    )

    target_id = "aaaaaaaaaaa"
    card = {
        "url": f"https://www.youtube.com/watch?v={target_id}",
        "title": "Target Video",
        "channel": "Target Channel",
    }

    # Driver lands on target video
    driver = MockDriver(current_url=f"https://www.youtube.com/watch?v={target_id}")
    worker.driver = driver

    watch_res = WatchResult(
        completed=True,
        playback_finished=True,
        watched_seconds=180.0,
        video_duration=200.0,
        ad_seconds=10.0,
    )

    with patch.object(worker, "click_home_video", return_value=True), \
         patch("src.automation.watching_worker.watch_video", return_value=watch_res) as mock_watch, \
         patch("src.automation.watching_worker.dismiss_youtube_dialogs"), \
         patch("src.automation.watching_worker.ensure_video_playing"), \
         patch("src.automation.watching_worker.human_like_delay"):

        res = worker._play_target_video(
            card_info=card,
            full_url=card["url"],
            title=card["title"],
            channel_display=card["channel"],
            session_watched_keys=set(),
            is_home=True,
        )

        assert res.completed
        mock_watch.assert_called_once()
        totals = store.lifetime_totals()
        assert totals.watch_seconds == 180.0
        assert totals.runs == 1
        assert totals.videos == 1
