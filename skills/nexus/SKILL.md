---
name: nexus
description: Plan, draft and schedule marketing posts in Nexus.
version: 0.1.0
author: Nexus (digital-marketing-agent)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Marketing, Social-Media, Calendar, Scheduling]
    related_skills: []
---

# Nexus marketing planner

Use this skill when the user asks to write, plan, schedule, list, move or cancel social
media posts or marketing tasks for a brand (Instagram, X, Facebook, LinkedIn, YouTube, blog),
including "plan the whole month" campaign requests.

The tools come from the **nexus-tasks** MCP server (`agent/nexus_tasks_mcp.py`). In Hermes Agent
they appear as `mcp_nexus_<tool>` (e.g. `mcp_nexus_draft_post`).

## Golden rules
1. Nothing is ever posted by you. Every post you create is saved as `pending_approval`;
   the human approves it in the Nexus app and the Nexus scheduler posts it at its time.
2. One post → `draft_post`. Several posts / a week / a month → ONE `plan_campaign` call.
3. Dates are ISO 8601 local time (`2026-10-20T18:30`), never in the past.
4. Platforms: `x`, `instagram`, `facebook`, `linkedin`, `youtube`, `blog`.
5. Call `get_brand_profile` (or `research_brand` before a monthly plan) when the voice is unknown.
6. Never invent task ids: use `list_tasks` / `get_calendar`.

## Procedure: "plan N posts" / "plan the month"
1. `research_brand` (once per conversation).
2. Pick dates: spread evenly, good local times (IG 12:00–13:00 / 18:00–21:00, X 09:00 / 17:00).
3. For each post write `caption` (platform-native, hook first, CTA), `hashtags` (3–8: broad +
   niche + local SEO), `poster_idea`, `title`.
4. `plan_campaign` with all posts.
5. Reply in 1–3 lines: count, date range, "awaiting your approval in Nexus".

## Procedure: one post
`get_brand_profile` if needed → `draft_post` → short confirmation.

## Files
- `system_prompt.md` — the system prompt Nexus itself uses for local models (fill-ins: date, tz, brand).
- `tools/<tool>.md` — one card per tool: when to use, steps, JSON Schema, example call.
- `schemas/<tool>.json`, `schemas/all_tools.json` — OpenAI/Hermes tool definitions.
- `examples/few_shot.md` — complete conversations in Hermes `<tool_call>` format.
