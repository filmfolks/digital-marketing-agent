#!/usr/bin/env python3
"""
Nexus MCP Bridge  🔌
====================
Model Context Protocol server that exposes the entire Nexus skill set to ANY
MCP-capable client — Google Antigravity, Claude Desktop, LM Studio (Hermes),
Cursor, Windsurf, Grok agentic clients, etc. This is how Nexus "connects other
tools": one standard protocol instead of N bespoke integrations.

Implements the MCP wire format (JSON-RPC 2.0 over stdio) directly so it runs
with ZERO dependencies. If you prefer the official SDK:
    pip install "mcp[cli]"     → https://github.com/modelcontextprotocol/python-sdk
and this file's tool registry maps 1:1 onto @mcp.tool() decorators.

Registered tools:
  generate_content      – marketing copy via configured AI engine/fallback
  plan_campaign         – multi-day campaign strategy (Meta/Google Ads aware)
  hashtag_research      – tiered hashtag sets per platform
  browser_navigate      – open URL in agent browser (read-only)
  browser_snapshot      – accessibility tree of current page
  browser_screenshot    – capture page image to ./shots
  browser_act           – click/type — REQUIRES user permission flag
  devtools_inspect      – console errors / perf / network from Chrome DevTools
  render_video          – poster→video pipeline (Adobe After Effects / FFmpeg)
  telegram_notify       – push message/report to user's Telegram
  read_agent_logs       – pull error/activity log for debugging

Run standalone (dev):   python3 nexus_mcp.py --self-test
Register in a client:   see mcp-config-examples.json in repo root
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROWSER_SCRIPT = ROOT / "agent" / "browser_automation.py"
LOGS_DIR = ROOT / "logs"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_py(script: Path, *args, timeout=60) -> dict:
    """Run one of Nexus's python skills as a subprocess, capture JSON output."""
    cmd = [sys.executable, str(script), *[str(a) for a in args]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        out = proc.stdout.strip()
        # The scripts print an [audit] line then JSON; take last {...} block
        start = out.find("{")
        parsed = json.loads(out[start:]) if start >= 0 else {"raw": out}
        return {"ok": proc.returncode == 0, "exit": proc.returncode,
                "data": parsed, "stderr": proc.stderr.strip()[:400]}
    except Exception as e:
        return {"ok": False, "error": str(e), "cmd": " ".join(cmd)}


# ────────────────────────────────────────────────────────────────
# TOOL REGISTRY  (name → handler + JSON schema)
# ────────────────────────────────────────────────────────────────
def tool_generate_content(params):
    topic = params.get("topic", "")
    brand = params.get("brand", os.environ.get("NEXUS_BRAND", "My Brand"))
    tone = params.get("tone", "professional")
    platforms = params.get("platforms", ["instagram"])
    # Local template engine keeps the contract dependency-free; swap in a real
    # LLM call here if OPENAI_API_KEY / XAI_API_KEY env vars exist.
    pieces = {p: f"[{p.upper()}] {topic} — {tone} tone, brand: {brand}. #{''.join(w.lower() for w in topic.split()[:3])}"
              for p in platforms}
    return {"ok": True, "generated_at": now_iso(), "content": pieces}


def tool_plan_campaign(params):
    goal = params.get("goal", "")
    days = int(params.get("days", 5))
    budget = params.get("budget_usd", 500)
    plan = []
    phases = [("Awareness", "Hook + reach: Reels/Stories, broad Meta targeting"),
              ("Consideration", "Value posts + retargeting custom audience (video 50% viewers)"),
              ("Conversion", "Offer + urgency, Google Search ads on brand+solution keywords")]
    for d in range(1, days + 1):
        phase = phases[min(int((d - 1) / max(1, days // 3)), 2)]
        plan.append({"day": d, "phase": phase[0], "activity": phase[1],
                     "spend_usd": round(budget / days, 2)})
    return {"ok": True, "goal": goal, "days": days, "total_budget_usd": budget, "plan": plan}


def tool_hashtag_research(params):
    niche = params.get("niche", "marketing")
    n = niche.replace(" ", "").lower()
    return {"ok": True, "tiers": {
        "broad (1M+)": [f"#{n}", "#marketing", "#socialmediamarketing", "#business"],
        "mid (100K-1M)": [f"#{n}tips", f"#{n}strategy", "#contentmarketing", "#digitalstrategy"],
        "niche (<100K)": [f"#{n}forbeginners", f"#{n}hacks", f"#growyour{n}", f"#{n}community"]}}


def tool_browser_navigate(params):
    return run_py(BROWSER_SCRIPT, "navigate", "--url", params.get("url", "about:blank"))


def tool_browser_snapshot(_params):
    return run_py(BROWSER_SCRIPT, "snapshot")


def tool_browser_screenshot(params):
    out = params.get("path", "shots/mcp_page.png")
    return run_py(BROWSER_SCRIPT, "screenshot", "--out", out)


def tool_browser_act(params):
    """State-changing action — permission-gated exactly like the CLI."""
    if not params.get("user_permission_granted"):
        return {"ok": False, "error": "PERMISSION_REQUIRED: set user_permission_granted=true only after the human approves in Nexus Settings/Telegram."}
    args = ["act", "--allow-actions", "--action", params.get("action", "click"),
            "--target", params.get("target", ""), "--value", params.get("value", ""),
            "--task", params.get("task", "mcp act")]
    return run_py(BROWSER_SCRIPT, *args)


def tool_devtools_inspect(params):
    what = params.get("what", "console")
    return run_py(BROWSER_SCRIPT, "devtools", "--what", what)


def tool_render_video(params):
    """Poster/video pipeline. Uses FFmpeg locally; After Effects hook documented."""
    images = params.get("images", [])
    out = params.get("output", "shots/nexus_video.mp4")
    duration = float(params.get("seconds_per_frame", 3))
    if not images:
        return {"ok": False, "error": "No images supplied"}
    listfile = LOGS_DIR / "ffmpeg_concat.txt"
    LOGS_DIR.mkdir(exist_ok=True)
    with open(listfile, "w") as f:
        for img in images:
            f.write(f"file '{img}'\nduration {duration}\n")
        f.write(f"file '{images[-1]}'\n")
    r = _ffmpeg(["-y", "-f", "concat", "-safe", "0",
        "-i", str(listfile), "-vsync", "v2", "-pix_fmt", "yuv420p", out])
    if r["ok"]:
        return {"ok": True, "output": out, "engine": "ffmpeg"}
    # Graceful degrade: hand off to After Effects ExtendScript bridge later
    return {"ok": False, "error": r.get("error", "ffmpeg failed"),
            "fallback": "Queue for Adobe After Effects render farm (see SKILL 'creative_studio')",
            "ae_script_hint": "app.project.items → comp → RMRender; bridge via CEP/UXP extension"}


def _ffmpeg(extra_args):
    import shutil
    if not shutil.which("ffmpeg"):
        return {"ok": False, "error": "ffmpeg not installed (brew/apt install ffmpeg)"}
    cmd = ["ffmpeg"] + extra_args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {"ok": proc.returncode == 0, "stderr": proc.stderr[-400:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_telegram_notify(params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = params.get("chat_id", os.environ.get("TELEGRAM_CHAT_ID", ""))
    text = params.get("text", "")
    if not token or not chat:
        return {"ok": False, "error": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars (see agent/telegram_bot.py)"}
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage?" + urllib.parse.urlencode(
        {"chat_id": chat, "text": text, "parse_mode": "Markdown"})
    try:
        import urllib.request
        resp = json.loads(urllib.request.urlopen(url, timeout=15).read())
        return {"ok": resp.get("ok", False), "data": resp}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_read_agent_logs(params):
    limit = int(params.get("limit", 50))
    entries = []
    audit_file = LOGS_DIR / "browser_audit.jsonl"
    if audit_file.exists():
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        entries = [json.loads(l) for l in lines[-limit:] if l.startswith("{")]
    ui_log = ROOT / "ui"  # browser-side logs live in localStorage; exported via UI
    return {"ok": True, "entries": entries,
            "note": "Browser UI logs: use Agent Logs page → Export JSON to share them."}


TOOLS = {
    "generate_content":   (tool_generate_content,   "Generate platform-native marketing copy",
                           {"type": "object", "properties": {
                               "topic": {"type": "string"}, "brand": {"type": "string"},
                               "tone": {"type": "string"}, "platforms": {"type": "array", "items": {"type": "string"}}},
                            "required": ["topic"]}),
    "plan_campaign":      (tool_plan_campaign,      "Multi-day campaign plan incl. Meta/Google Ads phases",
                           {"type": "object", "properties": {
                               "goal": {"type": "string"}, "days": {"type": "integer"},
                               "budget_usd": {"type": "number"}}, "required": ["goal"]}),
    "hashtag_research":   (tool_hashtag_research,   "Tiered hashtag sets for a niche",
                           {"type": "object", "properties": {"niche": {"type": "string"}}, "required": ["niche"]}),
    "browser_navigate":   (tool_browser_navigate,   "Open a URL in the agent-controlled browser (read-only)",
                           {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
    "browser_snapshot":   (tool_browser_snapshot,   "Accessibility-style text snapshot of current page",
                           {"type": "object", "properties": {}}),
    "browser_screenshot": (tool_browser_screenshot, "Screenshot current page",
                           {"type": "object", "properties": {"path": {"type": "string"}}}),
    "browser_act":        (tool_browser_act,        "Click/type in the browser — requires user_permission_granted",
                           {"type": "object", "properties": {
                               "action": {"type": "string", "enum": ["click", "type", "press", "scroll"]},
                               "target": {"type": "string"}, "value": {"type": "string"},
                               "task": {"type": "string"},
                               "user_permission_granted": {"type": "boolean"}},
                            "required": ["action", "target", "user_permission_granted"]}),
    "devtools_inspect":   (tool_devtools_inspect,   "Chrome DevTools data: console/perf/network",
                           {"type": "object", "properties": {
                               "what": {"type": "string", "enum": ["console", "perf", "network"]}}}),
    "render_video":       (tool_render_video,       "Render poster images into a video (FFmpeg; After Effects fallback)",
                           {"type": "object", "properties": {
                               "images": {"type": "array", "items": {"type": "string"}},
                               "output": {"type": "string"}, "seconds_per_frame": {"type": "number"}}}),
    "telegram_notify":    (tool_telegram_notify,    "Send a message/report to the user's Telegram",
                           {"type": "object", "properties": {
                               "text": {"type": "string"}, "chat_id": {"type": "string"}}, "required": ["text"]}),
    "read_agent_logs":    (tool_read_agent_logs,    "Read recent agent/browser audit log entries",
                           {"type": "object", "properties": {"limit": {"type": "integer"}}}),
}


# ────────────────────────────────────────────────────────────────
# MCP JSON-RPC loop (stdio transport)
# ────────────────────────────────────────────────────────────────
SERVER_INFO = {"name": "nexus-social-manager", "version": "2.0.0"}
PROTOCOL_VERSION = "2024-11-05"


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(req):
    method = req.get("method", "")
    rid = req.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION, "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}}}}

    if method in ("notifications/initialized", "initialized"):
        return None  # notification, no response

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    if method == "tools/list":
        tools = [{"name": name, "description": desc, "inputSchema": schema}
                 for name, (_, desc, schema) in TOOLS.items()]
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}

    if method == "tools/call":
        name = req.get("params", {}).get("name", "")
        args = req.get("params", {}).get("arguments", {}) or {}
        entry = TOOLS.get(name)
        if not entry:
            return {"jsonrpc": "2.0", "id": rid, "error":
                    {"code": -32601, "message": f"Unknown tool: {name}"}}
        try:
            result = entry[0](args)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "isError": not result.get("ok", False)}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"Tool crashed: {e}"}],
                "isError": True}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": [
            {"uri": "file://campaign.json", "name": "Active campaign brief",
             "description": "Current campaign settings used by the agent"},
            {"uri": "file://skill/digital-marketer/SKILL.md", "name": "Digital Marketer Skill",
             "description": "Full skill set incl. Meta/Google Ads, browser automation"},
        ]}}

    if method == "resources/read":
        uri = req.get("params", {}).get("uri", "").replace("file://", "")
        path = ROOT / uri
        text = path.read_text(encoding="utf-8") if path.exists() else "(missing)"
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "contents": [{"uri": req["params"]["uri"], "mimeType": "text/plain", "text": text}]}}

    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def self_test():
    print("=== Nexus MCP self-test ===")
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "nexus-social-manager"
    listing = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in listing["result"]["tools"]]
    print(f"Tools exposed ({len(names)}): {', '.join(names)}")
    r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "generate_content", "arguments": {"topic": "Launch week"}}})
    assert not r["result"]["isError"]
    r2 = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "browser_act", "arguments": {"action": "click", "target": "x"}}})
    assert "PERMISSION_REQUIRED" in r2["result"]["content"][0]["text"]
    print("Permission gate works ✔  |  generate_content works ✔  |  initialize/tools/list ✔")
    print("Self-test PASSED")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        resp = handle(req)
        if resp is not None:
            send(resp)


if __name__ == "__main__":
    main()
