#!/usr/bin/env bash
# Install the Nexus relay as a macOS LaunchAgent (starts at login, restarted if it dies).
#   ./scripts/install_daemon.sh             write the plist and load it
#   ./scripts/install_daemon.sh --no-load   only write the plist
#   ./scripts/install_daemon.sh --print     print the plist and exit
# Note: the Antigravity brain discovers the running Antigravity app at request time,
# so the app must be open (and signed in) for Antigravity answers; the relay itself
# starts fine without it and other brains keep working.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${NEXUS_DAEMON_LABEL:-com.nexus.agent}"   # use a different label to run two Nexus folders side by side
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/.venv/bin/python"
[ "$(uname)" = "Darwin" ] || { echo "macOS only" >&2; exit 1; }
[ -x "$PY" ] || { echo "missing $PY — run ./start.sh once first" >&2; exit 1; }
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
render() {
cat <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(xml "$PY")</string>
        <string>$(xml "$ROOT/agent/nexus_relay.py")</string>
    </array>
    <key>WorkingDirectory</key><string>$(xml "$ROOT")</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$(xml "$ROOT/logs/daemon.out.log")</string>
    <key>StandardErrorPath</key><string>$(xml "$ROOT/logs/daemon.err.log")</string>
</dict>
</plist>
PL
}
case "${1:-}" in
    --print) render; exit 0 ;;
esac
# Never silently take over a LaunchAgent that runs ANOTHER Nexus folder.
if [ -f "$PLIST" ] && ! grep -qF "$(xml "$ROOT/agent/nexus_relay.py")" "$PLIST" && [ "${FORCE:-0}" != "1" ]; then
    echo "$PLIST already runs a different Nexus folder:" >&2
    grep -o '<string>[^<]*nexus_relay.py</string>' "$PLIST" >&2 || true
    echo "Use NEXUS_DAEMON_LABEL=com.nexus.agent.<name> for a second copy, or FORCE=1 to replace it." >&2
    exit 1
fi
if [ -f "$PLIST" ]; then cp -p "$PLIST" "$PLIST.bak-$(date +%Y%m%d-%H%M%S)"; fi
render > "$PLIST.tmp"
plutil -lint "$PLIST.tmp" >/dev/null
mv "$PLIST.tmp" "$PLIST"
echo "→ wrote $PLIST"
if [ "${1:-}" = "--no-load" ]; then exit 0; fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
echo "→ loaded $LABEL (logs: $ROOT/logs/daemon.*.log)"
PORT="$("$PY" -c 'import sys; sys.path.insert(0, "agent"); import client_config; print(client_config.relay_port())' 2>/dev/null || echo 8712)"
echo "  check: curl -s http://127.0.0.1:$PORT/health"
