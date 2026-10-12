"""API-first publishing with retries/backoff and the browser fallback (all mocked)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

pub = load_agent("publish")


class Script:
    """A sequence of canned results; records calls."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append(a)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def run(platform="x", api=None, browser=None, **kw):
    sleeps = []
    res = pub.publish_with_fallback(
        platform, kw.pop("text", "hello"), {}, kw.pop("media", ""),
        api_call=api, browser_runner=browser or Script({"ok": True, "exit": 0}),
        sleep=sleeps.append, backoff_base=kw.pop("backoff_base", 1.0),
        api_retries=kw.pop("api_retries", 2), browser_retries=kw.pop("browser_retries", 1), **kw)
    return res, sleeps


class ApiFirst(unittest.TestCase):
    def test_api_success_never_touches_the_browser(self):
        browser = Script({"ok": True, "exit": 0})
        res, sleeps = run(api=Script({"ok": True, "status": 201, "id": "1"}), browser=browser)
        self.assertTrue(res["ok"])
        self.assertEqual(res["route"], "api")
        self.assertEqual(browser.calls, [])
        self.assertEqual(sleeps, [])

    def test_transient_5xx_is_retried_then_succeeds_on_api(self):
        api = Script({"ok": False, "status": 503}, {"ok": True, "status": 201, "id": "9"})
        res, sleeps = run(api=api)
        self.assertEqual(res["route"], "api")
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(len(sleeps), 1)

    def test_rate_limit_retries_with_growing_backoff_then_falls_back(self):
        api = Script({"ok": False, "status": 429, "error": "Too Many Requests"})
        browser = Script({"ok": True, "exit": 0})
        res, sleeps = run(api=api, browser=browser, api_retries=3)
        self.assertEqual(len(api.calls), 4)
        self.assertEqual(len(sleeps), 3)
        self.assertLess(sleeps[0], sleeps[2])          # exponential (jitter is ±25%)
        self.assertEqual(res["route"], "browser")
        self.assertTrue(res["ok"])
        self.assertIn("Too Many", res["api_error"])

    def test_missing_keys_go_straight_to_the_browser(self):
        api = Script({"ok": False, "error": "Missing Twitter/X bearer token"})
        browser = Script({"ok": True, "exit": 0})
        res, sleeps = run(api=api, browser=browser)
        self.assertEqual(len(api.calls), 1)            # no pointless retry
        self.assertEqual(sleeps, [])
        self.assertEqual(res["route"], "browser")
        self.assertEqual(browser.calls[0][0][:2], ["--allow-actions", "x_post"])

    def test_4xx_auth_error_falls_back_without_retry(self):
        api = Script({"ok": False, "status": 403, "error": "forbidden"})
        res, _ = run(api=api)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(res["route"], "browser")

    def test_attempts_record_every_route(self):
        api = Script({"ok": False, "status": 500})
        browser = Script({"ok": False, "exit": 4, "error": "selector"}, {"ok": True, "exit": 0})
        res, _ = run(api=api, browser=browser, api_retries=1, browser_retries=2)
        routes = [(a["route"], a["ok"]) for a in res["attempts"]]
        self.assertEqual(routes, [("api", False), ("api", False), ("browser", False), ("browser", True)])


class BrowserFallback(unittest.TestCase):
    def test_logged_out_is_not_retried_and_is_surfaced(self):
        browser = Script({"ok": False, "exit": 3, "logged_out": True, "error": "Not logged in to x"})
        res, _ = run(api=Script({"ok": False, "error": "Missing"}), browser=browser, browser_retries=3)
        self.assertEqual(len(browser.calls), 1)
        self.assertFalse(res["ok"])
        self.assertTrue(res["logged_out"])
        self.assertEqual(res["attempts"][-1]["outcome"], "logged_out")

    def test_fallback_disabled_reports_api_error_only(self):
        browser = Script({"ok": True, "exit": 0})
        res, _ = run(api=Script({"ok": False, "status": 401, "error": "bad"}), browser=browser, fallback=False)
        self.assertEqual(browser.calls, [])
        self.assertEqual(res["route"], "api")
        self.assertIn("disabled", res["fallback"])

    def test_platform_without_browser_flow(self):
        res, _ = run(platform="blogger", api=Script({"ok": False, "error": "Missing"}))
        self.assertEqual(res["route"], "api")
        self.assertIn("no browser flow", res["fallback"])

    def test_browser_dry_run_flag_is_passed_through(self):
        browser = Script({"ok": True, "exit": 0, "dry_run": True})
        res, _ = run(api=Script({"ok": False, "error": "Missing"}), browser=browser, browser_dry_run=True)
        self.assertIn("--dry-run", browser.calls[0][0])
        self.assertTrue(res["dry_run"])


class ArgsTranslation(unittest.TestCase):
    def test_x_long_post_flag_only_when_enabled(self):
        long = "a" * 400
        self.assertNotIn("--long-post", pub.browser_args_for("x", long, "", False))
        import os
        os.environ["NEXUS_X_LONG_POST"] = "1"
        try:
            self.assertIn("--long-post", pub.browser_args_for("twitter", long, "", False))
        finally:
            del os.environ["NEXUS_X_LONG_POST"]

    def test_local_media_is_attached_remote_url_is_not(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            self.assertIn(f.name, pub.browser_args_for("x", "hi", f.name, False))
        self.assertNotIn("--media", pub.browser_args_for("x", "hi", "https://cdn/x.png", False))

    def test_instagram_needs_a_local_image(self):
        self.assertIsNone(pub.browser_args_for("instagram", "hi", "", False))

    def test_unknown_browser_output_is_parsed_safely(self):
        self.assertEqual(pub.classify_api_failure({"status": 502}), "retry")
        self.assertEqual(pub.classify_api_failure({"status": 400}), "fallback")
        self.assertEqual(pub.classify_api_failure({"error": "Missing"}), "fallback")


class AuditRoute(unittest.TestCase):
    def test_publish_job_writes_route_and_attempts_to_the_audit_log(self):
        with tempfile.TemporaryDirectory() as d:
            saved_audit, saved_run, saved_pub = pub.AUDIT, pub.run_browser, dict(pub.PUBLISHERS)
            pub.AUDIT = Path(d) / "audit.jsonl"
            pub.PUBLISHERS["x"] = lambda t, c, m: {"ok": False, "error": "Missing Twitter/X bearer token"}
            # publish_job uses the module-level default runner
            pub.publish_with_fallback.__defaults__  # noqa: B018 (sanity: function exists)
            orig = pub.publish_with_fallback

            def patched(*a, **k):
                k["browser_runner"] = lambda args: {"ok": True, "exit": 0, "platform": "x"}
                k["sleep"] = lambda s: None
                return orig(*a, **k)
            pub.publish_with_fallback = patched
            try:
                job = pub.publish_job({"platforms": ["x"], "content": "hi", "fallback": True})
            finally:
                pub.publish_with_fallback = orig
                pub.AUDIT, pub.run_browser = saved_audit, saved_run
                pub.PUBLISHERS.clear(); pub.PUBLISHERS.update(saved_pub)
            self.assertEqual(job["status"], "published")
            entry = json.loads((Path(d) / "audit.jsonl").read_text().splitlines()[-1])
            self.assertEqual(entry["route"], "browser")
            self.assertEqual([a["route"] for a in entry["attempts"]], ["api", "browser"])
            self.assertNotIn("token", json.dumps(entry).lower().replace("bearer token", ""))


if __name__ == "__main__":
    unittest.main()
