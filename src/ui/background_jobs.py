"""Run blocking work without letting worker threads call Tk."""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class _Callbacks:
    done: Callable[[Any], None]
    error: Callable[[Exception], None]
    current: Callable[[], bool]
    progress: Optional[Callable[[Any], None]] = None
    discard: Optional[Callable[[], None]] = None


class BackgroundJobs:
    """Callbacks and validity checks run only when the UI thread drains results.

    Work receives a plain-data progress reporter. It must not access widgets
    or mutable UI models, even to schedule an ``after`` callback.
    """

    def __init__(self):
        self._owner = threading.get_ident()
        self._results: queue.Queue = queue.Queue()
        self._callbacks: dict[int, _Callbacks] = {}
        self._next_id = 0

    def _assert_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Background job callbacks must run on the UI thread")

    def submit(self, work, *, done, error, current=lambda: True, progress=None, discard=None) -> int:
        self._assert_owner()
        self._next_id += 1
        job_id = self._next_id
        self._callbacks[job_id] = _Callbacks(done, error, current, progress, discard)
        results = self._results

        def run():
            try:
                value = work(lambda data: results.put((job_id, "progress", data)))
            except Exception as exc:
                results.put((job_id, "error", exc))
            else:
                results.put((job_id, "done", value))

        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception:
            del self._callbacks[job_id]
            raise
        return job_id

    def cancel(self, job_id: int) -> None:
        self._assert_owner()
        self._callbacks.pop(job_id, None)

    def drain(self, limit=10) -> None:
        self._assert_owner()
        for _ in range(limit):
            try:
                job_id, kind, value = self._results.get_nowait()
            except queue.Empty:
                break
            callbacks = self._callbacks.get(job_id)
            if callbacks is None:
                continue
            try:
                if not callbacks.current():
                    self._callbacks.pop(job_id, None)
                    if callbacks.discard:
                        callbacks.discard()
                elif kind == "progress":
                    if callbacks.progress:
                        callbacks.progress(value)
                else:
                    self._callbacks.pop(job_id, None)
                    if kind == "done":
                        callbacks.done(value)
                    else:
                        callbacks.error(value)
            except Exception:
                self._callbacks.pop(job_id, None)
                logging.exception("Failed to apply background job %s", job_id)
