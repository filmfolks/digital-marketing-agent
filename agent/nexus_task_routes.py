"""
Relay routes for chat, tasks, calendar and the scheduler (mixed into nexus_relay.Handler).

GET  /tasks?status=&brand=&campaign_id=&start=&end=     list tasks
GET  /tasks/<id>                                        one task
GET  /campaigns                                         campaigns
GET  /calendar?start=YYYY-MM-DD&end=YYYY-MM-DD&brand=   tasks grouped by day (user tz)
GET  /calendar.ics[?token=]                             iCalendar export (subscribe from Calendar apps)
GET  /nexus/tools                                       the action schema (OpenAI tools format)
GET  /nexus/brain-status?provider=&url=&model=          is that brain reachable / which model is loaded
GET  /scheduler                                         scheduler status
POST /tasks                    {title, content, platforms, scheduled_at, ...}  → pending_approval (or draft)
POST /tasks/<id>/update        {fields...}
POST /tasks/<id>/approve       human approval  (approved; the scheduler posts it at scheduled_at)
POST /tasks/<id>/unapprove     back to pending_approval
POST /tasks/<id>/cancel
POST /tasks/approve-all        {ids?:[...], campaign_id?}
POST /campaigns/<id>/auto-approve {value: bool}      explicit human opt-in
POST /nexus/chat               {message, history?, brain:{provider,model,base_url,mode}, brand?}  SSE
POST /scheduler/tick           {dry_run?: true}    run due tasks now (dry_run validates only)

All POSTs go through the relay's token/origin checks. Every write is in the activity log.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import timedelta

import brain_router
import nexus_chat
import nexus_scheduler
import nexus_tasks as nt
import nexus_actions

TASK_RE = re.compile(r"^/tasks/([A-Za-z0-9-]{3,64})(?:/(update|approve|unapprove|cancel))?$")
CAMP_RE = re.compile(r"^/campaigns/([A-Za-z0-9-]{3,64})/auto-approve$")


def _store():
    return nt.TaskStore()


class TaskRoutesMixin:
    def _q(self) -> dict:
        q = urllib.parse.parse_qs(self.path.partition("?")[2])
        return {k: v[0] for k, v in q.items() if v}

    def task_get(self, path: str):
        """Returns True when the path was handled."""
        if not (path in ("/tasks", "/campaigns", "/calendar", "/calendar.ics", "/nexus/tools", "/scheduler",
                         "/nexus/brain-status") or TASK_RE.match(path)):
            return False
        q = self._q()
        if path == "/calendar.ics":
            tok = getattr(self.server, "token", "")
            if tok and not (self._token_ok() or q.get("token") == tok):
                self._error(401, "Invalid or missing token")
                return True
        elif not self._token_ok():
            self._error(401, "Invalid or missing X-Nexus-Token")
            return True
        store = _store()
        try:
            if path == "/tasks":
                start = nt.parse_when(q["start"]) if q.get("start") else None
                end = nt.parse_when(q["end"]) if q.get("end") else None
                status = q.get("status").split(",") if q.get("status") else None
                self._reply(200, {"ok": True, "timezone": nt.tz_name(), "tasks": store.list(
                    status=status, brand=q.get("brand"), campaign_id=q.get("campaign_id"), start=start, end=end)})
            elif path == "/campaigns":
                self._reply(200, {"ok": True, "campaigns": store.campaigns()})
            elif path == "/calendar":
                tz = nt.user_tz()
                start = nt.parse_when(q.get("start") or nt.now(tz).date().replace(day=1).isoformat()).replace(hour=0, minute=0)
                end = nt.parse_when(q["end"]).replace(hour=0, minute=0) + timedelta(days=1) if q.get("end") else start + timedelta(days=42)
                self._reply(200, {"ok": True, **store.calendar(start, end, brand=q.get("brand"))})
            elif path == "/calendar.ics":
                body = nt.to_ics(store.list(brand=q.get("brand"))).encode("utf-8")
                self._activity_status = 200
                self.send_response(200)
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="nexus-calendar.ics"')
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif path == "/nexus/tools":
                self._reply(200, {"ok": True, "tools": nexus_actions.openai_tools()})
            elif path == "/scheduler":
                sch = getattr(self.server, "scheduler", None)
                self._reply(200, {"ok": True, "scheduler": sch.status() if sch else {"running": False}})
            elif path == "/nexus/brain-status":
                cfg = brain_router.BrainConfig(q.get("provider", ""), q.get("model", ""), q.get("url", ""))
                self._reply(200, {"ok": True, **brain_router.status(cfg)})
            else:
                self._reply(200, {"ok": True, "task": store.get(TASK_RE.match(path).group(1))})
        except (nt.TaskError, ValueError, brain_router.BrainError) as e:
            self._error(404 if "no task" in str(e) else 400, str(e))
        return True

    def task_post(self, path: str, body: dict):
        if not (path in ("/tasks", "/tasks/approve-all", "/nexus/chat", "/scheduler/tick")
                or TASK_RE.match(path) or CAMP_RE.match(path)):
            return False
        store = _store()
        try:
            if path == "/nexus/chat":
                self._nexus_chat(body)
            elif path == "/tasks":
                status = body.get("status") if body.get("status") in ("draft", "pending_approval") else "pending_approval"
                self._reply(200, {"ok": True, "task": store.create(body, status=status, by="user")})
            elif path == "/tasks/approve-all":
                self._reply(200, {"ok": True, **store.approve_many(body.get("ids") or [], campaign_id=body.get("campaign_id"),
                                                                   by="owner (ui)")})
            elif path == "/scheduler/tick":
                sch = nexus_scheduler.Scheduler(store, dry_run=bool(body.get("dry_run", True)))
                self._reply(200, {"ok": True, **sch.tick()})
            elif CAMP_RE.match(path):
                self._reply(200, {"ok": True, "campaign": store.set_campaign_auto_approve(
                    CAMP_RE.match(path).group(1), bool(body.get("value")), by="user")})
            else:
                tid, action = TASK_RE.match(path).groups()
                if action == "update":
                    t = store.update(tid, body, by="user")
                elif action == "approve":
                    if body.get("scheduled_at"):
                        store.update(tid, {"scheduled_at": body["scheduled_at"]}, by="user")
                    t = store.approve(tid, by="owner (ui)")
                elif action == "unapprove":
                    t = store.transition(tid, "pending_approval", by="owner (ui)")
                elif action == "cancel":
                    t = store.cancel(tid, by="owner (ui)")
                else:
                    t = store.get(tid)
                self._reply(200, {"ok": True, "task": t})
        except (nt.TaskError, ValueError) as e:
            self._error(404 if "no task" in str(e) or "no campaign" in str(e) else 400, str(e))
        return True

    def _nexus_chat(self, body: dict):
        message = str(body.get("message") or "").strip()
        if not message:
            return self._error(400, "message is required")
        try:
            cfg = brain_router.BrainConfig.from_dict(body.get("brain") or {})
        except brain_router.BrainError as e:
            return self._error(400, str(e))
        history = [m for m in (body.get("history") or []) if isinstance(m, dict)
                   and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-12:]
        self._activity_status = 200
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        try:
            for ev in nexus_chat.run_turn(history, message, cfg, brand_id=body.get("brand") or None):
                kind = ev.pop("type", "message")
                self._sse(kind, ev)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:                                # noqa: BLE001 — reported to the client
            try:
                self._sse("error", {"error": str(e), "kind": type(e).__name__})
            except OSError:
                pass
