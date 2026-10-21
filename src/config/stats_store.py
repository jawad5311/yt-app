import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..models.video_task import VideoTask
from ..models.watch_stats import (
    StatRecord,
    TaskOutcome,
    Totals,
    fold_records,
    resolve_video_key,
    totals_for,
)

SCHEMA_VERSION = 1


class StatsStore:
    """Thread-safe lifetime watch statistics in ``stats.json``.

    Records are aggregated counters keyed by video identity (see
    ``resolve_video_key``), snapshotted with the channel name they ran under.
    One small atomic write happens per completed task, never per tick.
    """

    def __init__(self, stats_file: Optional[Path] = None):
        if stats_file is None:
            stats_file = Path(__file__).resolve().parent.parent.parent / "stats.json"
        self.stats_file = Path(stats_file)
        self._lock = threading.RLock()
        self.records: Dict[str, StatRecord] = {}
        self.load()

    # ------------------------------------------------------------------ I/O

    def load(self) -> None:
        """Load stats from disk, recovering from a corrupt file."""
        with self._lock:
            self.records = {}
            if not self.stats_file.exists():
                return
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except Exception as exc:
                logging.error("Error reading stats file %s: %s", self.stats_file, exc)
                self._quarantine_unlocked()
                return
            if not isinstance(loaded, dict):
                self._quarantine_unlocked()
                return
            raw = loaded.get("videos", {})
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(key, str) and key:
                        self.records[key] = StatRecord.from_dict(value)

    def _quarantine_unlocked(self) -> None:
        """Move an unreadable stats file aside so tracking can start fresh."""
        try:
            backup = self.stats_file.with_suffix(".json.bak")
            self.stats_file.replace(backup)
            logging.warning("Quarantined corrupt stats file to %s", backup)
        except OSError:
            pass

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "videos": {key: record.to_dict() for key, record in self.records.items()},
        }
        temp_path = self.stats_file.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.stats_file)

    # ------------------------------------------------------------- recording

    def record(self, outcome: TaskOutcome) -> Optional[StatRecord]:
        """Fold one finished task into its record and persist."""
        base_key = outcome.task_key or outcome.video_key
        key = outcome.video_key or base_key
        if not key:
            return None
        with self._lock:
            record = self.records.get(key)
            # A task that first failed (task-derived key) and later resolved to
            # a real video ID must stay one record, not two.
            if base_key and base_key != key:
                base_record = self.records.get(base_key)
                if base_record is not None:
                    if record is None:
                        record = base_record
                    else:
                        fold_records(record, base_record)
                    del self.records[base_key]
            if record is None:
                record = StatRecord()
            record.merge(outcome, datetime.now().isoformat(timespec="seconds"))
            self.records[key] = record
            self._save_unlocked()
            return record

    def find_for_task(self, task: VideoTask) -> Optional[StatRecord]:
        """The record a queue card should display for this task."""
        base = resolve_video_key(task)
        with self._lock:
            record = self.records.get(base)
            if record is not None:
                return record
            for candidate in self.records.values():
                if base in candidate.task_keys:
                    return candidate
            return None

    # -------------------------------------------------------------- queries

    def get(self, key: str) -> Optional[StatRecord]:
        with self._lock:
            return self.records.get(key)

    def all(self) -> Dict[str, StatRecord]:
        with self._lock:
            return dict(self.records)

    def for_channel(self, channel: str) -> List[StatRecord]:
        with self._lock:
            return [record for record in self.records.values() if record.channel == channel]

    def channel_totals(self, channel: str) -> Totals:
        return totals_for(self.for_channel(channel))

    def lifetime_totals(self) -> Totals:
        with self._lock:
            return totals_for(self.records.values())

    # ------------------------------------------------------- channel changes

    def rename_channel(self, old_name: str, new_name: str) -> int:
        """Cascade a channel rename into snapshotted records."""
        return self._set_channel(lambda name: new_name if name == old_name else name)

    def unassign_channel(self, channel: str) -> int:
        """Detach videos of a deleted channel: their stats go uncategorized."""
        return self._set_channel(lambda name: "" if name == channel else name)

    def clear_channel_assignments(self) -> int:
        """Every record becomes uncategorized."""
        return self._set_channel(lambda name: "")

    def _set_channel(self, transform: Callable[[str], str]) -> int:
        changed = 0
        with self._lock:
            for record in self.records.values():
                new_name = transform(record.channel)
                if new_name != record.channel:
                    record.channel = new_name
                    changed += 1
            if changed:
                self._save_unlocked()
        return changed

    # ---------------------------------------------------------------- reset

    def clear_all(self) -> None:
        with self._lock:
            self.records = {}
            self._save_unlocked()

    def clear_video(self, key: str) -> bool:
        with self._lock:
            if key not in self.records:
                return False
            del self.records[key]
            self._save_unlocked()
            return True

    def remove_for_task(self, task: VideoTask) -> bool:
        """Remove a task's record (matching by direct key or alias)."""
        key = resolve_video_key(task)
        with self._lock:
            if key in self.records:
                del self.records[key]
                self._save_unlocked()
                return True
            for k, candidate in list(self.records.items()):
                if key in candidate.task_keys:
                    del self.records[k]
                    self._save_unlocked()
                    return True
            return False

    def update_task_metadata(self, task: VideoTask) -> bool:
        """Update label and channel on an existing task's stat record."""
        with self._lock:
            base = resolve_video_key(task)
            rec = self.records.get(base)
            if rec is None:
                for candidate in self.records.values():
                    if base in candidate.task_keys:
                        rec = candidate
                        break
            if rec is not None:
                changed = False
                new_label = (task.title or task.keywords or "").strip()
                if new_label and rec.label != new_label:
                    rec.label = new_label
                    changed = True
                new_channel = (task.channel or "").strip()
                if rec.channel != new_channel:
                    rec.channel = new_channel
                    changed = True
                if changed:
                    self._save_unlocked()
                return changed
            return False

    def handle_task_edited(self, old_task: VideoTask, new_task: VideoTask) -> None:
        """Sync stats on task edit:
        - If URL/video identity changed: clear old video stats.
        - If metadata (title/channel) changed: update the existing stat record.
        """
        old_key = resolve_video_key(old_task)
        new_key = resolve_video_key(new_task)
        if old_key != new_key:
            self.remove_for_task(old_task)
        else:
            self.update_task_metadata(new_task)
