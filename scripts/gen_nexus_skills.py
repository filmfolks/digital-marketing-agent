#!/usr/bin/env python3
"""Regenerate skills/nexus/{schemas,tools}/ from agent/nexus_actions.py.

    python3 scripts/gen_nexus_skills.py          # write
    python3 scripts/gen_nexus_skills.py --check  # exit 1 if out of date (used by the tests)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import nexus_actions as na  # noqa: E402

OUT = ROOT / "skills" / "nexus"

GUIDE = {
    "draft_post": {
        "when": "The user wants ONE post (\"write a post\", \"tweet about…\", \"an Instagram post for Friday\").",
        "steps": ["If the brand voice is unknown, call get_brand_profile first.",
                  "Pick the platform(s) the user named; default to the brand's main platform.",
                  "Write the caption for that platform (X ≤ 280 chars incl. hashtags).",
                  "Add 3–8 hashtags: broad + niche + local SEO.",
                  "Add a one-sentence poster_idea.",
                  "Set scheduled_at only if the user gave a day/time or asked to schedule it.",
                  "Call draft_post once. Tell the user it awaits approval."],
        "example": {"platforms": ["instagram"], "caption": "Double smash, double cheese, zero regrets. Our new Midnight Melt drops Friday — who's in? 🍔",
                    "hashtags": ["#burger", "#smashburger", "#bangalorefood", "#midnightmelt"],
                    "poster_idea": "Close-up of the burger with dripping cheese on a dark background, neon text 'MIDNIGHT MELT'.",
                    "scheduled_at": "2026-10-23T19:00"},
    },
    "plan_campaign": {
        "when": "Several posts: \"plan the month\", \"3 posts for next week\", \"a launch campaign\".",
        "steps": ["Call research_brand (or get_brand_profile) once if you have not seen the brand yet.",
                  "Decide the date range (whole month = first to last day; next week = Monday–Sunday).",
                  "Spread posts evenly; no two posts on one platform at the same time.",
                  "For EACH post: scheduled_at, platforms, caption, hashtags, poster_idea, short title.",
                  "Put ALL posts in ONE plan_campaign call (max 62).",
                  "Answer with a 1–3 line summary; the user approves on the calendar."],
        "example": {"name": "Burger week", "goal": "Launch the Midnight Melt", "start": "2026-10-19", "end": "2026-10-25",
                    "posts": [{"scheduled_at": "2026-10-19T19:00", "platforms": ["instagram"], "title": "Teaser",
                               "caption": "Something cheesy is coming Friday… 👀", "hashtags": ["#burger", "#comingsoon"],
                               "poster_idea": "Silhouette of a burger under a sheet, 'FRIDAY' in neon."},
                              {"scheduled_at": "2026-10-23T19:00", "platforms": ["instagram", "facebook"], "title": "Launch",
                               "caption": "Midnight Melt is HERE. Double smash, double cheese. Tag your burger buddy!",
                               "hashtags": ["#smashburger", "#newlaunch"], "poster_idea": "Hero shot with cheese pull."}]},
    },
    "create_task": {
        "when": "A to-do or reminder that is not a finished post (\"remind me to shoot reels Tuesday\").",
        "steps": ["Write a short title and the instruction.", "Add scheduled_at if a time was given.", "Call create_task."],
        "example": {"title": "Shoot 3 reels in the kitchen", "instruction": "Film burger assembly from top view", "scheduled_at": "2026-10-20T11:00"},
    },
    "schedule_task": {
        "when": "Move a task to another date/time.",
        "steps": ["Find the task_id (list_tasks or get_calendar if you do not have it).", "Call schedule_task with the new ISO time."],
        "example": {"task_id": "t-20261013-ab12cd34", "scheduled_at": "2026-10-21T18:30"},
    },
    "list_tasks": {
        "when": "The user asks what is planned / pending / done.",
        "steps": ["Map the request to a status filter (pending_approval, approved, done…).", "Call list_tasks.", "Summarise briefly."],
        "example": {"status": "pending_approval", "limit": 20},
    },
    "update_task": {
        "when": "Change text, hashtags, platforms or the poster idea of a task.",
        "steps": ["Get the task_id.", "Send only the fields that change.", "Mention that an approved post goes back to approval after edits."],
        "example": {"task_id": "t-20261013-ab12cd34", "content": "New caption text", "hashtags": ["#burger"]},
    },
    "cancel_task": {
        "when": "Delete / cancel / drop a planned task.",
        "steps": ["Get the task_id.", "Call cancel_task with a short reason."],
        "example": {"task_id": "t-20261013-ab12cd34", "reason": "Event moved"},
    },
    "get_brand_profile": {
        "when": "You need voice, audience, pillars or hashtags before writing.",
        "steps": ["Call with no arguments for the active brand, or brand=<id>."],
        "example": {},
    },
    "get_calendar": {
        "when": "The user asks what is on the calendar, or you need free slots before planning.",
        "steps": ["Pass start/end as YYYY-MM-DD (default: next 30 days)."],
        "example": {"start": "2026-11-01", "end": "2026-11-30"},
    },
    "research_brand": {
        "when": "Before planning a month or when the user asks what we know about the brand.",
        "steps": ["Call it once; use the result for pillars, offers, local keywords."],
        "example": {},
    },
}


def render_card(name: str) -> str:
    t, g = na.TOOLS[name], GUIDE[name]
    lines = [f"# Tool: `{name}`", "", t["description"], "", "## When to use", g["when"], "", "## Steps"]
    lines += [f"{i}. {s}" for i, s in enumerate(g["steps"], 1)]
    lines += ["", "## Arguments (JSON Schema)", "```json", json.dumps(t["parameters"], indent=2, ensure_ascii=False), "```",
              "", "## Example call", "```", "<tool_call>",
              json.dumps({"name": name, "arguments": g["example"]}, ensure_ascii=False), "</tool_call>", "```", "",
              "## Never", "- Never say it was posted: approval and posting happen in the app.",
              "- Never invent task ids; get them from list_tasks/get_calendar.", ""]
    return "\n".join(lines)


def files() -> dict:
    out = {}
    for name in na.TOOLS:
        assert name in GUIDE, f"missing GUIDE for {name}"
        ok = na.validate(na.coerce(GUIDE[name]["example"], na.TOOLS[name]["parameters"]), na.TOOLS[name]["parameters"])
        assert not ok, f"example for {name} is invalid: {ok}"
        out[OUT / "schemas" / f"{name}.json"] = json.dumps({"type": "function", "function": {
            "name": name, "description": na.TOOLS[name]["description"], "parameters": na.TOOLS[name]["parameters"]}},
            indent=2, ensure_ascii=False) + "\n"
        out[OUT / "tools" / f"{name}.md"] = render_card(name)
    out[OUT / "schemas" / "all_tools.json"] = json.dumps(na.openai_tools(), indent=2, ensure_ascii=False) + "\n"
    return out


def main(argv=None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    stale = []
    for path, text in files().items():
        cur = path.read_text(encoding="utf-8") if path.exists() else None
        if cur != text:
            stale.append(str(path.relative_to(ROOT)))
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    if check and stale:
        print("out of date: " + ", ".join(stale) + " (run python3 scripts/gen_nexus_skills.py)")
        return 1
    print(("up to date" if check else f"wrote {len(stale)} file(s)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
