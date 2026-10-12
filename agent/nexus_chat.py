#!/usr/bin/env python3
"""
Nexus instruction chat — the agent loop
=======================================
User message → brain (any provider, via brain_router) → validated tool calls →
nexus_actions.Executor → tool results back to the brain → final answer.

Yields events so the relay can stream them as SSE:

    {"type": "meta", "brain": "lmstudio:hermes-3...", "mode": "native"}
    {"type": "tool_call", "name": "draft_post", "arguments": {...}}
    {"type": "tool_result", "name": "draft_post", "result": {...}}
    {"type": "task", "task": {...}}           one per task created (cards in the UI)
    {"type": "text", "text": "..."}           the brain's answer
    {"type": "warning", "text": "..."}        e.g. output needed repairs / failed validation
    {"type": "done", "tasks_created": n, "steps": k}

Nothing here can publish: the tool set has no publish/approve tool.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Callable, Iterator, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import brain_router as br  # noqa: E402
import nexus_actions as na  # noqa: E402
import nexus_tasks as nt  # noqa: E402

SKILL_DIR = ROOT / "skills" / "nexus"
MAX_STEPS = 6


def system_prompt(brand: Optional[dict] = None, *, now=None) -> str:
    tmpl_path = SKILL_DIR / "system_prompt.md"
    try:
        tmpl = tmpl_path.read_text(encoding="utf-8")
    except OSError:
        tmpl = "You are Nexus, a marketing assistant. Today is {today} ({weekday}), timezone {timezone}.\n{brand_block}"
    now = now or nt.now()
    brand_block = "No brand profile is loaded. Ask for the brand name if it matters."
    if brand:
        p = brand.get("profile") or {}
        b = brand.get("brand") or {}
        bits = {"brand_id": brand.get("id"), "name": b.get("business_name") or p.get("business_name") or brand.get("id"),
                "voice": p.get("voice") or p.get("tone"), "audience": p.get("audience") or p.get("target_audience"),
                "pillars": [x.get("name") if isinstance(x, dict) else x for x in (p.get("pillars") or [])][:6],
                "hashtags": (p.get("hashtags") or [])[:12] if isinstance(p.get("hashtags"), list) else p.get("hashtags"),
                "location": b.get("location") or p.get("location")}
        brand_block = "Active brand (use it unless the user names another):\n" + json.dumps(
            {k: v for k, v in bits.items() if v}, ensure_ascii=False)
    next_monday = (now + timedelta(days=(7 - now.weekday()) or 7)).date().isoformat()
    return (tmpl.replace("{today}", now.date().isoformat()).replace("{weekday}", now.strftime("%A"))
            .replace("{now}", now.strftime("%Y-%m-%dT%H:%M")).replace("{timezone}", nt.tz_name())
            .replace("{next_monday}", next_monday).replace("{brand_block}", brand_block))


def run_turn(history: List[dict], user_message: str, cfg: br.BrainConfig, *,
             store: Optional[nt.TaskStore] = None, brand_id: Optional[str] = None,
             brand_loader: Callable = na.load_brand, max_steps: int = MAX_STEPS,
             post: Callable = br.http_post_json, now=None) -> Iterator[dict]:
    """Run one user turn. ``history`` = earlier neutral messages (no system)."""
    brand = brand_loader(brand_id) or {}
    ex = na.Executor(store, brain=cfg.label(), brand=brand.get("id") or brand_id, brand_loader=brand_loader)
    messages = [{"role": "system", "content": system_prompt(brand, now=now)}] + \
               [m for m in history if m.get("role") in ("user", "assistant")] + \
               [{"role": "user", "content": user_message}]
    tools = na.openai_tools()
    yield {"type": "meta", "brain": cfg.label(), "provider": cfg.provider}
    steps = 0
    seen = set()
    final_text = ""
    while steps < max_steps:
        steps += 1
        reply = br.chat(messages, tools=tools, cfg=cfg, post=post)
        if steps == 1:
            yield {"type": "meta", "brain": cfg.label(), "model": reply.get("model"), "mode": reply.get("mode")}
        if reply.get("repairs"):
            yield {"type": "warning", "text": "repaired model output: " + ", ".join(sorted(set(reply["repairs"])))}
        if reply.get("failed_validation"):
            yield {"type": "warning", "text": "the model's tool calls stayed invalid after retries: "
                   + "; ".join(reply["errors"][-1])[:600]}
        calls = reply.get("tool_calls") or []
        if not calls:
            final_text = reply.get("text") or ""
            break
        messages.append({"role": "assistant", "content": reply.get("text") or "", "tool_calls": calls})
        for c in calls:
            key = (c["name"], json.dumps(c["arguments"], sort_keys=True))
            if key in seen:                                 # small models love to repeat themselves
                result = {"ok": False, "error": "duplicate call ignored; this exact call already ran"}
            else:
                seen.add(key)
                yield {"type": "tool_call", "name": c["name"], "arguments": c["arguments"]}
                before = len(ex.created)
                result = ex.run(c["name"], c["arguments"])
                for t in ex.created[before:]:
                    yield {"type": "task", "task": t}
            yield {"type": "tool_result", "name": c["name"], "result": result}
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                             "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
        if reply.get("text"):
            final_text = reply["text"]
    else:
        yield {"type": "warning", "text": f"stopped after {max_steps} steps"}
    if not final_text and ex.created:
        final_text = f"I saved {len(ex.created)} task(s) as pending approval. Review them below and approve the ones you want posted."
    yield {"type": "text", "text": final_text}
    yield {"type": "done", "tasks_created": len(ex.created), "steps": steps,
           "task_ids": [t["id"] for t in ex.created]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Talk to Nexus from the terminal (creates pending_approval tasks only)")
    ap.add_argument("message")
    ap.add_argument("--brain", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--mode", default="auto", choices=["auto", "native", "hermes", "json"])
    ap.add_argument("--brand", default=None)
    a = ap.parse_args(argv)
    cfg = br.BrainConfig(a.brain, a.model, a.url, mode=a.mode, session_id="nexus-cli")
    for ev in run_turn([], a.message, cfg, brand_id=a.brand):
        print(json.dumps(ev, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
