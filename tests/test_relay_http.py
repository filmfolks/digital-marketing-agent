"""HTTP-level tests for the relay security contract.

Boots a real ThreadingHTTPServer on an ephemeral loopback port and drives it with
urllib, so the auth, origin and permission rules are verified as they actually
behave over the wire — not just as pure functions.

This is the local-first analogue of the CI `relay-smoke` job.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

relay = load_agent("nexus_relay")

TOKEN = "test-token-abcdefghijklmnop"


class RelayServerCase(unittest.TestCase):
    """One server for the class; each test uses a fresh queue file."""

    token = TOKEN

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._saved_logs = relay.LOGS
        cls._saved_queue = relay.QUEUE_FILE
        relay.LOGS = Path(cls._tmp.name)
        relay.QUEUE_FILE = Path(cls._tmp.name) / "publish_queue.json"

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        cls.server.token = TOKEN
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        relay.LOGS = cls._saved_logs
        relay.QUEUE_FILE = cls._saved_queue
        cls._tmp.cleanup()

    def setUp(self):
        relay.QUEUE_FILE.unlink(missing_ok=True)

    # ── helpers ─────────────────────────────────────────────────────────────
    def request(self, method, path, *, body=None, token=None, origin=None, host=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Nexus-Token", token)
        if origin:
            req.add_header("Origin", origin)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read().decode()
                return res.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return e.code, payload

    # ── health ──────────────────────────────────────────────────────────────
    def test_health_is_open_and_reports_capabilities(self):
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["service"], "nexus-relay")
        self.assertTrue(body["auth"], "the UI needs to know whether a token is required")

    # ── token gate ──────────────────────────────────────────────────────────
    def test_queue_requires_a_token(self):
        status, _ = self.request("GET", "/queue")
        self.assertEqual(status, 401)

    def test_queue_rejects_a_wrong_token(self):
        status, _ = self.request("GET", "/queue", token="wrong")
        self.assertEqual(status, 401)

    def test_queue_accepts_the_right_token(self):
        status, body = self.request("GET", "/queue", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 0)

    def test_post_requires_a_token(self):
        status, _ = self.request("POST", "/browser", body={"cmd": "status"})
        self.assertEqual(status, 401)

    # ── CORS / origin ───────────────────────────────────────────────────────
    def test_remote_origin_is_refused(self):
        """Without this, any website you visit could drive your local browser."""
        status, _ = self.request("OPTIONS", "/browser", origin="https://evil.example.com")
        self.assertEqual(status, 403)

    def test_local_origin_is_allowed(self):
        status, _ = self.request("OPTIONS", "/browser", origin="http://localhost:8080")
        self.assertEqual(status, 204)

    def test_file_protocol_null_origin_is_allowed(self):
        status, _ = self.request("OPTIONS", "/browser", origin="null")
        self.assertEqual(status, 204)

    def test_lookalike_origin_is_refused(self):
        for origin in ("http://localhost.evil.com", "http://127.0.0.1.evil.com"):
            status, _ = self.request("OPTIONS", "/browser", origin=origin)
            self.assertEqual(status, 403, origin)

    def test_remote_origin_cannot_post_even_with_a_valid_token(self):
        status, _ = self.request("POST", "/browser", body={"cmd": "status"},
                                 token=TOKEN, origin="https://evil.example.com")
        self.assertEqual(status, 403)

    # ── DNS rebinding ───────────────────────────────────────────────────────
    def test_foreign_host_header_is_refused(self):
        status, _ = self.request("GET", "/health", host="evil.example.com")
        self.assertEqual(status, 403)

    # ── input validation ────────────────────────────────────────────────────
    def test_preflight_with_foreign_host_is_refused(self):
        code, _ = self.request("OPTIONS", "/publish", origin="http://localhost:8000", host="evil.example")
        self.assertEqual(code, 403)

    def test_shots_never_send_wildcard_cors_and_reject_non_images(self):
        shots = relay.ROOT / "shots"
        shots.mkdir(exist_ok=True)
        img = shots / "_test_cors_probe.png"
        txt = shots / "_test_probe.txt"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        txt.write_text("secret")
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/shots/_test_cors_probe.png")
            with urllib.request.urlopen(req, timeout=10) as res:
                self.assertEqual(res.status, 200)
                self.assertEqual(res.headers.get("Content-Type"), "image/png")
                self.assertIsNone(res.headers.get("Access-Control-Allow-Origin"))
            code, _ = self.request("GET", "/shots/_test_probe.txt")
            self.assertEqual(code, 404)
        finally:
            img.unlink(missing_ok=True)
            txt.unlink(missing_ok=True)

    def test_pwa_is_served_from_the_relay(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/app/manifest.json")
        with urllib.request.urlopen(req, timeout=10) as res:
            self.assertEqual(res.headers.get("Content-Type"), "application/manifest+json")
            self.assertIn("Nexus", res.read().decode())

    def test_static_route_blocks_traversal_and_secrets(self):
        for bad in ("/app/../agent/publish.py", "/app/%2e%2e/.env", "/app/credentials.js", "/app/.hidden"):
            code, _ = self.request("GET", bad)
            self.assertEqual(code, 404, bad)

    def test_health_reports_operational_state(self):
        code, body = self.request("GET", "/health")
        self.assertEqual(code, 200)
        for k in ("uptime_s", "queue_counts", "lan", "tls", "playwright", "browser_fallback"):
            self.assertIn(k, body)
        self.assertFalse(body["lan"])

    def test_social_dry_run_x_post_is_allowed_without_action_permission(self):
        # Validation failure (empty text) proves the route exists without a browser.
        code, body = self.request("POST", "/social", body={"workflow": "x_post", "dry_run": True},
                                  token=TOKEN)
        self.assertEqual(code, 400)
        self.assertIn("text is required", body["error"])

    def test_real_x_post_needs_action_permission(self):
        code, _ = self.request("POST", "/social", body={"workflow": "x_post", "text": "hi"}, token=TOKEN)
        self.assertEqual(code, 403)

    def test_unknown_route_is_404(self):
        status, _ = self.request("GET", "/nope", token=TOKEN)
        self.assertEqual(status, 404)

    def test_malformed_json_is_400(self):
        url = f"http://127.0.0.1:{self.port}/publish"
        req = urllib.request.Request(url, data=b"{not json", method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Nexus-Token", TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    def test_unknown_browser_command_is_400(self):
        status, body = self.request("POST", "/browser", token=TOKEN,
                                    body={"cmd": "rm -rf /"})
        self.assertEqual(status, 400)
        self.assertIn("unknown cmd", body["error"])

    def test_browser_command_without_required_field_is_400(self):
        status, _ = self.request("POST", "/browser", token=TOKEN, body={"cmd": "navigate"})
        self.assertEqual(status, 400)

    # ── permission gate ─────────────────────────────────────────────────────
    def test_state_changing_browser_action_needs_explicit_permission(self):
        status, body = self.request("POST", "/browser", token=TOKEN, body={
            "cmd": "act-click", "selector": "button#publish",
        })
        self.assertEqual(status, 403)
        self.assertIn("permission", body["error"].lower())

    def test_permission_is_refused_before_any_browser_is_launched(self):
        """A blocked action must not depend on Playwright being installed."""
        status, body = self.request("POST", "/browser", token=TOKEN, body={
            "cmd": "act-type", "selector": "#x", "value": "hi",
        })
        self.assertEqual(status, 403)
        self.assertNotIn("engine", json.dumps(body).lower())

    # ── publish ─────────────────────────────────────────────────────────────
    def test_publish_requires_content_and_platforms(self):
        status, body = self.request("POST", "/publish", token=TOKEN,
                                    body={"platforms": ["facebook"]})
        self.assertEqual(status, 400)
        self.assertIn("content", body["error"])

        status, body = self.request("POST", "/publish", token=TOKEN, body={"content": "hi"})
        self.assertEqual(status, 400)
        self.assertIn("platform", body["error"])

    def test_publish_dry_run_validates_without_publishing(self):
        status, body = self.request("POST", "/publish", token=TOKEN, body={
            "content": "relay integration test", "platforms": ["facebook"], "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "validated")
        self.assertTrue(body["results"][0]["dry_run"])

    def test_dry_run_records_the_job_in_the_queue_without_credentials(self):
        self.request("POST", "/publish", token=TOKEN, body={
            "id": "job-http-1", "content": "hi", "platforms": ["facebook"],
            "dry_run": True, "credentials": {"fb-page-token": "SUPERSECRET"},
        })
        status, body = self.request("GET", "/queue", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["counts"], {"validated": 1})
        self.assertNotIn("SUPERSECRET", json.dumps(body))
        self.assertNotIn("credentials", json.dumps(body["jobs"]))

    def test_scheduled_publish_is_queued_not_sent(self):
        status, body = self.request("POST", "/publish", token=TOKEN, body={
            "id": "job-http-2", "content": "later", "platforms": ["linkedin"],
            "schedule": "2030-01-01T09:00:00.000Z",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "scheduled")
        self.assertTrue(body["results"][0]["scheduled"])

        _, queue = self.request("GET", "/queue", token=TOKEN)
        self.assertEqual(queue["counts"], {"scheduled": 1})

    def test_publish_handoff_file_is_cleaned_up(self):
        self.request("POST", "/publish", token=TOKEN, body={
            "content": "hi", "platforms": ["facebook"], "dry_run": True,
        })
        self.assertFalse((Path(self._tmp.name) / "publish_job.json").exists(),
                         "the in-flight hand-off file must not be left behind")

    # ── MCP passthrough ─────────────────────────────────────────────────────
    def test_mcp_tool_call_round_trips(self):
        status, body = self.request("POST", "/mcp", token=TOKEN, body={
            "tool": "plan_campaign", "arguments": {"goal": "launch", "days": 3},
        })
        self.assertEqual(status, 200)
        self.assertIn("content", body)


class LanRelayCase(RelayServerCase):
    """LAN mode: any Host, same-origin PWA allowed, token mandatory everywhere it matters."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server.lan = True
        cls.server.extra_origins = {"https://nexus.example.com"}

    def test_lan_host_header_is_accepted_with_token(self):
        code, _ = self.request("GET", "/queue", token=TOKEN, host="192.168.1.20:8712")
        self.assertEqual(code, 200)

    def test_lan_without_token_is_refused(self):
        code, _ = self.request("GET", "/queue", host="192.168.1.20:8712")
        self.assertEqual(code, 401)

    def test_same_origin_pwa_is_allowed(self):
        code, _ = self.request("GET", "/queue", token=TOKEN, host="192.168.1.20:8712",
                               origin="http://192.168.1.20:8712")
        self.assertEqual(code, 200)

    def test_listed_hosted_origin_is_allowed_others_refused(self):
        code, _ = self.request("GET", "/queue", token=TOKEN, origin="https://nexus.example.com")
        self.assertEqual(code, 200)
        code, _ = self.request("GET", "/queue", token=TOKEN, origin="https://evil.example.com")
        self.assertEqual(code, 403)

    def test_shots_require_token_in_lan_mode(self):
        code, _ = self.request("GET", "/shots/anything.png", host="192.168.1.20:8712")
        self.assertEqual(code, 401)

    # inherited tests that assert loopback-only Host behaviour do not apply
    def test_foreign_host_header_is_refused(self):
        self.skipTest("LAN mode accepts any Host; the token is the gate")

    def test_preflight_with_foreign_host_is_refused(self):
        self.skipTest("LAN mode accepts any Host; the token is the gate")

    def test_shots_never_send_wildcard_cors_and_reject_non_images(self):
        self.skipTest("covered in loopback mode; LAN mode additionally requires the token")

    def test_health_reports_operational_state(self):
        code, body = self.request("GET", "/health")
        self.assertTrue(body["lan"])


class ConfigValidation(unittest.TestCase):
    def args(self, **kw):
        import types
        base = dict(port=8712, lan=False, bind="127.0.0.1", token="", tls_cert="", tls_key="", allow_origin=[])
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_lan_requires_a_long_token(self):
        errs, _ = relay.validate_config(self.args(lan=True, bind="0.0.0.0"))
        self.assertTrue(any("requires a token" in e for e in errs))
        errs, _ = relay.validate_config(self.args(lan=True, bind="0.0.0.0", token="short"))
        self.assertTrue(any("at least" in e for e in errs))
        errs, warns = relay.validate_config(self.args(lan=True, bind="0.0.0.0", token="x" * 32))
        self.assertEqual(errs, [])
        self.assertTrue(any("TLS" in w for w in warns))

    def test_non_loopback_bind_counts_as_lan(self):
        errs, _ = relay.validate_config(self.args(bind="192.168.1.5"))
        self.assertTrue(errs)

    def test_tls_pair_and_files_checked(self):
        errs, _ = relay.validate_config(self.args(tls_cert="/nope.pem"))
        self.assertTrue(any("together" in e for e in errs))

    def test_bad_numeric_env_rejected(self):
        import os
        os.environ["NEXUS_API_RETRIES"] = "lots"
        try:
            errs, _ = relay.validate_config(self.args())
        finally:
            del os.environ["NEXUS_API_RETRIES"]
        self.assertTrue(any("NEXUS_API_RETRIES" in e for e in errs))

    def test_dotenv_is_loaded_and_real_env_wins(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("NEXUS_T1=from-file\nNEXUS_T2=\nNEXUS_T2=second\n# c\nNEXUS_T3='q'\n")
            os.environ["NEXUS_T1"] = "real"
            try:
                relay.load_env([env])
                self.assertEqual(os.environ["NEXUS_T1"], "real")
                self.assertEqual(os.environ["NEXUS_T2"], "second")   # empty line skipped
                self.assertEqual(os.environ["NEXUS_T3"], "q")
            finally:
                for k in ("NEXUS_T1", "NEXUS_T2", "NEXUS_T3"):
                    os.environ.pop(k, None)

    def test_bad_origin_rejected(self):
        errs, _ = relay.validate_config(self.args(allow_origin=["nexus.example.com/path"]))
        self.assertTrue(errs)


if __name__ == "__main__":
    unittest.main()
