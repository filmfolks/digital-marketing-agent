#!/usr/bin/env python3
"""
Nexus end-to-end readiness check — verifies everything up to (never including)
a real post.

    python3 scripts/e2e_check.py                          # logins + X dry-run
    python3 scripts/e2e_check.py --canva-url https://www.canva.com/design/XXXX/edit
    python3 scripts/e2e_check.py --headed                 # watch it work

Steps
  1. relay configuration validates
  2. Playwright + a browser channel are available, stealth mode reported
  3. X and Canva persistent profiles are logged in
  4. X composer dry-run (types the text, stops before "Post")
  5. Canva export downloads the design            (if --canva-url)
  6. Canva → X dry-run with the export attached    (if --canva-url)

Exit code 0 only if every step that ran passed. Works on macOS, Linux, Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"
PY = sys.executable


def run(args: list, timeout: int = 600) -> tuple[int, dict, str]:
    try:
        p = subprocess.run([PY, *args], capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return 124, {"ok": False, "error": f"timed out after {timeout}s"}, ""
    payload: dict = {}
    for line in reversed(p.stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    return p.returncode, payload, (p.stdout + p.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canva-url", default=os.environ.get("NEXUS_E2E_CANVA_URL", ""))
    ap.add_argument("--text", default="Nexus e2e dry-run — this is never posted.")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-logins", action="store_true")
    a = ap.parse_args()

    social = [str(AGENT / "social_browser_agent.py")] + (["--headed"] if a.headed else [])
    steps: list[tuple[str, bool, str]] = []

    def step(name, ok, detail=""):
        steps.append((name, ok, detail))
        detail = (str(detail).strip().splitlines() or [""])[0]
        print(f"{'✔' if ok else '✘'} {name}" + (f" — {detail}" if detail else ""), flush=True)

    code, _, out = run([str(AGENT / "nexus_relay.py"), "--check-config"], timeout=30)
    step("relay configuration", code == 0, out.strip().splitlines()[-1] if out.strip() else "")

    sys.path.insert(0, str(AGENT))
    try:
        import importlib.util
        has_pw = importlib.util.find_spec("playwright") is not None
        from browser_core import detect_channel, load_stealth
        st = load_stealth()
        step("playwright installed", has_pw, "pip install -r requirements.txt" if not has_pw else "")
        step("browser channel", True, detect_channel() or "bundled chromium (run: playwright install chromium)")
        step("stealth", st.mode != "none", f"{st.mode}: {st.reason}")
    except Exception as e:                                       # noqa: BLE001
        step("browser environment", False, str(e))
        has_pw = False
    if not has_pw:
        return 1

    if not a.skip_logins:
        code, payload, _ = run([*social, "check_logins", "--platforms", "x,canva" if a.canva_url else "x"])
        for r in payload.get("results", []):
            step(f"logged in: {r['platform']}", r.get("logged_in", False), r.get("message", "").splitlines()[0] if r.get("message") else "")

    code, payload, _ = run([*social, "x_post", "--text", a.text, "--dry-run"])
    step("X composer dry-run", code == 0 and payload.get("dry_run") is True,
         payload.get("screenshot") or payload.get("error", ""))

    if a.canva_url:
        code, payload, _ = run([*social, "canva_export", "--design-url", a.canva_url])
        step("Canva export", code == 0, payload.get("file") or payload.get("error", ""))
        code, payload, _ = run([*social, "canva_to_x", "--design-url", a.canva_url, "--text", a.text, "--dry-run"])
        step("Canva → X dry-run (image attached)", code == 0 and payload.get("dry_run") is True,
             payload.get("error") or payload.get("screenshot") or "")
    else:
        print("· Canva steps skipped (pass --canva-url or set NEXUS_E2E_CANVA_URL)")

    failed = [s for s in steps if not s[1]]
    print(f"\n{len(steps) - len(failed)}/{len(steps)} checks passed. Nothing was posted.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
