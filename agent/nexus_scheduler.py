#!/usr/bin/env python3
"""
Nexus scheduler
===============
Runs APPROVED tasks at their time through the existing publish flow
(agent/publish.py: API first, browser fallback). Lives inside the relay as a
daemon thread; safe across restarts:

* Only ``approved`` tasks whose ``scheduled_at`` <= now are considered.
* ``approved -> running`` is a compare-and-set under the store lock, so two
  ticks (or two relays) can never post the same task twice. A second guard,
  data/tasks/scheduler.lock, keeps a single scheduler per data folder.
* Missed while the relay was down? Within ``grace_minutes`` (default 120,
  NEXUS_SCHEDULER_GRACE_MIN) it still runs; older ones are NOT posted late —
  they move to ``failed`` with error "missed" so a human can reschedule.
* A task found in ``running`` at startup was interrupted mid-post. It is
  marked ``failed`` ("interrupted — check the platform before retrying")
  instead of being retried, because the post may already be live.
* NEXUS_SCHEDULER_DRY_RUN=1 (or POST /scheduler/tick {dry_run:true}) only previews:
  due tasks are checked with publish.py --dry-run and keep their status.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import nexus_tasks as nt  # noqa: E402


def _last_json(out: str) -> dict:
    """publish.py prints one (indented) JSON object last; anything before it is noise."""
    lines = out.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("{"):
            try:
                v = json.loads("\n".join(lines[i:]))
                return v if isinstance(v, dict) else {}
            except ValueError:
                continue
    return {}


def publish_runner(task: dict, *, dry_run: bool = False, run: Callable = subprocess.run) -> dict:
    """Publish one task through agent/publish.py --from-json. Returns {ok, results, permalinks}."""
    jdir = nt.data_root() / "tasks" / "tmp"
    jdir.mkdir(parents=True, exist_ok=True)
    jf = jdir / f"{task['id']}-publish.json"
    media = task.get("media") or []
    payload = {"platforms": task["platforms"], "content": task["content"], "media": media[0] if media else "",
               "source": f"task:{task['id']}"}
    variants = {p: m for p, m in (task.get("media_variants") or {}).items() if m}
    if variants:
        payload["media_by_platform"] = variants      # right size per platform (IG 4:5, X 16:9, ...)
    jf.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    args = [sys.executable, str(ROOT / "agent" / "publish.py"), "--from-json", str(jf)]
    if dry_run:
        args.append("--dry-run")
    try:
        # A scheduled post must never be posted twice: a browser "failure" can
        # happen AFTER the Post click (e.g. permalink lookup), so no automatic
        # browser/API retries here — a failed task waits for a human.
        env = {**os.environ, "NEXUS_BROWSER_RETRIES": "0", "NEXUS_API_RETRIES": "0"}
        proc = run(args, capture_output=True, text=True, timeout=1200, cwd=str(ROOT), env=env)
    finally:
        try:
            jf.unlink()
        except OSError:
            pass
    out = proc.stdout or ""
    res = _last_json(out)
    ok = proc.returncode == 0 and res.get("status") in (("validated", "published") if dry_run else ("published",))
    links = []
    rs = res.get("results") or []
    for v in (rs.values() if isinstance(rs, dict) else rs):
        if isinstance(v, dict) and (v.get("permalink") or v.get("url")):
            links.append(v.get("permalink") or v.get("url"))
    if res.get("permalink"):
        links.append(res["permalink"])
    return {"ok": ok, "dry_run": dry_run, "status": res.get("status"), "permalinks": links,
            "result": res or {"stdout": out[-800:], "stderr": (proc.stderr or "")[-800:]}}


class Scheduler:
    def __init__(self, store: Optional[nt.TaskStore] = None, *, runner: Optional[Callable[[dict], dict]] = None,
                 clock: Optional[Callable[[], datetime]] = None, grace_minutes: Optional[float] = None,
                 interval_s: float = 30.0, dry_run: Optional[bool] = None, media: Optional[Callable] = "default"):
        self.store = store or nt.TaskStore()
        self.clock = clock or (lambda: nt.now())
        self.dry_run = (os.environ.get("NEXUS_SCHEDULER_DRY_RUN", "") in ("1", "true", "yes")) if dry_run is None else dry_run
        self.runner = runner or (lambda t: publish_runner(t, dry_run=self.dry_run))
        self.grace = timedelta(minutes=float(os.environ.get("NEXUS_SCHEDULER_GRACE_MIN", "120")
                                             if grace_minutes is None else grace_minutes))
        self.interval_s = interval_s
        if media == "default":
            import nexus_media
            media = nexus_media.ensure
        self.media = media                                    # None disables (tests)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = nt.FileLock(self.store.root / "scheduler.lock", timeout=0.5, stale_s=max(120.0, interval_s * 4))
        self.last_tick: Optional[str] = None
        self._previewed: set = set()

    # ── one pass ────────────────────────────────────────────────────────────
    def recover(self) -> List[str]:
        """Called once at start: tasks stuck in running were interrupted."""
        out = []
        for t in self.store.list(status="running"):
            try:
                self.store.transition(t["id"], "failed", by="scheduler", expect="running",
                                      error="interrupted while posting (relay stopped); check the platform before retrying")
                out.append(t["id"])
            except nt.TaskError:
                pass
        return out

    def due(self) -> List[dict]:
        now = self.clock()
        return [t for t in self.store.list(status="approved")
                if t.get("scheduled_at") and nt.parse_when(t["scheduled_at"]) <= now]

    def tick(self) -> dict:
        now = self.clock()
        self.last_tick = nt.iso(now)
        ran, missed, failed = [], [], []
        for t in self.due():
            when = nt.parse_when(t["scheduled_at"])
            if now - when > self.grace:
                if self.dry_run:
                    missed.append(t["id"])
                    continue
                try:
                    self.store.transition(t["id"], "running", by="scheduler", expect="approved")
                    self.store.transition(t["id"], "failed", by="scheduler", expect="running",
                                          error=f"missed: was due {t['scheduled_at']}, more than {self.grace} ago; reschedule it")
                    missed.append(t["id"])
                except nt.TaskError:
                    pass
                continue
            if self.dry_run:
                # Preview only: validate through publish.py --dry-run, change nothing.
                if t["id"] not in self._previewed:
                    self._previewed.add(t["id"])
                    try:
                        res = self.runner(t)
                    except Exception as e:                    # noqa: BLE001
                        res = {"ok": False, "error": str(e)}
                    ran.append({"id": t["id"], "would_post": t["platforms"], "check": res.get("ok"),
                                "detail": res.get("error") or res.get("status")})
                continue
            claimed = self.store.claim(t["id"])
            if not claimed:
                continue                                      # someone else is running it
            claimed = self.ensure_media(claimed)
            try:
                res = self.runner(claimed)
            except Exception as e:                            # noqa: BLE001
                res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            res = {**res, "at": nt.iso(self.clock())}
            if res.get("ok"):
                self.store.transition(t["id"], "done", by="scheduler", expect="running", result=res)
                ran.append(t["id"])
            else:
                self.store.transition(t["id"], "failed", by="scheduler", expect="running", result=res,
                                      error=str(res.get("error") or res.get("status") or "publish failed")[:500])
                failed.append(t["id"])
        return {"at": self.last_tick, "ran": ran, "failed": failed, "missed": missed, "dry_run": self.dry_run}

    def ensure_media(self, task: dict) -> dict:
        """Right before posting: a platform that needs an image (Instagram) must have one.
        Runs the full provider chain (NEXUS_IMAGE_PROVIDERS); the offline card renderer
        is the last link, so a post never fails for missing media. Never raises."""
        if not self.media:
            return task
        try:
            import nexus_media
            if not nexus_media.missing_for(task):
                return task
            res = self.media(task["id"], store=self.store, by="scheduler", when="before_post")
            if not res.get("ok"):
                res = self.media(task["id"], store=self.store, by="scheduler", when="before_post",
                                 providers=["card"], force=True)
            return self.store.get(task["id"])
        except Exception as e:                                # noqa: BLE001 — post anyway, publish reports it
            print(f"[scheduler] media for {task.get('id')}: {e}")
            return task

    # ── background thread ──────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the background thread. If data/tasks/scheduler.lock is held (another
        live scheduler, or a relay that was killed seconds ago and left its lock),
        the thread waits for it instead of giving up: a live scheduler keeps the
        lock fresh, a dead one's lock goes stale after ``stale_s`` and is broken.
        Before this, a quick relay restart silently left the scheduler OFF."""
        self.waiting_for_lock = False
        self._thread = threading.Thread(target=self._loop, name="nexus-scheduler", daemon=True)
        self._thread.start()
        return True

    def _take_lock(self) -> bool:
        while not self._stop.is_set():
            try:
                self._lock.acquire()
                self.waiting_for_lock = False
                self._has_lock = True
                return True
            except TimeoutError:
                if not self.waiting_for_lock:
                    print("[scheduler] data/tasks/scheduler.lock is held (another scheduler, or a stale lock "
                          f"from a killed relay — broken after {int(self._lock.stale_s)}s); waiting")
                self.waiting_for_lock = True
                self._stop.wait(min(10.0, self.interval_s))
        return False

    def _loop(self) -> None:
        if not self._take_lock():
            return
        recovered = self.recover()
        if recovered:
            print(f"[scheduler] marked {len(recovered)} interrupted task(s) failed: {recovered}")
        print("[scheduler] running")
        while not self._stop.is_set():
            try:
                os.utime(str(self._lock.path))                 # heartbeat: keeps the lock from looking stale
                r = self.tick()
                if r["ran"] or r["failed"] or r["missed"]:
                    print(f"[scheduler] {json.dumps(r)}")
            except Exception as e:                            # noqa: BLE001
                print(f"[scheduler] tick error: {e}")
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if getattr(self, "_has_lock", False):
            self._lock.release()

    def status(self) -> dict:
        return {"running": bool(self._thread and self._thread.is_alive()), "last_tick": self.last_tick,
                "interval_s": self.interval_s,
                "waiting_for_lock": bool(getattr(self, "waiting_for_lock", False)), "grace_minutes": self.grace.total_seconds() / 60,
                "dry_run": self.dry_run, "timezone": nt.tz_name()}
