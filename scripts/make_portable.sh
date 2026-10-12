#!/usr/bin/env bash
# Clean portable zip of Nexus (no .venv, .env, logs/profiles, caches). See scripts/make_portable.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec "${PYTHON:-python3}" scripts/make_portable.py "$@"
