#!/usr/bin/env python3
"""Sho — an external agent brain for Nexus.

When the AI provider is ``sho``, Nexus does not call an LLM. Each generation or
planning request becomes a job file the external agent picks up:

    data/brain/inbox/<id>.json    job: kind, task, brand profile, status
    data/brain/outbox/<id>.json   result written by the agent (or POST /brain/result)

The UI and CLI wait for (or poll) the result with a timeout. Relay endpoints:

    GET  /brain/jobs[?status=pending]      list jobs           (token)
    GET  /brain/jobs/<id>                  one job + result    (token)
    POST /brain/job      {kind, task}      create a job        (token)
    POST /brain/result   {id, result}      agent answers       (token REQUIRED)
    POST /brain/approve  {id}              owner approves post (token REQUIRED)

Posting is gated: a ``post`` job is created as ``pending_approval`` and nothing
publishes it until the owner approves it explicitly (``brain_bridge.py approve
<id>`` or POST /brain/approve). ``publish_approved`` refuses anything else.

CLI:
    brain_bridge.py list [--status S]
    brain_bridge.py show <id>
    brain_bridge.py answer <id> --file result.json|result.txt
    brain_bridge.py approve <id> [--publish] [--dry-run | --fallback-dry-run]
    brain_bridge.py reject <id> [--reason TEXT]
    brain_bridge.py new <kind> --task '{"topic": "..."}'      (handy for testing)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("generate", "profile", "plan", "post", "chat")
STATUSES = ("pending", "answered", "pending_approval", "approved", "rejected", "published", "failed", "expired")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
DEFAULT_TIMEOUT = int(os.environ.get("NEXUS_BRAIN_TIMEOUT", "600"))


def brain_dir() -> Path:
    return Path(os.environ.get("NEXUS_BRAIN_DIR") or (ROOT / "data" / "brain"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not ID_RE.match(job_id):
        raise ValueError(f"invalid job id: {job_id!r}")
    return job_id


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)                      # atomic: the agent never reads half a file


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def inbox(job_id: str) -> Path:
    return brain_dir() / "inbox" / f"{_check_id(job_id)}.json"


def outbox(job_id: str) -> Path:
    return brain_dir() / "outbox" / f"{_check_id(job_id)}.json"


def active_brand() -> tuple[str | None, dict | None]:
    """(brand_id, profile) of the active brand, falling back to the client build's brand."""
    try:
        sys.path.insert(0, str(ROOT / "agent"))
        import brand_research as br  # noqa: WPS433
        store = br.BrandStore()
        bid = store.active()
        if not bid:
            try:
                import client_config
                bid = client_config.load_client().get("client_id") or None
            except ImportError:
                bid = None
        return bid, (store.read(bid, "profile") if bid else None)
    except Exception:
        return None, None


# =============================================================================
# Jobs
# =============================================================================
def create_job(kind: str, task: dict, *, brand_id: str | None = None, brand_profile: dict | None = None,
               timeout_s: int | None = None, source: str = "cli") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not isinstance(task, dict) or not task:
        raise ValueError("task must be a non-empty object")
    if brand_profile is None and brand_id is None:
        brand_id, brand_profile = active_brand()
    job_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{kind}-{secrets.token_hex(3)}"
    job = {
        "id": job_id, "kind": kind,
        # Posts are never published without an explicit approval.
        "status": "pending",
        "approval_required": kind == "post",
        "approved": False,
        "task": task,
        "brand_id": brand_id,
        "brand_profile": brand_profile,
        "instructions": INSTRUCTIONS.get(kind, ""),
        "result_path": str(outbox(job_id)),
        "created_at": now_iso(),
        "timeout_s": int(timeout_s or DEFAULT_TIMEOUT),
        "source": source,
        "history": [{"at": now_iso(), "event": "created", "by": source}],
    }
    _write(inbox(job_id), job)
    return job


INSTRUCTIONS = {
    "generate": "Write the post(s) for task.platforms about task.topic, on-brand per brand_profile. "
                "Result: {\"content\": {\"<platform>\": \"text\"}} or plain text for a single platform.",
    "profile": "Return the Brand Profile JSON described in task.schema_hint (task.system/task.user hold the full prompt).",
    "plan": "Return the 30-day plan JSON described in task.schema_hint (task.system/task.user hold the full prompt).",
    "post": "Draft (or finalise) the post in task. Result: {\"platform\": \"x\", \"text\": \"...\", \"media\": \"optional path\"}. "
            "The owner must approve before anything is published.",
    "chat": "Answer task.message. Result: {\"text\": \"...\"}.",
}


def get_job(job_id: str) -> dict:
    job = _read(inbox(job_id))
    if not job:
        raise ValueError(f"unknown job {job_id}")
    res = _read(outbox(job_id))
    if res is not None and job["status"] == "pending":
        # The agent dropped a file straight into the outbox: fold it in.
        job = _record_result(job, res.get("result", res), by=res.get("by", "agent (outbox file)"))
    return job


def list_jobs(status: str | None = None) -> list:
    d = brain_dir() / "inbox"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            job = get_job(p.stem)
        except ValueError:
            continue
        if status and job["status"] != status:
            continue
        out.append({k: job.get(k) for k in ("id", "kind", "status", "approved", "brand_id", "created_at", "answered_at")}
                   | {"summary": _summary(job)})
    return out


def _summary(job: dict) -> str:
    t = job.get("task") or {}
    return str(t.get("topic") or t.get("text") or t.get("message") or t.get("title") or job["kind"])[:100]


def _record_result(job: dict, result, *, by: str) -> dict:
    if job["status"] not in ("pending",):
        raise ValueError(f"job {job['id']} is {job['status']}; it already has a result")
    if result in (None, "", {}):
        raise ValueError("empty result")
    job["result"] = result
    job["answered_at"] = now_iso()
    job["status"] = "pending_approval" if job.get("approval_required") else "answered"
    job["history"].append({"at": now_iso(), "event": "answered", "by": by})
    _write(inbox(job["id"]), job)
    if not outbox(job["id"]).exists():
        _write(outbox(job["id"]), {"id": job["id"], "result": result, "by": by, "at": job["answered_at"]})
    return job


def submit_result(job_id: str, result, *, by: str = "agent") -> dict:
    job = _read(inbox(job_id))
    if not job:
        raise ValueError(f"unknown job {job_id}")
    return _record_result(job, result, by=by)


def approve(job_id: str, *, by: str = "owner") -> dict:
    job = get_job(job_id)
    if not job.get("approval_required"):
        raise ValueError(f"job {job_id} is a {job['kind']} job; only post jobs need approval")
    if job["status"] != "pending_approval":
        raise ValueError(f"job {job_id} is {job['status']}, not pending_approval")
    job.update(status="approved", approved=True, approved_at=now_iso(), approved_by=by)
    job["history"].append({"at": now_iso(), "event": "approved", "by": by})
    _write(inbox(job_id), job)
    return job


def reject(job_id: str, *, reason: str = "", by: str = "owner") -> dict:
    job = get_job(job_id)
    if job["status"] in ("published", "rejected"):
        raise ValueError(f"job {job_id} is already {job['status']}")
    job.update(status="rejected", approved=False)
    job["history"].append({"at": now_iso(), "event": "rejected", "by": by, "reason": reason})
    _write(inbox(job_id), job)
    return job


def post_payload(job: dict) -> dict:
    r = job.get("result")
    r = r if isinstance(r, dict) else {"text": str(r or "")}
    platform = (r.get("platform") or (job.get("task") or {}).get("platform") or "x").lower()
    text = r.get("text") or r.get("content") or ""
    if isinstance(text, dict):
        text = text.get(platform) or next(iter(text.values()), "")
    if not str(text).strip():
        raise ValueError("approved post has no text")
    return {"platforms": [platform], "content": str(text), "media": r.get("media") or "",
            "source": f"brain:{job['id']}"}


def publish_approved(job_id: str, *, dry_run: bool = False, fallback_dry_run: bool = False,
                     run: Callable = subprocess.run) -> dict:
    """Publish an APPROVED post job via publish.py. Anything else is refused."""
    job = get_job(job_id)
    if not (job.get("kind") == "post" and job.get("approved") is True and job["status"] == "approved"):
        raise PermissionError(f"job {job_id} is not approved for posting (status {job['status']})")
    payload = post_payload(job)
    jf = brain_dir() / "tmp" / f"{job_id}-publish.json"
    _write(jf, payload)
    args = [sys.executable, str(ROOT / "agent" / "publish.py"), "--from-json", str(jf)]
    if dry_run:
        args.append("--dry-run")
    elif fallback_dry_run:
        args.append("--fallback-dry-run")
    try:
        proc = run(args, capture_output=True, text=True, timeout=1200)
    finally:
        jf.unlink(missing_ok=True)
    try:
        res = json.loads(proc.stdout[proc.stdout.index("{"):]) if "{" in (proc.stdout or "") else {}
    except ValueError:
        res = {}
    ok = proc.returncode == 0 and res.get("status") not in ("failed",)
    real = not dry_run and not fallback_dry_run
    job = get_job(job_id)
    job["history"].append({"at": now_iso(), "event": "publish" + ("" if real else "-dry-run"),
                           "ok": ok, "status": res.get("status")})
    if real:
        job["status"] = "published" if ok else "failed"
        job["publish_result"] = res
    _write(inbox(job_id), job)
    return {"ok": ok, "job": job_id, "dry_run": not real, "result": res or {"stdout": proc.stdout[-800:],
                                                                         "stderr": proc.stderr[-800:]}}


def wait_for_result(job_id: str, timeout_s: float | None = None, *, poll_s: float = 2.0,
                    sleep: Callable = time.sleep, clock: Callable = time.monotonic) -> dict:
    """Block until the agent answers, or mark the job expired and raise TimeoutError."""
    job = get_job(job_id)
    limit = clock() + float(timeout_s if timeout_s is not None else job.get("timeout_s", DEFAULT_TIMEOUT))
    while job["status"] == "pending":
        if clock() >= limit:
            job["status"] = "expired"
            job["history"].append({"at": now_iso(), "event": "expired"})
            _write(inbox(job_id), job)
            raise TimeoutError(f"Sho did not answer job {job_id} in time")
        sleep(poll_s)
        job = get_job(job_id)
    return job


def ask(kind: str, task: dict, *, timeout_s: float | None = None, **kw) -> dict:
    """Create a job and wait for its result (what the 'sho' provider does)."""
    wait_kw = {k: kw.pop(k) for k in ("sleep", "clock", "poll_s") if k in kw}
    job = create_job(kind, task, **kw)
    return wait_for_result(job["id"], timeout_s, **wait_kw)


def result_text(job: dict) -> str:
    r = job.get("result")
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        for k in ("text", "content", "json"):
            if isinstance(r.get(k), str):
                return r[k]
        return json.dumps(r, ensure_ascii=False)
    return str(r or "")


# =============================================================================
# CLI
# =============================================================================
def _load_answer(path: str):
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except ValueError:
        return raw.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sho external-brain bridge for Nexus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--status", choices=STATUSES)
    sh = sub.add_parser("show"); sh.add_argument("id")
    an = sub.add_parser("answer"); an.add_argument("id"); an.add_argument("--file", required=True)
    apv = sub.add_parser("approve"); apv.add_argument("id")
    apv.add_argument("--publish", action="store_true", help="publish right after approving")
    mode = apv.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate only, no network/browser")
    mode.add_argument("--fallback-dry-run", action="store_true", help="browser fallback stops before Post")
    rj = sub.add_parser("reject"); rj.add_argument("id"); rj.add_argument("--reason", default="")
    nw = sub.add_parser("new"); nw.add_argument("kind", choices=KINDS); nw.add_argument("--task", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            out = list_jobs(a.status)
        elif a.cmd == "show":
            out = get_job(a.id)
        elif a.cmd == "answer":
            out = submit_result(a.id, _load_answer(a.file), by="cli")
        elif a.cmd == "approve":
            out = approve(a.id, by="cli")
            if a.publish:
                out = publish_approved(a.id, dry_run=a.dry_run, fallback_dry_run=a.fallback_dry_run)
        elif a.cmd == "reject":
            out = reject(a.id, reason=a.reason, by="cli")
        else:
            out = create_job(a.kind, json.loads(a.task))
    except (ValueError, PermissionError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if not (isinstance(out, dict) and out.get("ok") is False) else 1


if __name__ == "__main__":
    sys.exit(main())
