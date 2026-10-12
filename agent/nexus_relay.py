#!/usr/bin/env python3
"""
Nexus Local Relay  🛠
=====================
Tiny stdlib-only HTTP bridge that lets the browser UI (ui/index.html) execute
the Python agent skills on YOUR machine:

    Browser UI  ──HTTP──▶  nexus_relay.py  ──subprocess──▶  agent/*.py
    (127.0.0.1:8712)

Endpoints
    GET  /health         liveness + version + queue size       (no auth)
    GET  /queue          current publish queue + status counts
    POST /browser        run a browser_automation.py command
    POST /publish        publish (or validate) a social post
    POST /publish-due    publish every queued/scheduled job that is due
    POST /mcp            generic MCP tool call through nexus_mcp.py
    POST /shutdown       graceful stop (local, token-gated)

Run it alongside the UI:
    python3 agent/nexus_relay.py                 # port 8712
    NEXUS_RELAY_PORT=9000 NEXUS_RELAY_TOKEN=xyz python3 agent/nexus_relay.py

Security model
    • Binds 127.0.0.1 only — never exposed to the network.
    • Origin allow-list: browsers may only call from a local/null origin, so a
      random website you have open cannot POST to this port (the JSON
      Content-Type forces a CORS preflight, which we refuse). Without this,
      `Access-Control-Allow-Origin: *` would have let any page drive your
      logged-in browser — a genuine CSRF hole.
    • Host header validation blocks DNS-rebinding attacks.
    • Optional shared token (NEXUS_RELAY_TOKEN, or --token): every non-GET
      request must carry `X-Nexus-Token`.
    • State-changing browser actions are refused unless allowActions=true,
      which the UI sets only when the user ticked Browser Actions Permission.
    • Every request is appended to logs/browser_audit.jsonl by the skill itself;
      the relay additionally logs one access line per request.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"
BROWSER = AGENT_DIR / "browser_automation.py"
PUBLISHER = AGENT_DIR / "publish.py"
MCP = AGENT_DIR / "nexus_mcp.py"
LOGS = ROOT / "logs"
VERSION = "3.0.0"

# Local-only origins. `null` is what a browser sends for file:// pages.
ALLOWED_ORIGIN_RE = re.compile(
    r"^(null|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?)$", re.I
)
ALLOWED_HOST_RE = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.I)

CMD_MAP = {
    "navigate":         lambda b: ["navigate", "--url", b["url"]],
    "snapshot":         lambda b: ["snapshot"],
    "extract":          lambda b: ["extract", "--selector", b["selector"]],
    "screenshot":       lambda b: ["screenshot", "--out", b.get("out", "shots/relay_page.png")],
    "devtools-console": lambda b: ["devtools", "--what", "console"],
    "devtools-perf":    lambda b: ["devtools", "--what", "perf"],
    "devtools-network": lambda b: ["devtools", "--what", "network"],
    "act-click":        lambda b: ["act", "--allow-actions", "--action", "click",
                                   "--target", b["selector"], "--task", "relay-ui"],
    "act-type":         lambda b: ["act", "--allow-actions", "--action", "type",
                                   "--target", b["selector"], "--value", b.get("value", ""),
                                   "--task", "relay-ui"],
    "status":           lambda b: ["status"],
    "close":            lambda b: ["close"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Handler(BaseHTTPRequestHandler):
    server_version = f"NexusRelay/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ── security helpers ────────────────────────────────────────────────────
    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True                     # curl / local scripts
        return bool(ALLOWED_ORIGIN_RE.match(origin.strip()))

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        return bool(ALLOWED_HOST_RE.match(host.strip()))

    def _token_ok(self) -> bool:
        expected = self.server.token           # type: ignore[attr-defined]
        if not expected:
            return True
        supplied = self.headers.get("X-Nexus-Token", "")
        return secrets.compare_digest(supplied, expected)

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and ALLOWED_ORIGIN_RE.match(origin.strip()):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Nexus-Token")

    def _reply(self, code: int, obj) -> None:
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, message: str, **extra) -> None:
        self._reply(code, {"ok": False, "error": message, **extra})

    # ── HTTP verbs ──────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        if not self._origin_allowed():
            return self._error(403, "Origin not allowed by the Nexus relay")
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._host_allowed():
            return self._error(403, "Invalid Host header (DNS-rebinding protection)")
        if not self._origin_allowed():
            return self._error(403, "Origin not allowed by the Nexus relay")
        path = self.path.split("?", 1)[0]

        if path == "/health":
            # Deliberately unauthenticated so the UI can show relay status.
            return self._reply(200, {
                "ok": True, "service": "nexus-relay", "version": VERSION,
                "auth": bool(self.server.token),                    # type: ignore[attr-defined]
                "queue": len(read_queue()),
                "time": now_iso(),
            })
        if path == "/queue":
            if not self._token_ok():
                return self._error(401, "Invalid or missing X-Nexus-Token")
            return self._reply(200, queue_payload())
        if path == "/tools":
            if not self._token_ok():
                return self._error(401, "Invalid or missing X-Nexus-Token")
            return self._reply(200, {"ok": True, "endpoints": list(CMD_MAP) + ["publish", "publish-due", "mcp", "queue", "health"]})
        return self._error(404, "not found")

    def do_POST(self):
        if not self._host_allowed():
            return self._error(403, "Invalid Host header (DNS-rebinding protection)")
        if not self._origin_allowed():
            return self._error(403, "Origin not allowed by the Nexus relay")
        if not self._token_ok():
            return self._error(401, "Invalid or missing X-Nexus-Token")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "invalid Content-Length")
        if length > 2_000_000:
            return self._error(413, "payload too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return self._error(400, f"invalid JSON body: {e}")

        path = self.path.split("?", 1)[0]
        if path == "/browser":
            return self._browser(body)
        if path == "/publish":
            return self._publish(body)
        if path == "/publish-due":
            payload, status = self._run_python(
                [sys.executable, str(PUBLISHER), "--due",
                 *(["--dry-run"] if body.get("dry_run") else [])], timeout=600)
            return self._reply(status, payload)
        if path == "/mcp":
            return self._mcp(body)
        if path == "/shutdown":
            print("[relay] shutdown requested by the UI")
            self._reply(200, {"ok": True, "stopping": True})
            self.server.shutdown()                                   # type: ignore[attr-defined]
            return
        return self._error(404, "not found")

    # ── handlers ────────────────────────────────────────────────────────────
    def _browser(self, body: dict):
        cmd = body.get("cmd", "")
        builder = CMD_MAP.get(cmd)
        if not builder:
            return self._error(400, f"unknown cmd: {cmd}")
        if cmd.startswith("act-") and not body.get("allowActions"):
            return self._error(403, "Browser actions permission NOT granted. "
                                    "Enable it on the Browser Agent page first.")
        try:
            args = builder(body)
        except KeyError as e:
            return self._error(400, f"missing field {e}")

        proc = subprocess.run([sys.executable, str(BROWSER), *args],
                              capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        # exit code 2 == deliberate POLICY BLOCK (permission/deny-list), not a crash.
        if proc.returncode == 2:
            return self._error(403, proc.stdout.strip() or proc.stderr[-500:],
                               blocked=True, cmd=" ".join(args))
        if proc.returncode != 0:
            return self._error(500, proc.stderr[-800:] or proc.stdout[-800:] or "browser command failed",
                               cmd=" ".join(args), exit=proc.returncode)
        return self._reply(200, {"ok": True, "cmd": " ".join(args), "exit": 0,
                                 "stdout": proc.stdout, "stderr": proc.stderr[-500:]})

    def _publish(self, body: dict):
        """Publish, or queue for later when a schedule is supplied.

        This route did not exist before, so every "Publish Now" click in the UI
        fell into the catch block and reported "relay offline" even with the
        relay running — publishing could never work.
        """
        text = (body.get("content") or "").strip()
        platforms = body.get("platforms") or []
        if not text:
            return self._error(400, "content is required")
        if not platforms:
            return self._error(400, "at least one platform is required")

        schedule = body.get("schedule")
        if schedule and not body.get("dry_run"):
            job = enqueue_job(body)
            return self._reply(200, {"ok": True, "status": "scheduled", "job": job,
                                     "results": [{"platform": p, "ok": True,
                                                  "scheduled": True} for p in platforms]})

        payload = json.loads(json.dumps(body, default=str))   # deep copy
        payload.pop("credentials_file", None)
        job_file = LOGS / "publish_job.json"
        job_file.parent.mkdir(parents=True, exist_ok=True)
        job_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        args = [sys.executable, str(PUBLISHER), "--from-json", str(job_file)]
        if body.get("dry_run"):
            args.append("--dry-run")
        try:
            payload, status = self._run_python(args, timeout=300)
        finally:
            # The job file exists only to hand the payload to publish.py; it is
            # not a record (logs/publish_audit.jsonl and the queue are).
            try:
                job_file.unlink()
            except OSError:
                pass
        # Keep the queue in step with what actually happened.
        try:
            if isinstance(payload, dict) and payload.get("status"):
                record_finished_job(payload)
        except Exception as e:                     # never fail the publish over bookkeeping
            print(f"[relay] could not record job result: {e}")
        return self._reply(status, payload)

    def _mcp(self, body: dict):
        payload = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "clientInfo": {"name": "nexus-relay", "version": VERSION},
                        "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": body.get("tool"), "arguments": body.get("arguments", {})}},
        ]
        stdin_data = "\n".join(json.dumps(r) for r in payload) + "\n"
        proc = subprocess.run([sys.executable, str(MCP)], input=stdin_data,
                              capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        for line in proc.stdout.splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2:
                return self._reply(200, msg.get("result", msg.get("error", {})))
        return self._error(500, proc.stderr[-800:] or "no response from the MCP server")

    def _run_python(self, args: list, timeout: int = 300):
        """Run a skill and return (payload, http_status).

        Split out from the reply so callers can inspect the outcome; the previous
        single-purpose helper replied and returned None, which made the publish
        handler's "did it work?" check always false and left its temp file behind.
        """
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=timeout, cwd=str(ROOT))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"command timed out after {timeout}s"}, 504
        raw = proc.stdout.strip()
        parsed = extract_json(raw)
        if parsed is None:
            return ({"ok": False, "error": proc.stderr[-800:] or raw[-800:] or "no JSON output"}, 500)
        if proc.returncode != 0 and (not isinstance(parsed, dict) or parsed.get("ok") is not False):
            parsed = {"ok": False, "exit": proc.returncode, "result": parsed,
                      "stderr": proc.stderr[-500:]}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", proc.returncode == 0)
        return parsed, 200

    def log_message(self, fmt, *args):
        print(f"[relay] {datetime.now().strftime('%H:%M:%S')} {self.address_string()} {fmt % args}")


def extract_json(text: str):
    """Pull the last complete JSON value out of mixed stdout/stderr noise.

    Skills print human-readable lines (`[audit] OK …`) alongside their JSON
    payload, so we scan for every position that could start a JSON value and
    keep the largest one that parses.
    """
    if not text:
        return None
    decoder = json.JSONDecoder()
    best = None
    for start, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, (dict, list)) and (best is None or end > best[0]):
            best = (end, obj)
    if best:
        return best[1]
    # Last resort: the skills may print a bare value.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ────────────────────────────────────────────────────────────────
# Publish queue helpers (single source of truth: logs/publish_queue.json)
# ────────────────────────────────────────────────────────────────
QUEUE_FILE = LOGS / "publish_queue.json"


def read_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def enqueue_job(body: dict) -> dict:
    jobs = read_queue()
    job = {
        "id": body.get("id") or f"job-{int(datetime.now().timestamp() * 1000)}",
        "content": body.get("content", ""),
        "platforms": body.get("platforms", []),
        "media": body.get("media", ""),
        "schedule": body.get("schedule"),
        "status": "scheduled",
        "created_at": body.get("created_at") or now_iso(),
        "credentials": body.get("credentials", {}),
    }
    jobs.append(job)
    LOGS.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(jobs[-200:], indent=2), encoding="utf-8")
    return job


def record_finished_job(job: dict) -> None:
    """Store the outcome of an immediate publish so the UI queue reflects it."""
    jobs = read_queue()
    entry = {
        "id": job.get("id") or f"job-{int(datetime.now().timestamp() * 1000)}",
        "content": (job.get("content") or "")[:500],
        "platforms": job.get("platforms", []),
        "status": job.get("status"),
        "results": job.get("results", []),
        "published_at": job.get("published_at") or now_iso(),
        "credentials": {},     # never persist credentials for a finished job
    }
    jobs.append(entry)
    LOGS.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(jobs[-200:], indent=2), encoding="utf-8")


def queue_payload() -> dict:
    """Queue contents for the UI — with credentials stripped.

    SECURITY: queue entries carry the platform tokens needed to publish. They are
    persisted locally so a scheduled post can go out later, but there is no
    reason to hand them back over HTTP.
    """
    jobs = read_queue()
    counts: dict = {}
    for j in jobs:
        counts[j.get("status", "unknown")] = counts.get(j.get("status", "unknown"), 0) + 1
    safe = [{k: v for k, v in j.items() if k != "credentials"} for j in jobs[-60:]]
    return {"ok": True, "count": len(jobs), "counts": counts, "jobs": safe}


def main() -> int:
    parser = argparse.ArgumentParser(description="Nexus local relay")
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEXUS_RELAY_PORT", "8712")))
    parser.add_argument("--token", default=os.environ.get("NEXUS_RELAY_TOKEN", ""),
                        help="shared secret required in X-Nexus-Token (recommended)")
    parser.add_argument("--generate-token", action="store_true",
                        help="print a fresh random token and exit")
    args = parser.parse_args()

    if args.generate_token:
        print(secrets.token_urlsafe(32))
        return 0

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.token = args.token          # type: ignore[attr-defined]
    srv.daemon_threads = True

    print(f"🛠  Nexus relay v{VERSION} on http://127.0.0.1:{args.port}")
    print("    endpoints: /health /queue /browser /publish /publish-due /mcp")
    if args.token:
        print("    auth: X-Nexus-Token required ✔")
    else:
        print("    auth: NONE — local origins only. Set NEXUS_RELAY_TOKEN for defence in depth.")
    print("    bound to 127.0.0.1. Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[relay] stopped by user")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
