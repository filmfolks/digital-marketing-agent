#!/usr/bin/env python3
"""
Nexus Telegram Bot Controller  📲
=================================
Control the entire Nexus Social Media Manager from your phone via Telegram.
Zero heavy dependencies — pure stdlib long-polling against the Bot API.

Open-source libraries you CAN use instead (recommended for production):
  • python-telegram-bot   https://github.com/python-telegram-bot/python-telegram-bot (LGPL-3.0)
  • aiogram               https://github.com/aiogram/aiogram                          (MIT)
This file intentionally uses raw HTTP so it runs anywhere with just Python 3.

WHAT IT DOES
  /status              agent health, engine, pending queue
  /generate <topic>    dispatch a content task to the AI engine chain
  /plan <goal> [days] [budget]   campaign plan (Meta/Google Ads phases)
  /hashtags <niche>    tiered hashtag sets
  /browse <url>        read-only page fetch + title (via browser skill)
  /shot <url>          screenshot a page → sent back as a photo
  /act <action> <selector> [text]   click/type — REQUIRES inline-keyboard approval
  /queue               queued posts today
  /approve <id>        approve a queued/pending action or post
  /reject <id>         reject it (logged with reason)
  /logs [n]            last n error/warn entries from the agent log
  /help                this list

APPROVAL MODEL (user permission is mandatory for actions):
  State-changing commands (/act, publishing) never execute silently. The bot
  sends an inline "✅ Approve / ❌ Reject" keyboard; execution happens ONLY
  after the SAME user taps Approve within 5 minutes. Every request, approval
  and rejection is written to logs/telegram_audit.jsonl.

SETUP (2 minutes):
  1. Talk to @BotFather → /newbot → copy the token.
  2. Message your bot once, then:  curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
     → copy your chat id.
  3. export TELEGRAM_BOT_TOKEN=... ; export TELEGRAM_CHAT_ID=...
  4. python3 agent/telegram_bot.py      (or run it under systemd/launchd)

SECURITY: only TELEGRAM_CHAT_ID (and any ids in NEXUS_TG_ALLOWLIST comma
list) may issue commands; everyone else gets a polite refusal that is logged.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"
LOGS = ROOT / "logs"
AUDIT = LOGS / "telegram_audit.jsonl"
PENDING_FILE = LOGS / "telegram_pending.json"   # approvals awaiting user

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED = set(filter(None, [OWNER, *os.environ.get("NEXUS_TG_ALLOWLIST", "").split(",")]))
MCP_CLIENT = str(AGENT_DIR / "nexus_mcp.py")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def tg(method, **params):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        audit("api_error", method, str(e))
        return {"ok": False, "error": str(e)}


def send(chat_id, text, keyboard=None, photo=None):
    if photo:
        return upload_photo(chat_id, photo, text)   # multipart sender below
    params = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown"}
    if keyboard:
        params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    r = tg("sendMessage", **params)
    # BUG FIX: AI-generated marketing copy frequently contains unbalanced ` * _ [ ]
    # characters, which makes Telegram reject Markdown with HTTP 400 — the message
    # was silently LOST (only an api_error audit line). Retry as plain text so the
    # user always receives the content.
    if not r.get("ok") and "can't parse" in str(r.get("error", "")).lower() + str(r):
        params.pop("parse_mode", None)
        r = tg("sendMessage", **params)
    return r


def audit(who, command, status, detail=""):
    LOGS.mkdir(exist_ok=True)
    entry = {"ts": now_iso(), "who": who, "command": command,
             "status": status, "detail": detail[:400]}
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ────────────────────────────────────────────────────────────────
# MCP tool caller — the bot drives Nexus through the same MCP server
# the desktop agents use (single source of truth for skills).
# ────────────────────────────────────────────────────────────────
def call_tool(name, arguments, timeout=90):
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "nexus-telegram-bot", "version": "1.0"},
                    "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}},
    ]
    payload = "\n".join(json.dumps(r) for r in reqs) + "\n"
    try:
        proc = subprocess.run([sys.executable, MCP_CLIENT], input=payload,
                              capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        for line in proc.stdout.splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2:
                content = msg.get("result", {}).get("content", [])
                text = content[0]["text"] if content else json.dumps(msg.get("error", {}))
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"ok": True, "raw": text}
        return {"ok": False, "error": "No response from MCP server", "stderr": proc.stderr[-300:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ────────────────────────────────────────────────────────────────
# Pending approvals (permission model)
# ────────────────────────────────────────────────────────────────
def load_pending():
    try:
        return json.loads(PENDING_FILE.read_text())
    except Exception:
        return {}


def save_pending(d):
    PENDING_FILE.write_text(json.dumps(d, indent=2))


def request_approval(chat_id, command, args_summary, exec_fn_desc):
    pid = f"{int(time.time()*1000)%1000000}"
    pending = load_pending()
    pending[pid] = {"chat_id": chat_id, "command": command, "args": exec_fn_desc,
                    "requested": time.time(), "summary": args_summary}
    save_pending(pending)
    kb = [[{"text": "✅ Approve", "callback_data": f"approve:{pid}"},
           {"text": "❌ Reject", "callback_data": f"reject:{pid}"}]]
    send(chat_id,
         f"🔐 *Permission required*\n`{command}`\n_{args_summary}_\n\n"
         "The agent will NOT act until you tap Approve (expires in 5 min).",
         keyboard=kb)
    audit(chat_id, command, "approval_requested", args_summary)
    return pid


def execute_pending(pid, chat_id):
    pending = load_pending()
    item = pending.get(pid)
    if not item:
        send(chat_id, "⌛ That approval expired or never existed.")
        return
    if time.time() - item["requested"] > 300:
        pending.pop(pid, None); save_pending(pending)
        send(chat_id, "⏱ Approval timed out (5 min). Request again if still needed.")
        audit(chat_id, item["command"], "approval_expired")
        return
    cmd = item["command"]
    if cmd.startswith("browser_act"):
        _, action, target, value = cmd.split("|", 3)
        result = call_tool("browser_act", {"action": action, "target": target,
                                           "value": value, "task": "telegram-approved",
                                           "user_permission_granted": True})
    elif cmd.startswith("publish|"):
        _, platform, text = cmd.split("|", 2)
        # BUG FIX: this used to return a hard-coded {"ok": True, "note": "Queued…"}
        # and never publish anything — the bot reported success for a post that
        # did not exist. It now runs the real publisher and reports the truth.
        result = call_tool("publish_post", {"platform": platform, "text": text})
    else:
        result = {"ok": False, "error": f"Unknown pending command shape: {cmd}"}
    pending.pop(pid, None); save_pending(pending)
    send(chat_id, f"🤖 Executed after approval:\n```{json.dumps(result)[:1500]}```")
    audit(chat_id, cmd, "approved_and_executed", json.dumps(result)[:200])


# ────────────────────────────────────────────────────────────────
# Command handlers
# ────────────────────────────────────────────────────────────────
HELP = (
    "*Nexus Bot — commands*\n"
    "/status – agent health\n"
    "/generate <topic> – make content now\n"
    "/plan <goal> [days] [budget] – campaign plan\n"
    "/hashtags <niche> – hashtag tiers\n"
    "/browse <url> – read a page (no clicks)\n"
    "/shot <url> – screenshot a page\n"
    "/act click <selector> | /act type <selector> <text>\n"
    "     ↳ requires ✅ tap-to-approve\n"
    "/post <instagram|facebook|twitter|linkedin|blogger> <text> – needs ✅ approval\n"
    "/queue – queued & scheduled posts\n"
    "/approve <id> – approve a pending action\n"
    "/reject <id> – reject it (logged)\n"
    "/logs [n] – recent errors/warnings\n"
    "/help – this message"
)


def cmd_generate(chat_id, arg):
    if not arg:
        return send(chat_id, "Usage: `/generate Launch week for our AI scheduling app`")
    send(chat_id, "⏳ Dispatching to the agent engine chain…")
    r = call_tool("generate_content", {"topic": arg, "platforms": ["instagram", "linkedin", "twitter"]})
    if r.get("ok"):
        body = "\n\n".join(f"*{k.upper()}*\n`{v}`" for k, v in r.get("content", {}).items())
        send(chat_id, f"✨ Generated:\n{body}")
    else:
        send(chat_id, f"🚨 Agent failed: `{r.get('error', 'unknown')}` — check /logs")


def cmd_plan(chat_id, arg):
    # BUG FIX: old parser blindly dropped the last two tokens as days+budget, so a
    # plain goal like "q3 launch push" became "q3" (words silently lost) and
    # "holiday sale campaign 10" mis-read 10 as budget. Now we only strip trailing
    # tokens that are actually numeric: [budget] then optional [days].
    parts = arg.split() if arg else []
    days, budget = 5, 500.0
    goal_parts = list(parts)
    if goal_parts and re.fullmatch(r"\d+(\.\d+)?", goal_parts[-1]):
        budget = float(goal_parts.pop())
        if goal_parts and goal_parts[-1].isdigit():
            days = int(goal_parts.pop())
    elif goal_parts and goal_parts[-1].isdigit():
        days = int(goal_parts.pop())
    goal = " ".join(goal_parts)
    if not goal:
        return send(chat_id, "Usage: `/plan grow newsletter signups 7 350`")
    r = call_tool("plan_campaign", {"goal": goal, "days": days, "budget_usd": budget})
    lines = [f"📅 *Plan:* {p['day']}. {p['phase']} — {p['activity']} (${p['spend_usd']}/day)"
             for p in r.get("plan", [])]
    send(chat_id, "\n".join(lines) if lines else f"Failed: {r}")


def cmd_hashtags(chat_id, arg):
    r = call_tool("hashtag_research", {"niche": arg or "marketing"})
    tiers = r.get("tiers", {})
    send(chat_id, "\n".join(f"*{k}*: {' '.join(v)}" for k, v in tiers.items()))


def cmd_browse(chat_id, arg):
    if not arg.startswith("http"):
        return send(chat_id, "Usage: `/browse https://example.com`")
    r = call_tool("browser_navigate", {"url": arg})
    snap = call_tool("browser_snapshot", {})
    head = (snap.get("data", {}).get("snapshot", "") or "")[:1500]
    send(chat_id, f"🌐 {json.dumps(r.get('data', {}))[:200]}\n\n*Page snapshot:*\n`{head}`")


def cmd_shot(chat_id, arg):
    if not arg.startswith("http"):
        return send(chat_id, "Usage: `/shot https://example.com`")
    call_tool("browser_navigate", {"url": arg})
    r = call_tool("browser_screenshot", {"path": f"shots/tg_{int(time.time())}.png"})
    path = r.get("data", {}).get("screenshot") or r.get("output")
    if path and Path(path).exists():
        send(chat_id, f"📸 Screenshot of {arg}", photo=path)
    else:
        send(chat_id, f"🚨 Screenshot failed: {json.dumps(r)[:300]}")


def upload_photo(chat_id, path, caption):
    """multipart/form-data sendPhoto using stdlib only."""
    import mimetypes, uuid
    boundary = uuid.uuid4().hex
    body = b""
    def field(name, value):
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
                f"\r\n\r\n{value}\r\n").encode()
    body += field("chat_id", str(chat_id)) + field("caption", caption)
    ctype = mimetypes.guess_type(path)[0] or "image/png"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\";"
             f" filename=\"{Path(path).name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    body += Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                                 data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        audit(chat_id, "upload_photo", "error", str(e))


def cmd_act(chat_id, arg):
    m = re.match(r"(click|type|press|scroll)\s+(\S+)\s*(.*)", arg or "")
    if not m:
        return send(chat_id, "Usage: `/act click button.publish` or `/act type textarea#caption Hello!`")
    action, target, value = m.group(1), m.group(2), m.group(3)
    request_approval(chat_id, f"browser_act|{action}|{target}|{value}",
                     f"{action} → {target}" + (f" with \"{value[:60]}\"" if value else ""),
                     f"{action}:{target}:{value}")


def cmd_post(chat_id, arg):
    m = re.match(r"(instagram|facebook|twitter|linkedin|blogger)\s+(.+)", arg or "", re.S | re.I)
    if not m:
        return send(chat_id, "Usage: `/post instagram Today's launch teaser copy…`")
    platform, text = m.group(1).lower(), m.group(2)
    request_approval(chat_id, f"publish|{platform}|{text}",
                     f"Publish to {platform}: \"{text[:80]}\"", f"publish:{platform}")


def cmd_queue(chat_id, _arg):
    """List queued/scheduled posts (the handler was documented but never existed,
    so /queue answered "Unknown command" )."""
    r = call_tool("read_publish_queue", {"limit": 15})
    jobs = r.get("jobs", [])
    if not jobs:
        return send(chat_id, "📭 The publish queue is empty.")
    lines = []
    for j in jobs:
        when = j.get("schedule") or "immediate"
        preview = (j.get("content") or "")[:60].replace("\n", " ")
        lines.append(f"• `{j.get('id')}` [{j.get('status')}] {','.join(j.get('platforms', []))} — {when}\n  _{preview}_")
    send(chat_id, "📦 *Publish queue*\n" + "\n".join(lines))


def cmd_approve(chat_id, arg):
    pid = (arg or "").strip()
    if not pid:
        pend = load_pending()
        if not pend:
            return send(chat_id, "Nothing is waiting for approval.")
        return send(chat_id, "Usage: `/approve <id>`. Pending: " + ", ".join(f"`{k}`" for k in pend))
    execute_pending(pid, chat_id)


def cmd_reject(chat_id, arg):
    pid = (arg or "").strip()
    pending = load_pending()
    item = pending.pop(pid, None)
    save_pending(pending)
    if not item:
        return send(chat_id, f"No pending request `{pid}`.")
    audit(chat_id, item["command"], "rejected_by_user")
    send(chat_id, f"🗑 Rejected & logged: {item['summary']}")


def cmd_logs(chat_id, arg):
    n = int(arg) if arg and arg.isdigit() else 10
    r = call_tool("read_agent_logs", {"limit": n})
    bad = [e for e in r.get("entries", []) if e.get("status") in ("error", "blocked")]
    if not bad:
        return send(chat_id, "✅ No errors/blocks in recent agent log.")
    txt = "\n".join(f"• `{e['ts'][11:19]}` {e['command']} → {e['status']}: {e['detail'][:80]}" for e in bad[-n:])
    send(chat_id, f"🚨 Recent issues:\n{txt}")


def cmd_status(chat_id, _arg):
    r = call_tool("read_agent_logs", {"limit": 200})
    total = len(r.get("entries", []))
    errs = sum(1 for e in r.get("entries", []) if e.get("status") == "error")
    pend = len(load_pending())
    engine = os.environ.get("NEXUS_ENGINE", "cloud(xAI Grok default chain)")
    send(chat_id,
         f"🟢 *Nexus status*\nEngine: `{engine}`\nBrowser ops logged: `{total}` | errors: `{errs}`\n"
         f"Pending approvals: `{pend}`\nUptime poll loop: OK")


HANDLERS = {
    "start": cmd_status, "help": lambda c, a: send(c, HELP),
    "status": cmd_status, "generate": cmd_generate, "plan": cmd_plan,
    "hashtags": cmd_hashtags, "browse": cmd_browse, "shot": cmd_shot,
    "act": cmd_act, "post": cmd_post, "logs": cmd_logs,
    "queue": cmd_queue, "approve": cmd_approve, "reject": cmd_reject,
}


def handle_message(msg):
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if chat_id not in ALLOWED:
        audit(chat_id, text[:40], "refused_unauthorized")
        if OWNER:
            send(OWNER, f"🚫 Blocked unauthorized command from `{chat_id}`: {text[:80]}")
        return
    if not text.startswith("/"):
        # Natural language → route straight to the agent
        return cmd_generate(chat_id, text)
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    handler = HANDLERS.get(cmd)
    if handler:
        audit(chat_id, cmd, "executing", arg[:120])
        try:
            handler(chat_id, arg)
        except Exception as e:
            audit(chat_id, cmd, "error", str(e))
            send(chat_id, f"🚨 Command crashed: `{e}` (documented in logs)")
    else:
        send(chat_id, f"Unknown command `/{cmd}`. Try /help")


def handle_callback(cb):
    chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    data = cb.get("data", "")
    if chat_id not in ALLOWED:
        return tg("answerCallbackQuery", callback_query_id=cb["id"], text="Not authorized")
    if data.startswith("approve:"):
        execute_pending(data.split(":", 1)[1], chat_id)
    elif data.startswith("reject:"):
        pid = data.split(":", 1)[1]
        pending = load_pending(); item = pending.pop(pid, None); save_pending(pending)
        if item:
            audit(chat_id, item["command"], "rejected_by_user")
            send(chat_id, f"🗑 Rejected & logged: {item['summary']}")
    tg("answerCallbackQuery", callback_query_id=cb["id"], text="OK")


def main():
    if not TOKEN:
        print("Set TELEGRAM_BOT_TOKEN (see header docstring). Exiting."); sys.exit(1)
    me = tg("getMe")
    if not me.get("ok"):
        print("Token invalid:", me); sys.exit(1)
    print(f"🤖 @{me['result']['username']} online. Owner chat: {OWNER or 'unset'}")
    offset = 0
    while True:
        upd = tg("getUpdates", offset=offset + 1, timeout=25,
                 allowed_updates=["message", "callback_query"])
        for u in upd.get("result", []):
            offset = u["update_id"]
            if "message" in u:
                handle_message(u["message"])
            elif "callback_query" in u:
                handle_callback(u["callback_query"])
        time.sleep(1)


if __name__ == "__main__":
    main()
