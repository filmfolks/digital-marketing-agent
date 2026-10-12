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
