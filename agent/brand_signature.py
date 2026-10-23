#!/usr/bin/env python3
"""Brand contacts & post signature.

One place that answers two questions for the rest of Nexus:

  1. What are the active brand's public details?  (business name, website,
     mobile / WhatsApp, email, logo, social links)
  2. What signature must ride along on every published post so a reader can
     actually reach the business?  (phone, website, social links)

It is deliberately stdlib-only and has no import-time side effects, because the
publisher (`agent/publish.py`) and the relay both import it as a subprocess, and
a broken import must never stop a post.

Storage is the brand record the Brand Setup page already writes:
``data/brands/<id>/brand.json`` (override the folder with NEXUS_BRANDS_DIR).
The logo is stored next to it as ``logo.png`` (see :func:`save_logo`).

Signature rules
---------------
* ``brand["signature"]["enabled"]`` (default True) turns it off.
* ``brand["signature"]["custom"]`` replaces the generated block verbatim.
* Otherwise a compact block is built from name / phone / website / social links,
  trimmed per platform so a 280-char tweet is never silently cut in half.
* :func:`apply_signature` is idempotent: if the block is already in the text it
  is not added twice.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Emoji used only when the brand has not opted out of them.
PHONE_ICON = "\U0001F4DE"          # 📞
SITE_ICON = "\U0001F310"           # 🌐

# Platform display order for the social line.
SOCIAL_ORDER = ("instagram", "facebook", "x", "twitter", "linkedin", "youtube", "tiktok", "whatsapp")
SOCIAL_LABEL = {"instagram": "IG", "facebook": "FB", "x": "X", "twitter": "X", "linkedin": "in",
                "youtube": "YT", "tiktok": "TikTok", "whatsapp": "WA"}
PROFILE_URLS = {"x": "https://x.com/{h}", "twitter": "https://x.com/{h}",
                "instagram": "https://www.instagram.com/{h}/", "facebook": "https://www.facebook.com/{h}",
                "linkedin": "https://www.linkedin.com/{h}", "youtube": "https://www.youtube.com/@{h}",
                "tiktok": "https://www.tiktok.com/@{h}"}

# How many characters the signature may add, per platform (None = unlimited).
# X is a hard 280; the others are soft. Instagram caps a caption at 2,200.
PLATFORM_LIMIT = {"x": 280, "twitter": 280, "instagram": 2200, "linkedin": 3000, "facebook": 63206}
# Which parts to include by default, per platform.
DEFAULT_INCLUDE = {
    "x": ("name", "website"),
    "twitter": ("name", "website"),
    "google_business": ("name", "phone", "website"),
}
FULL_INCLUDE = ("name", "phone", "email", "website", "social")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE_KEEP = re.compile(r"[^\d+]")


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────
def brands_dir() -> Path:
    return Path(os.environ.get("NEXUS_BRANDS_DIR") or (ROOT / "data" / "brands"))


def active_brand_id() -> str:
    """client_id of a client build, else the active brand ('' when none)."""
    try:
        import client_config
        return str(client_config.active_brand_id() or "")
    except Exception:                                   # noqa: BLE001 — never fatal
        pass
    try:
        bid = (brands_dir() / "active.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return bid if bid and (brands_dir() / bid / "brand.json").is_file() else ""


def load_brand(brand_id: str | None = None) -> dict:
    """The brand record, or {} — callers must treat every field as optional."""
    bid = brand_id or active_brand_id()
    if not bid:
        return {}
    try:
        data = json.loads((brands_dir() / bid / "brand.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation (shared by BrandStore so the UI can never store junk)
# ─────────────────────────────────────────────────────────────────────────────
def clean_phone(value) -> str:
    """Keep a human-typable phone number: digits, spaces, +, -, (), /."""
    s = str(value or "").strip()
    if not s:
        return ""
    s = re.sub(r"[()/.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


def wa_number(value) -> str:
    """Digits only, with an optional leading '+', for wa.me links."""
    s = _PHONE_KEEP.sub("", str(value or ""))
    return s[:20]


def clean_email(value) -> str:
    s = str(value or "").strip().lower()
    # tolerate "Name <mail@x.com>" and "mailto:" prefixes
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", s)
    return m.group(0) if m else (s if EMAIL_RE.match(s) else "")


def clean_social_links(raw) -> dict:
    """{platform: url} with https URLs, de-duplicated and ordered."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        plat = str(k or "").strip().lower()
        plat = {"twitter": "x"}.get(plat, plat)
        if plat not in SOCIAL_ORDER:
            continue
        if isinstance(v, dict):                     # tolerate {profile: url}
            v = v.get("url") or v.get("profile") or v.get("page") or v.get("handle") or ""
        s = str(v or "").strip()
        if not s:
            continue
        if not re.match(r"^https?://", s, re.I):
            if s.startswith("@"):
                s = PROFILE_URLS.get(plat, "https://{h}").format(h=s.lstrip("@"))
            else:
                s = "https://" + s.lstrip("/")
        out.setdefault(plat, s)
    return {k: out[k] for k in SOCIAL_ORDER if k in out}


def normalise_contacts(inputs: dict, existing: dict | None = None) -> dict:
    """Return the contact-shaped subset of a brand form submission.

    Only keys present in ``inputs`` are taken, so a partial save (e.g. the logo
    endpoint) never wipes the rest. Pass an empty string to clear a field.
    """
    inputs = inputs if isinstance(inputs, dict) else {}
    out: dict = {}

    def take(key, fn):
        if key in inputs:
            out[key] = fn(inputs.get(key))

    take("mobile", clean_phone)
    take("whatsapp", clean_phone)
    take("email", clean_email)
    if "emails" in inputs:
        raw = inputs.get("emails")
        vals = raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or ""))
        out["emails"] = list(dict.fromkeys(e for e in (clean_email(x) for x in vals) if e))[:10]
    if "social_links" in inputs:
        out["social_links"] = clean_social_links(inputs.get("social_links"))
    if "logo" in inputs:
        out["logo"] = str(inputs.get("logo") or "").strip()[:500]
    # Signature settings: a shallow merge so a partial toggle keeps the rest.
    if "signature" in inputs:
        sig = inputs.get("signature")
        if isinstance(sig, dict):
            merged = {**(((existing or {}).get("signature")) or {})}
            merged.update({k: v for k, v in sig.items() if v is not None})
            out["signature"] = merged
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Signature building
# ─────────────────────────────────────────────────────────────────────────────
def _display_phone(brand: dict) -> str:
    return clean_phone(brand.get("mobile") or brand.get("whatsapp"))


def _website(brand: dict) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(brand.get("website") or "").strip()).rstrip("/")


def _short_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "").strip()).rstrip("/")


def _social_items(brand: dict) -> list:
    """[(label, display)] from social_links, falling back to handles."""
    links = clean_social_links(brand.get("social_links"))
    items = []
    for plat in SOCIAL_ORDER:
        if plat == "twitter":
            continue
        url = links.get(plat)
        if url:
            items.append((SOCIAL_LABEL.get(plat, plat), _short_url(url)))
            continue
        handle = (brand.get("handles") or {}).get(plat)
        if handle:
            items.append((SOCIAL_LABEL.get(plat, plat), "@" + str(handle).lstrip("@")))
    return items


def _include_for(brand: dict, platform: str | None) -> tuple:
    sig = brand.get("signature") if isinstance(brand.get("signature"), dict) else {}
    inc = sig.get("include")
    if isinstance(inc, str):
        inc = [x.strip() for x in inc.split(",") if x.strip()]
    if isinstance(inc, (list, tuple)) and inc:
        return tuple(x for x in inc if x in FULL_INCLUDE)
    plat = (platform or "").lower()
    return DEFAULT_INCLUDE.get(plat, FULL_INCLUDE)


def _use_emoji(brand: dict) -> bool:
    sig = brand.get("signature") if isinstance(brand.get("signature"), dict) else {}
    if "emoji" in sig:
        return bool(sig["emoji"])
    policy = ((brand.get("brand_voice") or {}).get("emoji_policy") if isinstance(brand.get("brand_voice"), dict) else "")
    return str(policy or "light").lower() != "none"


def signature_lines(brand: dict, platform: str | None = None) -> list:
    """The signature as a list of lines ([] when disabled / nothing to show)."""
    if not brand:
        return []
    sig = brand.get("signature") if isinstance(brand.get("signature"), dict) else {}
    if sig.get("enabled") is False:
        return []
    custom = str(sig.get("custom") or "").strip()
    if custom:
        return custom.splitlines()
    inc = _include_for(brand, platform)
    single = (platform or "").lower() in ("x", "twitter")
    name = str(brand.get("business_name") or "").strip()
    phone = _display_phone(brand)
    email = clean_email(brand.get("email"))
    site = _website(brand)
    socials = _social_items(brand)
    emoji = _use_emoji(brand)

    parts: list = []
    if "name" in inc and name:
        parts.append(name)
    contact_bits = []
    if "phone" in inc and phone:
        contact_bits.append(f"{PHONE_ICON} {phone}" if emoji else f"Call: {phone}")
    if "email" in inc and email:
        contact_bits.append(email)
    if "website" in inc and site:
        contact_bits.append(f"{SITE_ICON} {site}" if emoji else site)
    if single:
        # One tidy line: "Name · +91… · site"
        parts = parts[:1] + [b for b in contact_bits if site in b or phone in b][:2]
        return [" · ".join(parts)] if parts else []
    if contact_bits:
        parts.extend(contact_bits)
    if "social" in inc and socials:
        parts.append(" · ".join(f"{label} {shown}" for label, shown in socials[:5]))
    return parts


def signature_text(brand: dict, platform: str | None = None) -> str:
    lines = signature_lines(brand, platform)
    return "\n".join(lines).strip()


def apply_signature(content: str, platform: str | None = None, brand: dict | None = None,
                    max_len: int | None = None) -> str:
    """Append the brand signature to ``content`` for one platform.

    Idempotent, and never grows the text past ``max_len`` (trimming the *content*,
    at a word boundary, rather than the signature).
    """
    text = (content or "").rstrip()
    brand = brand if brand is not None else load_brand()
    sig = signature_text(brand, platform)
    if not sig:
        return content or ""
    if sig in text:
        return text
    multi = "\n" in sig
    marker = "\n\n" if multi else " "
    if not text:
        return sig
    if max_len is None:
        max_len = PLATFORM_LIMIT.get((platform or "").lower())
    candidate = f"{text}{marker}{sig}"
    if max_len and len(candidate) > max_len:
        budget = max_len - len(marker) - len(sig)
        if budget < 40:                              # signature alone barely fits
            return text
        body = text[:budget].rstrip(" ,;:-\n")
        if len(text) > budget:
            body = body.rsplit(" ", 1)[0].rstrip(" ,;:-\n")
        if not body:
            return text
        candidate = f"{body}{marker}{sig}"
    return candidate


def preview(brand: dict, platform: str = "instagram") -> str:
    """A representative caption ending, for the UI's signature preview."""
    sample = "Your post caption goes here."
    return apply_signature(sample, platform, brand)


# ─────────────────────────────────────────────────────────────────────────────
# Logo upload
# ─────────────────────────────────────────────────────────────────────────────
LOGO_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
              (b"RIFF", ".webp"), (b"GIF8", ".gif"))
MAX_LOGO_BYTES = 2_000_000


def detect_image(raw: bytes) -> str:
    for magic, ext in LOGO_MAGIC:
        if raw.startswith(magic):
            if ext == ".webp" and raw[8:12] != b"WEBP":
                continue
            return ext
    return ""


def save_logo(brand_id: str, data_url: str) -> dict:
    """Decode a data URL (or base64 blob) into ``data/brands/<id>/logo.png``.

    Returns {"ok", "logo", "bytes", "error"}. Never raises.
    """
    bid = str(brand_id or "").strip()
    if not bid or re.sub(r"[^a-z0-9_-]", "", bid.lower()) != bid.lower():
        return {"ok": False, "error": "invalid brand id"}
    payload = str(data_url or "")
    m = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.*)$", payload, re.S)
    if m:
        payload = m.group(1)
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
    except (ValueError, TypeError):                    # binascii.Error subclasses ValueError
        return {"ok": False, "error": "not valid base64 image data"}
    if not raw:
        return {"ok": False, "error": "empty image"}
    if len(raw) > MAX_LOGO_BYTES:
        return {"ok": False, "error": f"logo larger than {MAX_LOGO_BYTES // 1_000_000} MB"}
    ext = detect_image(raw)
    if not ext:
        return {"ok": False, "error": "unsupported image type (use PNG, JPG, GIF or WEBP)"}
    d = brands_dir() / bid
    try:
        d.mkdir(parents=True, exist_ok=True)
        target = d / "logo.png"
        tmp = d / ".logo.png.tmp"
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    except OSError as e:
        return {"ok": False, "error": f"could not save logo: {e}"}
    return {"ok": True, "logo": str(target), "bytes": len(raw), "format": ext}


def remove_logo(brand_id: str) -> dict:
    d = brands_dir() / str(brand_id or "")
    removed = []
    for name in ("logo.png", "logo.jpg", "logo.webp"):
        p = d / name
        try:
            if p.is_file():
                p.unlink()
                removed.append(name)
        except OSError:
            pass
    return {"ok": True, "removed": removed}


def logo_for(brand_id: str) -> str:
    """Path to the saved logo for a brand, or '' — used by the media renderer."""
    d = brands_dir() / str(brand_id or "")
    for name in ("logo.png", "logo.jpg", "logo.webp"):
        if (d / name).is_file():
            return str(d / name)
    return ""


if __name__ == "__main__":                              # tiny manual check
    b = load_brand()
    print(json.dumps({"brand_id": active_brand_id(), "contacts": {
        k: b.get(k) for k in ("business_name", "website", "mobile", "whatsapp", "email",
                              "social_links", "logo")}, "signature": signature_text(b, "instagram")},
        indent=2, ensure_ascii=False))
