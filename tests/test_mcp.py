"""Regression tests for agent/nexus_mcp.py (JSON-RPC surface + security gates)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT, load_agent  # noqa: E402

mcp = load_agent("nexus_mcp")


class TestJsonExtraction(unittest.TestCase):
    def test_parses_json_followed_by_audit_noise(self):
        """The MCP server used to fail every browser tool because the skill
        printed an [audit] line after its JSON payload."""
        text = '{\n  "url": "https://x.test",\n  "title": "X"\n}\n[audit] OK      navigate: https://x.test\n'
        self.assertEqual(mcp._extract_json(text), {"url": "https://x.test", "title": "X"})

    def test_parses_leading_noise(self):
        self.assertEqual(mcp._extract_json('[info] using CDP\n{"ok": true}'), {"ok": True})

    def test_returns_none_for_garbage(self):
        self.assertIsNone(mcp._extract_json("Traceback: boom"))


class TestProtocol(unittest.TestCase):
    def test_initialize_echoes_requested_version(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(res["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(res["result"]["serverInfo"]["name"], "nexus-social-manager")

    def test_tools_list_exposes_the_full_catalogue(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in res["result"]["tools"]}
        for expected in ("generate_content", "plan_campaign", "hashtag_research",
                         "browser_act", "browser_snapshot", "devtools_inspect",
                         "read_agent_logs", "publish_post", "read_publish_queue"):
            self.assertIn(expected, names)

    def test_unknown_tool_is_a_jsonrpc_error(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(res["error"]["code"], -32601)

    def test_unknown_method_is_a_jsonrpc_error(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"})
        self.assertEqual(res["error"]["code"], -32601)

    def test_notifications_get_no_response(self):
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "initialized"}))

    def test_ping(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(res["result"], {})


class TestPermissionGate(unittest.TestCase):
    def test_browser_act_refuses_without_explicit_permission(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "browser_act",
                                     "arguments": {"action": "click", "target": "#x"}}})
        self.assertTrue(res["result"]["isError"])
        self.assertIn("PERMISSION_REQUIRED", res["result"]["content"][0]["text"])

    def test_browser_act_refuses_when_explicitly_false(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "browser_act",
                                     "arguments": {"action": "click", "target": "#x",
                                                   "user_permission_granted": False}}})
        self.assertTrue(res["result"]["isError"])


class TestResourceSecurity(unittest.TestCase):
    def _read(self, uri):
        res = mcp.handle({"jsonrpc": "2.0", "id": 6, "method": "resources/read",
                          "params": {"uri": uri}})
        return res

    def test_legitimate_resource_reads(self):
        res = self._read("file://campaign.json")
        self.assertIn("contents", res["result"])
        self.assertIn("My Brand", res["result"]["contents"][0]["text"])

    def test_path_traversal_is_refused(self):
        """`file://../../etc/passwd` must not escape the project root."""
        for uri in ("file://../../../../etc/passwd",
                    "file:///etc/passwd",
                    "file://../../.ssh/id_rsa",
                    "file://..%2f..%2fetc%2fpasswd"):
            res = self._read(uri)
            if "error" in res:
                self.assertEqual(res["error"]["code"], -32602, uri)
            else:
                self.assertNotIn("root:", res["result"]["contents"][0]["text"], uri)

    def test_missing_file_reports_missing(self):
        res = self._read("file://does/not/exist.txt")
        self.assertIn("(missing)", res["result"]["contents"][0]["text"])


class TestToolHandlers(unittest.TestCase):
    def test_plan_campaign_covers_every_requested_day(self):
        plan = mcp.tool_plan_campaign({"goal": "launch", "days": 7, "budget_usd": 700})
        self.assertEqual(len(plan["plan"]), 7)
        self.assertAlmostEqual(sum(d["spend_usd"] for d in plan["plan"]), 700, places=1)
        self.assertEqual(plan["plan"][0]["phase"], "Awareness")
        self.assertEqual(plan["plan"][-1]["phase"], "Conversion")

    def test_hashtag_research_returns_tiers(self):
        out = mcp.tool_hashtag_research({"niche": "SaaS Tools"})
        self.assertIn("broad (1M+)", out["tiers"])
        self.assertTrue(all(t.startswith("#") for tags in out["tiers"].values() for t in tags))

    def test_generate_content_requires_a_topic(self):
        self.assertFalse(mcp.tool_generate_content({"topic": ""})["ok"])

    def test_render_video_requires_images(self):
        self.assertFalse(mcp.tool_render_video({"images": []})["ok"])

    def test_publish_post_requires_platform_and_text(self):
        self.assertFalse(mcp.tool_publish_post({"platform": "", "text": "x"})["ok"])
        self.assertFalse(mcp.tool_publish_post({"platform": "facebook", "text": ""})["ok"])

    def test_publish_queue_strips_credentials(self):
        import tempfile
        original = mcp.LOGS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            mcp.LOGS_DIR = Path(tmp)
            (Path(tmp) / "publish_queue.json").write_text(json.dumps([
                {"id": "a", "status": "queued", "credentials": {"fb-page-token": "SECRET"}},
            ]))
            try:
                out = mcp.tool_publish_queue({"limit": 5})
            finally:
                mcp.LOGS_DIR = original
        self.assertTrue(out["ok"])
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertEqual(out["counts"], {"queued": 1})


if __name__ == "__main__":
    unittest.main()
