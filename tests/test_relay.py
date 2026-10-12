"""Regression tests for agent/nexus_relay.py.

These lock in the fixes for the bugs that made the relay unusable:
  • the missing /publish route (publishing never worked)
  • wildcard CORS (any website could drive the local browser)
  • credentials leaking back out of /queue
  • stdout parsing that could not tolerate extra log lines
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

relay = load_agent("nexus_relay")


class TestExtractJson(unittest.TestCase):
    def test_parses_clean_payload(self):
        self.assertEqual(relay.extract_json('{"a": 1}'), {"a": 1})

    def test_tolerates_leading_log_lines(self):
        out = '[info] starting\n{"ok": true, "x": 2}\n'
        self.assertEqual(relay.extract_json(out), {"ok": True, "x": 2})

    def test_tolerates_trailing_log_lines(self):
        """The exact shape that used to break MCP: JSON followed by an audit line."""
        out = '{\n  "snapshot": "h1 :: Hi"\n}\n[audit] OK      snapshot: ok\n'
        self.assertEqual(relay.extract_json(out), {"snapshot": "h1 :: Hi"})

    def test_prefers_the_largest_object(self):
        out = '{} {"real": "payload", "n": 3} trailing'
        self.assertEqual(relay.extract_json(out), {"real": "payload", "n": 3})

    def test_handles_arrays(self):
        self.assertEqual(relay.extract_json('[1, 2, 3]'), [1, 2, 3])

    def test_returns_none_when_absent(self):
        self.assertIsNone(relay.extract_json("no json at all"))
        self.assertIsNone(relay.extract_json(""))


class TestOriginAllowList(unittest.TestCase):
    def test_accepts_local_and_null_origins(self):
        for origin in ("null", "http://localhost:8080", "http://127.0.0.1:5500",
                       "https://localhost", "http://[::1]:8712"):
            self.assertTrue(relay.ALLOWED_ORIGIN_RE.match(origin), origin)

    def test_rejects_remote_origins(self):
        for origin in ("https://evil.example.com", "http://localhost.evil.com",
                       "https://attacker.io:8712", "http://127.0.0.1.evil.com"):
            self.assertFalse(relay.ALLOWED_ORIGIN_RE.match(origin), origin)

    def test_host_header_validation_blocks_dns_rebinding(self):
        self.assertTrue(relay.ALLOWED_HOST_RE.match("127.0.0.1:8712"))
        self.assertTrue(relay.ALLOWED_HOST_RE.match("localhost"))
        self.assertFalse(relay.ALLOWED_HOST_RE.match("evil.example.com"))
        self.assertFalse(relay.ALLOWED_HOST_RE.match("127.0.0.1.evil.com"))


class TestQueuePayload(unittest.TestCase):
    def test_credentials_are_never_returned(self):
        """Tokens are persisted locally for deferred publishing, but must not
        travel back over HTTP."""
        original = relay.read_queue
        relay.read_queue = lambda: [
            {"id": "job-1", "status": "scheduled",
             "credentials": {"li-access-token": "SECRET"}, "content": "hi"},
            {"id": "job-2", "status": "published", "content": "done"},
        ]
        try:
            payload = relay.queue_payload()
        finally:
            relay.read_queue = original

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["counts"], {"scheduled": 1, "published": 1})
        for job in payload["jobs"]:
            self.assertNotIn("credentials", job)
        self.assertNotIn("SECRET", json.dumps(payload))

    def test_counts_unknown_status(self):
        original = relay.read_queue
        relay.read_queue = lambda: [{"id": "x"}]
        try:
            self.assertEqual(relay.queue_payload()["counts"], {"unknown": 1})
        finally:
            relay.read_queue = original


class TestCommandMap(unittest.TestCase):
    def test_every_ui_command_is_capable(self):
        """The UI dispatches these exact strings; a typo means a dead button."""
        for cmd in ("navigate", "snapshot", "extract", "screenshot",
                    "devtools-console", "devtools-perf", "act-click", "act-type",
                    "status", "close"):
            self.assertIn(cmd, relay.CMD_MAP, cmd)

    def test_builders_produce_cli_arguments(self):
        self.assertEqual(relay.CMD_MAP["navigate"]({"url": "https://x.test"}),
                         ["navigate", "--url", "https://x.test"])
        self.assertEqual(relay.CMD_MAP["act-type"]({"selector": "#a", "value": "hi"}),
                         ["act", "--allow-actions", "--action", "type",
                          "--target", "#a", "--value", "hi", "--task", "relay-ui"])

    def test_missing_field_raises_keyerror_for_a_400(self):
        with self.assertRaises(KeyError):
            relay.CMD_MAP["navigate"]({})
        with self.assertRaises(KeyError):
            relay.CMD_MAP["extract"]({})


if __name__ == "__main__":
    unittest.main()


class TestFinishedJobRecording(unittest.TestCase):
    """A completed immediate publish must appear in the queue for the UI — and
    must never persist the credentials it was handed."""

    def test_records_outcome_without_credentials(self):
        import tempfile
        original = relay.QUEUE_FILE
        original_logs = relay.LOGS
        with tempfile.TemporaryDirectory() as tmp:
            relay.LOGS = Path(tmp)
            relay.QUEUE_FILE = Path(tmp) / "publish_queue.json"
            try:
                relay.record_finished_job({
                    "id": "job-1", "status": "published",
                    "content": "hello", "platforms": ["facebook"],
                    "results": [{"platform": "facebook", "ok": True, "id": "123"}],
                    "credentials": {"fb-page-token": "SECRET"},
                })
                jobs = relay.read_queue()
            finally:
                relay.QUEUE_FILE = original
                relay.LOGS = original_logs

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "published")
        self.assertEqual(jobs[0]["results"][0]["id"], "123")
        self.assertEqual(jobs[0]["credentials"], {})
        self.assertNotIn("SECRET", json.dumps(jobs))

    def test_queue_is_capped(self):
        import tempfile
        original = relay.QUEUE_FILE
        original_logs = relay.LOGS
        with tempfile.TemporaryDirectory() as tmp:
            relay.LOGS = Path(tmp)
            relay.QUEUE_FILE = Path(tmp) / "publish_queue.json"
            try:
                for i in range(210):
                    relay.record_finished_job({"id": f"job-{i}", "status": "published"})
                jobs = relay.read_queue()
            finally:
                relay.QUEUE_FILE = original
                relay.LOGS = original_logs
        self.assertEqual(len(jobs), 200)
        self.assertEqual(jobs[-1]["id"], "job-209")
