# YouTube Playback Automation Tool (V2.1 Modular Rebuild)

A modular desktop tool for controlled YouTube playback testing through organic search navigation and configurable watch durations.

> 📄 See **[FEATURES.md](FEATURES.md)** for the full feature list (stable / testing / roadmap) and **[TODO.md](TODO.md)** for the current work tracker.

## Features

- **Organic Keyword Search & Navigation**: Opens the YouTube home page, types the target keyword character-by-character with human micro-delays, scans the top 15 results for a matching video ID or title, then smooth-scrolls and clicks the result. A direct-URL fallback runs only when no organic match is found (and can be disabled in Settings).
- **Stealth Browser Engine**: Powered by `undetected-chromedriver` and `selenium`, with the installed Chrome major version auto-detected from the Windows registry and a separate Chrome profile preserved per account under `runtime/chrome_profiles/`.
- **Multi-Account & Multi-Threaded Execution**: Supports loading multiple accounts (`email:password`) and running 1–10 parallel browser sessions.
- **Playback-Only Automation**: Automated likes and comments are intentionally disabled.
- **Ad Handling**: The *Skip Ads* switch (default **on**) installs uBlock Origin Lite (pinned MV3 build, SHA-256 verified) into every session so most ads never render, then clicks through anything that still plays after a randomized **30–35 s** (past the monetisation window). With the switch **off**, ads are watched up to a cumulative *Max Ad Watch* cap before an escape-skip fires.
- **Configurable Watch Durations**: Presets from 30 Seconds up to 24 Hours, plus a *Full Video* mode (still under testing — see [FEATURES.md](FEATURES.md)).
- **Reliability**: Every task gets one automatic retry, watches abort cleanly if no player is found, worker progress/status is streamed to the Dashboard, and failed sign-ins are surfaced in the UI instead of only in the logs.
- **Quality**: 90+ unit tests (`pytest`), lint (`ruff`), type checks (`mypy`), and a GitHub Actions CI pipeline.
- **Decoupled Architecture**: Clean separation of UI (`CustomTkinter`), automation engine, data models, and thread-safe configuration management.

## Project Architecture

```text
G:\Projects\yt-tool\
├── settings.json               # App settings, video queue & channels (git-ignored — created on first run)
├── credentials.json            # Local Google account credentials (git-ignored — never commit)
├── requirements.txt            # Python dependencies
├── requirements-dev.txt        # Test / lint / type-check tooling
├── pyproject.toml              # pytest, ruff and mypy configuration
├── main.py                     # Entry point (GUI launcher & diagnostic mode)
├── README.md                   # Setup & usage guide
├── FEATURES.md                 # Feature inventory: stable / testing / to be implemented
├── TESTING.md                  # Full staged test path (automated gates → browser scenarios)
├── TODO.md                     # Work tracker
├── tests/                      # pytest suite (models, config, logging, navigation, ads, adblock)
├── .github/workflows/ci.yml    # CI: ruff + mypy + pytest on Windows
├── logs/                       # Generated: automation.log (*.log git-ignored)
├── runtime/                    # Generated: per-account Chrome profiles, cached chromedriver (git-ignored)
├── extensions/                 # Generated: uBlock Origin Lite, provisioned on first use (git-ignored)
└── src/                        # Source Package
    ├── ai/                     # Retired AI commenting support (stub kept for history)
    ├── automation/             # Chrome driver setup, Google login, organic navigation & YouTube actions
    ├── config/                 # Thread-safe settings and credentials manager
    ├── models/                 # Account, VideoTask & Channel data models
    ├── ui/                     # CustomTkinter GUI views (Dashboard, Accounts, Queue, Channels, Settings, Logs)
    └── utils/                  # Logger with API key masking & typing helper utilities
```

## Setup & Running

### 1. Install Dependencies

Use Python 3.11+:

```powershell
python -m pip install -r requirements.txt
```

### 2. Launch Application

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

### 3. Configure Settings

Open the **Settings** screen:

| Control | Default | Notes |
| --- | --- | --- |
| Enable Direct Link Fallback | on | When organic search finds no match, navigate straight to the task URL |
| Parallel Browsers Count | 1 | Accounts launched simultaneously; clamped to **1–10** |
| Default Watch Duration | 5 Minutes | Duration pre-selected when adding a new queue task |
| Full Video Timeout (minutes) | 360 | Safety net for *Full Video* mode; `0` = unlimited |
| Skip Ads | on | uBlock Origin Lite is installed each session; surviving ads are clicked after 30–35 s |
| Max Ad Watch (seconds) | 32 | Cumulative ad budget while *Skip Ads* is **off**; `0` = unlimited |
| Appearance Mode | Dark | `Dark` / `Light` / `System`, applied immediately |

Click **💾 Save Settings** to apply. Settings are persisted in `settings.json`.

### 4. Start a Run

1. Open the **Dashboard** — the cards show *Loaded Accounts*, *Queued Videos*, and *Bot Status*.
2. Click **▶ Start Automation**. Each worker signs in, walks the organic search flow for every queued task, then watches it for the configured duration.
3. Watch the **progress bar and status line** on the Dashboard (completed/total tasks across all workers), with detailed output on the **Logs** screen and in `logs/automation.log` (API keys are redacted).
4. A failed sign-in or a failed task is reported on the Dashboard status line as well as in the logs; failed tasks are retried once automatically.
5. Click **⏹ Stop Automation** to stop; workers finish their current check-cycle and clean up their browser sessions.

## Project Layout Notes

- `credentials.json` is git-ignored and must never be committed.
- `settings.json` is git-ignored too: it is per-machine state (settings, video queue, channels) and is recreated with defaults on first run, so local edits never show up in `git status`.
- `logs/*.log` and `runtime/` are generated at runtime and are not tracked (`logs/.gitkeep` keeps the folder in the repo).
