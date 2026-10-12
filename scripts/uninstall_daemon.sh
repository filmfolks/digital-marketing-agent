#!/usr/bin/env bash
# Stop and remove the Nexus relay LaunchAgent (com.nexus.agent).
set -euo pipefail
LABEL="${NEXUS_DAEMON_LABEL:-com.nexus.agent}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ -f "$PLIST" ]; then rm -f "$PLIST"; echo "→ removed $PLIST"; else echo "→ not installed"; fi
