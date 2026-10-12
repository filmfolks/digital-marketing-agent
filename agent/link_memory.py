#!/usr/bin/env python3
"""Link memory: every browser flow records the pages it visits per platform
(profile, compose, page, post permalinks) so later runs reuse known URLs.

Stored at data/brands/<brand>/link_memory.json (override: NEXUS_LINK_MEMORY).
Lookup order for a preferred URL: curated social_links (config/client.json in a
client build, else the active brand's brand.json social_links / handles) → most recent remembered link of that kind → the flow's default.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("profile", "compose", "page", "home", "post", "other")
MAX_PER_PLATFORM = 200

# social_links key → link kind, per platform
CONFIG_KEYS = {
    "x": {"profile": "profile", "compose": "compose"},
    "instagram": {"profile": "profile", "home": "home"},
    "facebook": {"page": "page", "your_pages": "other"},
}


def _client() -> dict:
    try:
        import client_config
        return client_config.load_client()
    except Exception:                                   # noqa: BLE001
        return {}


def _active_brand() -> str:
    try:
        import client_config
        return client_config.active_brand_id()
    except Exception:                                   # noqa: BLE001
        return ""


def _social_links() -> dict:
    try:
        import client_config
        return client_config.social_links()
    except Exception:                                   # noqa: BLE001
        return {}


def brand_id() -> str:
    b = os.environ.get("NEXUS_BRAND") or _active_brand() or "default"
    return re.sub(r"[^a-z0-9_-]", "", b.lower()) or "default"


def memory_path() -> Path:
    env = os.environ.get("NEXUS_LINK_MEMORY")
    if env:
        return Path(env)
    base = Path(os.environ.get("NEXUS_BRANDS_DIR") or ROOT / "data" / "brands")
    return base / brand_id() / "link_memory.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(url: str) -> str:
    """Canonical form for dedupe: https, lower host, no fragment, no tracking
    query (Facebook story_fbid/id are kept), no trailing slash except root."""
    u = urllib.parse.urlsplit((url or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        return ""
    keep = {"story_fbid", "id", "fbid", "category"}
    q = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(u.query) if k in keep])
    path = u.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(("https", u.netloc.lower(), path, q, ""))


def load() -> dict:
    try:
        data = json.loads(memory_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("platforms"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"brand": brand_id(), "platforms": {}, "updated": None}


def _save(data: dict) -> None:
    p = memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def record(platform: str, kind: str, url: str, label: str = "", data: dict | None = None) -> dict | None:
    """Merge one link (deduped by normalized URL). Never raises: memory is a
    convenience and must not fail a post."""
    try:
        n = normalize(url)
        if not n or not platform:
            return None
        kind = kind if kind in KINDS else "other"
        mem = load() if data is None else data
        links = mem["platforms"].setdefault(platform, [])
        ts = now_iso()
        for item in links:
            if item["url"] == n:
                item["last_seen"], item["count"] = ts, item.get("count", 1) + 1
                if kind != "other":
                    item["kind"] = kind
                if label:
                    item["label"] = label[:120]
                break
        else:
            item = {"url": n, "kind": kind, "label": label[:120], "first_seen": ts, "last_seen": ts, "count": 1}
            links.append(item)
        if len(links) > MAX_PER_PLATFORM:            # keep posts + newest
            links.sort(key=lambda i: i["last_seen"], reverse=True)
            del links[MAX_PER_PLATFORM:]
        mem["updated"] = ts
        mem["brand"] = mem.get("brand") or brand_id()
        _save(mem)
        return item
    except Exception:                                  # noqa: BLE001
        return None


def config_links(platform: str) -> dict:
    """{kind: url} from the curated social_links for a platform."""
    sl = (_social_links() or {}).get(platform) or {}
    out = {}
    if isinstance(sl, dict):
        for key, kind in CONFIG_KEYS.get(platform, {}).items():
            if isinstance(sl.get(key), str) and normalize(sl[key]):
                out.setdefault(kind, sl[key])
    return out


def preferred(platform: str, kind: str, default: str = "") -> str:
    cfg = config_links(platform).get(kind)
    if cfg:
        return cfg
    links = [i for i in load()["platforms"].get(platform, []) if i.get("kind") == kind]
    if links:
        return max(links, key=lambda i: i["last_seen"])["url"]
    return default


def view() -> dict:
    """Everything the UI shows: config links + remembered links, newest first."""
    mem = load()
    out = {}
    for plat in sorted(set(mem["platforms"]) | set(CONFIG_KEYS)):
        items = [{"url": u, "kind": k, "label": "from config", "source": "config"}
                 for k, u in config_links(plat).items()]
        seen = {normalize(i["url"]) for i in items}
        for i in sorted(mem["platforms"].get(plat, []), key=lambda i: i["last_seen"], reverse=True):
            if i["url"] not in seen:
                items.append({**i, "source": "visited"})
        if items:
            out[plat] = items
    return {"ok": True, "brand": mem.get("brand") or brand_id(), "path": str(memory_path()),
            "updated": mem.get("updated"), "platforms": out}


if __name__ == "__main__":
    print(json.dumps(view(), indent=2))
