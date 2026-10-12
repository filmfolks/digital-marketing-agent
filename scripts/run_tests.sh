#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────
# Nexus — full test suite
#
#   ./scripts/run_tests.sh              # everything
#   ./scripts/run_tests.sh --python     # only the Python suites
#   ./scripts/run_tests.sh --ui         # only the front-end suites
#   ./scripts/run_tests.sh --lint       # syntax + integrity checks only
#   ./scripts/run_tests.sh --fast      # everything except the headless browser test
#   ./scripts/run_tests.sh --smoke     # only the headless browser test
# ───────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_PY=1
RUN_UI=1
RUN_LINT=1
RUN_SMOKE=1
case "${1:-}" in
    --python) RUN_UI=0; RUN_LINT=0; RUN_SMOKE=0 ;;
    --ui)     RUN_PY=0; RUN_LINT=0; RUN_SMOKE=0 ;;
    --lint)   RUN_PY=0; RUN_UI=0; RUN_SMOKE=0 ;;
    --fast)   RUN_SMOKE=0 ;;
    --smoke)  RUN_PY=0; RUN_UI=0; RUN_LINT=0; RUN_SMOKE=1 ;;
esac

PY="${PYTHON:-python3}"
NODE="${NODE:-node}"
FAILURES=0

section() { printf '\n\033[1;35m── %s ─────────────────────────────────\033[0m\n' "$1"; }
ok()      { printf '\033[0;32m  ✔ %s\033[0m\n' "$1"; }
bad()     { printf '\033[0;31m  ✘ %s\033[0m\n' "$1"; FAILURES=$((FAILURES + 1)); }

# ── Syntax / integrity ────────────────────────────────────────
if [ "$RUN_LINT" = "1" ]; then
    section "Syntax"

    if "$PY" -m py_compile agent/*.py skill/digital-marketer/scripts/*.py tests/*.py; then
        ok "Python sources compile"
    else
        bad "Python syntax errors"
    fi

    for f in ui/script.js ui/ai-client.js ui/sw.js; do
        if command -v "$NODE" >/dev/null 2>&1; then
            if "$NODE" --check "$f" >/dev/null 2>&1; then ok "$f parses"; else bad "$f has a syntax error"; fi
        fi
    done

    # Every element the JS reaches for must exist in the markup, or a whole
    # feature silently dies at runtime.
    if [ -f ui/index.html ]; then
        grep -oE "getElementById\(['\`][^'\`]+['\`]\)" ui/script.js \
            | sed -E "s/getElementById\(['\`]([^'\`]+)['\`]\)/\1/" | sort -u > /tmp/nexus_js_ids.txt
        grep -oE 'id="[^"]+"' ui/index.html | sed -E 's/id="([^"]+)"/\1/' | sort -u > /tmp/nexus_html_ids.txt
        MISSING="$(comm -23 /tmp/nexus_js_ids.txt /tmp/nexus_html_ids.txt | grep -v '\${' || true)"
        if [ -z "$MISSING" ]; then
            ok "every getElementById target exists in index.html"
        else
            bad "JS references missing DOM ids:"
            echo "$MISSING" | sed 's/^/      /'
        fi
    fi

    # Inline handlers must resolve to a real function.
    grep -oE 'on(click|change|input|submit)="[a-zA-Z_$][a-zA-Z0-9_$]*' ui/index.html \
        | sed -E 's/on[a-z]+="//' | sort -u > /tmp/nexus_handlers.txt
    grep -oE '^(async )?function [a-zA-Z_$][a-zA-Z0-9_$]*|^const [a-zA-Z_$][a-zA-Z0-9_$]*' ui/script.js \
        | sed -E 's/^(async )?function //; s/^const //' | sort -u > /tmp/nexus_funcs.txt
    DEAD="$(comm -23 /tmp/nexus_handlers.txt /tmp/nexus_funcs.txt | grep -vx 'AgentLog' || true)"
    if [ -z "$DEAD" ]; then
        ok "every inline onclick handler is defined"
    else
        bad "inline handlers with no implementation:"
        echo "$DEAD" | sed 's/^/      /'
    fi
fi

# ── Python suites ─────────────────────────────────────────────
if [ "$RUN_PY" = "1" ]; then
    section "Python tests"
    if "$PY" -m unittest discover -s tests -p "test_*.py" -v; then
        ok "python suite passed"
    else
        bad "python suite failed"
    fi
fi

# ── Front-end suites ──────────────────────────────────────────
if [ "$RUN_UI" = "1" ]; then
    section "Front-end tests"
    if ! command -v "$NODE" >/dev/null 2>&1; then
        printf '  (skipped — node not found)\n'
    elif "$NODE" tests/test_ui.mjs; then
        ok "front-end suite passed"
    else
        bad "front-end suite failed"
    fi
fi

# ── Headless browser smoke test ───────────────────────────────
if [ "$RUN_SMOKE" = "1" ]; then
    section "Headless browser smoke test"
    if command -v "$NODE" >/dev/null 2>&1 && "$NODE" scripts/smoke_ui.mjs; then
        ok "the app boots and works in a real browser"
    else
        bad "headless smoke test failed"
    fi
fi

# ── Result ────────────────────────────────────────────────────
printf '\n'
if [ "$FAILURES" -eq 0 ]; then
    printf '\033[0;32m✅ All checks passed.\033[0m\n\n'
    exit 0
fi
printf '\033[0;31m❌ %s check group(s) failed.\033[0m\n\n' "$FAILURES"
exit 1
