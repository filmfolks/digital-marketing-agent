#!/bin/bash
# Nexus — Mac setup for the stock /usr/bin/python3 (3.9.x, Xcode CLT).
# Creates .venv and installs only what posting needs (exact pins, binary wheels).
# No browser download: in CDP mode Nexus attaches to your own running Chrome.
#   ./scripts/mac_setup_py39.sh            # posting only
#   ./scripts/mac_setup_py39.sh --fresh    # delete and rebuild .venv
#   ./scripts/mac_setup_py39.sh --with-ai  # + optional AI SDKs (not needed: providers are called over HTTP)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" - <<'PYCHK'
import sys
v = sys.version_info
if v < (3, 9):
    sys.exit(f"Python 3.9+ required, found {sys.version.split()[0]}")
print(f"using Python {sys.version.split()[0]} ({sys.executable})")
PYCHK
# A .venv copied from another computer (or another Python) is unusable: rebuild it.
if [ -d .venv ] && { [ "${1:-}" = "--fresh" ] || [ "${2:-}" = "--fresh" ] || ! .venv/bin/python -c "import sys" >/dev/null 2>&1; }; then
  echo "-> rebuilding .venv (copied from another machine, other Python, or --fresh)"
  rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade "pip>=24,<26" --disable-pip-version-check -q
.venv/bin/python -m pip install --only-binary=:all: -r requirements-posting.txt
if [ "${1:-}" = "--with-ai" ] || [ "${2:-}" = "--with-ai" ]; then
  .venv/bin/python -m pip install --only-binary=:all: -r requirements.txt || \
    echo "WARN: optional AI SDKs failed to install; posting still works."
fi
[ -f .env ] || { [ -f .env.example ] && cp .env.example .env && echo "created .env from .env.example"; }
.venv/bin/python - <<'PYCHK'
import sys
sys.path.insert(0, "agent")
import playwright, browser_core
from importlib.metadata import version
print("playwright", version("playwright"), "| stealth:", browser_core.load_stealth().mode,
      "| CDP target:", browser_core.resolve_cdp_url() or "(launch mode)")
PYCHK
echo
echo "Done. Next (Chrome must be running with --remote-debugging-port=9222, or your client build's cdp_port):"
echo "  .venv/bin/python agent/social_browser_agent.py --cdp-url http://127.0.0.1:9222 check_logins --platforms x"
