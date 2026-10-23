#!/usr/bin/env python3
"""Google Business lead scraping.

Builds a contactable prospect list from a Google Maps / Places search, then
enriches each prospect with the email addresses and phone numbers published on
its own website. The list is what `agent/email_outreach.py` sends to.

    python3 agent/lead_gen.py scan --query "dental clinics" --location "Pune" \
        --campaign pune-dentists --limit 20
    python3 agent/lead_gen.py list   --campaign pune-dentists
    python3 agent/lead_gen.py export --campaign pune-dentists --format csv

How prospects are found
  1. Google Places API (New) Text Search when GOOGLE_PLACES_API_KEY is set —
     paginated, returns name / address / phone / website / rating per business.
  2. Otherwise the public Google Maps results page through the shared browser
     (best-effort; Google changes that markup often). Places API is strongly
     recommended.

How emails are found
  Each prospect's website is crawled politely (robots.txt, same domain, capped
  pages, identifying UA) and emails/phones are pulled from the markup, including
  Cloudflare-obfuscated addresses. Nothing is guessed or pattern-generated.

Compliance
  This collects only publicly published business contact details. It is the
  caller's responsibility to email lawfully (consent rules, opt-out, GDPR/PECR/
  CAN-SPAM). The sending side (`email_outreach.py`) enforces an opt-out footer,
  a suppression list and a daily cap.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import brand_research as br  # noqa: E402

PLACES_FIELDS = ",".join([
    "nextPageToken", "places.id", "places.displayName", "places.formattedAddress",
    "places.primaryTypeDisplayName", "places.types", "places.rating", "places.userRatingCount",
    "places.websiteUri", "places.nationalPhoneNumber", "places.internationalPhoneNumber",
    "places.googleMapsUri", "places.businessStatus",
])
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
DEFAULT_LIMIT = int(os.environ.get("NEXUS_LEAD_LIMIT", "20"))
MAX_LIMIT = int(os.environ.get("NEXUS_LEAD_MAX_LIMIT", "200"))
ENRICH_PAGES = int(os.environ.get("NEXUS_LEAD_ENRICH_PAGES", "6"))

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Emails that are never a personal/business inbox worth mailing.
BAD_EMAIL = re.compile(
    r"^(no-?reply|noreply|do-?not-?reply|donotreply|postmaster|abuse|webmaster|mailer-daemon)@"
    r"|^support@(shopify|wix|wordpress|squarespace)\."
    r"|@(sentry\.io|wixpress\.com|example\.(com|org)|domain\.com|email\.com|yourdomain\.com)$", re.I)
# Domains unlikely to be the business itself (site builders, CDNs, socials).
SKIP_DOMAINS = ("facebook.com", "instagram.com", "google.com", "sentry.io", "wixpress.com",
                "gstatic.com", "cloudflare.com", "schema.org", "w3.org")


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────
def leads_dir() -> Path:
    return Path(os.environ.get("NEXUS_LEADS_DIR") or (ROOT / "data" / "leads"))


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:60] or "campaign"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def campaign_path(campaign_id: str) -> Path:
    return leads_dir() / f"{slugify(campaign_id)}.json"


def empty_campaign(campaign_id: str, query: str = "", location: str = "") -> dict:
    return {"id": slugify(campaign_id), "query": query, "location": location,
            "created_at": now_iso(), "updated_at": now_iso(), "count": 0, "leads": []}


def load_campaign(campaign_id: str) -> dict:
    try:
        data = json.loads(campaign_path(campaign_id).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("leads"), list):
            return data
    except (OSError, ValueError):
        pass
    return empty_campaign(campaign_id)


def save_campaign(campaign: dict) -> dict:
    campaign["count"] = len(campaign.get("leads") or [])
    campaign["updated_at"] = now_iso()
    _atomic_write(campaign_path(campaign["id"]), campaign)
    return campaign


def list_campaigns() -> list:
    d = leads_dir()
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(c, dict) and "leads" in c:
            out.append({"id": c.get("id") or f.stem, "query": c.get("query", ""),
                        "location": c.get("location", ""), "count": len(c.get("leads") or []),
                        "updated_at": c.get("updated_at")})
    return sorted(out, key=lambda c: c.get("updated_at") or "", reverse=True)


def delete_campaign(campaign_id: str) -> bool:
    try:
        campaign_path(campaign_id).unlink()
        return True
    except OSError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Suppression list (opt-outs) — shared by every campaign
# ─────────────────────────────────────────────────────────────────────────────
def suppression_path() -> Path:
    return leads_dir() / "_suppression.json"


def load_suppression() -> set:
    try:
        data = json.loads(suppression_path().read_text(encoding="utf-8"))
        return {str(e).lower() for e in (data.get("emails") or [])}
    except (OSError, ValueError):
        return set()


def suppress(email: str, reason: str = "") -> dict:
    email = str(email or "").strip().lower()
    if not email:
        return {"ok": False, "error": "email is required"}
    try:
        data = json.loads(suppression_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"emails": []}
    if email not in {e.lower() for e in data.get("emails", [])}:
        data.setdefault("emails", []).append(email)
    data.setdefault("reasons", {})[email] = reason or data.get("reasons", {}).get(email, "")
    _atomic_write(suppression_path(), data)
    return {"ok": True, "email": email, "count": len(data["emails"])}


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation / dedupe
# ─────────────────────────────────────────────────────────────────────────────
def domain_of(url: str) -> str:
    host = (br.urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def lead_id(name: str, website: str = "", phone: str = "") -> str:
    key = domain_of(website) or re.sub(r"\D", "", phone or "") or str(name or "").lower()
    return "lead-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def best_email(emails: list, domain: str = "") -> str:
    """Prefer an address on the business's own domain, then a role inbox."""
    good = [e for e in (emails or []) if isinstance(e, str) and e and not BAD_EMAIL.match(e)]
    if not good:
        return ""
    if domain:
        own = [e for e in good if e.lower().endswith("@" + domain)]
        if own:
            good = own
    pref = ("info@", "contact@", "hello@", "sales@", "enquiry@", "enquiries@", "admin@", "support@", "office@")
    return sorted(good, key=lambda e: next((i for i, p in enumerate(pref) if e.lower().startswith(p)), 99))[0]


def normalise_contact(contact: dict, website: str = "") -> dict:
    """Turn a raw place into our lead shape."""
    name = str(contact.get("name") or "").strip()
    site = str(contact.get("website") or "").strip()
    phone = br.re.sub(r"\s+", " ", str(contact.get("phone") or "").strip())
    emails = [e for e in (contact.get("emails") or []) if e]
    dom = domain_of(site)
    return {
        "id": contact.get("id") or lead_id(name, site, phone),
        "name": name,
        "category": str(contact.get("category") or "").strip(),
        "address": str(contact.get("address") or "").strip(),
        "phone": phone,
        "whatsapp": (contact.get("whatsapp") or [""])[0] if isinstance(contact.get("whatsapp"), list) else str(contact.get("whatsapp") or ""),
        "website": site,
        "domain": dom,
        "email": best_email(emails, dom),
        "emails": list(dict.fromkeys(emails))[:10],
        "maps_url": str(contact.get("maps_url") or "").strip(),
        "rating": contact.get("rating"),
        "review_count": contact.get("review_count"),
        "source": str(contact.get("source") or ""),
        "status": contact.get("status") or ("new" if (emails or phone) else "no_contact"),
        "enriched_at": contact.get("enriched_at"),
        "last_contacted_at": contact.get("last_contacted_at"),
        "error": contact.get("error", ""),
    }


def merge_lead(leads: list, lead: dict) -> None:
    """Merge by id, keeping the richer of the two (never lose a found email)."""
    for i, ex in enumerate(leads):
        if ex.get("id") == lead.get("id") or (lead.get("domain") and ex.get("domain") == lead.get("domain")):
            merged = {**ex}
            for k, v in lead.items():
                if v in (None, "", [], {}) and merged.get(k) not in (None, "", [], {}):
                    continue
                if k in ("emails",) and isinstance(v, list):
                    merged[k] = list(dict.fromkeys((ex.get("emails") or []) + v))
                elif k in ("status", "last_contacted_at") and merged.get(k) not in (None, "", "new"):
                    continue
                else:
                    merged[k] = v
            if not merged.get("email"):
                merged["email"] = best_email(merged.get("emails") or [], merged.get("domain", ""))
            leads[i] = merged
            return
    leads.append(lead)


# ─────────────────────────────────────────────────────────────────────────────
# Discovery: Places API (New) or the Maps results page
# ─────────────────────────────────────────────────────────────────────────────
def places_search(query: str, api_key: str, *, limit: int = DEFAULT_LIMIT,
                  post=None, sleep=time.sleep) -> dict:
    """Google Places API (New) Text Search, paginated. Returns {ok, leads, error}."""
    post = post or br.http_post_json
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    leads: list = []
    token = ""
    error = ""
    while len(leads) < limit:
        payload = {"textQuery": query, "maxResultCount": min(20, limit - len(leads))}
        if token:
            payload["pageToken"] = token
        status, body = post(PLACES_URL, payload,
                            {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": PLACES_FIELDS})
        if status != 200 or not isinstance(body, dict):
            error = f"HTTP {status}: {str(body)[:200]}"
            break
        for p in body.get("places", []) or []:
            leads.append(normalise_contact({
                "name": (p.get("displayName") or {}).get("text", ""),
                "category": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
                "address": p.get("formattedAddress", ""),
                "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber", ""),
                "website": p.get("websiteUri", ""),
                "maps_url": p.get("googleMapsUri", ""),
                "rating": p.get("rating"), "review_count": p.get("userRatingCount"),
                "source": "places_api",
            }))
            if len(leads) >= limit:
                break
        token = body.get("nextPageToken") or ""
        if not token:
            break
        sleep(2.0)                                   # Google asks for a short pause between pages
    if not leads and not error:
        error = "no results (try a more specific query or add a location)"
    return {"ok": bool(leads), "leads": leads, "error": error, "source": "places_api"}


def maps_browser_search(query: str, *, limit: int = DEFAULT_LIMIT, scrolls: int = 6) -> dict:
    """Best-effort scrape of the public Google Maps results grid. Places API is
    recommended; this only reads names/addresses/links visible on the page."""
    try:
        from browser_core import BrowserSession
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "leads": [], "error": f"browser unavailable: {e}", "source": "maps_browser"}
    url = "https://www.google.com/maps/search/" + br.urllib.parse.quote_plus(query)
    leads: list = []
    try:
        with BrowserSession(platform="google") as s:
            pg = s.page
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(2500)
            for label in ("Accept all", "Reject all", "I agree"):
                try:
                    b = pg.get_by_role("button", name=label).first
                    if b.count() and b.is_visible():
                        b.click()
                        pg.wait_for_timeout(2000)
                        break
                except Exception:
                    pass
            for _ in range(max(1, scrolls)):
                try:
                    pg.locator('div[role="feed"]').first.evaluate("el => el.scrollBy(0, 3000)")
                except Exception:
                    pg.mouse.wheel(0, 3000)
                pg.wait_for_timeout(900)
                cards = pg.locator('a.hfpxzc')
                n = cards.count()
                if n >= limit:
                    break
            cards = pg.locator('a.hfpxzc')
            seen = set()
            for i in range(min(cards.count(), limit * 2)):
                try:
                    el = cards.nth(i)
                    name = (el.get_attribute("aria-label") or "").strip()
                    href = (el.get_attribute("href") or "").strip()
                    if not name or href in seen:
                        continue
                    seen.add(href)
                    parent = el.locator("xpath=ancestor::div[contains(@class,'Nv2PK')][1]")
                    addr = ""
                    try:
                        addr = (parent.locator("div.W4Efsd").first.inner_text() or "").strip()
                    except Exception:
                        pass
                    leads.append(normalise_contact({"name": name, "address": addr,
                                                    "maps_url": href, "source": "maps_browser"}))
                    if len(leads) >= limit:
                        break
                except Exception:
                    continue
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "leads": leads, "error": str(e)[:200], "source": "maps_browser"}
    if not leads:
        return {"ok": False, "leads": [], "error": "no listings read (Google layout changed or bot-check)",
                "source": "maps_browser"}
    return {"ok": True, "leads": leads, "error": "", "source": "maps_browser"}


# ─────────────────────────────────────────────────────────────────────────────
# Enrichment: crawl each website for emails + phones
# ─────────────────────────────────────────────────────────────────────────────
def enrich_lead(contact: dict, *, fetch=None, render=None, max_pages: int = ENRICH_PAGES,
                sleep=time.sleep) -> dict:
    """Add emails/phones from the lead's own website. Never raises."""
    site = str(contact.get("website") or "").strip()
    if not site:
        return contact
    fetch = fetch or br.http_fetch
    try:
        crawl = br.crawl_site(site, max_pages=max_pages, fetch=fetch, render=render, sleep=sleep)
        summary = br.summarize_site(crawl)
        info = summary.get("contact") or {}
        emails = [e for e in (info.get("emails") or []) if e and not BAD_EMAIL.match(e)]
        phones = info.get("phones") or []
        dom = domain_of(site)
        contact = {**contact,
                   "emails": list(dict.fromkeys((contact.get("emails") or []) + emails)),
                   "phone": contact.get("phone") or (phones[0] if phones else ""),
                   "whatsapp": contact.get("whatsapp") or ((info.get("whatsapp") or [""])[0] if info.get("whatsapp") else ""),
                   "category": contact.get("category") or (summary.get("category") or ""),
                   "enriched_at": now_iso()}
        if not contact.get("email"):
            contact["email"] = best_email(contact["emails"], dom)
        if not contact.get("email") and not contact.get("phone"):
            contact["status"] = "no_contact"
    except Exception as e:                                   # noqa: BLE001
        contact = {**contact, "error": str(e)[:200], "enriched_at": now_iso()}
    return contact


def enrich_all(leads: list, *, fetch=None, render=None, max_pages: int = ENRICH_PAGES,
               delay: float = 1.0, sleep=time.sleep, cap: int = 0) -> list:
    """Enrich every lead that has a website but no email yet."""
    done = 0
    for lead in leads:
        if cap and done >= cap:
            break
        if not lead.get("website") or lead.get("email"):
            continue
        if done:
            sleep(delay)
        merged = enrich_lead(lead, fetch=fetch, render=render, max_pages=max_pages, sleep=sleep)
        lead.clear()
        lead.update(merged)
        done += 1
    return leads


# ─────────────────────────────────────────────────────────────────────────────
# Scan (discover + enrich + merge into a campaign)
# ─────────────────────────────────────────────────────────────────────────────
def scan(query: str, location: str = "", *, campaign: str | None = None, limit: int = DEFAULT_LIMIT,
         api_key: str | None = None, enrich: bool = True, fetch=None, post=None, browser=None,
         render=None, sleep=time.sleep, delay: float = 1.0, max_pages: int = ENRICH_PAGES) -> dict:
    """Discover prospects for ``query`` and store them in a campaign file."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    full = f"{query} {location}".strip() if location and location.lower() not in query.lower() else query
    campaign_id = slugify(campaign or full)
    key = api_key if api_key is not None else os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if key:
        found = places_search(full, key, limit=limit, post=post, sleep=sleep)
    else:
        browser = browser or maps_browser_search
        found = browser(full, limit=limit)
        found.setdefault("note", "Places API key not set — scraped the Maps page (best-effort).")
    leads = found.get("leads") or []
    if enrich and leads:
        enrich_all(leads, fetch=fetch, render=render, max_pages=max_pages, sleep=sleep, delay=delay)

    camp = load_campaign(campaign_id)
    camp["id"] = campaign_id
    camp["query"] = camp.get("query") or query
    camp["location"] = camp.get("location") or location
    camp.setdefault("created_at", now_iso())
    for lead in leads:
        merge_lead(camp["leads"], lead)
    save_campaign(camp)
    stats = campaign_stats(camp)
    return {"ok": True, "campaign": campaign_id, "source": found.get("source"),
            "found": len(leads), "error": found.get("error", ""), "note": found.get("note", ""),
            "stats": stats, "campaign_data": camp}


def campaign_stats(camp: dict) -> dict:
    leads = camp.get("leads") or []
    with_email = sum(1 for l in leads if l.get("email"))
    with_phone = sum(1 for l in leads if l.get("phone"))
    return {"total": len(leads), "with_email": with_email, "with_phone": with_phone,
            "contactable": sum(1 for l in leads if l.get("email") or l.get("phone")),
            "contacted": sum(1 for l in leads if l.get("status") == "contacted")}


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────
CSV_FIELDS = ["name", "category", "email", "phone", "whatsapp", "website", "address",
              "rating", "review_count", "status", "maps_url", "source", "last_contacted_at"]


def export_csv(camp: dict, only_with_email: bool = False) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for lead in camp.get("leads") or []:
        if only_with_email and not lead.get("email"):
            continue
        row = {k: lead.get(k, "") for k in CSV_FIELDS}
        for k, v in row.items():                     # neutralise spreadsheet formula injection
            if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                row[k] = "'" + v
        w.writerow(row)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    br.load_env()
    ap = argparse.ArgumentParser(description="Nexus Google Business lead scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--query", required=True)
    s.add_argument("--location", default="")
    s.add_argument("--campaign", default="")
    s.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    s.add_argument("--no-enrich", action="store_true")
    s.add_argument("--no-render", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--campaign", default="")
    e = sub.add_parser("export")
    e.add_argument("--campaign", required=True)
    e.add_argument("--format", choices=["csv", "json"], default="csv")
    e.add_argument("--only-email", action="store_true")
    sup = sub.add_parser("suppress"); sup.add_argument("--email", required=True); sup.add_argument("--reason", default="")
    sub.add_parser("campaigns")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "scan":
            out = scan(a.query, a.location, campaign=a.campaign, limit=a.limit, enrich=not a.no_enrich,
                       render=None if a.no_render else br.playwright_render)
        elif a.cmd == "campaigns":
            out = {"ok": True, "campaigns": list_campaigns()}
        elif a.cmd == "list":
            if a.campaign:
                c = load_campaign(a.campaign)
                out = {"ok": True, "campaign": c["id"], "stats": campaign_stats(c), "leads": c["leads"]}
            else:
                out = {"ok": True, "campaigns": list_campaigns()}
        elif a.cmd == "suppress":
            out = suppress(a.email, a.reason)
        else:
            c = load_campaign(a.campaign)
            if a.format == "csv":
                sys.stdout.write(export_csv(c, only_with_email=a.only_email))
                return 0
            out = {"ok": True, **c}
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
