# TODO

Work tracker for the YouTube Playback Automation Tool (V2.1).
Related docs: [README.md](README.md) (setup/usage) · [FEATURES.md](FEATURES.md) (feature inventory).

---

## ✅ Shipped (this pass)

### Bugs fixed

- [x] **Missing `By` import in `src/automation/youtube_actions.py`** — `By.CSS_SELECTOR` was used without importing `selenium.webdriver.common.by.By`, and the `NameError` was swallowed, so Skip-Ad and the play-button fallback never ran. Import added; ad-skip and play fallback now execute.
- [x] **Dashboard progress bar never updated** — workers now emit `progress` events; the Dashboard shows aggregated `Completed X / Y tasks` across all workers.
- [x] **Window title said "Views + Comment"** — now *YouTube Playback Automation Tool - V2.1 (Modular)*.
- [x] **`settings.json` tracked *and* git-ignored** — decided: it stays **tracked** (sanitized settings + queue, no credentials) and the stale `.gitignore` entry was removed; `credentials.json` remains ignored.
  - [x] **Reversed** — `settings.json` is now **git-ignored** as per-machine state (settings, video queue, channels). It is untracked with `git rm --cached`, the local file is kept, and first run recreates it from `DEFAULT_DATA`; `credentials.json` stays ignored.
- [x] **Dead config keys** — `AccessKey` removed from defaults and purged from existing files; `WatchtimeDuration` now drives the Add Task default; `AppearanceMode` now drives the real theme.
- [x] **`logs/.gitkeep` referenced but missing** — created; the `logs/` folder is now kept in the repo.

### Natural Human Interactivity Simulation

- [x] **Randomized Scrolling & Pausing** — simulates scrolling down to read comments or description during long videos, pauses to read with natural jitter and description expand, then scrolls back.
- [x] **Variable Playback Speeds** — occasionally sets playback speed to 1.25x via YouTube Player API and video element.
- [x] **Bandwidth Saver Quality** — adjusts video quality to 360p or 720p to save bandwidth and mimic real user settings.
- [x] **Random Micro-Pauses** — pauses playback for 2-5 seconds midway through long videos to simulate human distraction, resuming cleanly with clock synchronization.
- [x] **Settings UI & Configuration** — complete suite of controls on the Settings screen with instant persistence in `settings.json`.

### Reliability features

- [x] One automatic retry per task (`MAX_ATTEMPTS_PER_TASK = 2`) before skipping.
- [x] Per-task success/failure counting plus an end-of-session summary per account.
- [x] Watch aborts after 60s with no `<video>` element (retryable failure) instead of hanging.
- [x] Full Video safety timeout (`FullVideoTimeoutMinutes`, default 360, `0` = unlimited).
- [x] Login/CAPTCHA failure surfaced on the Dashboard status line (red), not only in logs.
- [x] Thread-safe worker → UI event queue (`progress` / `status` / `finished`); no cross-thread Tk calls.
- [x] **Offline ChromeDriver cache** — the driver binary is resolved once (Google CDN → npmmirror fallback) and cached in `runtime/tools/chromedriver/`, so launches never depend on a live Chrome-for-Testing download.

### Ad handling (Skip Ads)

- [x] **Strict ad detection** — `ytp-showing-ad`, non-empty `.video-ads`, `.ytp-ad-player-container`, or `adformat=` in source; the up-next countdown no longer false-positives.
- [x] **`SkipAds` toggle (default on)** — installs uBlock Origin Lite (pinned MV3, SHA-256 verified) into the session via the browser's DevTools socket (`Extensions.loadUnpacked`), because branded Chrome 137+ ignores `--load-extension` and ChromeDriver cannot reach the browser-level `Extensions` domain. Verified end-to-end: tracker requests are redirected to the extension's no-op resources (CDP Network evidence).
- [x] **Rebuilt click fallback** — any ad that still plays (including the up-next/countdown hand-off) is clicked after a randomized **30–35 s** so monetisation is credited.
- [x] **`SkipAds` off** — ads are watched up to a cumulative `MaxAdWatchSeconds` cap (default 32, `0` = unlimited) before an escape-skip.
- [x] Ad seconds never count toward fixed-duration budgets or completion.

### Configuration features

- [x] **Default Watch Duration** setting — pre-selects the duration for new queue tasks (validated against the option list).
- [x] **Appearance Mode** setting — `Dark` / `Light` / `System`, applied immediately and persisted.
- [x] **Full Video Timeout** setting — validated 0–1440 minutes.
- [x] **Skip Ads** toggle and **Max Ad Watch (seconds)** entry on the Settings screen, persisted to `settings.json`.
- [x] All settings exposed and saved from the Settings screen with validation.

### Channels (video grouping & auto-fetch)

- [x] **`Channel` model** (`src/models/channel.py`) — name + URL with URL normalization (`youtube.com/@x` → `https://…`), duplicate/blank-name validation, and `group_videos_by_channel()` bucketing (channel order preserved, original queue indexes preserved, trailing *Uncategorized* group).
- [x] **📺 Channels screen** — create / edit / delete / clear channels with per-channel video counts and a **🔗 Open** shortcut; sidebar entry added.
- [x] **Automated Video Fetcher** (`src/utils/channel_fetcher.py`) — zero-dependency extraction of video IDs, titles, and durations directly from YouTube channels.
- [x] **Interactive Selection Preview Modal** (`src/ui/views/channel_import_modal.py`) — review discovered videos with duration badges, duplicate detection, and one-click import.
- [x] **Metadata storage & Full Video duration** — `duration_seconds` and `duration_text` saved to `settings.json`, with imported watch time defaulted to "Full Video".
- [x] **On-Demand Channel Sync** — **🔄 Fetch** button on channel rows and inside the channel details modal for ongoing imports.
- [x] **Video → channel relation** — `VideoTask.channel` stored in `settings.json`; the task dialog gained a **Channel** dropdown and a **+ New Channel** button that saves without leaving the modal.
- [x] **Grouped Video Queue** — one section header per channel (name, URL, video count) with **🔗 Open Channel** / **⚙ Manage**, plus an *Uncategorized* section; empty channels still render.
- [x] **Rename cascades, delete detaches** — renaming a channel keeps its videos attached; deleting one moves them to *Uncategorized* (videos are never deleted).
- [x] **Persistence & hygiene** — `channels` key added to `ConfigManager` defaults; blanks, non-dicts, and duplicate names are stripped from `settings.json` on load.

### Watch statistics

- [x] **`WatchResult`** (`src/models/watch_stats.py`) — `watch_video()` returns measured `watched_seconds` (main-video playback only, ad/paused gaps excluded), `ad_seconds`, `completed`, `interrupted` and a `reason`; `bool(result)` keeps the old contract.
- [x] **`TaskOutcome`** — `_run_task()` returns one recordable outcome per task; retries fold into it (`attempts` + accumulated watch time) and a **⏹ Stop** is `interrupted` (a run, never a failure).
- [x] **Stable identity** — records are keyed by the resolved video ID (`driver.current_url`, then the task URL, then `url:`/`task:` fallbacks) with the task-derived key kept as an alias; a first failed run folds into the later resolved one.
- [x] **`StatsStore`** (`src/config/stats_store.py`) — versioned `stats.json` (`version: 1`), lock-guarded read-modify-write, one atomic tmp+replace write per completed task, corrupt files quarantined to `stats.json.bak`.
- [x] **Channel snapshot attribution** — the channel name is stored at run time; rename/delete hooks cascade into the stats so history survives reassignment, deletion and channel removal.
- [x] **UI** — queue card line (`▶ 12 runs · 4h 32m watched · last ✅`), channel section header totals, Channels row totals, a Dashboard **Total Watch Time** card, and a **🗑 Reset Stats** button (confirmed).
- [x] **Git hygiene** — `stats.json` is git-ignored alongside `settings.json`.

### Quality features

- [x] `tests/` pytest suite — **146 passing tests**: duration parsing, account line parsing, secret redaction, video-ID extraction, `ConfigManager` (defaults, purging, credentials migration, queue round-trips), channel model/grouping, watch-statistics models + `StatsStore` (identity, folding, cascades, concurrency), worker outcome mapping, ad-state/policy, ad-blocker provisioning (scripted-WebSocket DevTools loader).
- [x] `pyproject.toml` — pytest, ruff (`E,F,W,I`), and mypy (`check_untyped_defs`) configuration.
- [x] `requirements-dev.txt` — pinned `pytest`, `ruff`, `mypy`.
- [x] `.github/workflows/ci.yml` — ruff + mypy, pytest on Python 3.11/3.12 (Windows).
- [x] `ruff check .` → clean · `mypy` → clean (41 files) · `pytest` → 146 passed.
- [x] Dead imports and unsorted import blocks cleaned up across the codebase.

## 🧪 Testing (manual / runtime)

- [ ] **Full Video watchtime** (feature under test — full checklist in [FEATURES.md](FEATURES.md)):
  - [ ] short video completes and logs correct total length
  - [ ] pre-roll ad excluded from completion detection
  - [ ] Skip-Ad button actually clicked (regression check for the `By` fix)
  - [ ] mid-roll ads: playback resumes after each break
  - [ ] manual pause is auto-resumed
  - [ ] Stop interrupts within one 3s cycle
  - [ ] Full Video + direct-link fallback (no keywords)
  - [ ] 30 min+ video: no false completion
  - [ ] blocked/unplayable video aborts after 60s and retries once
  - [ ] safety timeout fires at the configured `FullVideoTimeoutMinutes`
- [ ] Parallel run with 3+ accounts (1–10 clamp, per-account profiles, `DRIVER_INIT_LOCK`).
- [ ] Organic search against the YouTube **textarea** (AI search) layout and the classic input layout.
- [ ] Long fixed duration (e.g. `1 Hour`) with Stop pressed halfway — driver must close cleanly.
- [ ] Fresh-machine onboarding: `pip install -r requirements.txt` → `python main.py --diagnose-browser`.
- [ ] Account import: `accounts.txt` with both `:` and `,` separators, plus legacy `settings.json` migration to `credentials.json`.
- [ ] Settings screen: change appearance/default duration/timeout, save, restart, confirm they persist.
- [ ] **Ad handling** (full checklist in [TESTING.md](TESTING.md) Stage 11): blocker loads (`Ad blocker loaded (efdmj…)`), ads blocked or skipped after 30–35 s, `SkipAds` off watches ads up to the cap, up-next countdown not treated as an ad.
- [ ] **Watch statistics** (full checklist in [TESTING.md](TESTING.md) Stage 3c): card line appears after a run, ad seconds excluded, a retried task counts as one run, **⏹ Stop** shows `last ⏸` without raising the failure count, channel rename/delete follows the totals, counters survive a restart, `git check-ignore stats.json` → ignored.

## 📄 Documentation

- [x] Refresh README (architecture tree, queue/settings/usage sections, removed duplicated "Legacy Account File Format" section).
- [x] Document the new Settings controls and the Dashboard progress/status line.
- [x] Document watch statistics (README §2c, FEATURES "Watch Statistics", TESTING Stage 3c).
- [x] Add a Development section (pytest / ruff / mypy / CI) to the README.
- [x] Add `FEATURES.md` (stable / testing / to-be-implemented).
- [x] Add `TODO.md`.
- [x] Add `TESTING.md` (full staged test path with results log).
- [x] Add `logs/.gitkeep`.
- [ ] Document the PyInstaller build once a packaging script exists.
- [ ] Record Full Video test results in FEATURES.md and tick the checklist.

## 🗓 Backlog (see FEATURES.md → To Be Implemented)

- [ ] Queue reordering / drag-and-drop.
- [ ] Per-task status badges and per-account task attribution.
- [ ] Batch import of video tasks from a text/CSV file.
- [ ] Session summary export (JSON/CSV).
- [ ] Scheduled runs and per-account proxy/user-agent settings.
- [ ] Retry/backoff policy for sign-in failures.
- [ ] PyInstaller build script.
- [ ] Browser-level integration smoke test and `pytest-cov` coverage reporting.
