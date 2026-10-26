import random
import threading
from typing import Any, Dict, List, Optional, Set

from ..models.video_task import VideoTask
from ..models.watch_stats import resolve_video_key


class TaskDispatcher:
    """Coordinates video task assignment across multiple parallel browser workers.

    Guarantees:
    1. Tasks are assigned to each worker in a randomized order.
    2. At any given moment, no two browser workers process or play the same video simultaneously.
    """

    def __init__(
        self,
        tasks: List[VideoTask],
        randomize: bool = True,
        stats_store: Optional[Any] = None,
    ):
        self.tasks: List[VideoTask] = list(tasks)
        self.randomize = randomize
        self.stats_store = stats_store
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        # Video keys currently being played by active workers
        self._currently_playing_keys: Set[str] = set()
        # Video keys excluded from further execution in this session (e.g. due to persistence failure)
        self._excluded_video_keys: Set[str] = set()
        # Remaining tasks per worker (worker_id -> List[VideoTask])
        self._worker_queues: Dict[int, List[VideoTask]] = {}

    def register_worker(self, worker_id: int) -> int:
        """Initialize and randomize the task queue for a specific worker.

        Returns total task count assigned to this worker.
        """
        with self._lock:
            worker_tasks = list(self.tasks)
            if self.randomize:
                random.shuffle(worker_tasks)
            self._worker_queues[worker_id] = worker_tasks
            return len(worker_tasks)

    def acquire_next_task(
        self,
        worker_id: int,
        stop_event: Optional[threading.Event] = None,
        poll_interval: float = 0.5,
    ) -> Optional[VideoTask]:
        """Acquire the next available video task for this worker.

        If a candidate video is currently being played by another browser,
        this worker picks another candidate from its remaining pool.
        If all remaining candidates are currently occupied by other browsers,
        the worker waits until a browser finishes and releases a video.

        Returns None when all tasks for this worker are finished or stop_event is set.
        """
        with self._condition:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return None

                queue = self._worker_queues.get(worker_id)
                if not queue:
                    return None

                # Drop any tasks that reached their run limit, target multiplier, or were excluded
                filtered_queue = []
                for t in queue:
                    v_key = resolve_video_key(t)
                    if v_key in self._excluded_video_keys:
                        continue
                    if self.stats_store is not None:
                        rec = self.stats_store.find_for_task(t)
                        if rec is not None:
                            c_runs = rec.runs
                            w_sec = rec.watch_seconds
                            v_dur = (rec.video_duration if rec.video_duration > 0 else 0.0) or getattr(t, "duration_seconds", 0.0)
                            if t.is_target_reached(c_runs, w_sec, v_dur):
                                continue
                    filtered_queue.append(t)
                queue = filtered_queue
                self._worker_queues[worker_id] = queue
                if not queue:
                    return None

                # Find candidate video not currently being played
                chosen_idx: Optional[int] = None
                for idx, task in enumerate(queue):
                    v_key = resolve_video_key(task)
                    if v_key not in self._currently_playing_keys:
                        chosen_idx = idx
                        break

                if chosen_idx is not None:
                    task = queue.pop(chosen_idx)
                    v_key = resolve_video_key(task)
                    self._currently_playing_keys.add(v_key)
                    return task

                # All remaining candidates for this worker are currently in-flight on other workers.
                # Wait for any worker to finish and notify.
                self._condition.wait(timeout=poll_interval)

    def release_task(self, task: VideoTask) -> None:
        """Release the video so another worker can process it."""
        with self._condition:
            v_key = resolve_video_key(task)
            self._currently_playing_keys.discard(v_key)
            self._condition.notify_all()

    def exclude_video_key(self, video_key: str, reason: str = "") -> None:
        """Exclude a video from all workers for the remainder of the session."""
        with self._condition:
            self._excluded_video_keys.add(video_key)
            self._currently_playing_keys.discard(video_key)
            self._condition.notify_all()

    def is_video_excluded(self, video_key: str) -> bool:
        with self._condition:
            return video_key in self._excluded_video_keys

    def wake_all(self) -> None:
        """Wake up any workers currently waiting on the condition."""
        with self._condition:
            self._condition.notify_all()

    def get_remaining_count(self, worker_id: int) -> int:
        with self._lock:
            return len(self._worker_queues.get(worker_id, []))
