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
var podActive = false;
if (pod && pod.childElementCount > 0) {
    var adText = pod.querySelector('.ytp-ad-text, .ytp-ad-preview-text, .ytp-ad-duration-remaining');
    if (adText && (adText.offsetParent !== null || (adText.innerText || '').trim().length > 0)) {
        podActive = true;
    }
    var skipBtn = pod.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, button[id*="skip-button"]');
    if (skipBtn && skipBtn.offsetParent !== null) {
        podActive = true;
    }
}

var apiAd = false;
if (player && typeof player.getAdState === 'function') {
    var adState = player.getAdState();
    if (adState === 1 || adState === 2) apiAd = true;
}

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
    isAd: showingAd || apiAd || podActive || srcIsAd
};
"""


def get_randomized_max_ad_seconds(base_seconds: float) -> float:
    """Calculate ±20% randomized ad watch duration.

    For example, 10 seconds yields a random value between 8.0 and 12.0 seconds.
    If base_seconds <= 0, returns 0.0 (unlimited ad watch).
    """
    if base_seconds <= 0:
        return 0.0
    low = base_seconds * 0.8
    high = base_seconds * 1.2
    return round(random.uniform(low, high), 2)


def pick_skip_delay(max_ad_seconds: Optional[float] = None) -> float:
    """Randomised skip window: ±20% around max_ad_seconds, or 5-10s fallback."""
    if max_ad_seconds is not None and max_ad_seconds > 0:
        return get_randomized_max_ad_seconds(max_ad_seconds)
    return round(random.uniform(SKIP_AFTER_MIN, SKIP_AFTER_MAX), 2)


@dataclass
class AdWatchState:
    """Tracks ad playback time for one watch session and decides when to skip."""

    total_ad_seconds: float = 0.0
    break_ad_seconds: float = 0.0
    ad_breaks: int = 0
    _skip_target: Optional[float] = field(default=None, repr=False)
    _base_max_ad_seconds: Optional[float] = field(default=None, repr=False)

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

    def get_skip_target(self, max_ad_seconds: Optional[float] = None) -> float:
        """Seconds this ad break must reach before skipping (±20% randomized around max_ad_seconds)."""
        if self._skip_target is None:
            target_base = max_ad_seconds if (max_ad_seconds is not None and max_ad_seconds > 0) else self._base_max_ad_seconds
            try:
                self._skip_target = pick_skip_delay(target_base)
            except TypeError:
                self._skip_target = pick_skip_delay()
        return self._skip_target

    @property
    def skip_target(self) -> float:
        """Seconds this ad break must reach before a skip."""
        if self._skip_target is None:
            self._skip_target = self.get_skip_target()
        return self._skip_target

    def skip_reason(self, skip_ads: bool, max_ad_seconds: float) -> Optional[str]:
        """Return a human-readable reason to click Skip, or ``None`` to keep waiting.

        ``skip_ads=True``  -> skip once this break reaches the ±20% randomized
        Max Ad Watch Time window (e.g. 8-12s for a 10s setting).
        ``skip_ads=False`` -> never skip before ``max_ad_seconds`` of cumulative
        ad time (0 disables the escape entirely).
        """
        if max_ad_seconds > 0:
            self._base_max_ad_seconds = float(max_ad_seconds)

        if skip_ads:
            target = self.get_skip_target(max_ad_seconds)
            if self.break_ad_seconds >= target:
                return (
                    f"monetisation skip window reached "
                    f"({self.break_ad_seconds:.0f}s of ~{target:.0f}s)"
                )
            return None

        if max_ad_seconds > 0 and self.total_ad_seconds >= max_ad_seconds:
            return (
                f"ad watch cap reached "
                f"({self.total_ad_seconds:.0f}s >= {max_ad_seconds}s)"
            )
        return None
