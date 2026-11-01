#!/usr/bin/env python3
"""Automated email outreach to scraped leads (agent/lead_gen.py).

Sends a personalised email about YOUR brand's services to the business contacts
collected from Google Business, through your own SMTP server. Safe by default:
`send_campaign` only validates (dry-run) unless you explicitly pass send=True.

    python3 agent/email_outreach.py status
    python3 agent/email_outreach.py preview --campaign pune-dentists
    python3 agent/email_outreach.py send --campaign pune-dentists --send

Configuration (.env) — all optional except the SMTP host:
    NEXUS_SMTP_HOST=smtp.gmail.com        NEXUS_SMTP_PORT=587
    NEXUS_SMTP_USER=you@gmail.com         NEXUS_SMTP_PASS=app-password
    NEXUS_SMTP_TLS=starttls               # starttls | ssl | none (inferred from port)
    NEXUS_EMAIL_FROM=you@yourdomain.com   # defaults to NEXUS_SMTP_USER
    NEXUS_EMAIL_FROM_NAME=Thosho Tech
    NEXUS_EMAIL_REPLY_TO=                 # optional
    NEXUS_EMAIL_POSTAL=                   # physical address for the compliance footer
    NEXUS_EMAIL_UNSUBSCRIBE=              # opt-out URL (a mailto:/reply line is always added)
    NEXUS_EMAIL_DAILY_CAP=50              # hard cap per rolling 24h, across campaigns
    NEXUS_EMAIL_MIN_DELAY=20              # seconds between sends

Deliverability & compliance notes
  * The footer always explains how to opt out; the suppression list is honoured.
  * A daily cap and a minimum delay prevent a mailbox being blacklisted.
  * Never email bought lists; only published business addresses, lawfully.
  * Every send is written to data/leads/_outreach_log.jsonl.
"""
from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import brand_signature as bs  # noqa: E402
import lead_gen as lg  # noqa: E402

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
def _intenv(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def smtp_config() -> dict:
    host = (os.environ.get("NEXUS_SMTP_HOST") or "").strip()
    port = _intenv("NEXUS_SMTP_PORT", 0)
    tls = (os.environ.get("NEXUS_SMTP_TLS") or "").strip().lower()
    if not tls:
        tls = "ssl" if port == 465 else ("none" if port == 25 else "starttls")
    user = (os.environ.get("NEXUS_SMTP_USER") or "").strip()
    sender = (os.environ.get("NEXUS_EMAIL_FROM") or user).strip()
    cfg = {
        "host": host, "port": port or (465 if tls == "ssl" else 587), "tls": tls,
        "user": user, "password": os.environ.get("NEXUS_SMTP_PASS") or "",
        "from": sender, "from_name": (os.environ.get("NEXUS_EMAIL_FROM_NAME") or "").strip(),
        "reply_to": (os.environ.get("NEXUS_EMAIL_REPLY_TO") or "").strip(),
        "postal": (os.environ.get("NEXUS_EMAIL_POSTAL") or "").strip(),
        "unsubscribe": (os.environ.get("NEXUS_EMAIL_UNSUBSCRIBE") or "").strip(),
        "daily_cap": _intenv("NEXUS_EMAIL_DAILY_CAP", 50),
        "min_delay": _intenv("NEXUS_EMAIL_MIN_DELAY", 20),
    }
    cfg["configured"] = bool(host and sender)
    if not host:
        cfg["error"] = "NEXUS_SMTP_HOST is not set"
    elif not sender:
        cfg["error"] = "set NEXUS_EMAIL_FROM (or NEXUS_SMTP_USER)"
    return cfg


def status() -> dict:
    cfg = smtp_config()
    safe = {k: v for k, v in cfg.items() if k not in ("password",)}
    safe["password_set"] = bool(cfg.get("password"))
    safe["sent_24h"] = sent_last_24h()
    safe["suppressed"] = len(lg.load_suppression())
    return {"ok": bool(cfg.get("configured")), **safe}


# ─────────────────────────────────────────────────────────────────────────────
# Brand context for the pitch
# ─────────────────────────────────────────────────────────────────────────────
def brand_context(brand_id: str | None = None) -> dict:
    bid = brand_id or bs.active_brand_id()
    brand = bs.load_brand(bid)
    profile = {}
    try:
        profile = json.loads((bs.brands_dir() / bid / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        profile = {}
    services = []
    for s in (profile.get("services") or []):
        services.append(s.get("name") if isinstance(s, dict) else str(s))
    services = [s for s in services if s]
    summary = profile.get("summary") or profile.get("value_proposition") or ""
    return {
        "id": bid,
        "business_name": brand.get("business_name") or "",
        "website": brand.get("website") or "",
        "phone": bs.clean_phone(brand.get("mobile") or brand.get("whatsapp")),
        "email": bs.clean_email(brand.get("email")),
        "city": brand.get("location") or "",
        "services": services,
        "services_text": ", ".join(services[:6]),
        "summary": summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_SUBJECT = "Helping {business_name} get more customers in {city}"
DEFAULT_BODY = """Hi {business_name} team,

I came across {business_name} while looking at {category} businesses in {city} and was impressed.

I'm with {sender_name}{sender_tail}. We help businesses like yours grow with {services_short}\
{offer_line}

If it's useful, I'd be happy to send a short, no-obligation plan for {business_name} — just reply to this email.

Warm regards,
{sender_name}
{website}"""


def render(text: str, lead: dict, brand: dict, extra: dict | None = None) -> str:
    """Fill {placeholders}. Unknown keys are left untouched so nothing is lost."""
    site = re.sub(r"^https?://", "", brand.get("website") or "").rstrip("/")
    sender = brand.get("from_name") or brand.get("business_name") or "our team"
    services = brand.get("services") or []
    vals = {
        "business_name": lead.get("name") or "there",
        "category": (lead.get("category") or "local").strip(),
        "city": (lead.get("city") or brand.get("city") or "your area").strip(),
        "address": lead.get("address") or "",
        "website": lead.get("website") or brand.get("website") or "",
        "sender_name": sender,
        "sender_website": brand.get("website") or "",
        "phone": brand.get("phone") or "",
        "email": brand.get("email") or "",
        "brand": brand.get("business_name") or "",
        "services": ", ".join(services[:6]),
        "services_short": ", ".join(services[:3]) if services else "digital marketing",
        "summary": brand.get("summary") or "",
        "offer": brand.get("offer") or "a free audit of your online presence",
        "offer_line": "",
        "sender_tail": (f" at {site}" if site else ""),
    }
    if vals["offer"]:
        vals["offer_line"] = f"\n\nWhat I'd suggest first: {vals['offer']}."
    if extra:
        vals.update({k: v for k, v in extra.items() if v is not None})
    out = text
    for k, v in vals.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def add_footer(body: str, cfg: dict, brand: dict) -> str:
    """Append the identity + opt-out block required by anti-spam law."""
    sender = cfg.get("from_name") or brand.get("business_name") or cfg.get("from") or "us"
    lines = ["", "--"]
    if cfg.get("postal"):
        lines.append(f"{sender} · {cfg['postal']}")
    else:
        lines.append(sender)
    unsub = cfg.get("unsubscribe") or ""
    if unsub:
        lines.append(f"Don't want these emails? Unsubscribe here: {unsub}")
    else:
        lines.append("Don't want these emails? Reply with 'STOP' and we'll remove you immediately.")
    if cfg.get("reply_to") or cfg.get("from"):
        lines.append(f"This was sent to you as a publicly listed business contact.")
    return body.rstrip() + "\n" + "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Sending
# ─────────────────────────────────────────────────────────────────────────────
def outreach_log_path() -> Path:
    return lg.leads_dir() / "_outreach_log.jsonl"


def log_send(entry: dict) -> None:
    p = outreach_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now().isoformat(), **entry}, ensure_ascii=False) + "\n")


def sent_last_24h() -> int:
    cutoff = now() - timedelta(hours=24)
    try:
        n = 0
        for line in outreach_log_path().read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                ts = e.get("ts", "").replace("Z", "+00:00")
                if e.get("status") == "sent" and datetime.fromisoformat(ts) >= cutoff:
                    n += 1
            except (ValueError, TypeError):
                continue
        return n
    except OSError:
        return 0


def deliver(to: str, subject: str, body: str, cfg: dict, send_fn=None) -> dict:
    """Send one plain-text email. Returns {ok, error}. Never raises."""
    to = (to or "").strip()
    if not EMAIL_RE.match(to):
        return {"ok": False, "error": f"invalid recipient {to!r}"}
    subject = re.sub(r"[\r\n]+", " ", str(subject or "")).strip()[:200]
    if not subject:
        return {"ok": False, "error": "subject is required"}
    if not cfg.get("configured"):
        return {"ok": False, "error": cfg.get("error") or "SMTP is not configured"}
    if send_fn is not None:
        return send_fn(to, subject, body, cfg)
    msg = EmailMessage()
    from_name = cfg.get("from_name") or ""
    msg["From"] = email.utils.formataddr((from_name, cfg["from"])) if from_name else cfg["from"]
    msg["To"] = to
    if cfg.get("reply_to"):
        msg["Reply-To"] = cfg["reply_to"]
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=(cfg["from"].split("@")[-1] or None))
    msg.set_content(body)
    try:
        if cfg["tls"] == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=ctx) as s:
                if cfg.get("user"):
                    s.login(cfg["user"], cfg.get("password", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
                s.ehlo()
                if cfg["tls"] == "starttls":
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if cfg.get("user"):
                    s.login(cfg["user"], cfg.get("password", ""))
                s.send_message(msg)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True}


def send_campaign(campaign_id: str, *, subject: str = "", body: str = "", brand_id: str | None = None,
                  send: bool = False, limit: int = 0, delay: int | None = None,
                  only_with_email: bool = True, update: bool = True, send_fn=None,
                  sleep=time.sleep, cfg: dict | None = None) -> dict:
    """Email the leads in a campaign.

    ``send=False`` (default) validates and renders everything but sends nothing.
    ``limit`` caps how many are contacted this run (0 = the daily cap).
    """
    campaign = lg.load_campaign(campaign_id)
    leads = campaign.get("leads") or []
    if not leads:
        return {"ok": False, "error": f"campaign {campaign_id!r} has no leads"}
    cfg = cfg or smtp_config()
    brand = brand_context(brand_id)
    subject = subject or DEFAULT_SUBJECT
    body_tpl = body or DEFAULT_BODY
    suppressed = lg.load_suppression()
    would_send_count = len([l for l in leads if (l.get("email") or "").strip()
                            and (l.get("email") or "").lower() not in suppressed
                            and l.get("status") not in ("unsubscribed", "contacted")])

    cap = int(cfg.get("daily_cap") or 0)
    remaining_cap = max(0, cap - sent_last_24h()) if cap else None
    if remaining_cap == 0:
        return {"ok": True, "sent": False, "dry_run": not send, "sent_count": 0, "failed": 0,
                "skipped": len(leads), "reason": f"daily cap of {cap} reached — try again later",
                "daily_remaining": 0, "previews": [], "campaign": campaign["id"]}
    if remaining_cap is None:                       # no cap configured
        hard_limit = limit if limit and limit > 0 else 0
    else:
        hard_limit = min(limit, remaining_cap) if limit and limit > 0 else remaining_cap
    min_delay = cfg.get("min_delay", 20) if delay is None else delay

    sent, skipped, failed, previews = 0, 0, 0, []
    contacted = []
    for lead in leads:
        if hard_limit and sent >= hard_limit:
            break
        to = (lead.get("email") or "").strip()
        if only_with_email and not to:
            skipped += 1
            continue
        if not to or not EMAIL_RE.match(to):
            skipped += 1
            continue
        if to.lower() in suppressed or lead.get("status") in ("unsubscribed", "contacted"):
            skipped += 1
            continue
        s = render(subject, lead, brand)
        b = add_footer(render(body_tpl, lead, brand), cfg, brand)
        if len(previews) < 3:
            previews.append({"to": to, "subject": s, "body": b})
        if not send:
            skipped += 1
            continue
        if sent:
            sleep(max(0, min_delay))
        res = deliver(to, s, b, cfg, send_fn=send_fn)
        entry = {"campaign": campaign["id"], "email": to, "subject": s,
                 "status": "sent" if res.get("ok") else "failed", "error": res.get("error", "")}
        log_send(entry)
        if res.get("ok"):
            sent += 1
            contacted.append(to)
            if update:
                lead["status"] = "contacted"
                lead["last_contacted_at"] = now().isoformat()
        else:
            failed += 1
            if update:
                lead["status"] = "failed" if lead.get("status") != "contacted" else lead["status"]
    if update and send:
        lg.save_campaign(campaign)
    return {"ok": True, "sent": True if send else False, "dry_run": not send,
            "would_send": would_send_count,
            "sent_count": sent, "failed": failed, "skipped": skipped,
            "daily_remaining": max(0, int(cfg.get("daily_cap", 50)) - sent_last_24h()) if cfg.get("daily_cap") else None,
            "previews": previews, "campaign": campaign["id"]}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    lg.br.load_env()
    ap = argparse.ArgumentParser(description="Nexus email outreach")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("preview"); p.add_argument("--campaign", required=True); p.add_argument("--limit", type=int, default=3)
    s = sub.add_parser("send")
    s.add_argument("--campaign", required=True)
    s.add_argument("--send", action="store_true", help="actually send (omit for a safe dry-run)")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--subject", default="")
    s.add_argument("--body", default="")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        out = status()
    elif a.cmd == "preview":
        out = send_campaign(a.campaign, send=False, limit=a.limit)
    else:
        out = send_campaign(a.campaign, send=a.send, limit=a.limit, subject=a.subject, body=a.body)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
