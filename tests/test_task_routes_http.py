"""Relay HTTP: tasks CRUD, approve, calendar, ICS, chat SSE with a fake brain, token rules."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

relay = load_agent("nexus_relay")
import brain_router  # noqa: E402

TOKEN = "test-token-abcdefghijklmnop"


class TaskRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        cls.server.token = TOKEN
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, body=None, token=TOKEN, raw=False):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Nexus-Token"] = token
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=h,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read().decode()
                return r.status, (data if raw else json.loads(data))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_crud_approve_calendar_ics(self):
        st, r = self.call("/tasks", {"title": "Launch", "content": "Hello", "platforms": ["x"],
                                     "scheduled_at": "2030-01-05T10:00", "status": "approved"})
        self.assertEqual(st, 200)
        tid = r["task"]["id"]
        self.assertEqual(r["task"]["status"], "pending_approval")      # cannot create approved
        st, r = self.call(f"/tasks/{tid}/update", {"content": "Hello world"})
        self.assertEqual(r["task"]["content"], "Hello world")
        st, r = self.call(f"/tasks/{tid}/approve", {"scheduled_at": "2030-01-06T11:00"})
        self.assertEqual((st, r["task"]["status"]), (200, "approved"))
        self.assertTrue(r["task"]["scheduled_at"].startswith("2030-01-06T11:00"))
        st, r = self.call("/calendar?start=2030-01-01&end=2030-01-31")
        self.assertIn("2030-01-06", r["days"])
        st, ics = self.call(f"/calendar.ics?token={TOKEN}", token=None, raw=True)
        self.assertEqual(st, 200)
        self.assertIn(f"UID:{tid}@nexus", ics)
        st, _ = self.call("/calendar.ics", token=None, raw=True)
        self.assertEqual(st, 401)
        st, r = self.call(f"/tasks/{tid}/cancel", {})
        self.assertEqual(r["task"]["status"], "cancelled")
        self.assertEqual(self.call("/tasks/t-missing")[0], 404)

    def test_token_required(self):
        self.assertEqual(self.call("/tasks", token="wrong")[0], 401)
        self.assertEqual(self.call("/tasks", {"title": "x"}, token=None)[0], 401)

    def test_chat_sse_with_fake_brain(self):
        replies = [{"text": "", "tool_calls": [{"id": "1", "name": "draft_post", "arguments": {
            "platforms": ["instagram"], "caption": "Burger time", "scheduled_at": "2030-02-01T19:00"}}]},
            {"text": "Drafted; approve it in Nexus.", "tool_calls": []}]
        real = brain_router.BrainConfig.from_dict

        def fake_cfg(d):
            cfg = real({"provider": "lmstudio", "model": "m"})
            cfg.provider, cfg.fake = "fake", (lambda **k: replies.pop(0))
            return cfg
        with mock.patch.object(brain_router.BrainConfig, "from_dict", staticmethod(fake_cfg)):
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/nexus/chat", method="POST",
                                         headers={"Content-Type": "application/json", "X-Nexus-Token": TOKEN},
                                         data=json.dumps({"message": "post about burgers",
                                                          "brain": {"provider": "lmstudio"}}).encode())
            with urllib.request.urlopen(req, timeout=20) as r:
                self.assertIn("text/event-stream", r.headers["Content-Type"])
                body = r.read().decode()
        events = [b.split("\n")[0][7:] for b in body.strip().split("\n\n")]
        self.assertIn("task", events)
        self.assertEqual(events[-1], "done")
        self.assertIn("Drafted; approve it in Nexus.", body)

    def test_unknown_brain_is_400(self):
        st, r = self.call("/nexus/chat", {"message": "hi", "brain": {"provider": "nope"}})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
