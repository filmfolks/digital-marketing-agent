"""Unit tests for social_browser_agent.py and the /social endpoint."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOCIAL = ROOT / "agent" / "social_browser_agent.py"
MCP = ROOT / "agent" / "nexus_mcp.py"


class TestSocialBrowserAgent(unittest.TestCase):
    def test_status_command(self):
        """status command should run without credentials and return JSON."""
        proc = subprocess.run([sys.executable, str(SOCIAL), "status"],
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout.strip())
        self.assertTrue(data.get("ok"))
        self.assertIn("profile_dir", data)
        self.assertIn("audit_log", data)

    def test_mutation_blocked_without_permission(self):
        """Mutating commands must fail with exit code 2 when --allow-actions is omitted."""
        proc = subprocess.run([sys.executable, str(SOCIAL), "instagram_post",
                               "--caption", "Test caption"],
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout.strip())
        self.assertFalse(data.get("ok"))
        self.assertIn("permission", data.get("error", "").lower())

    def test_deny_pattern_blocked(self):
        """Destructive targets must be blocked by safety policy even with --allow-actions."""
        proc = subprocess.run([sys.executable, str(SOCIAL), "--allow-actions", "generic_post",
                               "--url", "https://example.com",
                               "--selector", "button",
                               "--text", "delete my account"],
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout.strip())
        self.assertFalse(data.get("ok"))
        self.assertTrue(data.get("blocked"))

    def test_mcp_social_tools_registered(self):
        """MCP server must expose social_browser_post and canva_automate tools."""
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        proc = subprocess.run([sys.executable, str(MCP)], input=req,
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        resp = json.loads(proc.stdout.strip())
        tool_names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("social_browser_post", tool_names)
        self.assertIn("canva_automate", tool_names)

    def test_mcp_permission_gate(self):
        """MCP social_browser_post must require user_permission_granted."""
        req = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "social_browser_post", "arguments": {"workflow": "status"}}
        }) + "\n"
        proc = subprocess.run([sys.executable, str(MCP)], input=req,
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        resp = json.loads(proc.stdout.strip())
        self.assertTrue(resp["result"].get("isError"))
        text = resp["result"]["content"][0]["text"]
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertIn("user_permission_granted", payload["error"])


if __name__ == "__main__":
    unittest.main()
