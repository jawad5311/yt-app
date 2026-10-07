# TESTING

End-to-end test path for the YouTube Playback Automation Tool (V2.1), ordered so that
cheap/no-browser checks run first and real browser scenarios last.

**Legend:** ✅ automated · 🖱 manual · 🧪 Full Video feature under test

Run stages in order — later stages depend on accounts/queue set up in earlier ones.
Record results in the checklist at the bottom. Estimated full pass: ~60–90 min
(minimum pass without 🧪/multi-account: ~20 min).

---

## Stage 0 — Automated gates ✅ (no browser, ~10s)

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m ruff check .          # expect: All checks passed!
python -m mypy                  # expect: Success: no issues found in 36 source files
python -m pytest                # expect: 97 passed
python -m compileall -q main.py src tests   # expect: silent, exit 0
```

- [x] ruff clean
- [x] mypy clean
- [x] 97 tests pass
- [x] `python -c "import main"` succeeds

## Stage 1 — App boot & configuration 🖱 (no browser)

1. `python main.py`
   - [x] Window title: **YouTube Playback Automation Tool - V2.1 (Modular)**
   - [x] Default view is Dashboard; sidebar has 6 entries (Dashboard, Accounts, Video Queue, Channels, Settings, Logs)
   - [x] Dashboard cards show account/queue counts and `Idle`
   - [x] No traceback in the Logs view or console
2. Settings screen:
   - [x] All 7 controls present (fallback switch, Parallel Browsers, Default Watch Duration, Full Video Timeout, Skip Ads, Max Ad Watch, Appearance Mode)
   - [x] Set Default Watch Duration → **1 Hour** → Save
   - [x] Set Appearance Mode → **Light** → **Save** → UI switches immediately
   - [x] Set Full Video Timeout → `90` → Save
   - [x] Set Max Ad Watch → `45` → Save → `settings.json` has `MaxAdWatchSeconds: 45`
   - [x] Toggle **Skip Ads** off → Save → persists after restart
   - [x] Enter Parallel Browsers `50` → Save → value clamps to `10`
   - [x] Enter Full Video Timeout `abc` → Save → error dialog, nothing saved
3. Restart the app:
   - [x] Appearance is Light, default duration is `1 Hour`, timeout shows `90`
   - [x] `settings.json` contains `FullVideoTimeoutMinutes` and **no** `AccessKey`
4. Set Appearance back to **Dark**, Default Watch Duration back to **5 Minutes**, timeout to `360`, Max Ad Watch back to `32`, and **Skip Ads** back on for the remaining stages.

## Stage 2 — Accounts 🖱 (no browser)

1. Accounts → **+ Add Single Account** → `nonsense` (no separator)
   - [x] Error dialog: *Invalid account format…*
2. Add `test.user+yt@gmail.com:dummy-password`
   - [x] Appears in the list, number `1.`
3. **Load Accounts File (accounts.txt)** with a file containing:
   ```text
   # comment line
   one@gmail.com:pw1
   two@gmail.com,pw2
   line-without-separator
   ```
   - [x] Only `one@` and `two@` imported (comment/invalid skipped)
4. **Remove** one row, then **🗑 Clear All**
   - [x] List empties
5. Re-add your real account(s) for the browser stages.
   - [x] `credentials.json` exists with `{"accounts": [...]}` structure
   - [x] `settings.json` has no `accounts` entries (legacy migration)
   - [x] `git check-ignore credentials.json` → ignored; `git check-ignore settings.json` → **not** ignored

## Stage 3 — Video Queue 🖱 (no browser)

1. **+ Add Target Video** with all three fields empty → **Add Task**
   - [x] Error: *Please provide at least one URL or Search Keywords!*
2. Add a task with only **keywords**, duration **30 Seconds**
   - [x] Card shows `#1`, keywords, `Watchtime: 30 Seconds`
   - [x] Duration dropdown opened on a **new** task pre-selects the Settings default (`5 Minutes` or whatever you set)
3. **✏ Edit** → change to `1 Minute` → **Save Changes**
   - [x] Card updates
4. **🔗 View**
   - [x] Browser opens (or *No direct video URL* notice if URL empty)
5. Add a second task, then **🗑 Delete** it
   - [x] Removed; queue persists after restart
6. Resize the window to its minimum (950×600)
   - [x] Buttons not clipped (responsive grid fix)
7. **🗑 Clear Queue** → confirm empty after restart.

## Stage 3b — Channels 🖱 (no browser)

1. Video Queue → **📺 Manage Channels** → **+ Add Channel** with an empty name → **Add Channel**
   - [ ] Error: *Channel name is required.*
2. Add `Cooking` with `youtube.com/@cooking`
   - [ ] Row shows the name, the normalized `https://…` URL and `0 video(s)`
3. Add a second channel named `cooking`
   - [ ] Error: *A channel named 'Cooking' already exists.*
4. **+ Add Target Video** → the Channel dropdown lists `(None)`, `Cooking`, …; pick `Cooking` → **Add Task**
   - [ ] Queue renders a `📺 Cooking • 1 video(s)` section header showing the channel URL
   - [ ] A task added with `(None)` lands under **Uncategorized**
   - [ ] A channel with no videos still renders its own (empty) section
5. In the task modal click **+ New Channel**, save `Gaming`
   - [ ] Dialog closes, the task modal survives, and its Channel dropdown now shows `Gaming` selected
6. **✏ Edit** `Cooking` → rename to `Cooking Shows` → **Save Changes**
   - [ ] The channel's videos keep their grouping (header follows the new name)
7. **🗑 Delete** `Cooking Shows`
   - [ ] Notice: *… video(s) moved to Uncategorized*; those videos reappear under **Uncategorized**
8. Restart the app
   - [ ] Channels and the video → channel relation survive (`settings.json` → `channels`)

## Stage 4 — Browser diagnostics 🖱

```powershell
python main.py --diagnose-browser
```

- [x] `[SUCCESS] Browser launched successfully!` and Chrome opens `accounts.google.com`
- [x] A profile folder appears under `runtime/chrome_profiles/`

## Stage 5 — Single-account happy path 🖱

Queue: **one** task = keywords `1 minute timer`, title `1 Minute Timer`, duration **30 Seconds**. Dashboard → **▶ Start Automation**.

- [x] Logs show `[Step 1/6]` → `[Step 6/6]` sequence
- [x] Search box filled **character by character**, submitted with ENTER
- [x] `Matched search result #N by Title` (or by Video ID)
- [x] `Successfully opened video from YouTube organic search results!`
- [x] `Watching video for up to 30 seconds...` then `Reached watch duration limit (30s).`
- [x] Progress label: `Completed 1 / 1 tasks — finished`; bar full
- [x] Summary: `Session completed for <email>: 1/1 tasks watched.`
- [x] Start re-enabled, Stop disabled, status back to `Idle`
- [x] `logs/automation.log` contains the same lines with timestamps
- [x] Browser closes; no orphan `chrome.exe`

## Stage 6 — Organic search variations 🖱

| # | Setup | Expected |
|---|---|---|
| 6.1 | Task with **URL only** (no keywords/title) | `[Step 1/6] … Search Query = 'None'` → direct fallback `[Step 6/6]` |
| 6.2 | Keywords that match nothing + URL, fallback **ON** | `Target video not found in top 15` → `[Step 6/6] Direct Link Fallback` → still watches |
| 6.3 | Same task, fallback **OFF** (Settings) | `Direct link fallback disabled…` → task fails (see Stage 8) |
| 6.4 | Title matching a video in the **textarea** (AI search) layout | `Matched search result … by Title` — no `searchbox` locator error |
| 6.5 | `window` timeouts on a slow page | `Page DOM ready; stopping background stream timeout…` and run continues |

## Stage 7 — 🧪 Full Video watchtime

Same checklist as [FEATURES.md](FEATURES.md); condensed here:

- [ ] Short video (< 1 min) completes → `Video playback finished! (Watched full length: MM:SS)`
- [ ] Pre-roll ad excluded; completion only after the main video
- [ ] **Skip-Ad regression**: `Clicked Skip Ad - monetisation window reached (Ns of ~Ms).` appears when a skippable ad shows (this was dead before the `By` fix)
- [ ] Mid-roll ads: `Ad is playing, waiting for main video...` then resumes
- [ ] Pause the video manually → `Video is paused…` / auto-resume within ~6s
- [ ] Progress lines every 30s: `Watching full video progress: MM:SS / MM:SS (NN%)`
- [ ] **⏹ Stop** during full mode → `Watchtime interrupted by user.` within ~3s, driver closes
- [ ] Full Video + direct-link fallback (URL only, no keywords) works
- [ ] 30 min+ video: no false completion before the end
- [ ] Safety timeout: set `FullVideoTimeoutMinutes` = `1`, run Full Video on a long video → after ~60s `Full video safety timeout reached (60s). Stopping watch.` (then set it back to `360`)

## Stage 8 — Reliability 🖱

| # | Setup | Expected |
|---|---|---|
| 8.1 Retry on navigation failure | Task with junk keywords (`zzz-no-match-9f2`) and **no URL** | `Could not navigate…` → `Retrying task once (attempt 2/2)…` → `Task failed after retry. Skipping…`; summary `0/1 tasks watched, 1 failed`; status line red |
| 8.2 No-player abort | Task with URL = `https://www.youtube.com/@YouTube` (a channel page, no player) | `No video element found for 60s. Aborting watch.` → retry once → fails after the second attempt (does **not** hang) |
| 8.3 Login failure surfaced | Add account with a **wrong password**, 1 task queued | After the ≤3 min wait: `Sign-in failed for <email>…` **on the Dashboard status line in red**, and tasks are skipped |
| 8.4 Stop mid-run | 3 tasks × 30 min duration → press **⏹** after task 1 starts | `Automation stopped by user.` / `Watchtime interrupted by user.`; workers clean up; Start re-enabled; no hung threads (app closes cleanly) |
| 8.5 Failure isolation | 2 tasks, first bad (8.1) then good | First fails with retry, second runs → summary `1/2 tasks watched, 1 failed` |

## Stage 9 — Multi-account / parallel 🖱

1. Load 3 accounts, Settings → Parallel Browsers `3`, queue 2 tasks × 30 Seconds → Start.
   - [ ] Three Chrome windows open, each with its own folder in `runtime/chrome_profiles/`
   - [ ] Progress aggregates: reaches `Completed 6 / 6 tasks` (3 workers × 2 tasks)
   - [ ] Three `Session completed for …` summaries
   - [ ] No `DRIVER_INIT_LOCK`/profile collision errors
2. Parallel = `2` with 3 accounts → only 2 browsers launch (`loaded_accounts[:parallel_limit]`).
3. Re-run a task and check `runtime/chrome_profiles/profile_<email>/` persists the signed-in session (second run logs `already signed in!`).

## Stage 10 — Settings effects 🖱

- [ ] Default Watch Duration = `30 Seconds` → new task opens pre-set to `30 Seconds`
- [ ] Appearance `System` follows OS theme; persists across restart
- [ ] Direct Link Fallback toggle affects Stage 6.2/6.3 behaviour without restart

## Stage 11 — Ad handling 🖱

Uses the Stage 5 queue/account. Watch a video that serves ads (any popular monetised video).

**Skip Ads ON (default)**

- [ ] First run logs `Downloading ad blocker (uBOLite_2026.930.1227.chromium.zip, ~9 MB)...` then `Ad blocker installed: extensions/uBOLite-2026.930.1227`; second run skips the download (reuses `extensions/`)
- [ ] Session start logs `Ad blocker loaded (efdmjfmgafbflcklekpnckemhhnjibpm).` and `Ad policy: blocker ON, fallback skips ads after the 30-35s monetisation window.`
- [ ] Pre-roll ad: either it never renders (blocker) or a line like `Ad playing: 6s into this break (skip at ~33s).` appears and the skip fires after **30–35 s**: `Clicked Skip Ad - monetisation window reached (…s of ~…s).`
- [ ] Ad time is excluded from the budget: a 30-second task interrupted by a 10 s ad still watches ~30 s of actual video
- [ ] Up-next / countdown card after the video ends is **not** treated as an ad (no `Ad playing…` for it)

**Skip Ads OFF**

- [ ] Settings → Skip Ads off → run: no `Ad blocker loaded` line; instead `Ad policy: watching all ads, escape cap at 32s.` then `Ad playing: …s break / …s total (cap 32s). Watching it.`
- [ ] Cumulative ad time reaches the cap → escape-skip fires (`Clicked Skip Ad - …`); if the ad is unskippable: `Ad cap reached but no Skip button found (unskippable ad). Waiting for it to finish.`
- [ ] Set Max Ad Watch to `0` → ads run to completion with no escape skip
- [ ] Turn **Skip Ads** back on for the remaining stages

**Blocker verification (optional, one-off)**

- [ ] `git status` shows no `extensions/` churn (git-ignored); the package hash matches the pinned SHA-256

## Stage 12 — Logging & privacy 🖱

- [ ] Logs view: **📋 Copy Logs** puts text on the clipboard; **Clear Logs** empties it (view still usable)
- [ ] `logs/automation.log` grows and is **not** tracked (`git status` shows no `.log`)
- [ ] No password or `settings.json` credentials ever appear in logs
- [ ] Any `key=…`/`sk-…`/`AIzaSy…` string would be `[REDACTED_API_KEY]` (covered by tests)
- [ ] `git status` shows no `credentials.json` and no `runtime/` churn

## Stage 13 — Clean-clone / CI ✅

- [ ] Fresh `git clone` → `python -m pip install -r requirements.txt -r requirements-dev.txt` → `python -m pytest` → 97 passed
- [ ] Push a branch → GitHub Actions **CI** job: `lint` (ruff+mypy) and `test` (3.11, 3.12) all green
- [ ] `git status` clean after a full run except intended changes (note: `settings.json` *is* tracked, so its edits show up)

---

## Results log

| Stage | Result (pass/fail) | Notes / screenshot |
| --- | --- | --- |
| 0 Automated gates | | |
| 1 Boot & config | | |
| 2 Accounts | | |
| 3 Queue | | |
| 3b Channels | | |
| 4 Diagnostics | | |
| 5 Happy path | | |
| 6 Organic search | | |
| 7 🧪 Full Video | | |
| 8 Reliability | | |
| 9 Multi-account | | |
| 10 Settings effects | | |
| 11 Ad handling | | |
| 12 Logging & privacy | | |
| 13 Clean clone / CI | | |

**Exit criteria for promoting Full Video out of 🧪 Testing:** every box in Stage 7 ticked,
then move the feature to ✅ Stable in [FEATURES.md](FEATURES.md) and tick the checklist there.
