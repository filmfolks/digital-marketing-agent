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
  4b. Instagram login + composer dry-run, stops before "Share"   (if --ig-media)
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


def cdp_probe(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """GET <cdp>/json/version: proves a debuggable Chrome is listening."""
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
        return True, str(info.get("Browser", "chrome"))
    except Exception as e:                                       # noqa: BLE001
        return False, (f"nothing answering at {url} ({e}). Start Chrome with --remote-debugging-port, "
                       f"or pass --cdp-url off")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canva-url", default=os.environ.get("NEXUS_E2E_CANVA_URL", ""))
    ap.add_argument("--text", default="Nexus e2e dry-run — this is never posted.")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-logins", action="store_true")
    ap.add_argument("--ig-media", default=os.environ.get("NEXUS_E2E_IG_MEDIA", ""),
                    help="image for the Instagram dry-run (also checks the Instagram login)")
    ap.add_argument("--cdp-url", default=None,
                    help="attach to a running Chrome (e.g. http://127.0.0.1:9222); 'off' = launch. "
                         "Default: NEXUS_CDP_URL, then config/client.json")
    a = ap.parse_args()
    if a.cdp_url is not None:
        os.environ["NEXUS_CDP_URL"] = a.cdp_url        # every child process below inherits it

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
        from browser_core import detect_channel, load_stealth, resolve_cdp_url
        step("playwright installed", has_pw, "pip install -r requirements-posting.txt" if not has_pw else "")
        cdp = resolve_cdp_url()
        if cdp:
            ok, detail = cdp_probe(cdp)
            step(f"attach to running Chrome at {cdp}", ok, detail)
            print("· stealth/UA overrides skipped in attach mode (it is your own Chrome)")
        else:
            st = load_stealth()
            step("browser channel", True, detect_channel() or "bundled chromium (run: playwright install chromium)")
            step("stealth", st.mode != "none", f"{st.mode}: {st.reason}")
    except Exception as e:                                       # noqa: BLE001
        step("browser environment", False, str(e))
        has_pw = False
    if not has_pw:
        return 1

    if not a.skip_logins:
        plats = ["x"] + (["canva"] if a.canva_url else []) + (["instagram"] if a.ig_media else [])
        code, payload, _ = run([*social, "check_logins", "--platforms", ",".join(plats)])
        for r in payload.get("results", []):
            step(f"logged in: {r['platform']}", r.get("logged_in", False), r.get("message", "").splitlines()[0] if r.get("message") else "")

    code, payload, _ = run([*social, "x_post", "--text", a.text, "--dry-run"])
    ok = code == 0 and payload.get("dry_run") is True
    step("X composer dry-run", ok,
         payload.get("screenshot", "") if ok else payload.get("error") or payload.get("screenshot", ""))

    if a.ig_media:
        code, payload, _ = run([*social, "ig_post", "--caption", a.text, "--media", a.ig_media, "--dry-run"])
        ok = code == 0 and payload.get("dry_run") is True
        step("Instagram composer dry-run (stops before Share)", ok,
             payload.get("screenshot", "") if ok else payload.get("error") or payload.get("screenshot", ""))
    else:
        print("· Instagram steps skipped (pass --ig-media <image> or set NEXUS_E2E_IG_MEDIA)")

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
