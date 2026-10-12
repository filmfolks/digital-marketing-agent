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
import mimetypes
import ssl
import time
import urllib.parse
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
SOCIAL = AGENT_DIR / "social_browser_agent.py"
LOGS = ROOT / "logs"
VERSION = "3.1.0"
UI_DIR = ROOT / "ui"
STARTED = time.time()
MIN_LAN_TOKEN = 24

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
        origin = origin.strip()
        if ALLOWED_ORIGIN_RE.match(origin):
            return True
        if getattr(self.server, "lan", False):
            # LAN/hosted mode: the PWA served by this relay (same origin) or an
            # explicitly listed origin. The required token is the real gate.
            host = self.headers.get("Host", "").strip().lower()
            parsed = urllib.parse.urlsplit(origin)
            if host and parsed.netloc.lower() == host:
                return True
            return origin.rstrip("/") in getattr(self.server, "extra_origins", set())
        return False

    def _host_allowed(self) -> bool:
        if getattr(self.server, "lan", False):
            return True                     # token is mandatory in LAN mode (see main)
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
        if origin and self._origin_allowed():
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
        if not self._host_allowed():
            return self._error(403, "Invalid Host header (DNS-rebinding protection)")
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
            return self._reply(200, health_payload(self.server))
        if path == "/queue":
            if not self._token_ok():
                return self._error(401, "Invalid or missing X-Nexus-Token")
            return self._reply(200, queue_payload())
        if path == "/tools":
            if not self._token_ok():
                return self._error(401, "Invalid or missing X-Nexus-Token")
            return self._reply(200, {"ok": True, "endpoints": list(CMD_MAP) + ["publish", "publish-due", "mcp", "queue", "health"]})
        if path == "/" or path == "/app":
            self.send_response(302)
            self.send_header("Location", "/app/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/app/"):
            return self._static(path[len("/app/"):] or "index.html")
        if path.startswith("/shots/"):
            if getattr(self.server, "lan", False) and not self._token_ok():
                return self._error(401, "Invalid or missing X-Nexus-Token")
            filename = os.path.basename(urllib.parse.unquote(path))
            ext = os.path.splitext(filename)[1].lower()
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".webp": "image/webp"}.get(ext)
            shot_file = ROOT / "shots" / filename
            if ctype and not filename.startswith(".") and shot_file.exists() and shot_file.is_file():
                try:
                    data = shot_file.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self._cors()   # same origin allow-list as every other route; never "*"
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception as e:
                    return self._error(500, f"could not read shot: {e}")
            return self._error(404, "screenshot not found")
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
        if path == "/social":
            return self._social(body)
        if path == "/shutdown":
            print("[relay] shutdown requested by the UI")
            self._reply(200, {"ok": True, "stopping": True})
            self.server.shutdown()                                   # type: ignore[attr-defined]
            return
        return self._error(404, "not found")

    def _static(self, rel: str):
        """Serve the PWA so a phone can load it from this relay (same origin)."""
        rel = urllib.parse.unquote(rel)
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            return self._error(404, "not found")
        if target.name.startswith(".") or target.name == "credentials.js" or not target.is_file():
            return self._error(404, "not found")
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest" or target.name == "manifest.json":
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        if target.name == "sw.js":
            self.send_header("Service-Worker-Allowed", "/app/")
        self.end_headers()
        self.wfile.write(data)

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
            payload, status = self._run_python(args, timeout=int(os.environ.get("NEXUS_PUBLISH_TIMEOUT", "1200")))
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

    def _social(self, body: dict):
        workflow = body.get("workflow", "status")
        read_only = workflow in ("status", "check_logins", "canva_export") or (
            workflow in ("x_post", "canva_to_x") and body.get("dry_run"))
        if not read_only and not body.get("allowActions"):
            return self._error(403, "Browser actions permission NOT granted. "
                                    "Enable it on the Browser Agent page first.")

        args = []
        if body.get("allowActions"):
            args.append("--allow-actions")
        if body.get("headed"):
            args.append("--headed")

        args.append(workflow)
        if workflow == "instagram_post":
            if not body.get("caption"):
                return self._error(400, "caption is required")
            args.extend(["--caption", str(body["caption"])])
            if body.get("image"):
                args.extend(["--image", str(body["image"])])
        elif workflow == "facebook_post":
            if not body.get("message"):
                return self._error(400, "message is required")
            args.extend(["--message", str(body["message"])])
            if body.get("page_url"):
                args.extend(["--page-url", str(body["page_url"])])
            if body.get("image"):
                args.extend(["--image", str(body["image"])])
        elif workflow == "blogger_post":
            if not body.get("title") or not body.get("body"):
                return self._error(400, "title and body are required")
            args.extend(["--title", str(body["title"]), "--body", str(body["body"])])
            if body.get("labels"):
                args.extend(["--labels", str(body["labels"])])
            if body.get("blog_url"):
                args.extend(["--blog-url", str(body["blog_url"])])
        elif workflow == "canva_create":
            if body.get("template"):
                args.extend(["--template", str(body["template"])])
            if body.get("headline"):
                args.extend(["--headline", str(body["headline"])])
            if body.get("out"):
                args.extend(["--out", str(body["out"])])
        elif workflow == "canva_to_instagram":
            if not body.get("design_url") or not body.get("caption"):
                return self._error(400, "design_url and caption are required")
            args.extend(["--design-url", str(body["design_url"]), "--caption", str(body["caption"])])
        elif workflow == "generic_post":
            if not body.get("url") or not body.get("text"):
                return self._error(400, "url and text are required")
            args.extend(["--url", str(body["url"]), "--text", str(body["text"])])
            if body.get("selector"):
                args.extend(["--selector", str(body["selector"])])
            if body.get("submit"):
                args.extend(["--submit", str(body["submit"])])
        elif workflow == "x_post":
            if not body.get("text"):
                return self._error(400, "text is required")
            args.extend(["--text", str(body["text"])])
            for m in (body.get("media") or [])[:4]:
                args.extend(["--media", str(m)])
            if body.get("long_post"):
                args.append("--long-post")
            if body.get("dry_run"):
                args.append("--dry-run")
        elif workflow == "canva_export":
            if not body.get("design_url"):
                return self._error(400, "design_url is required")
            args.extend(["--design-url", str(body["design_url"])])
            if body.get("file_type"):
                args.extend(["--file-type", str(body["file_type"]).upper()])
        elif workflow == "canva_to_x":
            if not body.get("design_url") or not body.get("text"):
                return self._error(400, "design_url and text are required")
            args.extend(["--design-url", str(body["design_url"]), "--text", str(body["text"])])
            if body.get("long_post"):
                args.append("--long-post")
            if body.get("dry_run"):
                args.append("--dry-run")
        elif workflow == "check_logins":
            args.extend(["--platforms", str(body.get("platforms") or "x,canva")])
        elif workflow == "status":
            pass
        else:
            return self._error(400, f"unknown workflow: {workflow}")

        proc = subprocess.run([sys.executable, str(SOCIAL), *args],
                              capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        if proc.returncode in (3, 4, 5):          # logged out / flow failed / engine missing
            parsed = extract_json(proc.stdout) or {}
            return self._error({3: 409, 4: 502, 5: 503}[proc.returncode],
                               parsed.get("error") or proc.stderr[-500:] or "social workflow failed",
                               workflow=workflow, exit=proc.returncode,
                               screenshot=parsed.get("screenshot"), logged_out=proc.returncode == 3)
        if proc.returncode == 2:
            return self._error(403, proc.stdout.strip() or proc.stderr[-500:],
                               blocked=True, cmd=" ".join(args))
        if proc.returncode != 0:
            return self._error(500, proc.stderr[-800:] or proc.stdout[-800:] or "social agent command failed",
                               cmd=" ".join(args), exit=proc.returncode)
        return self._reply(200, {"ok": True, "workflow": workflow, "cmd": " ".join(args), "exit": 0,
                                 "stdout": proc.stdout, "stderr": proc.stderr[-500:]})

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
        if os.environ.get("NEXUS_LOG_FORMAT", "").lower() == "json":
            print(json.dumps({"ts": now_iso(), "level": "info", "logger": "nexus.relay",
                              "client": self.address_string(), "msg": fmt % args}), flush=True)
        else:
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


def atomic_write_json(path: Path, data) -> None:
    """Write-then-rename so a crash mid-write can never leave a truncated queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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
    atomic_write_json(QUEUE_FILE, jobs[-200:])
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
    atomic_write_json(QUEUE_FILE, jobs[-200:])


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


def load_env(paths=None) -> None:
    """Read .env so NEXUS_RELAY_TOKEN etc. work without exporting them.
    (Previously the relay ignored .env entirely: a token set there was silently
    not enforced.) Real environment variables win; empty values are skipped."""
    for p in paths or (ROOT / ".env", Path.cwd() / ".env"):
        p = Path(p)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v


def health_payload(server) -> dict:
    jobs = read_queue()
    counts: dict = {}
    for j in jobs:
        counts[j.get("status", "unknown")] = counts.get(j.get("status", "unknown"), 0) + 1
    try:
        import importlib.util
        playwright_ok = importlib.util.find_spec("playwright") is not None
    except Exception:
        playwright_ok = False
    profiles_dir = Path(os.environ.get("NEXUS_PROFILES_DIR") or (LOGS / "profiles"))
    profiles = sorted(p.name for p in profiles_dir.iterdir() if p.is_dir()) if profiles_dir.is_dir() else []
    return {
        "ok": True, "service": "nexus-relay", "version": VERSION,
        "auth": bool(getattr(server, "token", "")),
        "lan": bool(getattr(server, "lan", False)),
        "tls": bool(getattr(server, "tls", False)),
        "uptime_s": int(time.time() - STARTED),
        "queue": len(jobs), "queue_counts": counts,
        "browser_fallback": os.environ.get("NEXUS_BROWSER_FALLBACK", "1") not in ("0", "false", "no", "off"),
        "playwright": playwright_ok,
        "profiles": profiles,
        "time": now_iso(),
    }


def validate_config(args) -> tuple[list, list]:
    """Return (errors, warnings). Errors stop the relay from starting."""
    errors, warnings = [], []
    if not (1 <= args.port <= 65535):
        errors.append(f"port {args.port} is out of range")
    lan = args.lan or args.bind not in ("127.0.0.1", "localhost", "::1")
    if lan:
        if not args.token:
            errors.append("LAN/hosted mode requires a token: set NEXUS_RELAY_TOKEN or pass --token "
                          "(generate one with --generate-token)")
        elif len(args.token) < MIN_LAN_TOKEN:
            errors.append(f"LAN/hosted mode needs a token of at least {MIN_LAN_TOKEN} characters")
        if not (args.tls_cert and args.tls_key):
            warnings.append("LAN mode without TLS: the token travels in clear text on your network. "
                            "Pass --tls-cert/--tls-key (e.g. from mkcert) or put the relay behind an HTTPS proxy.")
    if bool(args.tls_cert) != bool(args.tls_key):
        errors.append("--tls-cert and --tls-key must be given together")
    for f in (args.tls_cert, args.tls_key):
        if f and not Path(f).is_file():
            errors.append(f"TLS file not found: {f}")
    for name in ("NEXUS_API_RETRIES", "NEXUS_BROWSER_RETRIES", "NEXUS_PUBLISH_TIMEOUT"):
        v = os.environ.get(name)
        if v is not None and not v.strip().isdigit():
            errors.append(f"{name} must be a non-negative integer (got {v!r})")
    v = os.environ.get("NEXUS_BACKOFF_BASE")
    if v is not None:
        try:
            float(v)
        except ValueError:
            errors.append(f"NEXUS_BACKOFF_BASE must be a number (got {v!r})")
    for o in args.allow_origin:
        if not re.match(r"^https?://[^/]+$", o.rstrip("/")):
            errors.append(f"--allow-origin must look like https://host[:port], got {o!r}")
    if not args.token and not lan:
        warnings.append("no token set — fine for local-only use; required for phone/LAN access")
    return errors, warnings


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)   # logs appear immediately under launchers/systemd
    except Exception:
        pass
    load_env()
    parser = argparse.ArgumentParser(description="Nexus local relay")
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEXUS_RELAY_PORT", "8712")))
    parser.add_argument("--token", default=os.environ.get("NEXUS_RELAY_TOKEN", ""),
                        help="shared secret required in X-Nexus-Token (mandatory with --lan)")
    parser.add_argument("--generate-token", action="store_true",
                        help="print a fresh random token and exit")
    parser.add_argument("--lan", action="store_true",
                        default=os.environ.get("NEXUS_RELAY_LAN", "") in ("1", "true", "yes"),
                        help="listen on all interfaces so a phone on your network can send jobs")
    parser.add_argument("--bind", default=os.environ.get("NEXUS_RELAY_BIND", ""),
                        help="explicit bind address (default 127.0.0.1, or 0.0.0.0 with --lan)")
    parser.add_argument("--tls-cert", default=os.environ.get("NEXUS_TLS_CERT", ""))
    parser.add_argument("--tls-key", default=os.environ.get("NEXUS_TLS_KEY", ""))
    parser.add_argument("--allow-origin", action="append",
                        default=[o for o in os.environ.get("NEXUS_ALLOWED_ORIGINS", "").split(",") if o],
                        help="extra browser origin allowed in LAN mode (e.g. https://nexus.example.com)")
    parser.add_argument("--check-config", action="store_true", help="validate configuration and exit")
    args = parser.parse_args()

    if args.generate_token:
        print(secrets.token_urlsafe(32))
        return 0

    args.bind = args.bind or ("0.0.0.0" if args.lan else "127.0.0.1")
    errors, warnings = validate_config(args)
    for w in warnings:
        print(f"⚠️  {w}")
    for e in errors:
        print(f"❌ {e}")
    if errors:
        return 2
    if args.check_config:
        print("✔ configuration OK")
        return 0
    lan = args.lan or args.bind not in ("127.0.0.1", "localhost", "::1")

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.token = args.token          # type: ignore[attr-defined]
    srv.lan = lan                   # type: ignore[attr-defined]
    srv.extra_origins = {o.rstrip("/") for o in args.allow_origin}  # type: ignore[attr-defined]
    srv.tls = False                 # type: ignore[attr-defined]
    srv.daemon_threads = True
    if args.tls_cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.tls_cert, args.tls_key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        srv.tls = True              # type: ignore[attr-defined]

    scheme = "https" if srv.tls else "http"   # type: ignore[attr-defined]
    print(f"🛠  Nexus relay v{VERSION} on {scheme}://{args.bind}:{args.port}")
    print("    endpoints: /health /queue /browser /publish /publish-due /social /mcp  ·  UI at /app/")
    if args.token:
        print("    auth: X-Nexus-Token required ✔")
    else:
        print("    auth: NONE — local origins only. Set NEXUS_RELAY_TOKEN for defence in depth.")
    if lan:
        print(f"    LAN mode: open {scheme}://<this-computer-ip>:{args.port}/app/ on your phone, "
              "then paste the token in Settings → Local Relay.")
    else:
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
