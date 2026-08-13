# YouTube Views + Comment Organic Bot (V2.1 Modular Rebuild)

A modern, modular desktop automation tool for YouTube engagement, organic search navigation, watch time simulation, liking, and AI-powered context-aware comment generation (Google Gemini / OpenAI ChatGPT).

## Features

- **Organic Keyword Search & Navigation**: Searches YouTube home page using target keywords, scrolls through result items, and organically clicks matching video titles/IDs before using direct URLs as a fallback.
- **Stealth Browser Engine**: Powered by `undetected-chromedriver` and `selenium`, suppressing CDP signals and preserving separate Chrome profile user data per account.
- **AI-Powered Contextual Commenting**: Scrapes video title, channel name, and description snippet, using Google Gemini API (with dynamic flash model discovery) or OpenAI ChatGPT API to generate natural, authentic comments.
- **Multi-Account & Multi-Threaded Execution**: Supports loading multiple accounts (`email:password`) and running parallel browser sessions.
- **Decoupled Architecture**: Clean separation of UI (`CustomTkinter`), Automation engine, AI services, Data models, and Thread-safe Config management.

## Project Architecture

```text
G:\Projects\yt-tool\
├── config.ini                  # Settings & API keys configuration
├── requirements.txt            # Python dependencies
├── main.py                     # Entry point (GUI launcher & diagnostic mode)
└── src/                        # Source Package
    ├── ai/                     # Gemini & OpenAI comment generators
    ├── automation/             # Chrome driver setup, Google login, organic navigation & YouTube actions
    ├── config/                 # Thread-safe config.ini manager
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

## Account File Format

Accounts can be loaded from `accounts.txt` in the format:

```text
email@gmail.com:password
email2@gmail.com,password2
```
