# Feature Reference

Feature inventory for the YouTube Playback Automation Tool (V2.1).

**Legend:** ✅ Stable · 🧪 Testing · 🗓 To Be Implemented

---

## ✅ Stable Features

### Organic Search & Navigation

- **6-step organic navigation flow** (`src/automation/navigation.py`):
  1. Parse the search query (keywords → title fallback) and extract the target video ID from the URL.
  2. Open `https://www.youtube.com` and dismiss the cookie/consent banner when present.
  3. Locate the search box and type the query **character-by-character with human micro-delays**, then submit with `ENTER`.
  4. Wait for results and scan the **top 15** for a video-ID or title match (case-insensitive).
  5. Smooth-scroll the matched result into view and click it organically.
  6. If nothing matched (or no query was given), fall back to the direct URL.
- **Search box compatibility**: handles both classic `<input>` and YouTube's newer `<textarea>` search layouts (AI search page).
- **Direct Link Fallback toggle** (Settings screen) — disable organic fallback entirely to enforce search-only navigation.
- **`safe_get()` navigation helper** — 35–45s page-load timeout; on timeout it calls `window.stop()` and continues instead of failing the task.
- **Video ID extraction** for `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/` and bare 11-character IDs.

### Google Sign-In

- Exact DevTools locators (`identifierId`, `name=identifier`, `aria-label='Email or phone'`) for the email step, with password and `Next` button fallbacks.
- Slow, human-style typing into both fields.
- Signed-in detection: existing sessions are reused and redirected straight to YouTube.
- **Up to 3 minutes of wait time** for a manual CAPTCHA / 2FA challenge in the open browser window.
- Cancellation support via the Stop button.

### Browser Engine

- `undetected-chromedriver` 3.5.5 + Selenium 4.40.
- **Dedicated Chrome profile per account** under `runtime/chrome_profiles/<profile>`, so sessions persist between runs.
- **Stale lock cleanup** (`SingletonLock`, `SingletonSocket`, `SingletonCookie`, `lockfile`) before startup to avoid Chrome crash-on-launch.
- **Chrome major version auto-detection** from the Windows registry (`BLBeacon`), passed to `version_main` so the driver matches the installed browser.
- **Offline ChromeDriver cache** — the matching driver binary is resolved once (Google Chrome-for-Testing CDN → npmmirror fallback) and cached under `runtime/tools/chromedriver/`, so launches no longer re-download from the network every time.
- **`DRIVER_INIT_LOCK`** serializes driver construction so parallel sessions do not collide.
- `page_load_strategy=eager`, fixed `1280x800` window, `en-US` locale, GPU/SHM flags disabled.

### Playback Engine (fixed durations)

- Watch durations from **30 Seconds to 24 Hours** (`30 Seconds`, `1/2/5/10/15/30/45 Minutes`, `1/2/3/6/12/24 Hours`).
- **HTML5 state inspection** every 3s: `paused`, `ended`, `currentTime`, `duration`, and ad presence (`.ad-showing`, `.ytp-ad-player-overlay`, `.video-ads`).
- **Auto-resume**: if the main video pauses mid-playback, `video.play()` is re-issued.
- **Auto-play on entry**: playback is only kicked off if the video is actually paused (JS first, real play button as fallback).
- **Ad-aware**: ad playback never counts toward completion; ad time is excluded from the finish check.
- **Random micro-scrolls** (~20% chance per cycle) for a more human session footprint.
- **Playback-only**: likes and comments are intentionally never automated.

### Ad Handling

- **Detection** (`src/automation/ads.py`): strict signals only — `ytp-showing-ad` class, a non-empty `.video-ads` container, `.ytp-ad-player-container`, or `adformat=` in the player source. The up-next countdown does **not** false-positive as an ad.
- **`SkipAds` ON (default)** — two layers:
  1. **Adblocker**: uBlock Origin Lite (pinned MV3 release, SHA-256 verified, provisioned into `extensions/`) is installed into the running session via the browser's DevTools socket (`Extensions.loadUnpacked`) — branded Chrome 137+ ignores `--load-extension`, and ChromeDriver cannot reach the browser-level `Extensions` domain, so the raw WebSocket path in `src/automation/adblock.py` is used. Verified end-to-end: tracker requests are redirected to the extension's no-op resources (CDP Network events show `chrome-extension://…/web_accessible_resources/…` responses).
  2. **Click fallback**: any ad that still plays is clicked after a randomized **30–35 s** (`SKIP_AFTER_MIN`/`SKIP_AFTER_MAX`) so the monetisation window is respected, including the up-next/countdown hand-off to the next video.
- **`SkipAds` OFF**: ads are watched normally; when cumulative ad time reaches `MaxAdWatchSeconds` (default 32, `0` = unlimited) an escape-skip fires as the safety net.
- **Ad time never counts** toward fixed-duration budgets or completion (`watched_elapsed = elapsed - total_ad_seconds`).
- **Graceful degradation**: if the blocker cannot be downloaded, verified, or loaded, the session runs blocker-free and relies on the click fallback alone.

### Multi-Account Execution

- Accounts loaded from the UI, an `accounts.txt` file (`email:password` or `email,password`), or `credentials.json` directly.
- Automatic migration of legacy accounts out of `settings.json` into `credentials.json`.
- **1–10 parallel browser sessions**, each with its own profile and worker thread.
- Graceful **Stop** button: workers check the stop event per task and per watch cycle, then close their drivers.

### Reliability & Progress Reporting

- **One automatic retry per task** — navigation failures and watch errors are retried once before the task is skipped (`MAX_ATTEMPTS_PER_TASK`).
- **Per-task success/failure reporting** — each session ends with a summary line such as `Session completed for <account>: 3/4 tasks watched, 1 failed`.
- **Watch abort guard** — if no `<video>` element is found for 60s the watch fails cleanly instead of hanging, and the task becomes retryable.
- **Full Video safety timeout** — full-length watching is capped by `FullVideoTimeoutMinutes` (default 6h, `0` = unlimited) so a player that never reaches `ended` cannot pin a worker forever.
- **Sign-in failure surfaced in the UI** — a failed login/CAPTCHA shows up on the Dashboard status line (red) rather than only in the log file.
- **Thread-safe UI event queue** — workers stream `progress` / `status` / `finished` events; all Tk updates happen on the main thread.
- **Atomic config writes** — settings and credentials are written to a temp file and swapped into place.

### Queue & Dashboard UI

- **Video Queue**: add / edit / delete / clear tasks; each card shows title, keywords, watch duration, and its recorded watch statistics.
- Queue is rendered **grouped by channel** — one section header per channel (name, URL, video count, run/watch-time totals) with an **Uncategorized** section for videos that belong to no channel.
- **🔗 View** button opens the queued video URL in the system browser.
- Responsive card grid that does not clip buttons in small windows.
- **Dashboard**: live cards for *Loaded Accounts*, *Queued Videos*, *Bot Status*, *Total Watch Time*, plus Start/Stop controls.
- Queue and settings persist in `settings.json` (thread-safe, atomic write via temp-file replace).

### Channels

- **Channels view** (sidebar → 📺 Channels): create / edit / delete named channels, each with a name and a channel URL.
- **Relation**: every queue task can be assigned to one channel (`channel` field on the task); a channel owns any number of videos.
- **Grouped queue**: the Video Queue screen groups videos under their channel header, with **🔗 Open Channel** and **⚙ Manage** shortcuts.
- **+ New Channel** inside the *Add/Edit Task* dialog creates a channel without leaving the modal and selects it straight away.
- Renaming a channel **cascades** to its videos; deleting a channel moves its videos to *Uncategorized* (never deletes videos).
- URLs are normalized (`youtube.com/@x` → `https://youtube.com/@x`); duplicate/blank names are rejected in the UI and stripped from `settings.json` on load.
- Channels persist in `settings.json` under the `channels` key (atomic write, like the queue).

### Watch Statistics

- **What is tracked** (`src/models/watch_stats.py` + `src/config/stats_store.py`): per-video `runs`, `successes`, `failures`, `attempts`, `watch_seconds`, `ad_seconds`, `last_run` and `last_result`; a channel's totals are the sum of its videos' records.
- **Measured, not configured**: `watch_video()` now returns a `WatchResult` (`completed`, `interrupted`, `watched_seconds`, `ad_seconds`, `reason`) and only accumulates seconds while the main video is actually playing — ad time and paused gaps are excluded, so "watch all ads" mode can never inflate the number.
- **One record per task**: `_run_task()` returns a `TaskOutcome` and its automatic retry folds into the same record (`attempts` counts the tries, watch time accumulates across them). A run stopped with **⏹** is recorded as `interrupted`: it counts as a run but never as a failure.
- **Identity**: records are keyed by the resolved video ID (`driver.current_url` after navigation, falling back to the task URL) with the task-derived key kept as an alias — keyword-only tasks show their card, and an early failed run folds into the later resolved one instead of duplicating.
- **Channel attribution is a snapshot**: the channel name is stored on the record when the run happens. Renaming a channel cascades into the stats, deleting a channel moves its records to *Uncategorized*, and deleting a video never erases watch time already delivered.
- **Where it shows**: queue card line `▶ 12 runs · 4h 32m watched · last ✅` (`⏸` = stopped, `❌` = failed); channel section header and Channels row `27 runs · 12h 04m watched`; Dashboard **Total Watch Time** card (lifetime).
- **Storage**: `stats.json` next to `settings.json` — versioned schema (`version: 1`), atomic tmp+replace write, one small write per completed task, lock-guarded for parallel workers, corrupt files quarantined to `stats.json.bak`, git-ignored from the start. Reset with **🗑 Reset Stats** in the queue header.

### Settings

| Setting | Control | Default | Notes |
| --- | --- | --- | --- |
| Direct Link Fallback | Switch | `true` | Toggle organic-search fallback to the direct URL |
| Parallel Browsers Count | Entry | `1` | Clamped to 1–10 on save |
| WatchtimeDuration | Dropdown | `5 Minutes` | Default duration pre-selected for **new** queue tasks |
| FullVideoTimeoutMinutes | Entry | `360` | Full Video safety cap in minutes; clamped 0–1440, `0` disables it |
| SkipAds | Switch | `true` | Adblocker + 30–35s click fallback; off = watch all ads |
| MaxAdWatchSeconds | Entry | `32` | Cumulative ad budget while `SkipAds` is off; `0` = unlimited |
| Appearance Mode | Dropdown | `Dark` | `Dark` / `Light` / `System`, applied immediately and persisted |
| Accounts File | Config only | `accounts.txt` | Fallback file auto-loaded when no accounts are saved |

Retired keys (`AccessKey`, `AIProvider`, `OpenAIKey`, `GeminiKey`, `OpenRouterKey`, `CommentType`) and removed video fields (`like`, `comment_type`, `custom_comment`) are **purged from `settings.json` on load**.

### Quality & CI

- **Test suite**: 146 pytest cases covering duration parsing, account line parsing, secret redaction, video-ID extraction, `ConfigManager` (defaults, purging, credentials migration, queue round-trips), channel serialization + queue grouping, watch-statistics models (identity keys, counter merging, formatting) and `StatsStore` (persistence, identity folding, channel cascades, multi-threaded increments), worker outcome mapping, ad-state parsing/policy, and ad-blocker provisioning (including the DevTools loader with a scripted WebSocket).
- **Lint**: `ruff` (`E`, `F`, `W`, `I` rules) configured in `pyproject.toml`.
- **Type check**: `mypy` over `src/` and `tests/` with `check_untyped_defs`.
- **CI**: `.github/workflows/ci.yml` runs ruff + mypy, and pytest on Python 3.11/3.12 (Windows).
- **Tooling**: `requirements-dev.txt` pins `pytest`, `ruff`, and `mypy`.

### Logging & Diagnostics

- UI **Logs** view fed by a polled queue, mirrored to `logs/automation.log`.
- **Secret redaction**: API-key-shaped strings (`key=…`, `sk-…`, `AIzaSy…`) are masked before they reach the log or the UI.
- `python main.py --diagnose-browser` — standalone Chrome/ChromeDriver health check.

### Retired Behaviour

- Automated **likes and comments removed** (`9f9bf50`); related config keys (`AIProvider`, `OpenAIKey`, `GeminiKey`, `OpenRouterKey`, `CommentType`) and per-video fields (`like`, `comment_type`, `custom_comment`) are stripped from disk on load.
- `src/ai/` is retained only as a stub: *"AI commenting support was removed from the application."*

---

## 🧪 Testing

### Full Video Watchtime

Watches a queued video until playback actually finishes instead of for a fixed duration.

- **UI**: `Full Video` is the first option in the *Watch Duration* dropdown on the Add/Edit task modal.
- **Model**: `VideoTask.is_full_video()` / `parse_duration_seconds()` return `None` for the `Full Video` label (`src/models/video_task.py`).
- **Engine**: `watch_video(driver, duration_seconds=None)` enters full mode (`src/automation/youtube_actions.py`) and loops until:
  - the non-ad video sets `ended`, or `currentTime >= duration - 1.5s`;
  - the Stop button sets the stop event;
  - no `<video>` element is found for 60s (watch fails → the task is retried once);
  - the **Full Video safety timeout** trips (`FullVideoTimeoutMinutes`, default 360 min, `0` = unlimited).
- **Progress logging** every 30 seconds in both modes: `Watching full video progress: MM:SS / MM:SS (NN%)`, plus an explicit *"Ad is playing, waiting for main video…"* line while ads run.
- **Completion log** with the total watched length, e.g. `Video playback finished! (Watched full length: 12:34)`.
- **Ad handling**: ad playback never counts toward completion, and the Skip-Ad button is clicked when visible. (Skip-Ad / play-button fallback previously failed silently on a missing `By` import — fixed.)

**Why it is still in Testing**

- Completion is inferred purely from page JS, so it needs verification against mid-rolls, unlisted/live content, age-gated videos, and very long (multi-hour) videos.
- The safety timeout and the 60s no-player abort still need to be confirmed against slow or consent-walled page loads.

**Test checklist**

- [ ] Short video (< 1 min) completes and logs the correct total length.
- [ ] Video with pre-roll ad: ad is excluded, main video completion is detected.
- [ ] Skip-Ad button is actually clicked when a skippable ad appears.
- [ ] Video with mid-roll ads resumes and continues after each break.
- [ ] Manual pause mid-video is resumed by the engine.
- [ ] Stop button interrupts full-video mode within one 3s cycle.
- [ ] Full Video selected together with direct-link fallback (no keywords).
- [ ] 1080p/4K long video (30 min+) stays alive without false completion.
- [ ] Video that cannot start (network/login wall) aborts after 60s and is retried once.
- [ ] Full Video safety timeout fires at the configured `FullVideoTimeoutMinutes`.

---

## 🗓 To Be Implemented

> The Reliability, Configuration, and Quality items from the original roadmap have shipped and moved up to **✅ Stable Features**.

### Dashboard & Queue

- [ ] Reorder queue tasks (move up/down) and drag-and-drop support.
- [ ] Per-task status badges (pending / running / done / failed) in the queue.
- [ ] Show which account is currently processing which task.
- [ ] Import a whole batch of videos from a text/CSV file.

### Operations

- [ ] PyInstaller build script/pipeline (`pyinstaller` is already in `requirements.txt`).
- [ ] Session summary export (JSON/CSV): accounts, tasks, durations, completion state.
- [ ] Scheduled runs (start at a given time, stop after N hours).
- [ ] Optional proxy / user-agent configuration per account.
- [ ] Retry/backoff policy for sign-in failures (currently reported and skipped).

### Quality

- [ ] Browser-level integration test (headless smoke run against a local fixture page).
- [ ] Coverage reporting (`pytest-cov`) and a coverage badge.
