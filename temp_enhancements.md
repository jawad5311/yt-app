# Enhancements Plan & Tracking

- [x] **Task 1: Add channel name in retries**
  - Organic search retry loop now appends the channel name (e.g. `"<title> <channel_name>"`) on the 3rd attempt for each title.
  - Test coverage added and verified in `tests/test_worker_stats.py`.

- [x] **Task 2: Archive channels**
  - Added `archived: bool` field to `Channel` model (serialized/deserialized seamlessly).
  - Archived channels are excluded from active automation execution (`is_video_task_runnable` returns `False`).
  - Added "📁 Archive" action in Queue view header to quickly move any channel and its videos to the archive list without losing any data.
  - Channels page now features an `Active` / `Archived` segmented tab bar with channel counts.
  - In `Archived` tab, users can restore channels with "📂 Restore" back to the active queue or delete them.

- [x] **Task 3: Collapsing and better UX from channels**
  - Queue view channel header is clickable and collapses/expands all video cards under it with `▶` / `▼` indicator arrows.
  - In Channels page, clicking a channel row opens a dedicated videos modal displaying all videos in that channel, their watch statistics, and direct edit/delete actions.
  - The channel videos modal includes a `+ New Video` button with that channel automatically pre-selected.

- [x] **Task 4: Rename "Skip ads" in settings to "Block Ads entirely"**
  - Renamed switch text in Settings to `Block Ads entirely (block with uBlock Lite + skip fallback after 30-35s)`.
  - Config key updated to `BlockAds` with fallback to `SkipAds` for backward compatibility.

- [x] **Task 5: Change default setting values**
  - Direct Link Fallback: `False` (disabled by default)
  - Default Watchtime Duration: `"Full Video"` (Full Video Watchtime)
  - Block Ads entirely: `False` (disabled by default)
  - Full Video Timeout: `5` minutes
