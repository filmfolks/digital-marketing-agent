#!/usr/bin/env python3
"""
Nexus task store + calendar
===========================
One thread-safe (and process-safe) JSON store for everything the brain plans:
single posts, scheduled posts and whole campaigns.

    data/tasks/tasks.json        all tasks (atomic writes)
    data/tasks/.lock             cross-process lock (mkdir-based, portable)

Status machine (the ONLY legal transitions)::

    draft ──▶ pending_approval ──▶ approved ──▶ running ──▶ done
      │              │                │            └──────▶ failed
      └──────────────┴────────────────┴──▶ cancelled
    failed ──▶ pending_approval / approved   (retry after a human looked at it)

* The model can only ever create ``draft`` or ``pending_approval`` tasks.
  ``approved`` is set by a human (UI / CLI / token-gated relay call), or by the
  store itself when the task's campaign was explicitly marked auto-approve by a human.
* ``approved -> running`` is a compare-and-set under the lock: that is what
  stops the scheduler from ever posting the same task twice.
* Every change is appended to the activity log.

Times are stored as ISO-8601 with an offset. The user's timezone comes from
NEXUS_TZ, then data/settings.json {"timezone"}, then Asia/Kolkata.

CLI:
    python3 agent/nexus_tasks.py list [--status pending_approval]
    python3 agent/nexus_tasks.py approve <id>
    python3 agent/nexus_tasks.py ics > calendar.ics
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ("draft", "pending_approval", "approved", "running", "done", "failed", "cancelled")
TRANSITIONS: Dict[str, tuple] = {
    "draft": ("pending_approval", "approved", "cancelled"),
    "pending_approval": ("approved", "draft", "cancelled"),
    "approved": ("running", "pending_approval", "cancelled"),
    "running": ("done", "failed"),
    "failed": ("pending_approval", "approved", "cancelled"),
    "done": (),
    "cancelled": ("pending_approval",),
}
PLATFORMS = ("x", "instagram", "facebook", "linkedin", "youtube", "blog")
EDITABLE = ("title", "instruction", "platforms", "content", "media", "scheduled_at", "brand",
            "hashtags", "poster_idea", "notes", "auto_approve")
DEFAULT_TZ = "Asia/Kolkata"
_FIXED = {"Asia/Kolkata": timedelta(hours=5, minutes=30), "Asia/Calcutta": timedelta(hours=5, minutes=30),
          "UTC": timedelta(0)}


# =============================================================================
# Time helpers
# =============================================================================
def tz_name() -> str:
    env = os.environ.get("NEXUS_TZ", "").strip()
    if env:
        return env
    try:
        s = json.loads((data_root() / "settings.json").read_text(encoding="utf-8"))
        if isinstance(s, dict) and s.get("timezone"):
            return str(s["timezone"])
    except (OSError, ValueError):
        pass
    return DEFAULT_TZ


def user_tz(name: Optional[str] = None) -> tzinfo:
    """zoneinfo when the tz database exists. Windows without the `tzdata` package
    has none, so the common zones fall back to a fixed offset instead of crashing."""
    name = name or tz_name()
    try:
        from zoneinfo import ZoneInfo           # Python 3.9+
        return ZoneInfo(name)
    except Exception:                           # noqa: BLE001 — ZoneInfoNotFoundError / ImportError
        off = _FIXED.get(name)
        if off is not None:
            return timezone(off, name)
        print(f"[tasks] unknown timezone {name!r}; using UTC (pip install tzdata on Windows)", file=sys.stderr)
        return timezone.utc


def now(tz: Optional[tzinfo] = None) -> datetime:
    return datetime.now(tz or user_tz())


def parse_when(value, tz: Optional[tzinfo] = None, *, ref: Optional[datetime] = None) -> Optional[datetime]:
    """ISO date/datetime (naive = user tz) → aware datetime. Date only → 10:00.
    Also accepts 'YYYY-MM-DD HH:MM' and a trailing 'Z'. Returns None for empty."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, 10, 0)
    else:
        s = str(value).strip().replace("Z", "+00:00")
        if "T" not in s and " " in s:
            s = s.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                d = date.fromisoformat(s[:10])
            except ValueError:
                raise ValueError(f"unrecognised date/time {value!r}; use ISO 8601 like 2026-10-20T18:30")
            dt = datetime(d.year, d.month, d.day, 10, 0)
        if len(s) == 10:
            dt = dt.replace(hour=10, minute=0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or user_tz())
    return dt


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


# =============================================================================
# Paths and locking
# =============================================================================
def data_root() -> Path:
    return Path(os.environ.get("NEXUS_DATA_DIR") or (ROOT / "data"))


class FileLock:
    """Portable inter-process lock: mkdir is atomic on POSIX and Windows.
    A lock older than ``stale_s`` (a crashed process) is broken."""

    def __init__(self, path: Path, timeout: float = 15.0, stale_s: float = 60.0):
        self.path, self.timeout, self.stale_s = Path(path), timeout, stale_s

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir()
                return
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_s:
                        self.path.rmdir()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(f"could not lock {self.path}")
                time.sleep(0.02)

    def release(self) -> None:
        try:
            self.path.rmdir()
        except OSError:
            pass


def _log(action: str, task: dict, **extra) -> None:
    try:
        sys.path.insert(0, str(ROOT / "agent"))
        import activity_log
        activity_log.record(agent="nexus_tasks", action=action, status=extra.pop("status", "ok"),
                            platform=",".join(task.get("platforms") or []),
                            content=task.get("content") or task.get("title") or "",
                            media=task.get("media") or [], brain=task.get("brain") or None,
                            task_id=task.get("id"), task_status=task.get("status"),
                            campaign_id=task.get("campaign_id"), **extra)
    except Exception:                                           # noqa: BLE001 — logging never breaks the store
        pass


# =============================================================================
# Store
# =============================================================================
class TaskError(ValueError):
    pass


class TaskStore:
    _locks: Dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, root: Optional[Path] = None, *, clock: Optional[Callable[[], datetime]] = None,
                 log: Callable = _log):
        self.root = Path(root) if root else data_root() / "tasks"
        self.path = self.root / "tasks.json"
        self.clock = clock or (lambda: now())
        self.log = log
        key = str(self.path.resolve())
        with TaskStore._locks_guard:
            self._tlock = TaskStore._locks.setdefault(key, threading.RLock())
        self._flock = FileLock(self.root / ".lock")

    # ── raw io ───────────────────────────────────────────────────────────────
    @contextmanager
    def locked(self):
        with self._tlock:
            self._flock.acquire()
            try:
                yield
            finally:
                self._flock.release()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("tasks"), dict):
                data.setdefault("campaigns", {})
                return data
        except FileNotFoundError:
            pass
        except ValueError:
            bad = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
            self.path.replace(bad)
            print(f"[tasks] corrupt store moved to {bad}", file=sys.stderr)
        return {"version": 1, "tasks": {}, "campaigns": {}}

    def _save(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".tasks.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        os.replace(str(tmp), str(self.path))

    def _stamp(self) -> str:
        return iso(self.clock())

    # ── normalisation ───────────────────────────────────────────────────────
    @staticmethod
    def norm_platforms(value) -> List[str]:
        if value in (None, ""):
            return []
        items = value if isinstance(value, (list, tuple)) else str(value).replace("/", ",").split(",")
        alias = {"twitter": "x", "ig": "instagram", "insta": "instagram", "fb": "facebook", "li": "linkedin",
                 "yt": "youtube"}
        out = []
        for p in items:
            k = str(p).strip().lower().lstrip("@")
            k = alias.get(k, k)
            if k not in PLATFORMS:
                raise TaskError(f"unknown platform {p!r}; use one of {list(PLATFORMS)}")
            if k not in out:
                out.append(k)
        return out

    def _clean(self, fields: dict) -> dict:
        out = {}
        for k in EDITABLE:
            if k not in fields:
                continue
            v = fields[k]
            if k == "platforms":
                v = self.norm_platforms(v)
            elif k == "scheduled_at":
                v = iso(parse_when(v))
            elif k == "media":
                v = [str(m) for m in (v if isinstance(v, list) else [v]) if m]
            elif k == "hashtags":
                v = [("#" + str(h).lstrip("#")).replace(" ", "") for h in (v if isinstance(v, list) else str(v).split()) if str(h).strip("# ")]
            elif k == "auto_approve":
                v = bool(v)
            else:
                v = "" if v is None else str(v)[:10000]
            out[k] = v
        return out

    # ── create / read ───────────────────────────────────────────────────────
    def create(self, fields: dict, *, status: str = "pending_approval", by: str = "user",
               brain: Optional[str] = None, campaign_id: Optional[str] = None) -> dict:
        if status not in ("draft", "pending_approval"):
            raise TaskError("new tasks start as draft or pending_approval; approval is a separate human step")
        clean = self._clean(fields)
        if not (clean.get("title") or clean.get("content") or clean.get("instruction")):
            raise TaskError("a task needs a title, instruction or content")
        stamp = self._stamp()
        task = {
            "id": f"t-{self.clock().strftime('%Y%m%d')}-{secrets.token_hex(4)}",
            "brand": clean.get("brand") or "", "title": clean.get("title") or (clean.get("content") or clean.get("instruction", ""))[:60],
            "instruction": clean.get("instruction", ""), "platforms": clean.get("platforms", []),
            "content": clean.get("content", ""), "media": clean.get("media", []),
            "hashtags": clean.get("hashtags", []), "poster_idea": clean.get("poster_idea", ""),
            "notes": clean.get("notes", ""), "scheduled_at": clean.get("scheduled_at"),
            "status": status, "auto_approve": False, "brain": brain or "", "campaign_id": campaign_id,
            "results": [], "created_by": by, "created_at": stamp, "updated_at": stamp,
            "history": [{"at": stamp, "event": "created", "status": status, "by": by}],
        }
        with self.locked():
            data = self._load()
            if campaign_id and campaign_id in data["campaigns"] and data["campaigns"][campaign_id].get("auto_approve") \
                    and status == "pending_approval":
                # A human explicitly marked this campaign auto-approve in the UI.
                task["status"] = "approved"
                task["auto_approve"] = True
                task["history"].append({"at": stamp, "event": "auto_approved", "by": "campaign:" + campaign_id})
            data["tasks"][task["id"]] = task
            self._save(data)
        self.log("task_created", task)
        return task

    def get(self, task_id: str) -> dict:
        task = self._load()["tasks"].get(str(task_id))
        if not task:
            raise TaskError(f"no task {task_id}")
        return task

    def list(self, *, status=None, brand: Optional[str] = None, campaign_id: Optional[str] = None,
             start: Optional[datetime] = None, end: Optional[datetime] = None) -> List[dict]:
        statuses = [status] if isinstance(status, str) else list(status or [])
        out = []
        for t in self._load()["tasks"].values():
            if statuses and t["status"] not in statuses:
                continue
            if brand and t.get("brand") != brand:
                continue
            if campaign_id and t.get("campaign_id") != campaign_id:
                continue
            if start or end:
                when = parse_when(t.get("scheduled_at"))
                if not when or (start and when < start) or (end and when >= end):
                    continue
            out.append(t)
        out.sort(key=lambda t: (t.get("scheduled_at") is None, parse_when(t.get("scheduled_at")) or datetime.max.replace(tzinfo=timezone.utc), t["created_at"]))
        return out

    # ── mutate ──────────────────────────────────────────────────────────────
    def _mutate(self, task_id: str, fn: Callable[[dict, dict], Optional[str]], action: str, **log_extra) -> dict:
        with self.locked():
            data = self._load()
            task = data["tasks"].get(str(task_id))
            if not task:
                raise TaskError(f"no task {task_id}")
            event = fn(task, data)
            task["updated_at"] = self._stamp()
            if event:
                task["history"].append({"at": task["updated_at"], **(event if isinstance(event, dict) else {"event": event})})
            self._save(data)
        self.log(action, task, **log_extra)
        return task

    def update(self, task_id: str, fields: dict, *, by: str = "user") -> dict:
        clean = self._clean(fields)
        if "auto_approve" in clean and by not in ("user", "owner", "ui"):
            raise TaskError("only a human can set auto_approve")

        def fn(task, _data):
            if task["status"] in ("running", "done"):
                raise TaskError(f"task is {task['status']}; it can no longer be edited")
            changed = [k for k, v in clean.items() if task.get(k) != v]
            task.update(clean)
            # Editing the content of an approved post sends it back for approval,
            # unless only the time moved (reschedule keeps the approval).
            if task["status"] == "approved" and set(changed) - {"scheduled_at", "notes"} and by not in ("user", "owner", "ui"):
                task["status"] = "pending_approval"
            return {"event": "updated", "fields": changed, "by": by, "status": task["status"]}
        return self._mutate(task_id, fn, "task_updated")

    def transition(self, task_id: str, to: str, *, by: str = "user", expect: Optional[str] = None,
                   **info) -> dict:
        """Change status. ``expect`` makes it a compare-and-set (used by the scheduler)."""
        if to not in STATUSES:
            raise TaskError(f"unknown status {to}")

        def fn(task, _data):
            cur = task["status"]
            if expect and cur != expect:
                raise TaskError(f"task {task_id} is {cur}, expected {expect}")
            if to not in TRANSITIONS.get(cur, ()):
                raise TaskError(f"cannot move task from {cur} to {to}")
            if to == "approved" and by in ("model", "brain") :
                raise TaskError("the model cannot approve tasks")
            if to == "approved" and not task.get("scheduled_at"):
                raise TaskError("set a date/time before approving")
            if to == "approved" and not (task.get("platforms") and (task.get("content") or "").strip()):
                raise TaskError("an approved post needs platforms and content")
            task["status"] = to
            for k in ("result", "error"):
                if k in info and info[k] is not None:
                    if k == "result":
                        task["results"].append(info[k])
                    else:
                        task["error"] = info[k]
            if to == "approved":
                task["approved_by"], task["approved_at"] = by, self._stamp()
            return {"event": to, "from": cur, "by": by, **({"error": info["error"]} if info.get("error") else {})}
        status = "failed" if to == "failed" else "ok"
        return self._mutate(task_id, fn, f"task_{to}", status=status)

    def approve(self, task_id: str, *, by: str = "user") -> dict:
        return self.transition(task_id, "approved", by=by)

    def cancel(self, task_id: str, *, by: str = "user") -> dict:
        return self.transition(task_id, "cancelled", by=by)

    def claim(self, task_id: str) -> Optional[dict]:
        """approved → running atomically. None if someone else got there first."""
        try:
            return self.transition(task_id, "running", by="scheduler", expect="approved")
        except TaskError:
            return None

    # ── campaigns ───────────────────────────────────────────────────────────
    def create_campaign(self, meta: dict, *, by: str = "user", brain: Optional[str] = None) -> dict:
        cid = f"c-{self.clock().strftime('%Y%m%d')}-{secrets.token_hex(3)}"
        camp = {"id": cid, "name": str(meta.get("name") or "Campaign")[:120], "brand": meta.get("brand") or "",
                "goal": str(meta.get("goal") or "")[:500], "start": meta.get("start"), "end": meta.get("end"),
                "auto_approve": False, "brain": brain or "", "created_at": self._stamp(), "created_by": by}
        with self.locked():
            data = self._load()
            data["campaigns"][cid] = camp
            self._save(data)
        self.log("campaign_created", {"title": camp["name"], "campaign_id": cid, "brain": brain})
        return camp

    def campaigns(self) -> List[dict]:
        return sorted(self._load()["campaigns"].values(), key=lambda c: c["created_at"], reverse=True)

    def set_campaign_auto_approve(self, campaign_id: str, value: bool, *, by: str = "user") -> dict:
        if by in ("model", "brain"):
            raise TaskError("only a human can change auto-approve")
        with self.locked():
            data = self._load()
            camp = data["campaigns"].get(campaign_id)
            if not camp:
                raise TaskError(f"no campaign {campaign_id}")
            camp["auto_approve"] = bool(value)
            self._save(data)
        self.log("campaign_auto_approve", {"title": camp["name"], "campaign_id": campaign_id}, value=bool(value))
        return camp

    def approve_many(self, ids: Iterable[str] = (), *, campaign_id: Optional[str] = None, by: str = "user") -> dict:
        ids = list(ids) or [t["id"] for t in self.list(status="pending_approval", campaign_id=campaign_id)]
        ok, errors = [], {}
        for tid in ids:
            try:
                ok.append(self.approve(tid, by=by)["id"])
            except TaskError as e:
                errors[tid] = str(e)
        return {"approved": ok, "errors": errors}

    # ── calendar ────────────────────────────────────────────────────────────
    def calendar(self, start: datetime, end: datetime, *, brand: Optional[str] = None) -> dict:
        days: Dict[str, list] = {}
        tz = user_tz()
        for t in self.list(brand=brand, start=start, end=end):
            if t["status"] == "cancelled":
                continue
            d = parse_when(t["scheduled_at"]).astimezone(tz).date().isoformat()
            days.setdefault(d, []).append(t)
        return {"start": iso(start), "end": iso(end), "timezone": tz_name(), "days": days}


# =============================================================================
# ICS export (RFC 5545)
# =============================================================================
def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while cut and (b[cut] & 0xC0) == 0x80:      # don't split a UTF-8 sequence
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def to_ics(tasks: Iterable[dict], *, name: str = "Nexus calendar", duration_min: int = 15,
           stamp: Optional[datetime] = None) -> str:
    stamp = (stamp or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Nexus//Tasks//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_ics_escape(name)}"]
    for t in tasks:
        when = parse_when(t.get("scheduled_at"))
        if not when or t.get("status") == "cancelled":
            continue
        u = when.astimezone(timezone.utc)
        plats = ", ".join(t.get("platforms") or [])
        desc = f"Status: {t['status']}\nPlatforms: {plats}\n\n{t.get('content') or t.get('instruction') or ''}"
        lines += ["BEGIN:VEVENT", f"UID:{t['id']}@nexus", f"DTSTAMP:{stamp}",
                  f"DTSTART:{u.strftime('%Y%m%dT%H%M%SZ')}",
                  f"DTEND:{(u + timedelta(minutes=duration_min)).strftime('%Y%m%dT%H%M%SZ')}",
                  _fold(f"SUMMARY:{_ics_escape('[' + t['status'] + '] ' + (t.get('title') or ''))}"),
                  _fold(f"DESCRIPTION:{_ics_escape(desc)}"),
                  f"CATEGORIES:{_ics_escape(plats or 'nexus')}",
                  "STATUS:" + ("CONFIRMED" if t["status"] in ("approved", "running", "done") else "TENTATIVE"),
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Nexus task store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--status")
    for c in ("approve", "cancel", "show"):
        sub.add_parser(c).add_argument("id")
    sub.add_parser("ics")
    a = ap.parse_args(argv)
    store = TaskStore()
    if a.cmd == "list":
        print(json.dumps(store.list(status=a.status), indent=2, ensure_ascii=False))
    elif a.cmd == "show":
        print(json.dumps(store.get(a.id), indent=2, ensure_ascii=False))
    elif a.cmd == "approve":
        print(json.dumps(store.approve(a.id, by="owner (cli)"), indent=2, ensure_ascii=False))
    elif a.cmd == "cancel":
        print(json.dumps(store.cancel(a.id, by="owner (cli)"), indent=2, ensure_ascii=False))
    elif a.cmd == "ics":
        sys.stdout.write(to_ics(store.list()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
