# Move Nexus to a Windows laptop (pendrive)

Status: the Windows scripts are written and unit-tested for logic, but have **not been run on a real Windows machine** yet. Report anything that breaks.

## 1. Make a clean copy on the Mac
```bash
./scripts/make_portable.sh                    # → dist/nexus-portable-YYYYMMDD-HHMM.zip
./scripts/make_portable.sh --include-brands   # also brand profiles/plans (no logs)
```
The zip leaves these out on purpose:
- `.venv`: tied to the machine and Python that created it, so a Mac venv cannot work on Windows
- `.env`: API keys and tokens
- `logs/`: browser profiles with live sessions, audit logs
- also `shots/`, `output/`, caches, `.git`, `node_modules`, activity/link logs

If you copy the folder by hand instead, skip those same items. **Never copy `.venv`.**

## 2. On the Windows laptop
1. Install **Python 3.9+** from python.org and tick *Add python.exe to PATH*. Install Chrome or Edge if you want to use them.
2. Unzip to a short path without special permissions, for example `C:\Nexus` (avoid running it straight from the pendrive, which is slow and may change drive letters).
3. Double-click `scripts\setup_windows.bat`. It deletes any foreign `.venv`, creates a new one, installs `requirements.txt` and Playwright Chromium (`-NoBrowser` skips the download; then set `NEXUS_BROWSER_CHANNEL=msedge` or `chrome`), creates `.env` from `.env.example` and validates the config.
4. Copy your secrets into `.env` by hand. Optionally do it over a secure channel, not the pendrive.
5. `start.bat` → open http://127.0.0.1:8712/app/. `start.ps1` also rebuilds `.venv` by itself if it was copied from another machine.
6. Log in again on each platform (`.venv\Scripts\python.exe agent\social_browser_agent.py login --platform x`). Sessions do not travel with the copy.
7. Optional auto-start: see [AUTOSTART.md](AUTOSTART.md#windows-task-scheduler-at-logon--untested-on-real-hardware).

## What is portable in the code
- All paths are built with `pathlib` relative to the project folder. There are no hard-coded `/Users/...` paths (a test enforces this).
- Browser discovery covers Chrome and Edge on Windows. The CDP attach port defaults to 9222.
- Antigravity live brain on Windows: see [BRAINS.md](BRAINS.md#antigravity-on-windows-untested).
- The macOS-only pieces are `scripts/install_daemon.sh` (LaunchAgent) and `mac_setup_py39.sh`. Their Windows equivalents are `install_daemon_windows.ps1` and `setup_windows.ps1`.
