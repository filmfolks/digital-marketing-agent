"""Optional client build configuration (config/client.json).

The main Nexus app ships WITHOUT config/client.json: every value then falls
back to DEFAULTS and brand data comes from data/brands/<active brand>/.
A client build drops in config/client.json to hard-wire one client. Every component reads the
client from here so the brand, ports and browser-profile folder cannot drift:

  * relay       default port, brand lock, /client endpoint, seeds the brand
  * browser     persistent profiles under <this folder>/logs/profiles
  * generator   default brand when no brand is active
  * UI          ui/client-config.js (kept identical by a test)

Precedence for the port: --port flag > NEXUS_RELAY_PORT > client.json > 8712.
The profiles folder is deliberately NOT overridable by NEXUS_PROFILES_DIR in
a client build: a value exported for the main app in your shell would
otherwise make both apps share (and fight over) one Chrome profile.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "client.json"


def config_path() -> Path:
    """Read at call time so tests (and a second client) can point elsewhere."""
    return Path(os.environ.get("NEXUS_CLIENT_CONFIG") or DEFAULT_CONFIG_PATH)

DEFAULTS = {
    "client_id": "", "business_name": "", "app_title": "Nexus", "website": "", "gbp_url": "",
    "handles": {}, "x_handle": "", "location": "", "target_market": "", "lock_brand": False,
    "relay_port": 8712, "cdp_port": 9222, "cdp_url": "", "profiles_dir": "",
}


def load_client(path: Path | None = None) -> dict:
    """Client config merged over defaults. Missing/invalid file → defaults (main app behaviour)."""
    p = Path(path or config_path())
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    cfg = {**DEFAULTS, **{k: v for k, v in data.items() if v is not None}}
    cfg["handles"] = {str(k): str(v).lstrip("@") for k, v in (cfg.get("handles") or {}).items()}
    return cfg


def profiles_dir(default: Path) -> Path:
    cfg = load_client()
    if cfg.get("profiles_dir"):
        p = Path(cfg["profiles_dir"])
        return p if p.is_absolute() else ROOT / p
    return Path(os.environ.get("NEXUS_PROFILES_DIR") or default)


def relay_port() -> int:
    env = os.environ.get("NEXUS_RELAY_PORT", "").strip()
    if env.isdigit():
        return int(env)
    return int(load_client().get("relay_port") or 8712)


def cdp_port() -> int:
    env = os.environ.get("NEXUS_CDP_PORT", "").strip()
    return int(env) if env.isdigit() else int(load_client().get("cdp_port") or 9222)


def public_view(cfg: dict | None = None) -> dict:
    """What the UI may see (no secrets live in client.json, but keep it explicit)."""
    cfg = cfg or load_client()
    keys = ("client_id", "business_name", "app_title", "website", "handles", "x_handle", "lock_brand", "relay_port")
    return {k: cfg.get(k) for k in keys}


def ensure_client_brand(store) -> str | None:
    """Create the client's brand if missing and make it active. Returns its id."""
    cfg = load_client()
    bid = cfg.get("client_id")
    if not bid:
        return None
    if not store.read(bid, "brand"):
        store.create({"id": bid, "business_name": cfg["business_name"], "website": cfg["website"], "gbp_url": cfg["gbp_url"],
                      "handles": cfg["handles"], "location": cfg["location"], "target_market": cfg["target_market"]})
    if cfg.get("lock_brand") or not store.active():
        store.set_active(bid)
    return bid


def brands_dir() -> Path:
    return Path(os.environ.get("NEXUS_BRANDS_DIR") or ROOT / "data" / "brands")


def active_brand_id() -> str:
    """client_id of a client build, else the active brand of the multi-brand app ('' if none)."""
    cfg = load_client()
    if cfg.get("client_id"):
        return str(cfg["client_id"])
    try:
        bid = (brands_dir() / "active.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return bid if bid and (brands_dir() / bid / "brand.json").is_file() else ""


def _brand_record(bid: str) -> dict:
    try:
        return json.loads((brands_dir() / bid / "brand.json").read_text(encoding="utf-8")) if bid else {}
    except (OSError, ValueError):
        return {}


PROFILE_URLS = {"x": "https://x.com/{}", "twitter": "https://x.com/{}",
                "instagram": "https://www.instagram.com/{}/", "facebook": "https://www.facebook.com/{}"}


def social_links() -> dict:
    """Curated social links: config/client.json social_links, else the active
    brand's brand.json "social_links", else derived from its handles."""
    cfg = load_client()
    if cfg.get("social_links"):
        return dict(cfg["social_links"])
    brand = _brand_record(active_brand_id())
    if isinstance(brand.get("social_links"), dict) and brand["social_links"]:
        return dict(brand["social_links"])
    out: dict = {}
    for plat, handle in (brand.get("handles") or {}).items():
        plat = "x" if plat == "twitter" else plat
        h = str(handle or "").strip().lstrip("@")
        if not h or plat not in PROFILE_URLS:
            continue
        url = PROFILE_URLS[plat].format(h)
        out.setdefault(plat, {"handle": "@" + h, ("page" if plat == "facebook" else "profile"): url})
    return out
