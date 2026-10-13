"""browser_core + social_flows against a fake Playwright page (no browser needed)."""

from __future__ import annotations

import os
from unittest import mock

import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

core = load_agent("browser_core")
core.TIMEOUT_SCALE = 0          # fake page: never wait for elements
sys.modules["browser_core"] = core
flows = load_agent("social_flows")


# ── fake page ────────────────────────────────────────────────────────────────
class FakeLoc:
    def __init__(self, page, key):
        self.page, self.key = page, key

    @property
    def first(self):
        return self

    def _el(self):
        return self.page.elements.get(self.key)

    def count(self):
        return 1 if self._el() is not None else 0

    def is_visible(self):
        el = self._el()
        return el is not None and el.get("visible", True)

    def is_disabled(self):
        return bool((self._el() or {}).get("disabled"))

    def click(self, **kw):
        el = self._el() or {}
        if el.get("blocked"):                       # invisible overlay intercepts the pointer
            raise RuntimeError("<div> intercepts pointer events")
        self.page.clicks.append(self.key)
        cb = el.get("on_click")
        if cb:
            cb(self.page)

    def focus(self):
        self.page.focused = self.key

    def evaluate(self, js, *a):
        if "focus" in js:                          # e => e.focus() (FB lexical editor)
            self.page.focused = self.key
        return None

    def dispatch_event(self, name):
        self.page.clicks.append(f"dispatch:{self.key}")
        cb = (self._el() or {}).get("on_click")
        if cb:
            cb(self.page)

    def get_attribute(self, name):
        return (self._el() or {}).get(name)

    def set_input_files(self, files):
        self.page.uploads.extend(files)


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def insert_text(self, t):
        self.page.typed += t

    def type(self, ch, delay=0):
        self.page.typed += ch

    def press(self, k):
        self.page.typed += "\n" if k == "Shift+Enter" else ""
        self.page.presses.append(k)
        if k == "Enter" and self.page.focused:      # keyboard activation of the focused element
            cb = (self.page.elements.get(self.page.focused) or {}).get("on_click")
            self.page.clicks.append(f"key:{self.page.focused}")
            if cb:
                cb(self.page)
        cb = self.page.on_key.get(k)
        if cb:
            cb(self.page)


class FakeDownload:
    def __init__(self, data):
        self.data = data

    def save_as(self, path):
        Path(path).write_bytes(self.data)


class FakePage:
    def __init__(self, elements, url_after_goto=None):
        self.elements = elements
        self.clicks, self.uploads, self.typed, self.visits = [], [], "", []
        self.url = ""
        self.url_after_goto = url_after_goto
        self.keyboard = FakeKeyboard(self)
        self.presses, self.focused, self.on_key, self.fronted = [], None, {}, 0
        self.eval_result = []

    def bring_to_front(self):
        self.fronted += 1

    def evaluate(self, js):
        return self.eval_result

    @contextmanager
    def expect_file_chooser(self, timeout=0):
        page = self
        holder = types.SimpleNamespace(value=types.SimpleNamespace(set_files=lambda f: page.uploads.extend(f)))
        yield holder

    def goto(self, url, **kw):
        self.visits.append(url)
        self.url = self.url_after_goto or url

    def wait_for_timeout(self, ms):
        pass

    def get_by_role(self, role, name=None):
        return FakeLoc(self, f"role:{role}:{name}")

    def get_by_label(self, v):
        return FakeLoc(self, f"label:{v}")

    def get_by_text(self, v, exact=False):
        return FakeLoc(self, f"text:{v}")

    def get_by_placeholder(self, v):
        return FakeLoc(self, f"placeholder:{v}")

    def locator(self, css):
        return FakeLoc(self, css)

    @contextmanager
    def expect_download(self, timeout=0):
        holder = types.SimpleNamespace(value=FakeDownload(b"\x89PNG fake"))
        yield holder


X_OK = {
    "[data-testid='tweetTextarea_0']": {},
    "[data-testid='tweetButton']": {},
    "input[data-testid='fileInput']": {},
}


def nopace():
    return core.Pacer(scale=0)


class StealthLoader(unittest.TestCase):
    def test_prefers_v2_stealth_class(self):
        applied = []

        class Stealth:
            def apply_stealth_sync(self, target):
                applied.append(target)
        plan = core.load_stealth(lambda name: types.SimpleNamespace(Stealth=Stealth, stealth_sync=lambda p: None))
        self.assertEqual(plan.mode, "v2")
        plan.apply("ctx")
        self.assertEqual(applied, ["ctx"])

    def test_falls_back_to_v1_stealth_sync(self):
        plan = core.load_stealth(lambda name: types.SimpleNamespace(stealth_sync=lambda p: None))
        self.assertEqual(plan.mode, "v1")

    def test_missing_package_is_reported_not_swallowed(self):
        def boom(name):
            raise ImportError("No module named 'pkg_resources'")
        plan = core.load_stealth(boom)
        self.assertEqual(plan.mode, "none")
        self.assertIn("pkg_resources", plan.reason)


class Environment(unittest.TestCase):
    def test_channel_detection_per_os(self):
        mac_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        self.assertEqual(core.detect_channel("Darwin", lambda p: p == mac_chrome), "chrome")
        self.assertEqual(core.detect_channel("Windows", lambda p: "msedge.exe" in p), "msedge")
        self.assertEqual(core.detect_channel("Darwin", lambda p: False), None)

    def test_channel_env_override(self):
        import os
        os.environ["NEXUS_BROWSER_CHANNEL"] = "msedge"
        try:
            self.assertEqual(core.detect_channel("Linux", lambda p: False), "msedge")
            os.environ["NEXUS_BROWSER_CHANNEL"] = "chromium"
            self.assertIsNone(core.detect_channel("Linux", lambda p: True))
        finally:
            del os.environ["NEXUS_BROWSER_CHANNEL"]

    def test_launch_kwargs_never_hardcode_ua_and_use_per_platform_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            saved = core.PROFILES_DIR
            core.PROFILES_DIR = Path(d)
            try:
                kw = core.BrowserSession(platform="x", channel="chrome").launch_kwargs()
                kw2 = core.BrowserSession(platform="canva").launch_kwargs()
            finally:
                core.PROFILES_DIR = saved
        self.assertNotIn("user_agent", kw)
        self.assertEqual(kw["channel"], "chrome")
        self.assertNotEqual(kw["user_data_dir"], kw2["user_data_dir"])
        self.assertTrue(kw["user_data_dir"].endswith("x"))
        self.assertNotEqual(kw.get("timezone_id"), "America/New_York" if core.system_timezone() != "America/New_York" else None)

    def test_profile_dir_sanitises_names(self):
        with tempfile.TemporaryDirectory() as d:
            saved = core.PROFILES_DIR
            core.PROFILES_DIR = Path(d)
            try:
                p = core.profile_dir("../../etc")
            finally:
                core.PROFILES_DIR = saved
            self.assertEqual(p.parent, Path(d))


class Locators(unittest.TestCase):
    def test_first_visible_candidate_wins(self):
        page = FakePage({"[data-testid='b']": {"visible": False}, "css-c": {}})
        loc = core.find_first(page, [core.Candidate("testid", "a"), core.Candidate("testid", "b"),
                                     core.Candidate("css", "css-c")], timeout_ms=0)
        self.assertEqual(loc.key, "css-c")

    def test_not_found_lists_every_candidate(self):
        with self.assertRaises(core.ElementNotFound) as cm:
            core.find_first(FakePage({}), [core.Candidate("role", "button", "Post")], timeout_ms=0)
        self.assertIn("role:button[Post]", str(cm.exception))


class LoginDetection(unittest.TestCase):
    @mock.patch.dict(os.environ, {"NEXUS_CDP_URL": "off"})   # independent of a .env attach URL
    def test_login_url_means_logged_out(self):
        page = FakePage({}, url_after_goto="https://x.com/i/flow/login")
        with self.assertRaises(core.LoggedOut) as cm:
            flows.x_post(page, "hi", pacer=nopace())
        self.assertIn("social_browser_agent.py login --platform x", str(cm.exception))

    def test_sign_in_button_means_logged_out(self):
        page = FakePage({"[data-testid='loginButton']": {}})
        self.assertTrue(core.is_logged_out(page, "x"))

    def test_check_login_needs_a_positive_marker(self):
        r = flows.check_login(FakePage({}), "canva", "https://www.canva.com/", pacer=nopace())
        self.assertFalse(r["logged_in"])                    # blank/error page is NOT "logged in"
        r = flows.check_login(FakePage({"role:button:Create a design": {}}), "canva",
                              "https://www.canva.com/", pacer=nopace())
        self.assertTrue(r["logged_in"])

    def test_bot_check_page_is_reported_never_solved(self):
        page = FakePage({"text:Verify you are human": {}, "role:button:Share": {}})
        with self.assertRaises(core.HumanCheckRequired) as cm:
            flows.canva_export(page, "https://www.canva.com/design/X/edit", pacer=nopace())
        self.assertIn("by hand", str(cm.exception))
        self.assertEqual(page.clicks, [])

    @mock.patch.dict(os.environ, {"NEXUS_CDP_URL": "off"})   # independent of a .env attach URL
    def test_check_login_reports_cleanly(self):
        r = flows.check_login(FakePage({}, url_after_goto="https://www.canva.com/login"), "canva",
                              "https://www.canva.com/", pacer=nopace())
        self.assertFalse(r["logged_in"])
        self.assertIn("Log in once", r["message"])


class XFlow(unittest.TestCase):
    def test_dry_run_fills_composer_but_never_clicks_post(self):
        page = FakePage(dict(X_OK))
        r = flows.x_post(page, "Hello from Nexus", dry_run=True, pacer=nopace(), screenshot=lambda t: f"/s/{t}.png")
        self.assertTrue(r["dry_run"])
        self.assertEqual(page.typed, "Hello from Nexus")
        self.assertNotIn("[data-testid='tweetButton']", page.clicks)
        self.assertEqual(r["screenshot"], "/s/dryrun.png")
        self.assertEqual(page.visits, [flows.X_COMPOSE_URL])

    def test_real_post_clicks_and_confirms(self):
        els = dict(X_OK)
        page = FakePage(els)
        sent = lambda p: p.elements.__setitem__("text:Your post was sent", {})   # noqa: E731
        page.on_key = {"Meta+Enter": sent, "Control+Enter": sent}
        r = flows.x_post(page, "hi", pacer=nopace())
        self.assertTrue(r["ok"])
        # Submitted with X's shortcut (the typeahead layer blocks clicking Post), tab in front.
        self.assertTrue({"Meta+Enter", "Control+Enter"} & set(page.presses))
        self.assertNotIn("[data-testid='tweetButton']", page.clicks)
        self.assertGreaterEqual(page.fronted, 1)

    def test_media_is_attached(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            page = FakePage(dict(X_OK))
            flows.x_post(page, "pic", [f.name], dry_run=True, pacer=nopace())
            self.assertEqual(page.uploads, [f.name])

    def test_long_post_requires_flag_and_detects_missing_premium(self):
        text = "x" * 1200
        with self.assertRaises(flows.FlowError):
            flows.x_post(FakePage(dict(X_OK)), text, pacer=nopace())
        els = dict(X_OK)
        els["[data-testid='tweetButton']"] = {"disabled": True}
        with self.assertRaises(flows.FlowError) as cm:
            flows.x_post(FakePage(els), text, long_post=True, pacer=nopace())
        self.assertIn("Premium", str(cm.exception))
        r = flows.x_post(FakePage(dict(X_OK)), text, long_post=True, dry_run=True, pacer=nopace())
        self.assertEqual(r["chars"], 1200)

    def test_falls_back_to_role_selector_when_testid_changes(self):
        page = FakePage({"role:textbox:Post text": {}, "role:button:Post": {}})
        r = flows.x_post(page, "resilient", dry_run=True, pacer=nopace())
        self.assertTrue(r["ok"])


class Navigation(unittest.TestCase):
    def test_site_unreachable_is_one_clear_line(self):
        page = FakePage(dict(X_OK))

        def boom(url, **kw):
            raise RuntimeError("Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at x\nCall log: ...")
        page.goto = boom
        with self.assertRaises(flows.FlowError) as cm:
            flows.x_post(page, "hi", pacer=nopace())
        self.assertNotIn("Call log", str(cm.exception))
        r = flows.check_login(page, "x", "https://x.com/home", pacer=nopace())
        self.assertFalse(r["logged_in"])


class CanvaFlow(unittest.TestCase):
    def test_export_downloads_the_design(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "design.png"
            page = FakePage({"role:button:Share": {}, "role:button:Download": {},
                             "[role='dialog'] button:has-text('Download')": {}})
            r = flows.canva_export(page, "https://www.canva.com/design/DAF123/edit", str(out), pacer=nopace())
            self.assertTrue(out.is_file())
            self.assertGreater(r["bytes"], 0)
            self.assertEqual(page.clicks[0], "role:button:Share")

    def test_rejects_non_design_url(self):
        with self.assertRaises(flows.FlowError):
            flows.canva_export(FakePage({}), "https://evil.example/design", pacer=nopace())

    def test_broken_design_link_gives_a_clear_error(self):
        page = FakePage({"text:We couldn't load this page": {}})
        with self.assertRaises(flows.FlowError) as cm:
            flows.canva_export(page, "https://www.canva.com/design/BAD/edit", pacer=nopace())
        self.assertIn("could not open the design", str(cm.exception))

    def test_logged_out_canva_is_detected(self):
        page = FakePage({"role:button:Log in": {}})
        with self.assertRaises(core.LoggedOut):
            flows.canva_export(page, "https://www.canva.com/design/X/edit", pacer=nopace())


class Logging(unittest.TestCase):
    def test_json_formatter_emits_fields(self):
        import json
        import logging
        rec = logging.LogRecord("nexus.browser", logging.INFO, __file__, 1, "hello", None, None)
        rec.fields = {"platform": "x"}
        out = json.loads(core.JsonFormatter().format(rec))
        self.assertEqual(out["platform"], "x")
        self.assertEqual(out["msg"], "hello")



IG_OK = {
    "svg[aria-label='Home']": {},
    "svg[aria-label='New post']": {},
    "[role='dialog'] input[type='file']": {},
    "role:button:Next": {},
    "role:textbox:Write a caption...": {},
    "role:button:Share": {},
}


class InstagramFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = str(Path(self.tmp.name) / "burger.png")
        Path(self.img).write_bytes(b"\x89PNG fake")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_uploads_and_captions_but_never_shares(self):
        page = FakePage(dict(IG_OK))
        r = flows.instagram_post(page, "Mini burgers 🍔 #acmebites", [self.img], dry_run=True,
                                 pacer=nopace(), screenshot=lambda t: f"/s/{t}.png")
        self.assertTrue(r["dry_run"])
        self.assertEqual(page.uploads, [self.img])
        self.assertEqual(page.typed, "Mini burgers 🍔 #acmebites ")   # trailing space closes the hashtag typeahead
        self.assertEqual(page.clicks.count("role:button:Next"), 2)
        self.assertNotIn("role:button:Share", page.clicks)
        self.assertEqual(r["screenshot"], "/s/dryrun.png")

    def test_real_post_needs_confirmation(self):
        els = dict(IG_OK)
        els["role:button:Share"] = {"on_click": lambda p: p.elements.__setitem__("text:Your post has been shared.", {})}
        r = flows.instagram_post(FakePage(els), "hi", [self.img], pacer=nopace())
        self.assertTrue(r["ok"])
        with self.assertRaises(flows.FlowError):
            flows.instagram_post(FakePage(dict(IG_OK)), "hi", [self.img], pacer=nopace())

    def test_logged_out_is_detected_before_anything_is_clicked(self):
        page = FakePage(dict(IG_OK), url_after_goto="https://www.instagram.com/accounts/login/")
        with self.assertRaises(core.LoggedOut):
            flows.instagram_post(page, "hi", [self.img], dry_run=True, pacer=nopace())
        self.assertEqual(page.clicks, [])

    def test_logged_out_landing_without_login_form_is_not_mistaken_for_logged_in(self):
        # Regression (live run 2026-10-12): the Meta sign-up page has no username
        # field; a loose "Create" selector clicked "Create new account".
        page = FakePage({"text:Create": {}, "text:Get started on Instagram": {}})
        with self.assertRaises(core.LoggedOut):
            flows.instagram_post(page, "hi", [self.img], dry_run=True, pacer=nopace())
        self.assertEqual(page.clicks, [])
        page = FakePage({"text:Create": {}, "svg[aria-label='New post']": {}})   # no marker, no login hints
        with self.assertRaises(core.LoggedOut):
            flows.instagram_post(page, "hi", [self.img], dry_run=True, pacer=nopace())
        self.assertEqual(page.clicks, [])

    def test_validation(self):
        with self.assertRaises(flows.FlowError):
            flows.validate_instagram("text only", [])
        with self.assertRaises(flows.FlowError):
            flows.validate_instagram("x" * 2201, [self.img])
        with self.assertRaises(flows.FlowError):
            flows.validate_instagram(" ".join(f"#t{i}" for i in range(31)), [self.img])
        with self.assertRaises(flows.FlowError):
            flows.validate_instagram("hi", [self.img + ".pdf"])

    def test_publish_routes_instagram_dry_run_to_the_new_flow(self):
        pub = load_agent("publish")
        args = pub.browser_args_for("instagram", "cap", self.img, True)
        self.assertEqual(args[:2], ["--allow-actions", "ig_post"])
        self.assertIn("--dry-run", args)
        self.assertIsNone(pub.browser_args_for("instagram", "cap", "", True))

if __name__ == "__main__":
    unittest.main()
