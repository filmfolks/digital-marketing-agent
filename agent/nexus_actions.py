#!/usr/bin/env python3
"""
Nexus action schema — the "command language" every brain speaks
================================================================
A small, closed set of tools. Each has a JSON Schema (OpenAI/Hermes/MCP
compatible) and a Python executor. The model never posts anything:

* draft_post / create_task / plan_campaign create tasks in ``pending_approval``
  (or ``draft``). A human approves them; the scheduler posts approved tasks
  at their time through the existing publish flow.
* There is deliberately NO approve/publish tool. ``update_task`` cannot change
  status and editing an approved post sends it back for approval.

The tiny validator below covers the JSON-Schema subset used here (type,
properties, required, enum, items, min/max, minLength/maxLength, minItems/
maxItems, additionalProperties=false, pattern) so it runs with zero deps on
Python 3.9 and the same schemas are shipped to the model.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import nexus_tasks as nt  # noqa: E402

PLATFORM_ENUM = list(nt.PLATFORMS)
BRIEF = {"type": "object", "description": "Image brief for the post picture (always include it).",
         "properties": {
             "headline": {"type": "string", "maxLength": 80, "description": "Big text on the image, max 8 words"},
             "subline": {"type": "string", "maxLength": 160, "description": "One short supporting line"},
             "cta": {"type": "string", "maxLength": 30, "description": "Button text, e.g. 'Download now'"},
             "style": {"type": "string", "maxLength": 300, "description": "Look and mood: colours, photo vs. graphic, subject"},
             "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                          "description": "2-4 stock-photo search words, e.g. ['smartphone', 'bookmarks']"},
         }}
EDITS = {"type": "object", "description": "What to change on the image. Only send what changes.",
         "properties": {
             "headline": {"type": "string", "maxLength": 80}, "subline": {"type": "string", "maxLength": 160},
             "cta": {"type": "string", "maxLength": 30}, "style": {"type": "string", "maxLength": 300},
             "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
             "bg": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "description": "Background colour #RRGGBB"},
             "bg2": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "description": "Second gradient colour"},
             "fg": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "description": "Text colour"},
             "accent": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "description": "Button/accent colour"},
             "provider": {"type": "string", "enum": ["openai", "gemini", "xai", "canva", "stock", "card"],
                          "description": "Force one source; 'card' = offline text card (always works)"},
         }, "additionalProperties": False}
DT = {"type": "string", "description": "ISO 8601 local date-time, e.g. 2026-10-20T18:30 (user timezone if no offset)"}

_post_item = {
    "type": "object",
    "properties": {
        "scheduled_at": DT,
        "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_ENUM}, "minItems": 1},
        "title": {"type": "string", "maxLength": 120},
        "caption": {"type": "string", "minLength": 1, "maxLength": 2200, "description": "Final post text"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "poster_idea": {"type": "string", "maxLength": 600, "description": "Visual / poster concept"},
        "image_brief": BRIEF,
    },
    "required": ["scheduled_at", "platforms", "caption"],
}

TOOLS: Dict[str, dict] = {
    "create_task": {
        "description": "Create a task on the calendar (a post to prepare or a reminder). It is saved as pending_approval; a human approves it before anything is posted.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "instruction": {"type": "string", "maxLength": 2000, "description": "What should be done"},
            "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_ENUM}},
            "content": {"type": "string", "maxLength": 5000, "description": "Post text if already written"},
            "scheduled_at": DT,
            "brand": {"type": "string"},
        }, "required": ["title"], "additionalProperties": False},
    },
    "draft_post": {
        "description": "Write one social post and save it as a pending_approval task (optionally scheduled). Nothing is published.",
        "parameters": {"type": "object", "properties": {
            "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_ENUM}, "minItems": 1},
            "caption": {"type": "string", "minLength": 1, "maxLength": 5000},
            "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
            "poster_idea": {"type": "string", "maxLength": 600},
            "image_brief": BRIEF,
            "scheduled_at": DT,
            "title": {"type": "string", "maxLength": 120},
            "brand": {"type": "string"},
        }, "required": ["platforms", "caption"], "additionalProperties": False},
    },
    "schedule_task": {
        "description": "Set or change the date/time of an existing task.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string", "minLength": 3}, "scheduled_at": DT,
        }, "required": ["task_id", "scheduled_at"], "additionalProperties": False},
    },
    "plan_campaign": {
        "description": "Plan a campaign (e.g. a whole month): returns many dated posts with captions, SEO hashtags and poster ideas. Every post becomes a pending_approval task.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 120},
            "goal": {"type": "string", "maxLength": 500},
            "brand": {"type": "string"},
            "start": {"type": "string", "description": "YYYY-MM-DD"},
            "end": {"type": "string", "description": "YYYY-MM-DD"},
            "posts": {"type": "array", "items": _post_item, "minItems": 1, "maxItems": 62},
        }, "required": ["name", "posts"], "additionalProperties": False},
    },
    "list_tasks": {
        "description": "List tasks, optionally filtered by status, brand or date range.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": list(nt.STATUSES)},
            "brand": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        }, "additionalProperties": False},
    },
    "update_task": {
        "description": "Edit a task's text, platforms, title, hashtags or poster idea. Cannot approve; edits to an approved post send it back for approval.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string", "minLength": 3},
            "title": {"type": "string", "maxLength": 120}, "content": {"type": "string", "maxLength": 5000},
            "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_ENUM}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "poster_idea": {"type": "string", "maxLength": 600}, "instruction": {"type": "string", "maxLength": 2000},
            "image_brief": BRIEF,
        }, "required": ["task_id"], "additionalProperties": False},
    },
    "generate_task_image": {
        "description": "Make the picture for a task if it has none (uses the task's image_brief; falls back to an offline brand card, so it always works). Returns the image path.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string", "minLength": 3},
            "provider": {"type": "string", "enum": ["openai", "gemini", "xai", "canva", "stock", "card"]},
        }, "required": ["task_id"], "additionalProperties": False},
    },
    "regenerate_task_image": {
        "description": "Redo a task's picture with changes the user asked for (new headline/subline/CTA text, colours, style, stock keywords or source). Regenerating the image of an approved post sends it back for approval.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string", "minLength": 3},
            "edits": EDITS,
        }, "required": ["task_id"], "additionalProperties": False},
    },
    "cancel_task": {
        "description": "Cancel a task that has not run yet.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string", "minLength": 3}, "reason": {"type": "string", "maxLength": 300},
        }, "required": ["task_id"], "additionalProperties": False},
    },
    "get_brand_profile": {
        "description": "Get the brand profile (voice, audience, pillars, hashtags, guardrails) of the active or named brand.",
        "parameters": {"type": "object", "properties": {"brand": {"type": "string"}}, "additionalProperties": False},
    },
    "get_calendar": {
        "description": "Get scheduled tasks grouped by day for a date range (default: next 30 days).",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string", "description": "YYYY-MM-DD"}, "end": {"type": "string", "description": "YYYY-MM-DD"},
            "brand": {"type": "string"},
        }, "additionalProperties": False},
    },
    "research_brand": {
        "description": "Return what Nexus already knows about the brand: saved website/Google-profile research and the profile summary. Use before planning.",
        "parameters": {"type": "object", "properties": {"brand": {"type": "string"}}, "additionalProperties": False},
    },
}


def openai_tools(names: Optional[List[str]] = None) -> List[dict]:
    return [{"type": "function", "function": {"name": n, "description": TOOLS[n]["description"],
                                              "parameters": TOOLS[n]["parameters"]}}
            for n in (names or TOOLS)]


def mcp_tools() -> List[dict]:
    return [{"name": n, "description": t["description"], "inputSchema": t["parameters"]} for n, t in TOOLS.items()]


# =============================================================================
# Validation + coercion
# =============================================================================
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def coerce(value: Any, schema: dict) -> Any:
    """Fix the common small-model mistakes before validating: numbers as strings,
    a comma string for an array, a single item for an array, platform case."""
    t = schema.get("type")
    if t == "array":
        if isinstance(value, str):
            value = [v.strip() for v in re.split(r"[,\n]", value) if v.strip()] if ("," in value or "\n" in value) else [value]
        elif isinstance(value, dict) and schema.get("items", {}).get("type") != "object":
            value = list(value.values())
        elif not isinstance(value, list) and value is not None:
            value = [value]
        if isinstance(value, list):
            return [coerce(v, schema.get("items", {})) for v in value]
        return value
    if t == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: (coerce(v, props[k]) if k in props else v) for k, v in value.items()}
    if t == "integer" and isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    if t == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if t == "string":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str) and "enum" in schema and value not in schema["enum"]:
            low = value.strip().lower()
            alias = {"twitter": "x", "ig": "instagram", "fb": "facebook"}.get(low, low)
            if alias in schema["enum"]:
                return alias
    return value


def validate(value: Any, schema: dict, path: str = "$") -> List[str]:
    errs: List[str] = []
    t = schema.get("type")
    if t:
        py = _TYPES[t]
        if not isinstance(value, py) or (t in ("integer", "number") and isinstance(value, bool)):
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: must not be empty" if schema.get("minLength") == 1 else f"{path}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: must be <= {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: at most {schema['maxItems']} items")
        for i, v in enumerate(value):
            errs += validate(v, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in value or value[r] in (None, ""):
                errs.append(f"{path}: missing required field '{r}'")
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    errs.append(f"{path}: unknown field '{k}' (allowed: {', '.join(props)})")
        for k, v in value.items():
            if k in props and v is not None:
                errs += validate(v, props[k], f"{path}.{k}")
    return errs


def check_call(name: str, args: Any) -> Tuple[Optional[str], dict, List[str]]:
    """Resolve the tool name (tolerant), coerce and validate arguments.
    Returns (canonical_name or None, coerced_args, errors)."""
    canon = resolve_name(name)
    if not canon:
        return None, {}, [f"unknown tool '{name}'. Available: {', '.join(TOOLS)}"]
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return canon, {}, ["arguments must be a JSON object"]
    schema = TOOLS[canon]["parameters"]
    # Drop nulls and keys the model invented that are harmless aliases.
    args = {k: v for k, v in args.items() if v is not None}
    alias = {"text": "caption", "post": "caption", "date": "scheduled_at", "time": "scheduled_at",
             "datetime": "scheduled_at", "platform": "platforms", "id": "task_id"}
    for k in list(args):
        tgt = alias.get(k)
        if tgt and tgt in schema["properties"] and tgt not in args and k not in schema["properties"]:
            args[tgt] = args.pop(k)
    if canon == "plan_campaign" and isinstance(args.get("posts"), list):
        for p in args["posts"]:
            if isinstance(p, dict):
                for k in list(p):
                    tgt = alias.get(k)
                    if tgt and tgt not in p and k not in _post_item["properties"]:
                        p[tgt] = p.pop(k)
                for k in [k for k in p if k not in _post_item["properties"]]:
                    p.pop(k)               # tolerate extra keys inside posts
    args = coerce(args, schema)
    return canon, args, validate(args, schema)


def resolve_name(name: str) -> Optional[str]:
    n = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    for prefix in ("nexus_", "mcp_nexus_", "functions_"):
        if n.startswith(prefix) and n[len(prefix):] in TOOLS:
            n = n[len(prefix):]
    return n if n in TOOLS else None


# =============================================================================
# Executor
# =============================================================================
class Executor:
    """Runs validated tool calls against the store. ``by`` is recorded on every task."""

    def __init__(self, store: Optional[nt.TaskStore] = None, *, brain: str = "", brand: Optional[str] = None,
                 brand_loader: Optional[Callable[[Optional[str]], dict]] = None):
        self.store = store or nt.TaskStore()
        self.brain = brain
        self.brand = brand
        self.brand_loader = brand_loader or load_brand
        self.created: List[dict] = []

    def _brand(self, args: dict) -> str:
        return args.get("brand") or self.brand or (self.brand_loader(None).get("id") or "")

    def run(self, name: str, args: Any) -> dict:
        canon, args, errors = check_call(name, args)
        if errors:
            return {"ok": False, "error": "invalid arguments", "details": errors,
                    "schema": TOOLS[canon]["parameters"] if canon else None}
        try:
            return getattr(self, "t_" + canon)(args)
        except (nt.TaskError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def _new(self, fields: dict, campaign_id=None) -> dict:
        t = self.store.create(fields, status="pending_approval", by="model", brain=self.brain, campaign_id=campaign_id)
        self.created.append(t)
        return t

    @staticmethod
    def _card(t: dict) -> dict:
        return {k: t.get(k) for k in ("id", "title", "status", "platforms", "scheduled_at", "content",
                                      "hashtags", "poster_idea", "image_brief", "media", "media_source", "campaign_id")}

    def t_create_task(self, a):
        t = self._new({**a, "brand": self._brand(a)})
        return {"ok": True, "task": self._card(t), "note": "saved as pending_approval; a human must approve it"}

    def t_draft_post(self, a):
        tags = [("#" + h.lstrip("#")) for h in a.get("hashtags", [])]
        content = a["caption"].rstrip()
        missing = [h for h in tags if h.lower() not in content.lower()]
        if missing:
            content += "\n\n" + " ".join(missing)
        t = self._new({"title": a.get("title") or a["caption"][:60], "content": content, "platforms": a["platforms"],
                       "hashtags": tags, "poster_idea": a.get("poster_idea", ""), "scheduled_at": a.get("scheduled_at"),
                       "image_brief": a.get("image_brief") or {}, "brand": self._brand(a)})
        return {"ok": True, "task": self._card(t), "note": "draft saved as pending_approval; nothing was posted"}

    def t_schedule_task(self, a):
        t = self.store.update(a["task_id"], {"scheduled_at": a["scheduled_at"]}, by="model")
        return {"ok": True, "task": self._card(t)}

    def t_update_task(self, a):
        fields = {k: v for k, v in a.items() if k != "task_id"}
        t = self.store.update(a["task_id"], fields, by="model")
        return {"ok": True, "task": self._card(t)}

    def t_cancel_task(self, a):
        t = self.store.cancel(a["task_id"], by="model")
        return {"ok": True, "task": self._card(t)}

    def t_plan_campaign(self, a):
        # Validate every date first so a bad plan creates nothing.
        posts = []
        for i, p in enumerate(a["posts"]):
            try:
                when = nt.parse_when(p["scheduled_at"])
            except ValueError as e:
                return {"ok": False, "error": f"posts[{i}].scheduled_at: {e}"}
            posts.append((when, p))
        brand = self._brand(a)
        camp = self.store.create_campaign({"name": a["name"], "goal": a.get("goal", ""), "brand": brand,
                                           "start": a.get("start"), "end": a.get("end")}, by="model", brain=self.brain)
        cards = []
        for when, p in sorted(posts, key=lambda x: x[0]):
            tags = [("#" + h.lstrip("#")) for h in p.get("hashtags", [])]
            content = p["caption"].rstrip()
            extra = [h for h in tags if h.lower() not in content.lower()]
            if extra:
                content += "\n\n" + " ".join(extra)
            t = self._new({"title": p.get("title") or p["caption"][:60], "content": content, "platforms": p["platforms"],
                           "hashtags": tags, "poster_idea": p.get("poster_idea", ""), "scheduled_at": when,
                           "image_brief": p.get("image_brief") or {}, "brand": brand}, campaign_id=camp["id"])
            cards.append(self._card(t))
        return {"ok": True, "campaign": camp, "tasks": cards,
                "note": f"{len(cards)} posts saved as pending_approval. Ask the user to review and approve."}

    def _image(self, task_id: str, *, force: bool, edits: Optional[dict]) -> dict:
        import nexus_media
        cur = self.store.get(task_id)
        edits = dict(edits or {})
        if not nexus_media.media_platforms(cur.get("platforms") or []):
            edits["force_platform"] = True
        res = nexus_media.ensure(task_id, store=self.store, by="model", when="tool", force=force, edits=edits or None)
        t = res.get("task") or self.store.get(task_id)
        if res.get("skipped"):
            return {"ok": True, "task": self._card(t), "note": f"no new image: {res.get('reason')}. "
                    "Use regenerate_task_image to change it."}
        return {"ok": True, "task": self._card(t), "image": (t.get("media") or [None])[0], "source": res.get("source"),
                "brief": res.get("brief"), "note": "image attached; the user sees it on the task card before approving"
                + (" (the post went back to pending_approval)" if cur.get("status") == "approved" else "")}

    def t_generate_task_image(self, a):
        return self._image(a["task_id"], force=False, edits={"provider": a["provider"]} if a.get("provider") else None)

    def t_regenerate_task_image(self, a):
        return self._image(a["task_id"], force=True, edits=a.get("edits") or {})

    def t_list_tasks(self, a):
        start = nt.parse_when(a["start"]) if a.get("start") else None
        end = nt.parse_when(a["end"]) if a.get("end") else None
        tasks = self.store.list(status=a.get("status"), brand=a.get("brand"), start=start, end=end)
        return {"ok": True, "count": len(tasks), "tasks": [self._card(t) for t in tasks[: a.get("limit", 30)]]}

    def t_get_calendar(self, a):
        tz = nt.user_tz()
        start = nt.parse_when(a["start"]).replace(hour=0, minute=0) if a.get("start") else nt.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end = nt.parse_when(a["end"]).replace(hour=0, minute=0) + timedelta(days=1) if a.get("end") else start + timedelta(days=30)
        cal = self.store.calendar(start, end, brand=a.get("brand"))
        cal["days"] = {d: [self._card(t) for t in ts] for d, ts in cal["days"].items()}
        return {"ok": True, **cal}

    def t_get_brand_profile(self, a):
        b = self.brand_loader(a.get("brand") or self.brand)
        if not b:
            return {"ok": False, "error": "no brand found; create one on the Brand page"}
        return {"ok": True, "brand": b.get("id"), "profile": b.get("profile") or {}, "brand_info": b.get("brand") or {}}

    def t_research_brand(self, a):
        b = self.brand_loader(a.get("brand") or self.brand)
        if not b:
            return {"ok": False, "error": "no brand found"}
        research = b.get("research") or {}
        summary = {k: research.get(k) for k in ("site", "gbp", "summary") if research.get(k)}
        return {"ok": True, "brand": b.get("id"), "research": _trim(summary, 4000),
                "profile_summary": _trim((b.get("profile") or {}), 2000),
                "note": "Saved research only. To refresh it, the user runs Brand → Research in the app."}


def _trim(obj, limit: int):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return obj if len(s) <= limit else json.loads(json.dumps({"truncated": s[:limit]}))


def load_brand(brand_id: Optional[str] = None) -> dict:
    """Brand bundle from data/brands/<id>/ via brand_research.BrandStore. {} when none."""
    try:
        import brand_research as br
        store = br.BrandStore()
        bid = brand_id or store.active()
        if not bid:
            return {}
        out = {"id": bid}
        for part in ("brand", "profile", "plan", "research"):
            try:
                v = store.read(bid, part)
            except Exception:                              # noqa: BLE001
                v = None
            if v:
                out[part] = v
        return out if len(out) > 1 else {}
    except Exception:                                      # noqa: BLE001
        return {}
