"""Persistent agent activity log: module, audit hook, backfill, relay API, IG permalink fallback."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT, load_agent  # noqa: E402

al = load_agent("activity_log")
sys.modules.setdefault("activity_log", al)


class TempLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "activity_log.jsonl"
        self.env = mock.patch.dict(os.environ, {"NEXUS_ACTIVITY_LOG": str(self.path)})
        self.env.start()
        sys.modules["activity_log"] = al
        self.text_file = Path(self.tmp.name) / "FB_post.txt"
        self.text_file.write_text("Garba nights + bulk orders\n", encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def lines(self):
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]


class ModuleTests(TempLog):
    def test_record_appends_full_entry_with_tz(self):
        al.record(agent="social_browser_agent", brain="Sho", action="post", platform="x", status="ok",
                  content="hello", media=["a.png"], permalink="https://x.com/a/status/1")
        al.record(action="post", platform="x", status="failed", error="boom")
        rows = self.lines()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["brain"], "Sho")
        self.assertRegex(rows[0]["ts"], r"[+-]\d\d:\d\d$")
        for k in ("ts", "agent", "brain", "action", "platform", "status", "content", "media",
                  "permalink", "screenshot", "error"):
            self.assertIn(k, rows[1])

    def test_never_raises_on_bad_path(self):
        with mock.patch.dict(os.environ, {"NEXUS_ACTIVITY_LOG": "/proc/forbidden/x.jsonl"}):
            self.assertIsNone(al.record(action="post", status="ok"))

    def test_from_audit_statuses(self):
        base = {"ts": "2026-10-12T18:47:49+00:00", "agent": "social_browser_agent", "command": "x_post",
                "args": {"text": "hi", "media": ["m.png"], "dry_run": True}, "status": "ok", "detail": "{}",
                "platform": "x", "dry_run": True}
        self.assertEqual(al.from_audit(base)["status"], "dry_run")
        self.assertEqual(al.from_audit({**base, "dry_run": False, "args": {"text": "hi"}})["status"], "ok")
        e = al.from_audit({**base, "status": "error", "detail": "Timeout"})
        self.assertEqual((e["status"], e["error"]), ("failed", "Timeout"))
        b = al.from_audit({**base, "command": "fb_post", "status": "blocked", "platform": None,
                           "args": {"text": "", "text_file": str(self.text_file)}})
        self.assertEqual((b["status"], b["platform"]), ("blocked", "facebook"))
        self.assertIn("Garba", b["content"])

    def test_permalink_from_extra_or_detail(self):
        e = al.from_audit({"ts": "t", "command": "ig_post", "args": {}, "status": "ok",
                           "detail": json.dumps({"permalink": "https://www.instagram.com/p/X/"})})
        self.assertEqual(e["permalink"], "https://www.instagram.com/p/X/")

    def test_query_filters_newest_first(self):
        al.record(action="post", platform="x", status="ok", ts="2026-10-12T10:00:00+00:00")
        al.record(action="post", platform="facebook", status="failed", ts="2026-10-12T12:00:00+00:00")
        al.record(action="post", platform="x", status="failed", ts="2026-10-12T11:00:00+05:30")
        q = al.query()
        self.assertEqual([e["platform"] for e in q["entries"]], ["facebook", "x", "x"])
        self.assertEqual(len(al.query(platform="x")["entries"]), 2)
        self.assertEqual(len(al.query(platform="x", status="failed")["entries"]), 1)

    def test_backfill_is_idempotent_and_has_known_posts(self):
        audit = Path(self.tmp.name) / "audit.jsonl"
        audit.write_text(json.dumps({"ts": "2026-10-12T20:15:04.958030+00:00", "agent": "social_browser_agent",
                                     "command": "ig_post", "args": {"caption": "cap", "dry_run": False,
                                                                    "media": ["m.png"]},
                                     "status": "ok", "detail": "{}", "platform": "instagram"}) + "\n")
        extra = Path(self.tmp.name) / "activity_backfill.json"
        extra.write_text(json.dumps({
            "known_posts": {"2026-10-12T20:15:04.958030+00:00": {"permalink": "https://www.instagram.com/examplebrand/p/ABC123/"}},
            "extra_events": [{"id": "manual-delete-1", "ts": "2026-10-13T00:00:00+05:30", "approx_time": True,
                              "agent": "operator", "action": "delete_post", "platform": "x", "status": "ok"}]}))
        first = al.backfill(audit, extra)
        self.assertEqual(al.backfill(audit, extra), [])
        ig = [e for e in first if e["platform"] == "instagram"][0]
        self.assertEqual(ig["permalink"], "https://www.instagram.com/examplebrand/p/ABC123/")
        self.assertTrue(any(e["action"] == "delete_post" and e.get("approx_time") for e in first))


class AuditHookTests(TempLog):
    def test_social_agent_audit_writes_activity(self):
        sba = load_agent("social_browser_agent")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sba, "AUDIT_LOG", Path(d) / "a.jsonl"):
            sba.audit("x_post", {"text": "post me", "dry_run": False}, "ok", "{}", platform="x",
                      permalink="https://x.com/a/status/9")
            sba.audit("fb_post", {"text": "x"}, "blocked", "missing --allow-actions")
        rows = self.lines()
        self.assertEqual([r["status"] for r in rows], ["ok", "blocked"])
        self.assertEqual(rows[0]["permalink"], "https://x.com/a/status/9")
        self.assertEqual(rows[0]["content"], "post me")

    def test_ig_permalink_fallback_scrapes_and_remembers(self):
        sba = load_agent("social_browser_agent")
        calls = {"n": 0}

        class P:
            def goto(self, url, **k): self.url = url
            def reload(self, **k): pass
            def wait_for_timeout(self, ms): pass
            def evaluate(self, js):
                calls["n"] += 1
                return [] if calls["n"] == 1 else ["https://www.instagram.com/examplebrand/p/NEW1/"]
        with mock.patch.object(sba.social_flows.link_memory, "record") as rec:
            import link_memory
            with mock.patch.object(link_memory, "record") as rec2:
                link = sba.ig_permalink_fallback(P(), "https://www.instagram.com/examplebrand/")
        self.assertEqual(link, "https://www.instagram.com/examplebrand/p/NEW1")
        self.assertTrue(rec.called or rec2.called)

    def test_ig_permalink_fallback_none_without_profile(self):
        sba = load_agent("social_browser_agent")
        page = types.SimpleNamespace(evaluate=lambda js: [])
        with mock.patch("link_memory.preferred", return_value=""), \
                mock.patch.object(sba.social_flows.link_memory, "_client", return_value={}):
            self.assertIsNone(sba.ig_permalink_fallback(page))


class RelayActivityTests(TempLog):
    def setUp(self):
        super().setUp()
        self.relay = load_agent("nexus_relay")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.relay.Handler)
        self.srv.token = "tok"
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def get(self, path, token="tok"):
        req = urllib.request.Request(self.base + path)
        if token:
            req.add_header("X-Nexus-Token", token)
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), r.headers.get("Content-Type")

    def test_activity_endpoint_filters_and_auth(self):
        al.record(action="post", platform="x", status="ok", ts="2026-10-12T10:00:00+00:00")
        al.record(action="post", platform="instagram", status="failed", ts="2026-10-12T11:00:00+00:00")
        _, body, _ = self.get("/activity")
        data = json.loads(body)
        self.assertEqual([e["platform"] for e in data["entries"]], ["instagram", "x"])
        _, body, _ = self.get("/activity?platform=x&status=ok")
        self.assertEqual(len(json.loads(body)["entries"]), 1)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/activity", token=None)
        self.assertEqual(cm.exception.code, 401)

    def test_media_endpoint_serves_only_repo_images(self):
        shots = ROOT / "shots"
        shots.mkdir(exist_ok=True)
        img = tempfile.NamedTemporaryFile(dir=shots, prefix="test-media-", suffix=".png", delete=False)
        img.write(b"\x89PNG\r\n\x1a\n"); img.close()
        self.addCleanup(os.unlink, img.name)
        rel = f"shots/{Path(img.name).name}"
        if (ROOT / rel).exists():
            code, _, ctype = self.get(f"/activity/media?path={rel}", token=None) if False else \
                self.get(f"/activity/media?path={rel}&token=tok", token=None)
            self.assertEqual((code, ctype), (200, "image/png"))
        for bad in ("../../etc/passwd", "config/client.json", "/etc/hosts", "agent/nexus_relay.py"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(f"/activity/media?path={bad}")
            self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(f"/activity/media?path={rel}", token=None)
        self.assertEqual(cm.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
