#!/usr/bin/env python3
"""
Nexus media resolver — "a post never goes out without its picture"
====================================================================
Runs at task creation, at approval and in the scheduler right before posting.
If a target platform needs an image and the task has none, it produces one via
a configurable fallback chain and attaches it to the task::

    NEXUS_IMAGE_PROVIDERS=openai,gemini,xai,canva,stock,card     (default order)

    openai   OpenAI Images (OPENAI_API_KEY; NEXUS_OPENAI_IMAGE_MODEL=gpt-image-1)
    gemini   Google Imagen via Gemini API (GEMINI_API_KEY or GOOGLE_API_KEY;
             NEXUS_GEMINI_IMAGE_MODEL=imagen-4.0-generate-001)
    xai      xAI image generation (XAI_API_KEY; NEXUS_XAI_IMAGE_MODEL=grok-2-image)
    canva    Canva Connect: autofill a brand template + export PNG
             (NEXUS_CANVA_TOKEN + NEXUS_CANVA_TEMPLATE_ID; skipped cleanly without them)
    stock    Free stock photo + headline overlay: Pexels (PEXELS_API_KEY),
             Unsplash (UNSPLASH_ACCESS_KEY), Pixabay (PIXABAY_API_KEY), then the keyless
             Openverse API limited to CC0 / public-domain images (NEXUS_STOCK_KEYLESS=0 disables)
    card     ALWAYS available, offline: Pillow text-to-image card in the brand colours

``card`` is appended to every chain, so media can never be missing.

Media rules (PLATFORM_RULES): Instagram REQUIRES an image; Facebook and LinkedIn
get one by preference; X is optional (it gets a variant when the task already
needs an image for another platform, or NEXUS_IMAGE_FOR_X=1).

Files land in data/brands/<brand>/media/<task>-<platform>-<stamp>.png. The task
gets ``media`` (primary image first), ``media_variants`` {platform: path},
``media_source`` and ``image_brief``; every attach is a history event and an
activity-log entry (``task_media``).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

# ─── platform rules ──────────────────────────────────────────────────────────
#                 need: required | preferred | optional ; sizes: first = default
PLATFORM_RULES: Dict[str, dict] = {
    "instagram": {"need": "required", "sizes": [(1080, 1350), (1080, 1080)]},
    "facebook": {"need": "preferred", "sizes": [(1200, 630), (1080, 1350)]},
    "linkedin": {"need": "preferred", "sizes": [(1200, 627)]},
    "x": {"need": "optional", "sizes": [(1600, 900)]},
}
DEFAULT_CHAIN = ["openai", "gemini", "xai", "canva", "stock", "card"]
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
HTTP_TIMEOUT = float(os.environ.get("NEXUS_IMAGE_TIMEOUT", "60"))


class MediaError(ValueError):
    pass


def chain(value: Optional[str] = None) -> List[str]:
    raw = value if value is not None else os.environ.get("NEXUS_IMAGE_PROVIDERS", "")
    items = [p.strip().lower() for p in str(raw).split(",") if p.strip()] or list(DEFAULT_CHAIN)
    items = [p for p in items if p in PROVIDERS]
    if "card" not in items:
        items.append("card")                       # the guarantee
    return items


def media_platforms(platforms: List[str]) -> List[str]:
    """Platforms of this task that should get an image (in the task's order)."""
    plats = [p for p in (platforms or []) if p in PLATFORM_RULES]
    wants = [p for p in plats if PLATFORM_RULES[p]["need"] in ("required", "preferred")]
    if "x" in plats and (wants or os.environ.get("NEXUS_IMAGE_FOR_X", "") in ("1", "true", "yes")):
        wants.append("x")
    return [p for p in plats if p in wants]


def requires_media(platforms: List[str]) -> bool:
    return bool(media_platforms(platforms))


def _usable(m: str) -> bool:
    m = str(m or "")
    if m.startswith(("http://", "https://")):
        return True
    return Path(m).is_file()


def has_media(task: dict) -> bool:
    return any(_usable(m) for m in (task.get("media") or []))


def missing_for(task: dict) -> List[str]:
    """Platforms that need an image the task does not have yet."""
    plats = media_platforms(task.get("platforms") or [])
    if not plats:
        return []
    # An existing (human-chosen) picture is never overwritten automatically.
    return [] if has_media(task) else plats


def primary_platform(plats: List[str]) -> str:
    return "instagram" if "instagram" in plats else plats[0]


# ─── brand ───────────────────────────────────────────────────────────────────
PALETTES = [  # (bg, bg2, fg, accent)
    ("#0B1F3A", "#163A6B", "#FFFFFF", "#FFB703"),
    ("#1B1036", "#3A1F6E", "#FFFFFF", "#00E5A8"),
    ("#0F2E2E", "#1D5C5C", "#FFFFFF", "#FF7A59"),
    ("#2B0F1F", "#5C1F3F", "#FFFFFF", "#FFD166"),
]
HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def brands_dir() -> Path:
    env = os.environ.get("NEXUS_BRANDS_DIR")
    if env:
        return Path(env)
    import nexus_tasks as nt
    return nt.data_root() / "brands"


def _hex(c) -> Optional[str]:
    if isinstance(c, dict):
        c = c.get("hex") or c.get("value")
    if isinstance(c, str) and HEX.match(c.strip()):
        return "#" + c.strip().lstrip("#").upper()
    return None


def load_brand_style(brand_id: str, root: Optional[Path] = None) -> dict:
    """Colours, logo, name, handles, website from data/brands/<id>/{brand,profile}.json.
    Colour keys looked for: colors / brand_colors / palette (list or {primary,secondary,accent})."""
    root = Path(root) if root else brands_dir()
    d = root / (brand_id or "default")
    info: dict = {}
    for part in ("brand", "profile"):
        try:
            v = json.loads((d / f"{part}.json").read_text(encoding="utf-8"))
            if isinstance(v, dict):
                for k, val in v.items():
                    info.setdefault(k, val)
        except (OSError, ValueError):
            pass
    pal = None
    for key in ("colors", "brand_colors", "palette", "colours"):
        v = info.get(key)
        if isinstance(v, dict):
            vals = [_hex(v.get(k)) for k in ("primary", "secondary", "text", "accent")]
            if vals[0]:
                pal = (vals[0], vals[1] or vals[0], vals[2] or "#FFFFFF", vals[3] or vals[1] or "#FFB703")
        elif isinstance(v, list):
            hs = [h for h in (_hex(x) for x in v) if h]
            if hs:
                pal = (hs[0], hs[1] if len(hs) > 1 else hs[0], "#FFFFFF", hs[2] if len(hs) > 2 else (hs[1] if len(hs) > 1 else "#FFB703"))
        if pal:
            break
    if not pal:
        idx = int(hashlib.sha1((brand_id or "x").encode()).hexdigest(), 16) % len(PALETTES)
        pal = PALETTES[idx]
    logo = None
    for cand in [info.get("logo"), info.get("logo_path")] + [str(d / n) for n in ("logo.png", "logo.jpg", "logo.webp")]:
        if cand and not str(cand).startswith("http") and Path(str(cand)).is_file():
            logo = str(cand)
            break
    return {"id": brand_id, "name": info.get("business_name") or brand_id or "", "bg": pal[0], "bg2": pal[1],
            "fg": pal[2], "accent": pal[3], "logo": logo, "handles": info.get("handles") or {},
            "website": info.get("website") or ""}


# ─── image brief ─────────────────────────────────────────────────────────────
EMOJI = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F\u200d]")
CTA_HINTS = [(r"download|play store|app store|install", "Download now"), (r"link in bio", "Link in bio"),
             (r"\bshop|buy|order", "Shop now"), (r"book|reserve", "Book now"), (r"sign ?up|register|join", "Join now"),
             (r"call|contact|dm", "Get in touch")]


def clean_text(s: str) -> str:
    s = EMOJI.sub("", str(s or ""))
    s = re.sub(r"#\w+", "", s)
    s = re.sub(r"https?://\S+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _words(s: str, n: int) -> str:
    w = clean_text(s).split()
    out = " ".join(w[:n])
    return out.rstrip(",;:—-– ") + ("…" if len(w) > n and not out.endswith((".", "!", "?")) else "")


def derive_brief(task: dict) -> dict:
    """A usable brief from the task text when the brain did not give one."""
    title = re.split(r"\s+[—–-]\s+", str(task.get("title") or ""))[0]
    content = clean_text(task.get("content") or task.get("instruction") or "")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", content) if len(s.strip()) > 3]
    headline = _words(sentences[0], 8) if sentences else _words(title, 8)
    if title and len(clean_text(title).split()) <= 6 and sentences and len(sentences[0].split()) > 8:
        headline = clean_text(title)
    sub = next((s for s in sentences[1:] if s), "") or (title if headline != clean_text(title) else "")
    cta = "Learn more"
    low = (task.get("content") or "").lower()
    for pat, label in CTA_HINTS:
        if re.search(pat, low):
            cta = label
            break
    kw = [w for w in re.findall(r"[a-zA-Z]{4,}", headline.lower()) if w not in
          {"with", "your", "that", "this", "from", "what", "have", "will", "into", "stop", "best"}][:4]
    return {"headline": headline.rstrip("…") or "New post", "subline": _words(sub, 16), "cta": cta,
            "style": task.get("poster_idea") or "clean, bold, brand colours", "keywords": kw or ["technology"]}


def merge_brief(task: dict, edits: Optional[dict] = None) -> dict:
    base = derive_brief(task)
    given = task.get("image_brief") if isinstance(task.get("image_brief"), dict) else {}
    out = {**base, **{k: v for k, v in given.items() if v not in (None, "", [])}}
    for k, v in (edits or {}).items():
        if v not in (None, ""):
            out[k] = v
    if isinstance(out.get("keywords"), str):
        out["keywords"] = [w.strip() for w in out["keywords"].split(",") if w.strip()]
    return out


def image_prompt(brief: dict, brand: dict) -> str:
    return (f"Social media marketing image for {brand.get('name')}. {brief.get('style')}. "
            f"Theme: {brief.get('headline')} — {brief.get('subline')}. Brand colours {brand['bg']} and {brand['accent']}. "
            "Modern, high quality, no watermark, no small unreadable text.")


# ─── Pillow card renderer (offline, always available) ───────────────────────
FONT_CANDIDATES = {
    "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
             "/Library/Fonts/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    "regular": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Verdana.ttf",
                "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
}


def _font(kind: str, size: int):
    from PIL import ImageFont
    env = os.environ.get("NEXUS_CARD_FONT_BOLD" if kind == "bold" else "NEXUS_CARD_FONT")
    for p in ([env] if env else []) + FONT_CANDIDATES[kind]:
        if p and Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)        # Pillow >= 10.1
    except TypeError:                                    # pragma: no cover
        return ImageFont.load_default()


def _rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(draw, text: str, font, width: int) -> List[str]:
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(draw, text: str, kind: str, width: int, height: int, max_size: int, min_size: int = 18,
             max_lines: int = 5, spacing: float = 1.15):
    """Largest font so the wrapped text fits in width x height. Returns (font, lines, line_h)."""
    size = max_size
    while True:
        f = _font(kind, size)
        lines = _wrap(draw, text, f, width)
        lh = int(size * spacing)
        too_wide = any(draw.textlength(ln, font=f) > width for ln in lines)
        if (len(lines) <= max_lines and lh * len(lines) <= height and not too_wide) or size <= min_size:
            return f, lines[:max_lines], lh
        size = max(min_size, int(size * 0.92))


def _gradient(size, c1, c2):
    from PIL import Image
    w, h = size
    top, bot = Image.new("RGB", (1, 2)), None
    top.putpixel((0, 0), _rgb(c1))
    top.putpixel((0, 1), _rgb(c2))
    bot = top.resize((w, h), Image.BILINEAR)
    return bot


def render_card(brief: dict, brand: dict, size: Tuple[int, int], out_path: Path, *,
                background=None, platform: str = "") -> Path:
    """Draw headline + subline + CTA + handle on brand colours (or over a photo)."""
    from PIL import Image, ImageDraw
    w, h = size
    bg, bg2 = brief.get("bg") or brand["bg"], brief.get("bg2") or brand["bg2"]
    fg, accent = brief.get("fg") or brand["fg"], brief.get("accent") or brand["accent"]
    for name, v in (("bg", bg), ("bg2", bg2), ("fg", fg), ("accent", accent)):
        if not _hex(v):
            raise MediaError(f"{name} must be a #RRGGBB colour, got {v!r}")
    bg, bg2, fg, accent = _hex(bg), _hex(bg2), _hex(fg), _hex(accent)
    if background is not None:
        img = _cover(background.convert("RGB"), size)
        shade = Image.new("RGBA", size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        for y in range(h):                                  # dark gradient for legibility
            a = int(70 + 150 * (y / h))
            sd.line([(0, y), (w, y)], fill=(*_rgb(bg), a))
        img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")
    else:
        img = _gradient(size, bg, bg2)
        deco = Image.new("RGBA", size, (0, 0, 0, 0))
        dd = ImageDraw.Draw(deco)
        r = int(max(w, h) * 0.45)
        dd.ellipse([w - r, -r // 2, w + r // 2, r], fill=(*_rgb(accent), 40))
        dd.ellipse([-r // 2, h - r // 1.6, r // 1.4, h + r // 2], fill=(*_rgb(fg), 18))
        img = Image.alpha_composite(img.convert("RGBA"), deco).convert("RGB")
    d = ImageDraw.Draw(img)
    m = int(min(w, h) * 0.075)
    unit = min(w, h)
    # header: logo / monogram + brand name
    lh = int(unit * 0.085)
    if brand.get("logo"):
        try:
            logo = Image.open(brand["logo"]).convert("RGBA")
            logo.thumbnail((lh * 3, lh))
            img.paste(logo, (m, m), logo)
            name_x = m + logo.width + int(lh * 0.3)
        except OSError:
            brand = {**brand, "logo": None}
    if not brand.get("logo"):
        d.ellipse([m, m, m + lh, m + lh], fill=accent)
        initials = "".join(p[0] for p in str(brand.get("name") or "N").split()[:2]).upper()
        f = _font("bold", int(lh * 0.42))
        tw = d.textlength(initials, font=f)
        d.text((m + (lh - tw) / 2, m + lh * 0.27), initials, font=f, fill=bg)
        name_x = m + lh + int(lh * 0.3)
    d.text((name_x, m + lh * 0.28), clean_text(brand.get("name") or ""), font=_font("bold", int(lh * 0.42)), fill=fg)
    # footer: handle + website
    handle = brief.get("handle")
    if not handle:
        hs = brand.get("handles") or {}
        hv = hs.get(platform) or next(iter(hs.values()), "") if hs else ""
        handle = ("@" + hv.lstrip("@")) if hv else ""
    ff = _font("regular", int(unit * 0.032))
    fy = h - m - int(unit * 0.04)
    if handle:
        d.text((w - m - d.textlength(handle, font=ff), fy), handle, font=ff, fill=fg)
    site = re.sub(r"^https?://(www\.)?", "", brand.get("website") or "").rstrip("/")
    if site:
        d.text((m, fy), site, font=ff, fill=fg)
    # body block
    landscape = w / h > 1.3
    text_w = int(w * (0.62 if landscape else 1.0)) - 2 * m if landscape else w - 2 * m
    top = m + lh + int(unit * 0.06)
    cta_h = int(unit * 0.09)
    bottom = fy - int(unit * 0.05) - cta_h - int(unit * 0.04)
    avail = bottom - top
    headline = clean_text(brief.get("headline") or "")
    subline = clean_text(brief.get("subline") or "")
    hf, hlines, hlh = fit_text(d, headline, "bold", text_w, int(avail * (0.62 if subline else 0.9)),
                               max_size=int(unit * (0.11 if landscape else 0.12)), min_size=int(unit * 0.04), max_lines=4)
    sf, slines, slh = (None, [], 0)
    if subline:
        sf, slines, slh = fit_text(d, subline, "regular", text_w, int(avail * 0.3),
                                   max_size=int(unit * 0.05), min_size=int(unit * 0.026), max_lines=3, spacing=1.3)
    block = hlh * len(hlines) + (int(unit * 0.035) + slh * len(slines) if slines else 0)
    y = top + max(0, (avail - block) // 2) if not landscape else top + max(0, (avail - block) // 2)
    d.rectangle([m, y - int(unit * 0.03), m + int(unit * 0.12), y - int(unit * 0.03) + max(4, unit // 120)], fill=accent)
    for ln in hlines:
        d.text((m, y), ln, font=hf, fill=fg)
        y += hlh
    if slines:
        y += int(unit * 0.035)
        for ln in slines:
            d.text((m, y), ln, font=sf, fill=fg)
            y += slh
    # CTA pill
    cta = clean_text(brief.get("cta") or "")
    if cta:
        cf = _font("bold", int(cta_h * 0.42))
        tw = d.textlength(cta, font=cf)
        x0, y0 = m, bottom + int(unit * 0.02)
        d.rounded_rectangle([x0, y0, x0 + tw + cta_h, y0 + cta_h], radius=cta_h // 2, fill=accent)
        d.text((x0 + cta_h / 2, y0 + cta_h * 0.27), cta, font=cf, fill=bg)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def _cover(img, size):
    """Scale + center-crop to exactly size."""
    from PIL import Image
    w, h = size
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, int(img.width * s + 0.5)), max(h, int(img.height * s + 0.5))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


# ─── network providers ───────────────────────────────────────────────────────
Http = Callable[..., bytes]


def http(method: str, url: str, *, headers: Optional[dict] = None, body=None, timeout: float = HTTP_TIMEOUT) -> bytes:
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    hdrs = {"User-Agent": "NexusMedia/1.0", **({"Content-Type": "application/json"} if isinstance(body, (dict, list)) else {}),
            **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise MediaError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}: {e.read()[:200]!r}") from None
    except (urllib.error.URLError, OSError) as e:
        raise MediaError(f"{urllib.parse.urlsplit(url).netloc}: {e}") from None


def _env(*names) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _aspect_word(size) -> str:
    w, h = size
    return "portrait" if h > w * 1.05 else ("landscape" if w > h * 1.05 else "square")


def p_openai(ctx, http_fn: Http):
    key = _env("OPENAI_API_KEY")
    if not key:
        return None
    w, h = ctx["base_size"]
    size = "1024x1536" if h > w * 1.05 else ("1536x1024" if w > h * 1.05 else "1024x1024")
    model = os.environ.get("NEXUS_OPENAI_IMAGE_MODEL", "gpt-image-1")
    body = {"model": model, "prompt": ctx["prompt"], "size": size, "n": 1}
    if model.startswith("dall-e"):
        body["response_format"] = "b64_json"
    r = json.loads(http_fn("POST", "https://api.openai.com/v1/images/generations",
                           headers={"Authorization": f"Bearer {key}"}, body=body))
    item = (r.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), {"model": model}
    if item.get("url"):
        return http_fn("GET", item["url"]), {"model": model}
    raise MediaError("OpenAI returned no image")


def p_gemini(ctx, http_fn: Http):
    key = _env("GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return None
    model = os.environ.get("NEXUS_GEMINI_IMAGE_MODEL", "imagen-4.0-generate-001")
    ar = {"portrait": "3:4", "landscape": "16:9", "square": "1:1"}[_aspect_word(ctx["base_size"])]
    r = json.loads(http_fn("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict",
                           headers={"x-goog-api-key": key},
                           body={"instances": [{"prompt": ctx["prompt"]}], "parameters": {"sampleCount": 1, "aspectRatio": ar}}))
    pred = (r.get("predictions") or [{}])[0]
    if not pred.get("bytesBase64Encoded"):
        raise MediaError("Imagen returned no image (prompt filtered?)")
    return base64.b64decode(pred["bytesBase64Encoded"]), {"model": model}


def p_xai(ctx, http_fn: Http):
    key = _env("XAI_API_KEY")
    if not key:
        return None
    model = os.environ.get("NEXUS_XAI_IMAGE_MODEL", "grok-2-image")
    r = json.loads(http_fn("POST", "https://api.x.ai/v1/images/generations", headers={"Authorization": f"Bearer {key}"},
                           body={"model": model, "prompt": ctx["prompt"][:1000], "n": 1, "response_format": "b64_json"}))
    item = (r.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), {"model": model}
    raise MediaError("xAI returned no image")


def p_canva(ctx, http_fn: Http, *, sleep=time.sleep, polls: int = 30):
    """Canva Connect: autofill brand template fields (headline, subline, cta, handle) → export PNG.
    Needs NEXUS_CANVA_TOKEN (OAuth access token with design:content:write, design:content:read,
    brandtemplate:content:read scopes) and NEXUS_CANVA_TEMPLATE_ID. Text fields in the template must be
    named headline / subline / cta (missing ones are ignored by Canva)."""
    tok, tpl = _env("NEXUS_CANVA_TOKEN"), _env("NEXUS_CANVA_TEMPLATE_ID")
    if not (tok and tpl):
        return None
    api = "https://api.canva.com/rest/v1"
    hdr = {"Authorization": f"Bearer {tok}"}
    b = ctx["brief"]
    data = {k: {"type": "text", "text": str(b.get(k) or "")} for k in ("headline", "subline", "cta") if b.get(k)}
    job = json.loads(http_fn("POST", f"{api}/autofills", headers=hdr, body={"brand_template_id": tpl, "data": data}))["job"]
    for _ in range(polls):
        if job.get("status") in ("success", "failed"):
            break
        sleep(2)
        job = json.loads(http_fn("GET", f"{api}/autofills/{job['id']}", headers=hdr))["job"]
    if job.get("status") != "success":
        raise MediaError(f"Canva autofill {job.get('status')}: {job.get('error')}")
    design_id = job["result"]["design"]["id"]
    ex = json.loads(http_fn("POST", f"{api}/exports", headers=hdr,
                            body={"design_id": design_id, "format": {"type": "png"}}))["job"]
    for _ in range(polls):
        if ex.get("status") in ("success", "failed"):
            break
        sleep(2)
        ex = json.loads(http_fn("GET", f"{api}/exports/{ex['id']}", headers=hdr))["job"]
    if ex.get("status") != "success" or not ex.get("urls"):
        raise MediaError(f"Canva export {ex.get('status')}: {ex.get('error')}")
    return http_fn("GET", ex["urls"][0]), {"design_id": design_id, "overlay": False}


def p_stock(ctx, http_fn: Http):
    q = " ".join(ctx["brief"].get("keywords") or [])[:80] or "technology"
    orient = _aspect_word(ctx["base_size"])
    qs = urllib.parse.quote(q)
    tried = []
    key = _env("PEXELS_API_KEY")
    if key:
        o = {"portrait": "portrait", "landscape": "landscape", "square": "square"}[orient]
        r = json.loads(http_fn("GET", f"https://api.pexels.com/v1/search?query={qs}&per_page=5&orientation={o}",
                               headers={"Authorization": key}))
        for p in r.get("photos") or []:
            return http_fn("GET", p["src"].get("large2x") or p["src"]["original"]), {
                "stock": "pexels", "credit": f"Photo by {p.get('photographer')} on Pexels", "url": p.get("url")}
        tried.append("pexels")
    key = _env("UNSPLASH_ACCESS_KEY")
    if key:
        o = {"portrait": "portrait", "landscape": "landscape", "square": "squarish"}[orient]
        r = json.loads(http_fn("GET", f"https://api.unsplash.com/search/photos?query={qs}&per_page=5&orientation={o}",
                               headers={"Authorization": f"Client-ID {key}"}))
        for p in r.get("results") or []:
            if (p.get("links") or {}).get("download_location"):   # Unsplash API guideline: count the download
                try:
                    http_fn("GET", p["links"]["download_location"], headers={"Authorization": f"Client-ID {key}"})
                except MediaError:
                    pass
            return http_fn("GET", p["urls"]["regular"]), {
                "stock": "unsplash", "credit": f"Photo by {p['user']['name']} on Unsplash", "url": p["links"]["html"]}
        tried.append("unsplash")
    key = _env("PIXABAY_API_KEY")
    if key:
        o = {"portrait": "vertical", "landscape": "horizontal", "square": "all"}[orient]
        r = json.loads(http_fn("GET", f"https://pixabay.com/api/?key={key}&q={qs}&image_type=photo&orientation={o}&safesearch=true&per_page=5"))
        for p in r.get("hits") or []:
            return http_fn("GET", p.get("largeImageURL") or p["webformatURL"]), {
                "stock": "pixabay", "credit": "Image from Pixabay", "url": p.get("pageURL")}
        tried.append("pixabay")
    if os.environ.get("NEXUS_STOCK_KEYLESS", "1") not in ("0", "false", "no"):
        # Openverse (WordPress Foundation) — keyless, anonymous requests are allowed; CC0/PDM only
        # so no attribution is legally required.
        ar = {"portrait": "tall", "landscape": "wide", "square": "square"}[orient]
        kws = [k for k in (ctx["brief"].get("keywords") or []) if k]
        queries = [q] + [k for k in kws if k != q][:2]
        tries = [(qq, ar) for qq in queries] + [(queries[-1], "")]
        for qq, aspect in tries[:4]:          # whole phrase, then single words, then any aspect ratio
            url = (f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(qq)}&license=cc0,pdm"
                   f"{'&aspect_ratio=' + aspect if aspect else ''}&page_size=5&mature=false")
            r = json.loads(http_fn("GET", url))
            for p in r.get("results") or []:
                try:
                    return http_fn("GET", p["url"]), {"stock": "openverse", "license": p.get("license"), "query": qq,
                                                      "credit": p.get("attribution") or "", "url": p.get("foreign_landing_url")}
                except MediaError:
                    continue
        tried.append("openverse")
    if not tried:
        return None
    raise MediaError(f"no stock photo for {q!r} ({', '.join(tried)})")


def p_card(ctx, http_fn: Http):
    return "CARD", {}


PROVIDERS: Dict[str, Callable] = {"openai": p_openai, "gemini": p_gemini, "xai": p_xai, "canva": p_canva,
                                  "stock": p_stock, "card": p_card}
OVERLAY = {"stock"}           # providers whose photo gets the headline/CTA overlay


# ─── resolve ─────────────────────────────────────────────────────────────────
_task_locks: Dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(tid: str) -> threading.Lock:
    with _guard:
        return _task_locks.setdefault(tid, threading.Lock())


def produce(task: dict, *, providers: Optional[List[str]] = None, edits: Optional[dict] = None,
            out_dir: Optional[Path] = None, brand_root: Optional[Path] = None, http_fn: Http = http,
            stamp: Optional[str] = None) -> dict:
    """Make the images for a task (does not touch the store). Returns
    {ok, media:[primary,...], variants:{platform:path}, source, brief, attempts}."""
    from PIL import Image
    plats = media_platforms(task.get("platforms") or [])
    if not plats:
        known = [p for p in (task.get("platforms") or []) if p in PLATFORM_RULES]
        plats = (known[:1] or ["instagram"]) if edits and edits.get("force_platform") else []
    if not plats:
        return {"ok": False, "skipped": True, "reason": "no platform of this task needs an image", "attempts": []}
    brand_id = task.get("brand") or os.environ.get("NEXUS_BRAND") or "default"
    brand = load_brand_style(brand_id, brand_root)
    brief = merge_brief(task, {k: v for k, v in (edits or {}).items() if k not in ("provider", "providers", "force_platform")})
    pick = (edits or {}).get("providers") or (edits or {}).get("provider")
    if pick:
        order = chain(",".join(pick) if isinstance(pick, (list, tuple)) else str(pick))
    else:
        order = chain(",".join(providers)) if providers else chain()
    prim = primary_platform(plats)
    sizes = {p: tuple(PLATFORM_RULES[p]["sizes"][0]) for p in plats}
    ctx = {"brief": brief, "brand": brand, "base_size": sizes[prim], "prompt": image_prompt(brief, brand), "task": task}
    if not out_dir and os.environ.get("NEXUS_MEDIA_DIR"):
        out_dir = Path(os.environ["NEXUS_MEDIA_DIR"]) / brand_id
    out_dir = Path(out_dir) if out_dir else brands_dir() / brand_id / "media"
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    attempts = []
    for name in order:
        try:
            res = PROVIDERS[name](ctx, http_fn)
        except Exception as e:                                # noqa: BLE001 — try the next provider
            attempts.append({"provider": name, "ok": False, "error": str(e)[:300]})
            continue
        if res is None:
            attempts.append({"provider": name, "ok": False, "skipped": "not configured"})
            continue
        raw, meta = res
        try:
            photo = None if raw == "CARD" else Image.open(io.BytesIO(raw))
            if photo is not None:
                photo.load()
            variants = {}
            for p in plats:
                path = out_dir / f"{task.get('id', 'task')}-{p}-{stamp}.png"
                if photo is None or (name in OVERLAY or meta.get("overlay")):
                    render_card(brief, brand, sizes[p], path, background=photo, platform=p)
                else:
                    _cover(photo.convert("RGB"), sizes[p]).save(path, "PNG", optimize=True)
                variants[p] = str(path)
        except Exception as e:                                # noqa: BLE001
            attempts.append({"provider": name, "ok": False, "error": f"render: {e}"[:300]})
            if name == "card":
                raise
            continue
        attempts.append({"provider": name, "ok": True, **{k: v for k, v in meta.items() if k != "overlay"}})
        media = [variants[prim]] + [v for p, v in variants.items() if p != prim]
        return {"ok": True, "media": media, "variants": variants, "source": name, "brief": brief,
                "attempts": attempts, "meta": meta}
    raise MediaError(f"every image provider failed: {attempts}")     # unreachable: card is last


def ensure(task_id: str, *, store=None, by: str = "nexus", when: str = "manual", force: bool = False,
           edits: Optional[dict] = None, providers: Optional[List[str]] = None, http_fn: Http = http,
           out_dir: Optional[Path] = None, brand_root: Optional[Path] = None, wait: bool = True) -> dict:
    """Attach an image to the task if one is needed and missing (or force=True to regenerate)."""
    import nexus_tasks as nt
    store = store or nt.TaskStore()
    lock = _lock_for(task_id)
    if not lock.acquire(blocking=wait):
        return {"ok": False, "skipped": True, "reason": "already generating"}
    try:
        task = store.get(task_id)
        if task.get("status") in ("done", "cancelled"):
            return {"ok": False, "skipped": True, "reason": f"task is {task['status']}"}
        if not force and not missing_for(task):
            return {"ok": True, "skipped": True, "reason": "media present or not needed", "media": task.get("media") or []}
        res = produce(task, providers=providers, edits=edits, out_dir=out_dir, brand_root=brand_root, http_fn=http_fn)
        if not res.get("ok"):
            return res
        t = store.set_media(task_id, res["media"], variants=res["variants"], source=res["source"], brief=res["brief"],
                            by=by, when=when, attempts=res["attempts"])
        return {**res, "task": t}
    finally:
        lock.release()


def ensure_async(task_id: str, **kw) -> threading.Thread:
    """Fire-and-forget (task creation / approval): never blocks or raises into the caller."""
    def run():
        try:
            ensure(task_id, wait=False, **kw)
        except Exception as e:                                # noqa: BLE001
            print(f"[media] {task_id}: {e}", file=sys.stderr)
    th = threading.Thread(target=run, name=f"nexus-media-{task_id}", daemon=True)
    th.start()
    return th


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate / regenerate the image of a Nexus task")
    ap.add_argument("task_id")
    ap.add_argument("--force", action="store_true", help="regenerate even if the task has media")
    ap.add_argument("--providers", default=None, help="e.g. card or stock,card")
    ap.add_argument("--edit", action="append", default=[], help="key=value (headline, subline, cta, bg, accent, ...)")
    a = ap.parse_args()
    ed = dict(e.split("=", 1) for e in a.edit)
    out = ensure(a.task_id, force=a.force, edits=ed or None, by="cli", when="cli",
                 providers=a.providers.split(",") if a.providers else None)
    out.pop("task", None)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
