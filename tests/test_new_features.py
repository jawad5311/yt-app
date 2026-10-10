import threading
import time
from typing import List

import pytest

from src.automation.dispatcher import TaskDispatcher
from src.automation.navigation import extract_video_id, navigate_to_video_organically
from src.models.channel import Channel, is_video_task_runnable
from src.models.video_task import VideoTask
from src.utils.helpers import clean_youtube_url


# ============================================================================
# 1. Check/Uncheck or Enable/Disable
# ============================================================================

def test_video_task_enabled_defaults_and_serialization():
    # Defaults to True
    task = VideoTask(title="My Video")
    assert task.enabled is True
    data = task.to_dict()
    assert data["enabled"] is True

    # Roundtrip with enabled=False
    task_disabled = VideoTask(title="Disabled Video", enabled=False)
    restored = VideoTask.from_dict(task_disabled.to_dict())
    assert restored.enabled is False

    # Tolerates string representation in from_dict
    assert VideoTask.from_dict({"enabled": "false"}).enabled is False
    assert VideoTask.from_dict({"enabled": "true"}).enabled is True
    assert VideoTask.from_dict({}).enabled is True


def test_channel_enabled_defaults_and_runnable_filtering():
    ch_active = Channel(name="Gaming", enabled=True)
    ch_disabled = Channel(name="Music", enabled=False)

    # 1. Uncategorized video: runnable depends solely on video task enabled
    t_uncategorized_enabled = VideoTask(title="Loose 1", enabled=True)
    t_uncategorized_disabled = VideoTask(title="Loose 2", enabled=False)
    assert is_video_task_runnable(t_uncategorized_enabled, [ch_active, ch_disabled]) is True
    assert is_video_task_runnable(t_uncategorized_disabled, [ch_active, ch_disabled]) is False

    # 2. Video assigned to active channel: runnable if task is enabled
    t_gaming_enabled = VideoTask(title="Game 1", channel="Gaming", enabled=True)
    t_gaming_disabled = VideoTask(title="Game 2", channel="Gaming", enabled=False)
    assert is_video_task_runnable(t_gaming_enabled, [ch_active, ch_disabled]) is True
    assert is_video_task_runnable(t_gaming_disabled, [ch_active, ch_disabled]) is False

    # 3. Video assigned to disabled channel: NEVER runnable, even if task.enabled is True
    t_music_enabled = VideoTask(title="Song 1", channel="Music", enabled=True)
    t_music_disabled = VideoTask(title="Song 2", channel="Music", enabled=False)
    assert is_video_task_runnable(t_music_enabled, [ch_active, ch_disabled]) is False
    assert is_video_task_runnable(t_music_disabled, [ch_active, ch_disabled]) is False


# ============================================================================
# 2. Random Video Assignment & Non-Overlapping Concurrency
# ============================================================================

def test_task_dispatcher_random_and_non_overlapping():
    tasks = [
        VideoTask(url=f"https://www.youtube.com/watch?v=videoID{i:05d}", title=f"Video {i}")
        for i in range(1, 6)
    ]
    dispatcher = TaskDispatcher(tasks=tasks, randomize=True)

    # Two parallel workers
    count_0 = dispatcher.register_worker(worker_id=0)
    count_1 = dispatcher.register_worker(worker_id=1)
    assert count_0 == 5
    assert count_1 == 5

    currently_playing = set()
    collision_detected = False
    processed_by_worker = {0: [], 1: []}
    lock = threading.Lock()

    def run_simulated_worker(worker_id: int):
        nonlocal collision_detected
        while True:
            task = dispatcher.acquire_next_task(worker_id, poll_interval=0.05)
            if task is None:
                break

            with lock:
                v_id = extract_video_id(task.url)
                if v_id in currently_playing:
                    collision_detected = True
                currently_playing.add(v_id)

            # Simulate playback duration
            time.sleep(0.02)

            with lock:
                currently_playing.discard(v_id)
                processed_by_worker[worker_id].append(v_id)

            dispatcher.release_task(task)

    t0 = threading.Thread(target=run_simulated_worker, args=(0,))
    t1 = threading.Thread(target=run_simulated_worker, args=(1,))
    t0.start()
    t1.start()
    t0.join()
    t1.join()

    assert not collision_detected, "Two parallel browser workers played the same video at the same time!"
    assert len(processed_by_worker[0]) == 5
    assert len(processed_by_worker[1]) == 5


def test_task_dispatcher_waits_when_all_remaining_candidates_are_busy():
    # Only 1 video task, but 2 workers
    single_task = [VideoTask(url="https://www.youtube.com/watch?v=soleVideo01", title="Sole Video")]
    dispatcher = TaskDispatcher(tasks=single_task, randomize=False)

    dispatcher.register_worker(worker_id=0)
    dispatcher.register_worker(worker_id=1)

    # Worker 0 acquires the task
    t_w0 = dispatcher.acquire_next_task(worker_id=0)
    assert t_w0 is not None
    assert extract_video_id(t_w0.url) == "soleVideo01"

    # Worker 1 tries to acquire with a stop event to test that it doesn't get the same video
    stop_event = threading.Event()
    stop_event.set()
    t_w1 = dispatcher.acquire_next_task(worker_id=1, stop_event=stop_event)
    assert t_w1 is None

    # Once Worker 0 releases, Worker 1 (without stop event) can acquire it
    dispatcher.release_task(t_w0)
    t_w1_active = dispatcher.acquire_next_task(worker_id=1)
    assert t_w1_active is not None
    assert extract_video_id(t_w1_active.url) == "soleVideo01"
    dispatcher.release_task(t_w1_active)


# ============================================================================
# 3. Link Cleaning
# ============================================================================

@pytest.mark.parametrize(
    "raw_url,expected_clean",
    [
        (
            "https://www.youtube.com/watch?v=gwUxHevLVNE&t=32s",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "https://www.youtube.com/watch?v=gwUxHevLVNE&ab_channel=SomeChannel&list=PL123&index=2&t=120s",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "https://youtu.be/gwUxHevLVNE?si=AbCdEfGh1234&t=15",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "https://m.youtube.com/watch?v=gwUxHevLVNE&pp=ygU12345",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "https://www.youtube.com/shorts/gwUxHevLVNE?feature=share",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "https://www.youtube.com/live/gwUxHevLVNE?feature=share",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "gwUxHevLVNE",
            "https://www.youtube.com/watch?v=gwUxHevLVNE",
        ),
        (
            "",
            "",
        ),
        (
            "   https://example.com/not-youtube   ",
            "https://example.com/not-youtube",
        ),
    ],
)
def test_clean_youtube_url(raw_url, expected_clean):
    assert clean_youtube_url(raw_url) == expected_clean


# ============================================================================
# 4. Three Title Keywords Lines & Retry Query Resolution
# ============================================================================

def test_video_task_three_keyword_lines():
    task = VideoTask(
        title="Main Title",
        keywords="Keyword Line 1",
        keywords_2="Keyword Line 2",
        keywords_3="Keyword Line 3",
    )
    assert task.get_keyword_queries() == ["Keyword Line 1", "Keyword Line 2", "Keyword Line 3"]

    # Roundtrip serialization
    restored = VideoTask.from_dict(task.to_dict())
    assert restored.keywords == "Keyword Line 1"
    assert restored.keywords_2 == "Keyword Line 2"
    assert restored.keywords_3 == "Keyword Line 3"
    assert restored.get_keyword_queries() == ["Keyword Line 1", "Keyword Line 2", "Keyword Line 3"]


def test_video_task_legacy_multiline_split():
    legacy_data = {
        "title": "Main Title",
        "keywords": "Line 1\nLine 2\nLine 3",
    }
    task = VideoTask.from_dict(legacy_data)
    assert task.keywords == "Line 1"
    assert task.keywords_2 == "Line 2"
    assert task.keywords_3 == "Line 3"
    assert task.get_keyword_queries() == ["Line 1", "Line 2", "Line 3"]


def test_video_task_fallback_to_title_when_no_keywords():
    task = VideoTask(title="Only Title Given", keywords="", keywords_2="", keywords_3="")
    assert task.get_keyword_queries() == ["Only Title Given"]


# ============================================================================
# 5. Organic Navigation Matching: URL match prioritized over Title
# ============================================================================

class MockElement:
    def __init__(self, href: str, title: str, text: str = ""):
        self._href = href
        self._title = title
        self._text = text
        self.clicked = False

    def get_attribute(self, name: str) -> str:
        if name == "href":
            return self._href
        if name == "title":
            return self._title
        return ""

    @property
    def text(self) -> str:
        return self._text

    def click(self):
        self.clicked = True


class MockSearchBox:
    def click(self): pass
    def clear(self): pass
    def send_keys(self, *args): pass


class MockDriver:
    current_url = "https://www.youtube.com/results?search_query=test"

    def __init__(self, results: List[MockElement]):
        self.results = results

    def set_page_load_timeout(self, timeout): pass
    def get(self, url): pass
    def find_elements(self, by, value): return []
    def execute_script(self, script, *args): pass


def test_navigation_prioritizes_url_over_title(monkeypatch):
    """Ensure that if Result #1 matches Title but Result #2 matches exact Video URL/ID,
    Result #2 is selected because URL matching is prioritized on pass 1."""
    # Result 1 has title "Target Title", but wrong video ID "WRONG_ID_001"
    elem_wrong_id_right_title = MockElement(
        href="https://www.youtube.com/watch?v=WRONG_ID_001",
        title="Target Title",
    )
    # Result 2 has different title, but correct video ID "TARGET_ID_99"
    elem_right_id = MockElement(
        href="https://www.youtube.com/watch?v=TARGET_ID_99",
        title="Another Title Mentioning Podcast",
    )

    mock_driver = MockDriver([elem_wrong_id_right_title, elem_right_id])
    task = VideoTask(
        url="https://www.youtube.com/watch?v=TARGET_ID_99",
        title="Target Title",
        keywords="Target Search Query",
    )

    from selenium.webdriver.support.ui import WebDriverWait
    monkeypatch.setattr("src.automation.navigation.safe_get", lambda *a, **k: None)
    monkeypatch.setattr("src.automation.navigation.human_like_delay", lambda *a, **k: None)
    monkeypatch.setattr("src.automation.navigation.slow_type", lambda *a, **k: None)

    until_calls = {"count": 0}

    def fake_until(self, ec):
        until_calls["count"] += 1
        if until_calls["count"] == 1:
            return MockSearchBox()
        return mock_driver.results

    monkeypatch.setattr(WebDriverWait, "until", fake_until)

    success = navigate_to_video_organically(mock_driver, task, direct_link_fallback=False)
    assert success is True
    # Crucial check: elem_right_id was clicked, NOT elem_wrong_id_right_title!
    assert elem_right_id.clicked is True
    assert elem_wrong_id_right_title.clicked is False
