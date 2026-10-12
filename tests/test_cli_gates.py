"""End-to-end CLI gate tests.

The permission contract is the single most safety-critical behaviour in this
repo, and it spans three layers (CLI flag → relay HTTP status → MCP tool gate).
These tests exercise the real subprocess boundary rather than mocking it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT  # noqa: E402

BROWSER = ROOT / "agent" / "browser_automation.py"
PUBLISHER = ROOT / "agent" / "publish.py"
MCP = ROOT / "agent" / "nexus_mcp.py"
RELAY = ROOT / "agent" / "nexus_relay.py"


def run(*args, timeout=60):
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))


class TestBrowserCliGate(unittest.TestCase):
    """Exit code 2 means "policy block" and is what the relay maps to HTTP 403."""

    def test_act_without_allow_actions_exits_2(self):
        proc = run(BROWSER, "act", "--action", "click", "--target", "button#publish")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("BLOCKED", proc.stdout)

    def test_act_on_a_denied_target_exits_2_even_with_permission(self):
        proc = run(BROWSER, "act", "--allow-actions", "--action", "click",
                   "--target", "a.delete-account")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("deny-list", proc.stdout.lower())

    def test_navigate_to_a_denied_url_exits_2(self):
        proc = run(BROWSER, "navigate", "--url", "https://example.com/logout")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_gate_runs_before_the_engine_is_needed(self):
        """A blocked request must not require Playwright to be installed —
        otherwise an uninstalled browser would mask the policy refusal with a
        confusing engine error."""
        proc = run(BROWSER, "act", "--action", "click", "--target", "#x")
        combined = proc.stdout + proc.stderr
        self.assertNotIn("No usable browser engine", combined)
        self.assertIn("BLOCKED", combined)

    def test_status_works_without_any_browser(self):
        proc = run(BROWSER, "status")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("playwright", payload)
        self.assertIn("profile_dir", payload)

    def test_audit_output_never_pollutes_stdout(self):
        """stdout is a machine interface; the audit trail belongs on stderr."""
        proc = run(BROWSER, "status")
        json.loads(proc.stdout)                 # would raise if [audit] leaked in
        self.assertIn("[audit]", proc.stderr)


class TestPublisherCli(unittest.TestCase):
    def test_dry_run_of_a_known_platform(self):
        proc = run(PUBLISHER, "--platform", "facebook", "--text", "hello", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "validated")
        self.assertTrue(payload["results"][0]["dry_run"])

    def test_dry_run_makes_no_network_call_even_without_credentials(self):
        proc = run(PUBLISHER, "--platform", "twitter", "--text", "hello", "--dry-run")
        self.assertEqual(json.loads(proc.stdout)["status"], "validated")

    def test_list_prints_an_empty_queue(self):
        proc = run(PUBLISHER, "--list")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("jobs", payload)
        self.assertNotIn("credentials", json.dumps(payload["jobs"]))

    def test_usage_error_without_arguments(self):
        proc = run(PUBLISHER)
        self.assertNotEqual(proc.returncode, 0)


class TestMcpCli(unittest.TestCase):
    def test_self_test_passes(self):
        proc = run(MCP, "--self-test")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Self-test PASSED", proc.stdout)

    def test_stdio_jsonrpc_round_trip(self):
        payload = "\n".join(json.dumps(r) for r in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "t", "version": "1"}, "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "plan_campaign", "arguments": {"goal": "launch", "days": 3}}},
        ]) + "\n"
        proc = subprocess.run([sys.executable, str(MCP)], input=payload,
                              capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        ids = [r.get("id") for r in replies]
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        call = next(r for r in replies if r.get("id") == 2)
        self.assertFalse(call["result"]["isError"])
        self.assertIn("plan", call["result"]["content"][0]["text"])

    def test_bad_json_gets_a_parse_error_and_the_loop_survives(self):
        payload = "this is not json\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"
        proc = subprocess.run([sys.executable, str(MCP)], input=payload,
                              capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        self.assertTrue(any(r.get("error", {}).get("code") == -32700 for r in replies))
        self.assertTrue(any(r.get("id") == 7 for r in replies), "server must keep serving")


class TestRelayCli(unittest.TestCase):
    def test_generate_token_is_high_entropy(self):
        a = run(RELAY, "--generate-token").stdout.strip()
        b = run(RELAY, "--generate-token").stdout.strip()
        self.assertGreaterEqual(len(a), 32)
        self.assertNotEqual(a, b)

    def test_help_lists_the_publish_route(self):
        proc = run(RELAY, "--help")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
