#!/usr/bin/env python3
"""
Nexus Local Relay  🛠
=====================
Tiny stdlib-only HTTP bridge that lets the browser UI (ui/index.html) execute
the Python agent skills on YOUR machine:

    Browser UI  ──POST http://localhost:8712/browser──▶  nexus_relay.py
                                                            │
                                                            ▼
                                    subprocess → agent/browser_automation.py
                                             → agent/nexus_mcp.py tools

Run it alongside the UI:
    python3 agent/nexus_relay.py            # port 8712, override with NEXUS_RELAY_PORT

Security:
    • Binds 127.0.0.1 only — never exposed to the network.
    • Enforces the same permission contract as the CLI: state-changing
      "act" commands are refused unless allowActions=true (which the UI sets
      ONLY when the user ticked Browser Actions Permission in the Browser
      Agent page).
    • Every request is appended to logs/browser_audit.jsonl by the skill
      itself; relay also keeps its own access log line per request.
"""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROWSER = ROOT / "agent" / "browser_automation.py"
PORT = int(os.environ.get("NEXUS_RELAY_PORT", "8712"))

CMD_MAP = {
    "navigate":         lambda b: ["navigate", "--url", b["url"]],
    "snapshot":         lambda b: ["snapshot"],
    "extract":          lambda b: ["extract", "--selector", b["selector"]],
    "screenshot":       lambda b: ["screenshot", "--out", b.get("out", "shots/relay_page.png")],
    "devtools-console": lambda b: ["devtools", "--what", "console"],
    "devtools-perf":    lambda b: ["devtools", "--what", "perf"],
    "act-click":        lambda b: ["act", "--allow-actions", "--action", "click",
                                   "--target", b["selector"], "--task", "relay-ui"],
    "act-type":         lambda b: ["act", "--allow-actions", "--action", "type",
                                   "--target", b["selector"], "--value", b.get("value", ""),
                                   "--task", "relay-ui"],
}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._reply(400, {"error": "invalid JSON"})

        if self.path == "/browser":
            cmd = body.get("cmd", "")
            builder = CMD_MAP.get(cmd)
            if not builder:
                return self._reply(400, {"error": f"unknown cmd: {cmd}"})
            if cmd.startswith("act-") and not body.get("allowActions"):
                return self._reply(403, {"error":
                    "Browser actions permission NOT granted. Enable it on the Browser Agent page first."})
            try:
                args = builder(body)
            except KeyError as e:
                return self._reply(400, {"error": f"missing field {e}"})
            proc = subprocess.run([sys.executable, str(BROWSER), *args],
                                  capture_output=True, text=True, timeout=90, cwd=str(ROOT))
            # BUG FIX: exit code 2 means a POLICY BLOCK (permission not granted /
            # deny-list), not a server crash. Previously relay returned HTTP 500 for
            # it, and the UI's fetch treated any non-2xx as "relay offline" and fell
            # back to the SIMULATED demo path — hiding the real refusal from the user.
            if proc.returncode == 2:
                return self._reply(403, {"blocked": True, "cmd": " ".join(args),
                                         "reason": proc.stdout.strip() or proc.stderr[-500:]})
            if proc.returncode != 0:
                return self._reply(500, {"cmd": " ".join(args), "exit": proc.returncode,
                                         "stdout": proc.stdout, "stderr": proc.stderr[-500:]})
            return self._reply(200, {"cmd": " ".join(args), "exit": 0,
                                     "stdout": proc.stdout, "stderr": proc.stderr[-500:]})

        if self.path == "/mcp":
            # Generic MCP tool call: {"tool": "...", "arguments": {...}}
            payload = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05",
                            "clientInfo": {"name": "nexus-relay", "version": "1"}, "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": body.get("tool"), "arguments": body.get("arguments", {})}},
            ]
            stdin_data = "\n".join(json.dumps(r) for r in payload) + "\n"
            proc = subprocess.run([sys.executable, str(ROOT / "agent" / "nexus_mcp.py")],
                                  input=stdin_data, capture_output=True, text=True,
                                  timeout=120, cwd=str(ROOT))
            for line in proc.stdout.splitlines():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == 2:
                    return self._reply(200, msg.get("result", msg.get("error", {})))
            return self._reply(500, {"error": proc.stderr[-500:] or "no response"})

        self._reply(404, {"error": "not found"})

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[relay] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"🛠 Nexus relay on http://localhost:{PORT}  (paths: /browser, /mcp)")
    print("   Ctrl+C to stop. Bound to localhost only.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[relay] stopped by user")
