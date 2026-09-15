# YouTube Playback Automation Tool (V2.1 Modular Rebuild)

A modular desktop tool for controlled YouTube playback testing through organic search navigation and configurable watch durations.

## Features

- **Organic Keyword Search & Navigation**: Searches YouTube home page using target keywords, scrolls through result items, and organically clicks matching video titles/IDs before using direct URLs as a fallback.
- **Stealth Browser Engine**: Powered by `undetected-chromedriver` and `selenium`, suppressing CDP signals and preserving separate Chrome profile user data per account.
- **Multi-Account & Multi-Threaded Execution**: Supports loading multiple accounts (`email:password`) and running parallel browser sessions.
- **Playback-Only Automation**: Automated likes and comments are intentionally disabled.
- **Decoupled Architecture**: Clean separation of UI (`CustomTkinter`), automation engine, data models, and thread-safe configuration management.

## Project Architecture

```text
G:\Projects\yt-tool\
├── settings.json               # Application settings & video queue (git-ignored)
├── credentials.json            # Local Google account credentials (git-ignored)
├── requirements.txt            # Python dependencies
├── main.py                     # Entry point (GUI launcher & diagnostic mode)
└── src/                        # Source Package
    ├── automation/             # Chrome driver setup, Google login, organic navigation & YouTube actions
    ├── config/                 # Thread-safe settings and credentials manager
    ├── models/                 # Account & VideoTask data models
    ├── ui/                     # CustomTkinter GUI views (Dashboard, Accounts, Queue, Settings, Logs)
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

```powershell
python main.py --diagnose-browser
```

## Loading Accounts

The app stores Google account credentials locally in `credentials.json`. This file is ignored by Git and must never be uploaded or shared.

### Option 1: Load accounts from the app

1. Start the app with `python main.py`.
2. Open the **Accounts** screen.
3. Click **Add Single Account**.
4. Enter one account as `email:password` and confirm.
5. Repeat for each account. The app saves them automatically to `credentials.json`.

### Option 2: Import an accounts text file

1. Create a local text file such as `accounts.txt`.
2. Put one account on each line using either format:

   ```text
   email@gmail.com:password
   email2@gmail.com,password2
   ```

3. Start the app and open **Accounts**.
4. Click **Load Accounts File (accounts.txt)** and select the file.
5. Confirm that the accounts appear in the list. The imported accounts are then saved to `credentials.json`.

### Option 3: Edit `credentials.json` directly

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

The number of accounts used in one run is controlled by **Settings → Parallel Browsers Count**. The value can be between 1 and 10.

## Legacy Account File Format

Accounts can be loaded from `accounts.txt` in the format:

```text
email@gmail.com:password
email2@gmail.com,password2
```
