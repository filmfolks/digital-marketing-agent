#!/usr/bin/env bash
# Nexus launcher (macOS / Linux).
#   ./start.sh            first run: create .venv, install deps + Chromium, start the relay
#   ./start.sh --lan      let your phone reach the relay (generates + saves a token if missing)
#   ./start.sh --check    validate config and exit
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
pick_python() {
  # PYTHON wins; else the first CPython 3.9-3.12 found (code is 3.9-compatible; 3.13+ lacks some wheels).
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return; fi
  for c in python3.12 python3.11 python3.10 python3.9 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,9)<=sys.version_info[:2]<=(3,12) else 1)' 2>/dev/null; then echo "$c"; return; fi
  done
  echo python3
}
PY_BOOT="$(pick_python)"
if [ -d .venv ] && ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
    echo "→ .venv was made on another machine (or is broken); recreating it"
    rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
    echo "→ creating .venv"
    "$PY_BOOT" -m venv .venv
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q -r requirements.txt
    .venv/bin/python -m playwright install chromium
fi
PY=.venv/bin/python
if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; echo "→ created .env from .env.example"; fi

current_token() { sed -n 's/^NEXUS_RELAY_TOKEN=//p' .env 2>/dev/null | tail -1 | tr -d '"'"'"; }

case "${1:-}" in
    --check) exec "$PY" agent/nexus_relay.py --check-config ;;
    --lan)
        TOKEN="${NEXUS_RELAY_TOKEN:-$(current_token)}"
        if [ -z "$TOKEN" ]; then
            TOKEN="$("$PY" agent/nexus_relay.py --generate-token)"
            if grep -q '^NEXUS_RELAY_TOKEN=' .env 2>/dev/null; then
                "$PY" - "$TOKEN" <<'PYEOF'
import re, sys, pathlib
p = pathlib.Path(".env"); s = p.read_text()
p.write_text(re.sub(r"(?m)^NEXUS_RELAY_TOKEN=.*$", "NEXUS_RELAY_TOKEN=" + sys.argv[1], s))
PYEOF
            else
                echo "NEXUS_RELAY_TOKEN=$TOKEN" >> .env
            fi
            echo "→ generated a relay token and saved it to .env"
        fi
        echo "→ phone token: $TOKEN"
        exec "$PY" agent/nexus_relay.py --lan
        ;;
esac
exec "$PY" agent/nexus_relay.py
