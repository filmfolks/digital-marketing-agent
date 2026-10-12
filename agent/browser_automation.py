#!/usr/bin/env python3
"""
Nexus Browser Automation Skill  🌐
==================================
Gives the Nexus AI agent the skill of operating a real Chrome/Chromium browser
using developer-tool level control — with EXPLICIT USER PERMISSION for every
sensitive action.

Two engines (auto-selected):
  1. Playwright  (pip install playwright && playwright install chromium)
     → https://github.com/microsoft/playwright          (Apache-2.0)
  2. Chrome DevTools Protocol (CDP) directly             (no heavy deps)
     → launch:  chrome --remote-debugging-port=9222
     → spec:    https://chromedevtools.github.io/devtools-protocol/

Open-source repos this skill interoperates with:
  • microsoft/playwright-mcp         — Playwright exposed as an MCP server
  • ChromeDevTools/chrome-devtools-mcp — official CDP MCP server
  • browser-use/browser-use          — LLM-driven browser agents
  • seleniumhq/selenium              — classic WebDriver fallback

Safety model ("browse only if user gives permission"):
  • Every command is logged to logs/browser_audit.jsonl (full audit trail).
  • Commands that MUTATE state (click/type/post) require the --allow-actions
    flag, which the UI sets only after the user grants "Browser Actions
    Permission" in Settings. Without it, read-only mode.
  • A deny-list blocks destructive targets (logout / delete account ...).

CLI examples:
  python3 browser_automation.py navigate --url https://example.com
  python3 browser_automation.py snapshot                       # text a11y tree
  python3 browser_automation.py extract --selector "h1, p"
  python3 browser_automation.py screenshot --out shots/page.png
  python3 browser_automation.py devtools --what console        # JS console msgs
  python3 browser_automation.py devtools --what perf           # timing metrics
  python3 browser_automation.py act --allow-actions --action click --target "button#publish"
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = ROOT / "logs" / "browser_audit.jsonl"

# Destructive patterns — refused even when actions are allowed
DENY_PATTERNS = [
    r"delete[_ -]?(my[_ -])?account", r"log\s*out", r"sign\s*out",
    r"remove[_ -]?payment", r"cancel[_ -]?subscription", r"/admin/(delete|users)",
]

PERMISSION_NOTE = (
    "BLOCKED: This is a state-changing browser action. The user must grant "
    "'Browser Actions Permission' in Nexus Settings (or pass --allow-actions "
    "after explicit consent). Read-only commands (navigate/snapshot/extract/"
    "screenshot/devtools) work without it."
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(command: str, args: dict, status: str, detail: str = "") -> None:
    """Every browser operation is documented — success or failure."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": now_iso(),
        "engine": get_engine_name(),
        "command": command,
        "args": {k: v for k, v in args.items() if k != "func"},
        "status": status,            # ok | blocked | error
        "detail": detail[:500],
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[audit] {status.upper():7s} {command}: {(detail or status)[:120]}")


def is_denied(*targets: str) -> bool:
    joined = " ".join(t.lower() for t in targets if t)
    return any(re.search(p, joined) for p in DENY_PATTERNS)


def get_engine_name() -> str:
    try:
        import playwright  # noqa: F401
        return "playwright"
    except ImportError:
        return "cdp"


# ────────────────────────────────────────────────────────────────
# ENGINE 1 — Playwright (preferred)
# ────────────────────────────────────────────────────────────────
class PlaywrightEngine:
    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._headless = headless
        self.browser = None
        self.page = None
        self.console_messages = []

    def _ensure(self):
        if self.browser is None:
            self.browser = self._pw.chromium.launch(headless=self._headless)
            ctx = self.browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="NexusAgent/1.0 (+local social media manager)")
            self.page = ctx.new_page()
            self.page.on("console", lambda m: self.console_messages.append(
                {"type": m.type, "text": m.text, "ts": now_iso()}))

    def navigate(self, url):
        self._ensure()
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(800)
        return {"url": self.page.url, "title": self.page.title()}

    def snapshot(self):
        self._ensure()
        # Accessibility-style snapshot (like playwright-mcp's browser_snapshot)
        return self.page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('h1,h2,h3,a,button,input,textarea,[role=button],[role=link]')
              .forEach(el => {
                const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0,80);
                if (label || el.href) out.push(`${el.tagName.toLowerCase()}${el.id ? '#'+el.id : ''} :: ${label || el.href}`);
              });
            return out.slice(0, 200).join('\\n');
        }""")

    def extract(self, selector):
        self._ensure()
        return [t.strip() for t in self.page.locator(selector).all_inner_texts() if t.strip()][:100]

    def screenshot(self, path):
        self._ensure()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.page.screenshot(path=path, full_page=True)
        return path

    def run_action(self, action, target, value=""):
        self._ensure()
        if action == "click":
            self.page.click(target, timeout=10000)
        elif action == "type":
            self.page.fill(target, value, timeout=10000)
        elif action == "press":
            self.page.keyboard.press(target)
        elif action == "scroll":
            self.page.mouse.wheel(0, int(value or 600))
        else:
            raise ValueError(f"Unknown action '{action}'")
        self.page.wait_for_timeout(500)
        return f"{action} '{target}' done — page title: {self.page.title()}"

    def devtools(self, what):
        self._ensure()
        if what == "console":
            return self.console_messages[-50:]
        if what == "perf":
            return self.page.evaluate("""() => ({
                domContentLoaded: Math.round(performance.timing.domContentLoadedEventEnd - performance.timing.navigationStart),
                loadComplete: Math.round(performance.timing.loadEventEnd - performance.timing.navigationStart),
                resourceCount: performance.getEntriesByType('resource').length,
            })""")
        if what == "network":
            return self.page.evaluate("""() => performance.getEntriesByType('resource')
                .slice(0,30).map(r => ({url:r.name.slice(0,120), status:r.responseStatus, ms:Math.round(r.duration)}))""")
        return {}

    def close(self):
        try:
            if self.browser:
                self.browser.close()
            self._pw.stop()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────
# ENGINE 2 — Chrome DevTools Protocol over websocket (fallback)
# Requires: chrome --remote-debugging-port=9222   (pip install websocket-client)
# ────────────────────────────────────────────────────────────────
class CDPEngine:
    BASE = "http://127.0.0.1:9222"

    def __init__(self):
        import websocket  # pip install websocket-client
        self._ws_mod = websocket
        self.ws = None
        self._id = 0

    def _attach(self):
        if self.ws:
            return
        try:
            tabs = json.loads(urllib.request.urlopen(f"{self.BASE}/json", timeout=3).read())
        except Exception as e:
            raise RuntimeError(
                "Chrome DevTools endpoint unreachable at :9222. Launch Chrome with "
                "--remote-debugging-port=9222. "
                f"({e})")
        page = next((t for t in tabs if t.get("type") == "page"), None)
        if not page:
            page = json.loads(urllib.request.urlopen(f"{self.BASE}/json/new?about:blank", timeout=3).read())
        self.ws = self._ws_mod.create_connection(page["webSocketDebuggerUrl"], timeout=20)

    def _cmd(self, method, params=None, wait=True):
        self._attach()
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not wait:
            return {}
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result", {})

    def _eval(self, expr):
        r = self._cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def navigate(self, url):
        self._cmd("Page.enable")
        self._cmd("Page.navigate", {"url": url})
        time.sleep(2.5)
        return {"url": self._eval("location.href") or url, "title": self._eval("document.title") or ""}

    def snapshot(self):
        return self._eval("""(() => {
            const out=[];
            document.querySelectorAll('h1,h2,h3,a,button,input,textarea').forEach(el=>{
              const l=(el.innerText||el.value||el.getAttribute('aria-label')||'').trim().slice(0,80);
              if(l||el.href) out.push(el.tagName.toLowerCase()+(el.id?'#'+el.id:'')+' :: '+(l||el.href));
            });
            return out.slice(0,200).join('\\n');})()""") or "(empty page)"

    def extract(self, selector):
        val = self._eval("(() => Array.from(document.querySelectorAll(%s))"
                         ".map(e => e.innerText.trim()).filter(Boolean).slice(0,100))()"
                         % json.dumps(selector))
        return val or []

    def screenshot(self, path):
        import base64
        r = self._cmd("Page.captureScreenshot", {"format": "png"})
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    def run_action(self, action, target, value=""):
        if action == "click":
            hit = self._eval("(() => {const e=document.querySelector(%s);"
                             "if(!e) return false; e.click(); return true;})()" % json.dumps(target))
            if not hit:
                raise RuntimeError(f"Selector not found: {target}")
        elif action == "type":
            self._eval("(() => {const e=document.querySelector(%s);"
                       "if(!e) return false; e.focus(); e.value=%s;"
                       "e.dispatchEvent(new Event('input',{bubbles:true})); return true;})()"
                       % (json.dumps(target), json.dumps(value)))
        else:
            raise ValueError(f"Action '{action}' needs the Playwright engine")
        time.sleep(0.5)
        return f"{action} '{target}' executed via CDP"

    def devtools(self, what):
        if what == "console":
            return self._eval("window.__nexusConsole || []") or \
                   ["(CDP console streaming requires Log.enable — see README; using page-side buffer)"]
        if what == "perf":
            return self._eval("""({
                domContentLoaded: Math.round(performance.timing.domContentLoadedEventEnd-performance.timing.navigationStart),
                loadComplete: Math.round(performance.timing.loadEventEnd-performance.timing.navigationStart)})""")
        return {}

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────
# Engine factory
# ────────────────────────────────────────────────────────────────
def _playwright_ready():
    """Playwright *module* importable AND a Chromium binary actually downloaded.

    BUG FIX: previously make_engine() only caught ImportError. When the
    playwright pip package is installed but `playwright install chromium`
    was never run, launching raised a non-ImportError and every browser
    command died with a confusing 'Executable doesn't exist' crash.
    Now we probe properly and fall back to CDP (or emit an actionable error).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright module not installed"
    try:
        pw = sync_playwright().start()
        try:
            exe = pw.chromium.executable_path
        finally:
            pw.stop()
        if not exe or not os.path.exists(exe):
            return False, "chromium binary missing — run: playwright install chromium"
        return True, exe
    except Exception as e:
        return False, f"playwright unusable ({str(e)[:120]}) — run: playwright install chromium"


def make_engine(args):
    if getattr(args, "force_cdp", False):
        return CDPEngine()
    ok, why = _playwright_ready()
    if ok:
        return PlaywrightEngine(headless=not getattr(args, "headed", False))
    # Fallback chain: CDP if websocket-client present, else raise clear guidance.
    try:
        import websocket  # noqa: F401
        print(f"[info] Playwright unavailable ({why}) → using Chrome DevTools Protocol on :9222")
        return CDPEngine()
    except ImportError:
        raise RuntimeError(
            f"No usable browser engine: {why}. Fix either:\n"
            f"  A) pip install playwright && playwright install chromium\n"
            f"  B) pip install websocket-client, then launch Chrome with "
            f"--remote-debugging-port=9222 and pass --force-cdp")


def emit(result):
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


# ────────────────────────────────────────────────────────────────
# Command handlers
# ────────────────────────────────────────────────────────────────
def cmd_navigate(engine, args):
    if is_denied(args.url):
        raise PermissionError(f"URL matches deny-list pattern: {args.url}")
    emit(engine.navigate(args.url))
    audit("navigate", vars(args), "ok", args.url)


def cmd_snapshot(engine, args):
    emit({"snapshot": engine.snapshot()})
    audit("snapshot", vars(args), "ok")


def cmd_extract(engine, args):
    emit({"selector": args.selector, "texts": engine.extract(args.selector)})
    audit("extract", vars(args), "ok", args.selector)


def cmd_screenshot(engine, args):
    path = engine.screenshot(args.out)
    emit({"screenshot": path})
    audit("screenshot", vars(args), "ok", path)


def cmd_act(engine, args):
    """State-changing action — requires explicit user permission."""
    if not args.allow_actions:
        audit("act", vars(args), "blocked", PERMISSION_NOTE)
        print(PERMISSION_NOTE)
        sys.exit(2)
    if is_denied(args.target, args.task or ""):
        raise PermissionError(f"Target matches deny-list: {args.target}")
    emit({"result": engine.run_action(args.action, args.target, args.value)})
    audit("act", vars(args), "ok", f"{args.action} {args.target}")


def cmd_devtools(engine, args):
    emit({"what": args.what, "data": engine.devtools(args.what)})
    audit("devtools", vars(args), "ok", args.what)


def precheck(args):
    """BUG FIX: permission & deny-list checks must run BEFORE any browser is
    launched. Previously main() did `engine = make_engine(args)` first, so a
    blocked 'act' request either wasted a full Chromium launch or — with no
    browser installed — crashed with an engine RuntimeError instead of the
    clean PERMISSION REQUIRED / deny-list message and exit code 2."""
    if args.cmd == "act":
        if not args.allow_actions:
            audit("act", vars(args), "blocked", PERMISSION_NOTE)
            print(PERMISSION_NOTE)
            sys.exit(2)
        if is_denied(args.target, args.task or ""):
            audit("act", vars(args), "blocked", f"Deny-list target: {args.target}")
            print(f"⛔ BLOCKED by safety deny-list: {args.target}")
            sys.exit(2)
    if args.cmd == "navigate" and is_denied(args.url):
        audit("navigate", vars(args), "blocked", args.url)
        print(f"⛔ BLOCKED by safety deny-list URL: {args.url}")
        sys.exit(2)


def main():
    p = argparse.ArgumentParser(
        description="Nexus Browser Automation Skill (Playwright / Chrome DevTools Protocol)")
    p.add_argument("--force-cdp", action="store_true", help="Use raw CDP instead of Playwright")
    p.add_argument("--headed", action="store_true", help="Show the browser window (user watches live)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("navigate"); sp.add_argument("--url", required=True); sp.set_defaults(func=cmd_navigate)
    sp = sub.add_parser("snapshot"); sp.set_defaults(func=cmd_snapshot)
    sp = sub.add_parser("extract"); sp.add_argument("--selector", required=True); sp.set_defaults(func=cmd_extract)
    sp = sub.add_parser("screenshot"); sp.add_argument("--out", default="shots/nexus_page.png"); sp.set_defaults(func=cmd_screenshot)
    sp = sub.add_parser("act")
    sp.add_argument("--action", choices=["click", "type", "press", "scroll"], required=True)
    sp.add_argument("--target", required=True, help="CSS selector / key name")
    sp.add_argument("--value", default="", help="Text to type / scroll px")
    sp.add_argument("--task", default="", help="Human-readable task description for the audit log")
    sp.add_argument("--allow-actions", action="store_true", help="User granted browser-action permission in Settings")
    sp.set_defaults(func=cmd_act)
    sp = sub.add_parser("devtools"); sp.add_argument("--what", choices=["console", "perf", "network"], default="console"); sp.set_defaults(func=cmd_devtools)

    args = p.parse_args()
    precheck(args)          # permission/deny checks BEFORE launching a browser
    engine = make_engine(args)
    try:
        args.func(engine, args)
    except SystemExit:
        raise
    except Exception as e:
        audit(args.cmd, vars(args), "error", str(e))
        print(json.dumps({"error": str(e), "type": type(e).__name__}, ensure_ascii=False))
        sys.exit(1)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
