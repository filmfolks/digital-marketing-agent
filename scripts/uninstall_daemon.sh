#!/usr/bin/env bash
# Stop and remove the Nexus relay LaunchAgent (com.nexus.agent).
#   --all   also the posting-Chrome (com.nexus.chrome*) and keep-awake (com.nexus.caffeinate) agents
set -euo pipefail
LABEL="${NEXUS_DAEMON_LABEL:-com.nexus.agent}"
LABELS=("$LABEL")
if [ "${1:-}" = "--all" ]; then
    for p in "$HOME"/Library/LaunchAgents/com.nexus.chrome*.plist "$HOME/Library/LaunchAgents/com.nexus.caffeinate.plist"; do
        [ -f "$p" ] && LABELS+=("$(basename "$p" .plist)")
    done
fi
for L in "${LABELS[@]}"; do
    PLIST="$HOME/Library/LaunchAgents/$L.plist"
    launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
    if [ -f "$PLIST" ]; then rm -f "$PLIST"; echo "→ removed $PLIST"; else echo "→ $L not installed"; fi
    rm -f "$HOME/Library/Application Support/Nexus/$L-launch.sh" "$HOME/Library/Application Support/Nexus/$L-launch.py"
done
