#!/usr/bin/env python3
"""Persistent AI-agent activity log.

Every agent action (each social post above all) is appended as one JSON line
to data/brands/<brand>/activity_log.jsonl (override: NEXUS_ACTIVITY_LOG):

    {"id", "ts" (ISO 8601 with tz), "agent", "brain", "action", "platform",
     "status" (ok | failed | blocked | dry_run), "content", "media": [...],
     "permalink", "screenshot", "error", "source", "approx_time", "note"}

The file is append-only: entries are never rewritten. Writing never raises,
so a broken disk cannot fail a post. The relay serves it at GET /activity
and the app shows it on the "Activity" page.

CLI:
    python agent/activity_log.py list [--platform x] [--status ok] [--limit 20]
    python agent/activity_log.py backfill            # idempotent
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ("ok", "failed", "blocked", "dry_run")
IST = timezone(timedelta(hours=5, minutes=30))
CONTENT_MAX = 10000

# social_browser_agent command → (platform, action)
COMMANDS = {
    "x_post": ("x", "post"), "ig_post": ("instagram", "post"), "fb_post": ("facebook", "post"),
    "instagram_post": ("instagram", "post"), "facebook_post": ("facebook", "post"),
    "blogger_post": ("blogger", "post"), "canva_to_instagram": ("instagram", "post"),
    "canva_to_x": ("x", "post"), "canva_create": ("canva", "design"), "canva_export": ("canva", "export"),
    "generic_post": ("web", "post"), "login": ("", "login"), "check_logins": ("", "check_logins"),
    "status": ("", "status"),
}


def _client() -> dict:
    try:
        import client_config
        return client_config.load_client()
    except Exception:                                   # noqa: BLE001
        return {}


def brand_id() -> str:
    try:
        import client_config
        active = client_config.active_brand_id()
    except Exception:                                   # noqa: BLE001
        active = ""
    b = os.environ.get("NEXUS_BRAND") or active or "default"
    return re.sub(r"[^a-z0-9_-]", "", b.lower()) or "default"


def default_brain() -> str:
    return os.environ.get("NEXUS_BRAIN") or _client().get("brain") or "Nexus"


def log_path() -> Path:
    env = os.environ.get("NEXUS_ACTIVITY_LOG")
    if env:
        return Path(env)
    base = Path(os.environ.get("NEXUS_BRANDS_DIR") or ROOT / "data" / "brands")
    return base / brand_id() / "activity_log.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _norm_status(status: str, dry_run: bool = False) -> str:
    s = (status or "").lower()
    if s in ("error", "fail", "failed"):
        return "failed"
    if s == "blocked":
        return "blocked"
    if s == "ok":
        return "dry_run" if dry_run else "ok"
    return s if s in STATUSES else "failed"


def make_entry(*, action: str, status: str, platform: str = "", content: str = "", media=None,
               permalink: str | None = None, screenshot: str | None = None, error: str | None = None,
               agent: str = "nexus", brain: str | None = None, ts: str | None = None, **extra) -> dict:
    entry = {
        "id": extra.pop("id", None) or uuid.uuid4().hex[:16],
        "ts": ts or now_iso(),
        "agent": agent,
        "brain": brain or default_brain(),
        "action": action,
        "platform": platform or "",
        "status": status if status in STATUSES else _norm_status(status),
        "content": (content or "")[:CONTENT_MAX],
        "media": [str(m) for m in (media or []) if m],
        "permalink": permalink or None,
        "screenshot": screenshot or None,
        "error": (error or None) and str(error)[:1000],
    }
    entry.update({k: v for k, v in extra.items() if v is not None})
    return entry


def append(entry: dict) -> dict | None:
    """Append one entry. Never raises."""
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry
    except Exception as e:                             # noqa: BLE001
        print(f"[activity] could not write activity log: {e}", file=sys.stderr)
        return None


def record(**fields) -> dict | None:
    try:
        return append(make_entry(**fields))
    except Exception as e:                             # noqa: BLE001
        print(f"[activity] could not record: {e}", file=sys.stderr)
        return None


def _detail_json(detail: str) -> dict:
    try:
        d = json.loads(detail or "")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _read_text_file(path: str) -> str:
    try:
        p = Path(path)
        p = p if p.is_absolute() else ROOT / p
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def from_audit(audit: dict) -> dict:
    """Map one logs/browser_audit.jsonl entry to an activity entry (not written)."""
    args = audit.get("args") or {}
    cmd = audit.get("command") or args.get("command") or "unknown"
    plat, action = COMMANDS.get(cmd, ("", cmd))
    plat = audit.get("platform") or plat or args.get("platform") or ""
    dry = bool(audit.get("dry_run", args.get("dry_run", False)))
    status = _norm_status(audit.get("status", ""), dry)
    det = _detail_json(audit.get("detail", ""))
    content = (args.get("text") or args.get("caption") or args.get("message") or args.get("body") or "")
    if not content:
        content = _read_text_file(args.get("text_file") or args.get("caption_file") or "") if (
            args.get("text_file") or args.get("caption_file")) else ""
    media = list(args.get("media") or []) or [m for m in (args.get("image"),) if m]
    error = None if status in ("ok", "dry_run") else (audit.get("detail") or audit.get("status"))
    return make_entry(
        ts=audit.get("ts"), agent=audit.get("agent") or "social_browser_agent",
        brain=audit.get("brain"), action=action if cmd in COMMANDS else cmd, platform=plat,
        status=status, content=content, media=media,
        permalink=audit.get("permalink") or det.get("permalink"),
        screenshot=audit.get("screenshot") or det.get("screenshot"), error=error,
        command=cmd, source="browser_audit",
        id="audit-" + hashlib.sha1(f"{audit.get('ts')}|{cmd}".encode()).hexdigest()[:12])


def record_audit(audit: dict) -> dict | None:
    """Hook for social_browser_agent.audit(): every command finish lands here."""
    try:
        return append(from_audit(audit))
    except Exception as e:                             # noqa: BLE001
        print(f"[activity] could not record audit entry: {e}", file=sys.stderr)
        return None


def read_all() -> list:
    out = []
    try:
        with log_path().open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except OSError:
        pass
    return out


def _ts_key(e: dict) -> float:
    try:
        return datetime.fromisoformat(str(e.get("ts"))).timestamp()
    except ValueError:
        return 0.0


def query(platform: str = "", status: str = "", action: str = "", limit: int = 200) -> dict:
    items = read_all()
    counts = {"platform": {}, "status": {}}
    for e in items:
        counts["platform"][e.get("platform") or "-"] = counts["platform"].get(e.get("platform") or "-", 0) + 1
        counts["status"][e.get("status") or "-"] = counts["status"].get(e.get("status") or "-", 0) + 1
    if platform:
        items = [e for e in items if (e.get("platform") or "") == platform]
    if status:
        items = [e for e in items if e.get("status") == status]
    if action:
        items = [e for e in items if e.get("action") == action]
    items.sort(key=_ts_key, reverse=True)
    total = len(items)
    return {"ok": True, "brand": brand_id(), "path": str(log_path()), "total": total,
            "counts": counts, "entries": items[:max(1, min(int(limit or 200), 2000))]}


# ── backfill ────────────────────────────────────────────────────────────────
# Real outcomes the audit log could not know (permalinks, deletions) are
# brand data, not code. They live in an optional JSON file:
#   data/brands/<brand>/activity_backfill.json   (override: NEXUS_ACTIVITY_BACKFILL)
#   {"drafts_csv": "path/to/x_drafts.csv",            # optional, draft_id,text columns
#    "known_posts": {"<audit ts>": {"permalink", "label", "note", "draft"}},
#    "extra_events": [{"id", "ts", "action", "platform", "status", ...}]}
def backfill_path() -> Path:
    env = os.environ.get("NEXUS_ACTIVITY_BACKFILL")
    if env:
        return Path(env)
    base = Path(os.environ.get("NEXUS_BRANDS_DIR") or ROOT / "data" / "brands")
    return base / (brand_id() or "default") / "activity_backfill.json"


def load_backfill(path: Path | None = None) -> dict:
    try:
        data = json.loads(Path(path or backfill_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"drafts_csv": data.get("drafts_csv") or "", "known_posts": dict(data.get("known_posts") or {}),
            "extra_events": list(data.get("extra_events") or [])}


def _x_draft(draft_id: str, csv_path: str = "") -> str:
    if not csv_path:
        return ""
    p = Path(csv_path)
    p = p if p.is_absolute() else ROOT / p
    try:
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("draft_id") == draft_id:
                    return (row.get("text") or "").strip()
    except OSError:
        pass
    return ""


def _fix_media(paths: list) -> list:
    """Audit media paths that were moved later (posters/ folder) → current path."""
    out = []
    for m in paths:
        p = ROOT / m
        alt = Path(m).parent / "posters" / Path(m).name
        out.append(str(alt) if not p.exists() and (ROOT / alt).exists() else m)
    return out


def backfill(audit_path: Path | None = None, extra_path: Path | None = None) -> list:
    """Idempotently add audit history + known posts. Returns the new entries."""
    bf = load_backfill(extra_path)
    KNOWN_POSTS, EXTRA_EVENTS = bf["known_posts"], bf["extra_events"]
    audit_path = audit_path or Path(os.environ.get("NEXUS_AUDIT_LOG") or ROOT / "logs" / "browser_audit.jsonl")
    have = {e.get("id") for e in read_all()}
    added = []
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            a = json.loads(line)
        except ValueError:
            continue
        e = from_audit(a)
        e["source"] = "backfill:browser_audit"
        e["media"] = _fix_media(e["media"])
        known = KNOWN_POSTS.get(a.get("ts"))
        if known:
            if known.get("permalink"):
                e["permalink"] = known["permalink"]
            if known.get("label"):
                e["label"] = known["label"]
            if known.get("note"):
                e["note"] = known["note"]
            if known.get("draft"):
                e["draft"] = known["draft"]
                e["content"] = e["content"] or _x_draft(known["draft"], bf["drafts_csv"])
        if e["id"] in have:
            continue
        append(e)
        have.add(e["id"])
        added.append(e)
    for ev in EXTRA_EVENTS:
        if ev["id"] in have:
            continue
        e = make_entry(**dict(ev))
        append(e)
        added.append(e)
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--platform", default="")
    ls.add_argument("--status", default="")
    ls.add_argument("--limit", type=int, default=50)
    sub.add_parser("backfill")
    sub.add_parser("path")
    a = ap.parse_args(argv)
    if a.cmd == "path":
        print(log_path())
    elif a.cmd == "backfill":
        new = backfill()
        print(json.dumps({"ok": True, "added": len(new), "path": str(log_path())}))
    else:
        print(json.dumps(query(a.platform, a.status, limit=a.limit), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
