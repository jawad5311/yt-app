"""Ad watch policy and player-state detection (pure logic, no Selenium imports).

Policy implemented here:

* ``SkipAds`` ON  -> the MV3 ad blocker removes most ads; for any ad that still
  renders, the fallback click fires **after a randomised 30-35s** so the ad still
  counts as monetised before we skip it.
* ``SkipAds`` OFF -> no blocker, ads are watched; if cumulative ad time reaches
  ``MaxAdWatchSeconds`` we escape by skipping (the "max timeout" safety net).
"""

import random
from dataclasses import dataclass, field
from typing import Optional

# A skippable ad watched for ~30s is monetised for the creator, so never skip
# earlier than 30s and skip somewhere in the 30-35s window.
SKIP_AFTER_MIN = 5.0
SKIP_AFTER_MAX = 10.0

# Single JS probe replacing the old ad detection: it reads the player state and
# classifies ads vs. main video. The old check matched `.video-ads` (always in
# the DOM) which is why the end-of-video / up-next countdown still reported
# "ad playing".
PLAYER_STATE_JS = """
var vids = document.querySelectorAll('video');
var v = null;
for (var i = 0; i < vids.length; i++) { if (!vids[i].paused && vids[i].currentTime > 0) { v = vids[i]; break; } }
if (!v) { for (var j = 0; j < vids.length; j++) { if (vids[j].readyState > 0) { v = vids[j]; break; } } }
if (!v && vids.length) { v = vids[0]; }
if (!v) return { found: false };

if (v.__ytToolSeekCount === undefined) {
    v.__ytToolSeekCount = 0;
    v.addEventListener('seeking', function() { v.__ytToolSeekCount += 1; });
}

var player = document.querySelector('.html5-video-player');
var showingAd = !!(player && (player.classList.contains('ytp-showing-ad')
                              || player.classList.contains('ad-showing')
                              || player.classList.contains('ad-interrupting')));
var pod = document.querySelector('.video-ads');
var podActive = !!(pod && pod.childElementCount > 0);
var adContainer = !!document.querySelector('.ytp-ad-player-container, .ytp-ad-module');
var src = String(v.currentSrc || v.src || '');
var srcIsAd = src.indexOf('adformat=') !== -1 || src.indexOf('/api/ads/') !== -1;

return {
    found: true,
    paused: v.paused,
    ended: v.ended,
    currentTime: v.currentTime || 0,
    duration: v.duration || 0,
    readyState: v.readyState,
    seeking: v.seeking,
    seekCount: v.__ytToolSeekCount,
    playbackRate: v.playbackRate,
    isAd: showingAd || podActive || adContainer || srcIsAd
};
"""


def pick_skip_delay() -> float:
    """Randomised skip window: skip an ad after 5-10 seconds randomly."""
    return random.uniform(SKIP_AFTER_MIN, SKIP_AFTER_MAX)


@dataclass
class AdWatchState:
    """Tracks ad playback time for one watch session and decides when to skip."""

    total_ad_seconds: float = 0.0
    break_ad_seconds: float = 0.0
    ad_breaks: int = 0
    _skip_target: Optional[float] = field(default=None, repr=False)

    def tick(self, is_ad: bool, dt: float) -> None:
        """Advance the clock by ``dt`` seconds for the current player state."""
        if dt <= 0:
            return
        if is_ad:
            if self.break_ad_seconds <= 0:
                self.ad_breaks += 1
                self._skip_target = None
            self.break_ad_seconds += dt
            self.total_ad_seconds += dt
        else:
            self.break_ad_seconds = 0.0
            self._skip_target = None

    @property
    def skip_target(self) -> float:
        """Seconds this ad break must reach before a monetising skip (30-35s)."""
        if self._skip_target is None:
            self._skip_target = pick_skip_delay()
        return self._skip_target

    def skip_reason(self, skip_ads: bool, max_ad_seconds: int) -> Optional[str]:
        """Return a human-readable reason to click Skip, or ``None`` to keep waiting.

        ``skip_ads=True``  -> skip once this break passed the 30-35s window.
        ``skip_ads=False`` -> never skip before ``max_ad_seconds`` of cumulative
        ad time (0 disables the escape entirely).
        """
        if skip_ads:
            if self.break_ad_seconds >= self.skip_target:
                return (
                    f"monetisation skip window reached "
                    f"({self.break_ad_seconds:.0f}s of ~{self.skip_target:.0f}s)"
                )
            return None

        if max_ad_seconds > 0 and self.total_ad_seconds >= max_ad_seconds:
            return (
                f"ad watch cap reached "
                f"({self.total_ad_seconds:.0f}s >= {max_ad_seconds}s)"
            )
        return None
