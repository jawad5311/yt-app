# YouTube Playback Automation Tool (V2.1 Modular Rebuild)

A modular desktop tool for controlled YouTube playback testing through organic search navigation and configurable watch durations.

> 📄 See **[FEATURES.md](FEATURES.md)** for the full feature list (stable / testing / roadmap) and **[TODO.md](TODO.md)** for the current work tracker.

## Features

- **Organic Keyword Search & Navigation**: Opens the YouTube home page, types the target keyword character-by-character with human micro-delays, scans the top 15 results for a matching video ID or title, then smooth-scrolls and clicks the result. A direct-URL fallback runs only when no organic match is found (and can be disabled in Settings).
- **Autonomous "Start Browsing" Recommendation Loop**: The Dashboard's primary run flow discovers and plays videos matching your saved channels directly from the YouTube Home feed (~100 videos inspected after a 5s load) and watch-page sidebar recommendations, chaining playback continuously without closing the browser window.
- **Stealth Browser Engine**: Powered by `undetected-chromedriver` and `selenium`, with the installed Chrome major version auto-detected from the Windows registry and a separate Chrome profile preserved per account under `runtime/chrome_profiles/`.
- **Multi-Account & Multi-Threaded Execution**: Supports loading multiple accounts (`email:password`) and running 1–10 parallel browser sessions.
- **Persistent Account Profiles**: Each account uses its own profile directory under `runtime/chrome_profiles`, preserving cookies, sessions, and browser settings between runs.
- **Natural Human Interactivity**: Simulates human viewer actions (occasional video/comment likes, scrolling to comments/description, micro-pauses, bandwidth-saving quality adjustments). AI-generated commenting has been removed.
- **Ad Handling**: The *Auto-Skip Ads* toggle (default **on**) uses script-based native detection to click YouTube's *Skip Ad* button when available (after 5–10s). Extension-based ad blockers have been removed. With the switch **off**, ads are watched up to a cumulative *Max Ad Watch* cap (default 32s, `0` = unlimited) before an escape-skip fires.
- **Configurable Watch Durations**: Presets from 30 Seconds up to 24 Hours, plus a *Full Video* mode with early stop buffer (default 10s) and safety timeout (default 5 min).
- **Reliability & Concurrency**: Dedicated profile naming (`profile_<safe_email>_<hash>`) protected by cross-thread and cross-process exclusive file locks. Stale lock cleanup validates running processes without terminating independent browser instances. Positive sign-in and account verification are enforced.
- **Independent Browsing Controls**: Continuous browsing limits (**Browsing Target Limit**, default 1 attempt; or explicit **Unlimited browsing**) are configured, validated, and persisted independently. The legacy queue-worker entry point remains in the application for compatibility, but it is not exposed as a Dashboard action on this branch.
- **Watch Statistics**: Authoritative watch statistics are calculated directly from video playback records (`stats.json`), excluding stalls, buffering, ad time, and previously resumed positions. Legacy channel summary entries are excluded to prevent double counting. Fractional seconds are retained and checkpointed without loss. (Note: Local statistics measure client-side playback elapsed time for testing and do not guarantee official YouTube analytics or monetization acceptance).
- **Quality**: 580+ unit tests across 47 test modules (`pytest`), lint (`ruff`), type checks (`mypy`), and a GitHub Actions CI pipeline.
- **Decoupled Architecture**: Clean separation of UI (`CustomTkinter`), automation engine, data models, and thread-safe configuration management.

## Project Architecture

```text
yt-tool/
├── settings.json               # App settings, video queue & channels (git-ignored — created on first run)
├── stats.json                  # Lifetime watch statistics per video (git-ignored — created on first recorded run)
├── credentials.json            # Local Google account credentials (git-ignored — never commit)
├── requirements.txt            # Python dependencies
├── requirements-dev.txt        # Test / lint / type-check tooling
├── pyproject.toml              # pytest, ruff and mypy configuration
├── main.py                     # Entry point (GUI launcher & diagnostic mode)
├── README.md                   # Setup & usage guide
├── FEATURES.md                 # Feature inventory: stable / testing / to be implemented
├── TESTING.md                  # Full staged test path (automated gates → browser scenarios)
├── TODO.md                     # Work tracker
├── tests/                      # pytest suite (models, config, watch stats, logging, navigation, ads, adblock)
├── .github/workflows/ci.yml    # CI: ruff + mypy + pytest on Windows
├── logs/                       # Generated: automation.log (*.log git-ignored)
├── runtime/                    # Generated: per-account Chrome profiles, cached chromedriver (git-ignored)
└── src/                        # Source Package
    ├── ai/                     # Retired AI commenting support (stub kept for history)
    ├── automation/             # Chrome driver setup, Google login, organic navigation & YouTube actions
    ├── config/                 # Thread-safe settings/credentials manager + watch-statistics store
    ├── models/                 # Account, VideoTask, Channel & watch-statistics data models
    ├── ui/                     # CustomTkinter GUI views (Dashboard, Accounts, Queue, Channels, Settings, Logs)
    └── utils/                  # Logger with API key masking & typing helper utilities
```

## Setup & Running

### 1. Python Version & Virtual Environment

> [!IMPORTANT]
> **Recommended Python Version: Python 3.11 or 3.12**
> The project and its automated test pipeline (CI) are verified against Python 3.11 and 3.12. Using newer Python versions (e.g. 3.15+) can cause dependency conflicts (such as PyInstaller reporting `Requires-Python <3.15` / `No matching distribution found`).

1. Check your installed Python versions:
   ```powershell
   py --list
   ```

2. If Python 3.11 or 3.12 is not installed, install it using Windows Package Manager (`winget`):
   ```powershell
   winget install Python.Python.3.11
   ```
   *(Restart your PowerShell terminal after installation)*

3. Create and activate a dedicated virtual environment:
   ```powershell
   py -3.11 -m venv venv
   # or: py -3.12 -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

4. Install dependencies:
   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

> [!TIP]
> **Troubleshooting `pyinstaller` version errors:**
> If you see `ERROR: Could not find a version that satisfies the requirement pyinstaller==...` or `Requires-Python <3.15`, your active environment is running a newer Python version that the pinned package does not support. Either recreate your virtual environment with Python 3.11/3.12 as shown above, or relax the requirement in `requirements.txt` to `pyinstaller>=6.21.0`.

### 2. Launch Application

- **One-click silent launch (no terminal window):**
  Double-click `run.bat` in the project root. It uses `pythonw.exe` from your virtual environment to launch the UI directly without keeping a command prompt open.

- **Or launch manually from terminal:**
  ```powershell
  python main.py
  ```

### 3. Run Browser Diagnostics

Verifies that Chrome/ChromeDriver can start and reach `accounts.google.com`:

```powershell
python main.py --diagnose-browser
```

### 4. Development Commands

```powershell
python -m pip install -r requirements-dev.txt   # test / lint tooling
python -m pytest                                # run the test suite
python -m ruff check .                          # lint
python -m mypy                                  # type check
```

The same checks run in CI (`.github/workflows/ci.yml`) on every push and pull request.

### 5. Building the Standalone Executable

To package a standalone Windows executable (`dist/YTAutomationTool/YTAutomationTool.exe`):

```powershell
.\build.bat
```

- **Clean Release Distribution**: The build script isolates artifacts in a staging folder and strictly excludes personal credentials (`credentials.json`), account import files (`accounts.txt`), personal settings (`settings.json`), statistics (`stats.json`), and temporary backup files (`*.bak`, `*.tmp`).
- **Safe First-Run**: A fresh installation created by `build.bat` follows standard first-run setup without requiring bundled credentials. Sanitized templates (`accounts.example.txt` and `settings.example.json`) are provided for reference.
- **Data Preservation**: Building into `dist/YTAutomationTool` preserves any existing user configuration in the target directory without recursive deletion.

## Usage

### 1. Load Accounts

The app stores Google account credentials locally in `credentials.json`. This file is ignored by Git and must never be uploaded or shared.

#### Option 1: Load accounts from the app

1. Start the app with `python main.py`.
2. Open the **Accounts** screen.
3. Click **+ Add Single Account**.
4. Enter one account as `email:password` and confirm.
5. Repeat for each account. The app saves them automatically to `credentials.json`.

#### Option 2: Import an accounts text file

1. Create a local text file such as `accounts.txt`.
2. Put one account on each line using either format:

   ```text
   email@gmail.com:password
   email2@gmail.com,password2
   ```

3. Start the app and open **Accounts**.
4. Click **Load Accounts File (accounts.txt)** and select the file.
5. Confirm that the accounts appear in the list. The imported accounts are then saved to `credentials.json`.

#### Option 3: Edit `credentials.json` directly

Create or edit the file in the project folder with this structure:

```json
{
  "accounts": [
    {"email": "email@gmail.com", "password": "password"},
    {"email": "email2@gmail.com", "password": "password2"}
  ]
}
```

Restart the app after editing the file. Use accounts you control, and do not commit this file to Git.

> Accounts left in legacy `settings.json` are migrated into `credentials.json` automatically on first load.

## Persistent Chrome Sessions

The first time an account runs, Google may require a normal sign-in or a manual CAPTCHA/2-Step Verification check. After that, the account’s cookies and session data are retained in its dedicated profile directory:

```text
runtime/chrome_profiles/profile_<safe_email>_<hash>/
```

On later runs, the app reuses that profile and normally does not need to sign in again. Google can still expire a session or request verification, so a repeat login may occasionally be necessary.

### 2. Add Video Tasks

Open the **Video Queue** screen and click **+ Add Target Video**. Each task accepts:

| Field | Required | Purpose |
| --- | --- | --- |
| Video URL | One of the three | Direct fallback link, and the source of the video ID used for organic matching |
| Search Keywords | One of the three | Query typed into the YouTube search box for organic navigation |
| Video Title | One of the three | Optional; matched (case-insensitive) against the top 15 search results |
| Watch Duration | Yes | `30 Seconds` … `24 Hours`, or `Full Video` (testing) |
| Channel | No | Groups the video under a channel on the queue screen; `(None)` = Uncategorized |

At least one of URL / Keywords / Title is required. Each queue card offers **🔗 View**, **✏ Edit**, and **🗑 Delete**, and **Clear Queue** empties the whole list. The queue is persisted in `settings.json`.

### 2b. Group Videos by Channel

Open the **📺 Channels** screen (or **📺 Manage Channels** in the queue header) and click **+ Add Channel**. A channel is just a *name* plus a *channel URL*, and it can own any number of queue tasks:

- The **Video Queue** renders one section per channel — header with the channel name, URL, video count, plus **🔗 Open Channel** and **⚙ Manage**. Videos with no channel fall into an **Uncategorized** section.
- The task dialog's **Channel** dropdown assigns a video; **+ New Channel** creates one in place without leaving the dialog.
- Renaming a channel keeps its videos attached; deleting a channel moves them to **Uncategorized** (videos are never deleted).
- Channels persist in `settings.json` under `channels`.

### 2c. Watch Statistics

Every task that reaches the watch phase is recorded once (retries fold into a single run) in a local, git-ignored `stats.json`:

- Each **queue card** gains a third line: `▶ 12 runs · 4h 32m watched · last ✅` — `⏸` = stopped by you, `❌` = failed.
- Each **channel section header** and **Channels row** adds `27 runs · 12h 04m watched`, the sum of that channel's records.
- The **Dashboard** shows a fourth card, **Total Watch Time**, with the lifetime total across every video.
- **🗑 Reset Stats** in the queue header wipes all statistics after a confirmation.

Watch time counts main-video playback only — ad seconds and paused gaps are excluded — and a run you stopped counts as a run but never as a failure. Records store the channel name as it was at run time, so renaming a channel follows its history and deleting a channel (or a video) only moves the totals to *Uncategorized*.

### 3. Configure Settings

Open the **Settings** screen:

| Control | Default | Notes |
| --- | --- | --- |
| Enable Direct Link Fallback | off (`false`) | When organic search finds no match, navigate straight to the task URL |
| Parallel Browsers Count | 1 | Accounts launched simultaneously; clamped to **1–10** |
| Default Watch Duration | Full Video | Duration pre-selected when adding a new queue task |
| Full Video Timeout (minutes) | 5 | Safety timeout for *Full Video* mode; clamped 0–1440, `0` = unlimited / disabled guard |
| Auto-Skip Ads | on (`true`) | Script-based native skip clicks *Skip Ad* after 5–10s; off = watch ads up to cap |
| Max Ad Watch (seconds) | 32 | Cumulative ad watch budget while *Auto-Skip Ads* is **off**; clamped 0–3600, `0` = unlimited |
| Full Video Stop Buffer (seconds) | 10 | Early stop buffer before video ends to avoid autoplay; `0` = randomized 7–12s |
| Default Automation Loops | 1 | Legacy queue-worker setting retained for compatibility; the Dashboard starts the browsing loop |
| Default Browsing Target Limit | 1 | Target channel video attempts per browsing session (1–1000) |
| Default Browsing Unlimited | off (`false`) | Explicit option for continuous browsing until stopped or targets achieved |
| Video Rewatch Cooldown (hours) | 12 | Cooldown between rewatches; supports fractionals (e.g. 1.5h), `0` = no cooldown |
| Simulate Natural Human Interactivity | on (`true`) | Master switch for human viewer simulation behaviors |
| Appearance Mode | Dark | `Dark` / `Light` / `System`, applied and persisted atomically |

Click **💾 Save Settings** to apply. Settings are persisted in `settings.json`.

### 4. Start Autonomous Watching Loop ("Start Browsing")

To launch continuous recommendation watching:

1. Add at least one channel in the **📺 Channels** tab with a valid channel URL (`https://youtube.com/@handle` or `/channel/...`).
2. Configure your desired **Browsing Target Limit** (default: 1 attempt; limit of 1 means at most 1 target video attempt) or check **Unlimited browsing**.
3. On the **Dashboard**, click **▶ Start Browsing**. This is the active Dashboard run action on this branch.
4. The worker verifies positive sign-in for the assigned account and opens `https://www.youtube.com/`.
5. It inspects feed video cards matching your active target channels. When a match is found, the card is opened and played.
6. Target video playback attempts are counted at scheduling time (failed or interrupted attempts consume the cap).
7. If the configured target attempt cap is reached, browsing stops cleanly without starting an unnecessary buffer video.
8. If more target attempts remain, the worker interleaves a 30–90s non-target buffer video, then checks watch-page recommendations and homepage feeds for the next target video.
9. Click **⏹ Stop Browsing** at any time to halt the loop cleanly.

The older queue-worker API is retained for compatibility with existing tests and integrations, but it does not have a visible Dashboard start button in this branch.

## Project Layout Notes

- `credentials.json` is git-ignored and must never be committed.
- `settings.json` is git-ignored too: it is per-machine state (settings, video queue, channels) and is recreated with defaults on first run, so local edits never show up in `git status`.
- `stats.json` follows the same rule: per-machine watch statistics (per-video runs, watch time, channel snapshots), created on the first recorded run and never committed.
- `logs/*.log` and `runtime/` are generated at runtime and are not tracked (`logs/.gitkeep` keeps the folder in the repo).
