"""Measure observed playback intervals without crediting stalls or seeks."""

import math
from typing import Any, Dict, Optional, Tuple


class PlaybackMeter:
    """Convert continuous media progress to real playback seconds.

    The first observation is a baseline, not time watched in this session.
    Missing players, ads, seeks and unready media break that baseline.
    """

    def __init__(self) -> None:
        self._previous: Optional[Tuple[float, float, float, int]] = None

    def reset(self) -> None:
        self._previous = None

    def sample(self, state: Dict[str, Any], now: float) -> float:
        try:
            position = float(state.get("currentTime", 0.0))
            rate = float(state.get("playbackRate", 1.0))
            seek_count = int(state.get("seekCount", 0))
            ready = int(state.get("readyState", 4)) >= 2
        except (TypeError, ValueError, OverflowError):
            self.reset()
            return 0.0
        if (
            not state.get("found") or state.get("isAd") or state.get("seeking")
            or not ready or rate <= 0 or position < 0
            or not all(math.isfinite(v) for v in (position, rate, now))
        ):
            self.reset()
            return 0.0

        previous = self._previous
        self._previous = (now, position, rate, seek_count)
        if previous is None:
            return 0.0
        prev_now, prev_position, prev_rate, prev_seek_count = previous
        elapsed = now - prev_now
        progress = position - prev_position
        if elapsed <= 0 or progress <= 0 or seek_count != prev_seek_count:
            return 0.0
        # A playback-rate change makes this sample's conversion ambiguous.
        # A jump beyond plausible media progress is a seek even if the page
        # did not expose the seeking event. Never credit the jumped interval.
        if rate != prev_rate or progress > elapsed * rate + 0.25:
            return 0.0
        return min(elapsed, progress / rate)
