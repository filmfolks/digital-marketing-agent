"""Skill set + MCP server: generated files in sync, SKILL.md frontmatter, few-shot examples valid, stdio JSON-RPC."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT  # noqa: E402
import brain_router as br  # noqa: E402
import nexus_actions as na  # noqa: E402
import nexus_tasks_mcp as mcp  # noqa: E402

SKILL = ROOT / "skills" / "nexus"


class SkillFiles(unittest.TestCase):
    def test_generated_files_are_in_sync(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_nexus_skills.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_skill_md_frontmatter_matches_hermes_and_agentskills_rules(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        fm = text[4:text.index("\n---\n", 4)]
        name = re.search(r"^name: (.+)$", fm, re.M).group(1).strip()
        desc = re.search(r"^description: (.+)$", fm, re.M).group(1).strip()
        self.assertEqual(name, SKILL.name)                       # must match the folder
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(desc), 60)                      # Hermes Agent new-skill budget
        self.assertTrue(desc.endswith("."))
        self.assertTrue(text[text.index("\n---\n", 4) + 5:].strip())

    def test_every_tool_has_card_and_schema(self):
        for name in na.TOOLS:
            self.assertTrue((SKILL / "tools" / f"{name}.md").exists(), name)
            self.assertTrue((SKILL / "schemas" / f"{name}.json").exists(), name)

    def test_few_shot_tool_calls_are_valid(self):
        text = (SKILL / "examples" / "few_shot.md").read_text(encoding="utf-8")
        calls, _, _ = br.parse_tool_calls(text, list(na.TOOLS))
        self.assertGreaterEqual(len(calls), 4)
        for c in calls:
            _, _, errs = na.check_call(c["name"], c["arguments"])
            self.assertEqual(errs, [], c)

    def test_system_prompt_placeholders(self):
        text = (SKILL / "system_prompt.md").read_text(encoding="utf-8")
        for ph in ("{today}", "{timezone}", "{brand_block}", "{next_monday}"):
            self.assertIn(ph, text)


class McpServer(unittest.TestCase):
    def test_initialize_list_call(self):
        init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(sorted(names), sorted(na.TOOLS))
        self.assertNotIn("publish_post", names)
        import nexus_tasks as nt
        ex = na.Executor(nt.TaskStore(Path(tempfile.mkdtemp()), log=lambda *a, **k: None), brand_loader=lambda b=None: {})
        r = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "draft_post", "arguments": {"platforms": ["x"], "caption": "hi"}}}, executor=ex)
        body = json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(body["task"]["status"], "pending_approval")
        bad = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "draft_post", "arguments": {}}}, executor=ex)
        self.assertTrue(bad["result"]["isError"])

    def test_resources_are_confined(self):
        lst = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"]["resources"]
        self.assertTrue(any(r["uri"].endswith("SKILL.md") for r in lst))
        r = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "nexus://skill/../../.env"}})
        self.assertIn("error", r)

    def test_stdio_subprocess(self):
        env = dict(os.environ)
        reqs = "\n".join(json.dumps(x) for x in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "not-a-request"]) + "\nthis is not json\n"
        p = subprocess.run([sys.executable, str(ROOT / "agent" / "nexus_tasks_mcp.py")], input=reqs,
                           capture_output=True, text=True, env=env, timeout=30)
        lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
        self.assertEqual([l.get("id") for l in lines], [1, 2, None, None])
        self.assertEqual(lines[2]["error"]["code"], -32600)
        self.assertEqual(lines[3]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
