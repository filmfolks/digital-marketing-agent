# Start Nexus automatically

## macOS (LaunchAgent)
```bash
./start.sh --check                 # creates .venv the first time
./scripts/install_daemon.sh        # writes ~/Library/LaunchAgents/com.nexus.agent.plist and loads it
curl -s http://127.0.0.1:8712/health
./scripts/uninstall_daemon.sh      # remove
```
- The relay starts at login and restarts if it dies. Logs go to `logs/daemon.out.log` / `logs/daemon.err.log`.
- **Two Nexus folders on one Mac** (for example the main app and a client build): give each one its own label, `NEXUS_DAEMON_LABEL=com.nexus.agent.main ./scripts/install_daemon.sh` (and the same variable for uninstall). The installer refuses to replace a LaunchAgent that runs a different folder unless you set `FORCE=1`.
- **Full Disk Access when the folder is under ~/Documents (or Desktop/Downloads).** macOS blocks background processes from reading these folders. If `logs/daemon.err.log` shows `Operation not permitted`, grant Full Disk Access to the Python binary that the venv points to. With the Xcode Command Line Tools Python 3.9 that binary is
  `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`
  1. System Settings → Privacy & Security → **Full Disk Access**.
  2. In Finder press **Cmd+Shift+G**, paste the folder path `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/`, and drag `python3.9` into the Full Disk Access list. The file picker behind "+" often hides this folder, so drag and drop is the reliable way.
  3. Turn the switch on, then `launchctl kickstart -k gui/$(id -u)/com.nexus.agent`.
  4. Or move the Nexus folder outside the protected folders (for example `~/Nexus`), which avoids the issue entirely.
  `readlink -f .venv/bin/python` (or `ls -l .venv/bin/`) shows which binary your venv really uses.
- The Antigravity live brain needs the Antigravity app open. The relay starts fine without it.

### Scheduled posts (built-in scheduler) — what must be running
Approved tasks with a `scheduled_at` are posted by the scheduler thread inside the relay — no brain, no chat, no human. It needs:

1. **The relay** (`agent/nexus_relay.py`, port 8712) with `.env`: `NEXUS_SCHEDULER=1`, `NEXUS_SCHEDULER_DRY_RUN=0`, `NEXUS_BRAND=<brand id>`. Check: `curl -s http://127.0.0.1:8712/scheduler` → `"running": true, "dry_run": false, "waiting_for_lock": false`.
   The LaunchAgent above (KeepAlive) is the recommended way. Manual restart without it:
   `kill <relay pid>` (SIGTERM releases `data/tasks/scheduler.lock`), then
   `nohup .venv/bin/python agent/nexus_relay.py >> logs/relay-run.log 2>&1 &`.
   After a hard kill the scheduler waits (status `waiting_for_lock`) until the old lock is stale (≤2 min), then resumes by itself.
2. **The posting Chrome** that `NEXUS_CDP_URL` points to (here `http://127.0.0.1:9233`), logged in to each account:
   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9233 \
     --user-data-dir="$HOME/NexusProfiles/chrome-thoshotech" --profile-directory="Profile 21" --no-first-run about:blank &
   ```
   To start it at login, add a second LaunchAgent (e.g. `com.nexus.chrome9233`) with that command as ProgramArguments and `RunAtLoad`; not installed by `install_daemon.sh`.
3. **The Mac awake.** A sleeping Mac runs nothing. Use System Settings → Energy → "Prevent automatic sleeping when the display is off", or `caffeinate -dims &`. A post missed by more than `NEXUS_SCHEDULER_GRACE_MIN` (default 120) is NOT posted late; it is marked failed "missed" so you can reschedule.

Double-post protection: approved → running is a compare-and-set; a scheduled run makes no automatic retries (a "failure" may come after the Post click); a task found `running` after a crash is marked failed ("check the platform before retrying"), never re-posted.

## Windows (Task Scheduler at logon) — UNTESTED on real hardware
```powershell
scripts\setup_windows.bat                                                     # once: .venv + deps + Chromium
powershell -ExecutionPolicy Bypass -File scripts\install_daemon_windows.ps1   # per-user task "Nexus Agent"
Invoke-RestMethod http://127.0.0.1:8712/health
powershell -ExecutionPolicy Bypass -File scripts\uninstall_daemon_windows.ps1
```
- It runs `.venv\Scripts\pythonw.exe agent\nexus_relay.py` with no console window at your logon, restarts it up to 3 times, and needs no admin rights.
- If Task Scheduler is locked down, add `-StartupFolder` to put a shortcut in `shell:startup` instead.
- A second copy needs `-TaskName "Nexus Agent (client)"`.
