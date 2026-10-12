#!/usr/bin/env python3
"""
Nexus Tasks MCP server (stdio, JSON-RPC 2.0, zero dependencies, Python 3.9+)
============================================================================
Exposes ONLY the Nexus action schema (agent/nexus_actions.py) to MCP clients
such as Hermes Agent, LM Studio (mcp.json), Claude Desktop or Cursor:

    create_task draft_post schedule_task plan_campaign list_tasks
    update_task cancel_task get_brand_profile get_calendar research_brand

There is no publish/approve tool: everything a model creates lands in
``pending_approval`` and a human approves it in the Nexus app. (The older
agent/nexus_mcp.py still has direct publishing tools; do not give that one to
an unattended local model.)

    python3 agent/nexus_tasks_mcp.py --self-test

Prompts: ``nexus_system`` returns skills/nexus/system_prompt.md filled in.
Resources: the skill cards under skills/nexus/.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import nexus_actions as na  # noqa: E402

SERVER_INFO = {"name": "nexus-tasks", "version": "1.0.0"}
PROTOCOL_VERSION = "2024-11-05"
SKILL = ROOT / "skills" / "nexus"


def _resources():
    out = [{"uri": "nexus://skill/SKILL.md", "name": "Nexus skill", "mimeType": "text/markdown"},
           {"uri": "nexus://skill/system_prompt.md", "name": "Nexus system prompt", "mimeType": "text/markdown"}]
    for p in sorted((SKILL / "tools").glob("*.md")):
        out.append({"uri": f"nexus://skill/tools/{p.name}", "name": f"Tool card: {p.stem}", "mimeType": "text/markdown"})
    return out


def handle(req: dict, executor: "na.Executor | None" = None):
    method = req.get("method", "")
    rid = req.get("id")
    params = req.get("params") or {}
    if rid is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION, "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "instructions": "Nexus marketing calendar. Tools create pending_approval tasks only; a human approves posting."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": na.mcp_tools()}}
    if method == "tools/call":
        ex = executor or na.Executor(brain=os.environ.get("NEXUS_MCP_BRAIN", "mcp-client"))
        result = ex.run(params.get("name", ""), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
            "isError": not result.get("ok", False)}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": _resources()}}
    if method == "resources/read":
        uri = params.get("uri", "")
        allowed = {r["uri"] for r in _resources()}
        if uri not in allowed:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "unknown resource"}}
        text = (SKILL / uri[len("nexus://skill/"):]).read_text(encoding="utf-8")
        return {"jsonrpc": "2.0", "id": rid, "result": {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"prompts": [
            {"name": "nexus_system", "description": "System prompt for planning posts with the Nexus tools"}]}}
    if method == "prompts/get":
        import nexus_chat
        text = nexus_chat.system_prompt(na.load_brand(None) or None)
        return {"jsonrpc": "2.0", "id": rid, "result": {"messages": [
            {"role": "user", "content": {"type": "text", "text": text}}]}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve(stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if not isinstance(req, dict):
                resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()
                continue
            try:
                resp = handle(req)
            except Exception as e:                                # noqa: BLE001
                resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


def self_test() -> int:
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    print(json.dumps({"ok": True, "server": init["result"]["serverInfo"], "tools": [t["name"] for t in tools]}, indent=2))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    serve()
