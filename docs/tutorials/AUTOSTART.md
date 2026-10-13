# Start Nexus automatically

## macOS (LaunchAgent)
```bash
./start.sh --check                    # creates .venv the first time
./scripts/install_autostart_mac.sh    # relay + posting Chrome + keep-awake (no sudo)
#   or only the relay:  ./scripts/install_daemon.sh
curl -s http://127.0.0.1:8712/scheduler
./scripts/uninstall_daemon.sh [--all] # remove (relay only, or all three)
```
`install_autostart_mac.sh` installs three LaunchAgents:

| Label | What | Behaviour |
|---|---|---|
| `com.nexus.agent` | relay + scheduler (`.venv/bin/python agent/nexus_relay.py`) | RunAtLoad + KeepAlive (restarted 10 s after it dies) |
| `com.nexus.chrome9233` | posting Chrome: `--remote-debugging-port=9233 --user-data-dir=~/NexusProfiles/chrome-thoshotech --profile-directory="Profile 21"` | Once at login. Does nothing if port 9233 already has a browser. Never kills one. Override with `NEXUS_CHROME_PORT`, `NEXUS_CHROME_USER_DATA`, `NEXUS_CHROME_PROFILE` |
| `com.nexus.caffeinate` | `/usr/bin/caffeinate -s -i` | Prevents idle and system sleep while on AC power. The display may still sleep |

- The relay starts at login and restarts if it dies. Its logs go to `logs/daemon.out.log` and `logs/daemon.err.log`. The launcher log is in `~/Library/Logs/Nexus/`.
- **Folder on an external drive** (e.g. `/Volumes/SANSSD/...`): launchd starts agents before USB volumes mount, and it cannot start a program that lives on a missing volume. The plist therefore runs the venv's *real* python binary, which sits on the internal disk (Homebrew), with a small stdlib-only launcher, `~/Library/Application Support/Nexus/com.nexus.agent-launch.py`. The launcher waits up to `NEXUS_VOLUME_WAIT_S` (600 s) for the folder, then `execv`s `.venv/bin/python agent/nexus_relay.py` in the same process, so the relay *is* the launchd job and KeepAlive restarts it. If another relay is already serving the port (for example one started by hand with `nohup`), the launcher waits until that relay stops and then takes over. Two relays never run at once.
- **External-drive privacy (TCC "Removable Volumes")**: macOS does not let a background launchd job read an external drive until the job's program is allowed. Depending on the binary, macOS either shows a one-time prompt ("python3.12 would like to access files on a removable volume": click **Allow**) or silently denies with "Operation not permitted". `/bin/bash` jobs are denied silently. If you clicked "Don't Allow", turn it on under Privacy & Security → Files & Folders → python3.12 → Removable Volumes. The launcher log `~/Library/Logs/Nexus/com.nexus.agent.launcher.log` then says `cannot read … give Full Disk Access to <path>`. The fix:
  1. System Settings → Privacy & Security → **Full Disk Access** → **+**. Press **Cmd+Shift+G** and paste the path that `install_daemon.sh` prints as "launchd program". For Homebrew Python 3.12 that is `/opt/homebrew/Cellar/python@3.12/3.12.12/Frameworks/Python.framework/Versions/3.12/bin/python3.12`. Add it, and also add `…/Versions/3.12/Resources/Python.app`, because the interpreter re-executes into that app. Turn both on.
  2. `launchctl kickstart -k gui/$(id -u)/com.nexus.agent` (or `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nexus.agent.plist` if it is not loaded).
  3. After `brew upgrade python@3.12` the Cellar path changes. Re-run `./scripts/install_daemon.sh` and grant the new path.
  Alternative: move the Nexus folder to the internal disk (e.g. `~/Nexus`). That avoids the issue entirely.
- **Keep the Mac awake, alternative with sudo** (system-wide, also on battery and lid events; run it yourself):
  `sudo pmset -a sleep 0 disksleep 0 && sudo pmset -a womp 1 autorestart 1`. Revert with `sudo pmset -a sleep 1`.
  `autorestart 1` brings the Mac back after a power cut. Then the three LaunchAgents start everything again at login, which needs automatic login or a logged-in session.
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
   `install_autostart_mac.sh` installs `com.nexus.chrome9233`, which starts it at login if it is not already running.
3. **The Mac awake.** A sleeping Mac runs nothing. `com.nexus.caffeinate` (above) handles this without sudo. Alternatives: System Settings → Energy → "Prevent automatic sleeping when the display is off", or the `pmset` command above. A post missed by more than `NEXUS_SCHEDULER_GRACE_MIN` (default 120) is NOT posted late; it is marked failed "missed" so you can reschedule.

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
