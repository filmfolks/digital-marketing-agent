"""CDP attach mode: reuse the owner's running Chrome, one new tab, nothing else touched."""

from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import AGENT, ROOT, load_module  # noqa: E402

sys.path.insert(0, str(AGENT))
import browser_core  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────
class FakePage:
    def __init__(self, ctx, owner="user"):
        self.ctx, self.owner, self.closed = ctx, owner, False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True
        self.ctx.pages.remove(self)


class FakeCtx:
    def __init__(self):
        self.pages, self.closed, self.stealth_applied = [], False, False
        self.pages.append(FakePage(self, "user"))            # the owner's x.com/home tab

    def new_page(self):
        p = FakePage(self, "nexus")
        self.pages.append(p)
        return p

    def close(self):
        self.closed = True

    def add_init_script(self, *a, **k):
        self.stealth_applied = True

    set_extra_http_headers = add_init_script


class FakeBrowser:
    def __init__(self, ctx):
        self.contexts, self.closed = [ctx], False

    def close(self):
        self.closed = True


class FakePW:
    def __init__(self, fail=False):
        self.ctx = FakeCtx()
        self.browser = FakeBrowser(self.ctx)
        self.fail, self.stopped, self.connected_to, self.launched = fail, False, None, False
        pw = self

        class Chromium:
            def connect_over_cdp(self, url, timeout=None):
                pw.connected_to = url
                if pw.fail:
                    raise RuntimeError("connect ECONNREFUSED 127.0.0.1:9232")
                return pw.browser

            def launch_persistent_context(self, **kw):
                pw.launched = True
                raise AssertionError("attach mode must not launch a browser")

        self.chromium = Chromium()

    def start(self):
        return self

    def stop(self):
        self.stopped = True


def fake_playwright(pw):
    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: pw
    pkg = types.ModuleType("playwright")
    pkg.sync_api = mod
    return mock.patch.dict(sys.modules, {"playwright": pkg, "playwright.sync_api": mod})


class Resolve(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("NEXUS_CDP_URL", None)

    def tearDown(self):
        self.env.stop()

    def test_precedence_and_forms(self):
        self.assertEqual(browser_core.resolve_cdp_url(), "")           # generic app: launch
        os.environ["NEXUS_CDP_URL"] = "http://127.0.0.1:9232/"
        self.assertEqual(browser_core.resolve_cdp_url(), "http://127.0.0.1:9232")
        self.assertEqual(browser_core.resolve_cdp_url("9333"), "http://127.0.0.1:9333")
        self.assertEqual(browser_core.resolve_cdp_url("localhost:9444"), "http://localhost:9444")
        for off in ("off", "0", "", "none"):
            self.assertEqual(browser_core.resolve_cdp_url(off), "", off)
        os.environ["NEXUS_CDP_URL"] = "off"
        self.assertEqual(browser_core.resolve_cdp_url(), "")

    def test_client_build_defaults_to_its_cdp_url(self):
        d = tempfile.mkdtemp()
        cfg = Path(d) / "client.json"
        cfg.write_text(json.dumps({"client_id": "acme", "cdp_url": "http://127.0.0.1:9232"}))
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": str(cfg)}):
            self.assertEqual(browser_core.resolve_cdp_url(), "http://127.0.0.1:9232")
            os.environ["NEXUS_CDP_URL"] = "off"                        # env still wins
            self.assertEqual(browser_core.resolve_cdp_url(), "")


class AttachSession(unittest.TestCase):
    def test_reuses_default_context_opens_and_closes_only_its_own_tab(self):
        pw = FakePW()
        user_tab = pw.ctx.pages[0]
        with fake_playwright(pw), mock.patch.object(browser_core, "load_stealth",
                                                    side_effect=AssertionError("no stealth in attach mode")):
            with browser_core.BrowserSession(platform="x", headless=True, cdp_url="http://127.0.0.1:9232") as s:
                self.assertTrue(s.attached)
                self.assertIs(s.ctx, pw.ctx)
                self.assertEqual(s.page.owner, "nexus")
                self.assertEqual(len(pw.ctx.pages), 2)
                self.assertEqual(s.stealth.mode, "none")
        self.assertEqual(pw.connected_to, "http://127.0.0.1:9232")
        self.assertFalse(pw.launched)
        self.assertEqual(pw.ctx.pages, [user_tab])                     # our tab gone, theirs kept
        self.assertFalse(user_tab.closed)
        self.assertFalse(pw.ctx.closed)                                # never ctx.close()
        self.assertFalse(pw.browser.closed)                            # never browser.close()
        self.assertFalse(pw.ctx.stealth_applied)                       # no UA / stealth scripts
        self.assertTrue(pw.stopped)

    def test_tab_closed_even_when_the_flow_raises(self):
        pw = FakePW()
        with fake_playwright(pw):
            with self.assertRaises(ValueError):
                with browser_core.BrowserSession(platform="x", cdp_url="9232"):
                    raise ValueError("flow failed")
        self.assertEqual(len(pw.ctx.pages), 1)
        self.assertFalse(pw.ctx.closed)

    def test_unreachable_chrome_is_a_clear_error(self):
        pw = FakePW(fail=True)
        with fake_playwright(pw):
            with self.assertRaises(browser_core.CDPUnavailable) as cm:
                browser_core.BrowserSession(platform="x", cdp_url="http://127.0.0.1:9232").__enter__()
        self.assertIn("--remote-debugging-port", str(cm.exception))
        self.assertIn("NEXUS_CDP_URL=off", str(cm.exception))
        self.assertTrue(pw.stopped)
        self.assertFalse(pw.launched)

    def test_logged_out_message_points_at_the_attached_chrome(self):
        with mock.patch.dict(os.environ, {"NEXUS_CDP_URL": "http://127.0.0.1:9232"}):
            msg = str(browser_core.LoggedOut("x"))
        self.assertIn("attached at http://127.0.0.1:9232", msg)

    def test_legacy_get_playwright_page_close_only_closes_our_tab(self):
        sba = load_module("nexus_sba_cdp", AGENT / "social_browser_agent.py")
        pw = FakePW()
        with fake_playwright(pw), mock.patch.dict(os.environ, {"NEXUS_CDP_URL": "http://127.0.0.1:9232"}):
            p, ctx, page = sba.get_playwright_page("instagram")
            self.assertEqual(len(pw.ctx.pages), 2)
            self.assertEqual(len(ctx.pages), 2)                       # proxies attribute access
            ctx.close()
            p.stop()
        self.assertEqual(len(pw.ctx.pages), 1)
        self.assertFalse(pw.ctx.closed)
        self.assertFalse(pw.browser.closed)


def _has_playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CliFlags(unittest.TestCase):
    @unittest.skipUnless(_has_playwright(), "playwright not installed")
    def test_social_agent_cdp_url_flag_reaches_the_session(self):
        port = _free_port()                                            # nothing listening
        env = dict(os.environ, NEXUS_BROWSER_FALLBACK="0")
        p = subprocess.run([sys.executable, str(AGENT / "social_browser_agent.py"), "--cdp-url",
                            f"http://127.0.0.1:{port}", "check_logins", "--platforms", "x"],
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT), env=env)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(p.returncode, 4, p.stderr[-500:])
        self.assertIn(f"no Chrome answering at http://127.0.0.1:{port}", out["results"][0]["message"])

    def test_publish_cdp_url_flag_is_passed_to_the_browser_fallback(self):
        pub = load_module("nexus_publish_cdp", AGENT / "publish.py")
        seen = {}

        def fake_run(args, **kw):
            seen["env"] = os.environ.get("NEXUS_CDP_URL")
            return subprocess.CompletedProcess(args, 0, json.dumps({"ok": True, "dry_run": True}), "")

        with mock.patch.dict(os.environ, {}), \
                mock.patch.object(sys, "argv", ["publish.py", "--list", "--cdp-url", "http://127.0.0.1:9232"]), \
                mock.patch.object(pub.subprocess, "run", fake_run):
            os.environ.pop("NEXUS_CDP_URL", None)
            with mock.patch("builtins.print"):
                pub.main()
            pub.run_browser(["x_post", "--text", "hi", "--dry-run"])
        self.assertEqual(seen["env"], "http://127.0.0.1:9232")

    def test_e2e_probe(self):
        e2e = load_module("nexus_e2e_cdp", ROOT / "scripts" / "e2e_check.py")
        ok, detail = e2e.cdp_probe(f"http://127.0.0.1:{_free_port()}", timeout=1)
        self.assertFalse(ok)
        self.assertIn("--remote-debugging-port", detail)


CHROME = shutil.which("google-chrome") or shutil.which("chromium") or os.environ.get("CHROME_PATH")


@unittest.skipUnless(CHROME and _has_playwright(), "needs Chrome + playwright")
class RealChrome(unittest.TestCase):
    """Start a real Chrome with --remote-debugging-port, attach, and check that
    the user's tab and the browser survive while ours is closed."""

    def setUp(self):
        self.port = _free_port()
        self.tmp = tempfile.mkdtemp()
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={self.port}",
                                      f"--user-data-dir={self.tmp}", "--no-first-run", "--no-sandbox",
                                      "data:text/html,<title>user-tab</title>"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/json/version", timeout=1).read()
                break
            except Exception:                                          # noqa: BLE001
                time.sleep(0.1)

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tabs(self):
        return [t for t in json.loads(urllib.request.urlopen(self.base + "/json/list", timeout=5).read())
                if t.get("type") == "page"]

    def test_attach_open_own_tab_and_leave_everything_else(self):
        before = {t["id"] for t in self.tabs()}
        self.assertEqual(len(before), 1)
        with browser_core.BrowserSession(platform="x", cdp_url=self.base) as s:
            self.assertTrue(s.attached)
            s.page.goto("data:text/html,<title>nexus-tab</title>")
            self.assertEqual(s.page.title(), "nexus-tab")
            self.assertEqual(len(self.tabs()), 2)
            ua = s.page.evaluate("navigator.userAgent")
        self.assertIn("HeadlessChrome", ua)                            # untouched: no UA override
        time.sleep(0.3)
        self.assertIsNone(self.proc.poll(), "Chrome must keep running")
        self.assertEqual({t["id"] for t in self.tabs()}, before)       # only our tab closed


class Python39Syntax(unittest.TestCase):
    """The owner's Mac runs /usr/bin/python3 3.9.6."""

    def test_every_module_parses_as_python_39(self):
        bad = []
        for p in sorted(list((ROOT / "agent").glob("*.py")) + list((ROOT / "scripts").glob("*.py")) +
                        list((ROOT / "tests").glob("*.py")) + list((ROOT / "skill").rglob("*.py"))):
            src = p.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src, feature_version=(3, 9))
            except SyntaxError as e:
                bad.append(f"{p.name}: {e}")
                continue
            has_future = "from __future__ import annotations" in src
            for node in ast.walk(tree):
                if isinstance(node, ast.Match if hasattr(ast, "Match") else ()):
                    bad.append(f"{p.name}:{node.lineno} match statement")
                if isinstance(node, (ast.FunctionDef, ast.AnnAssign, ast.arg)) and not has_future:
                    ann = getattr(node, "returns", None) or getattr(node, "annotation", None)
                    if ann is not None and any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
                                               for n in ast.walk(ann)):
                        bad.append(f"{p.name}:{node.lineno} X | Y annotation without __future__")
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "zip" and \
                        any(k.arg == "strict" for k in node.keywords):
                    bad.append(f"{p.name}:{node.lineno} zip(strict=)")
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "isinstance" and \
                        len(node.args) == 2 and isinstance(node.args[1], ast.BinOp):
                    bad.append(f"{p.name}:{node.lineno} isinstance(x, A | B)")
        self.assertEqual(bad, [])

    def test_posting_requirements_are_exact_pins(self):
        lines = [ln.split("#")[0].strip() for ln in (ROOT / "requirements-posting.txt").read_text().splitlines()]
        lines = [ln for ln in lines if ln]
        names = {ln.split("==")[0].lower() for ln in lines}
        self.assertTrue(all("==" in ln for ln in lines), lines)
        self.assertTrue({"playwright", "playwright-stealth", "python-dotenv", "requests", "pydantic"} <= names)
        self.assertFalse({"litellm", "google-genai", "openai"} & names)


if __name__ == "__main__":
    unittest.main()
