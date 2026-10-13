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
   niche + local SEO), `poster_idea`, `image_brief`, `title`.
4. `plan_campaign` with all posts.
5. Reply in 1–3 lines: count, date range, "awaiting your approval in Nexus".

## Procedure: one post
`get_brand_profile` if needed → `draft_post` → short confirmation.

## Images: step by step (small/local models: follow exactly)
1. Every `draft_post` / `plan_campaign` post gets an `image_brief`:
   `{"headline": "<max 8 words>", "subline": "<one line>", "cta": "<2-3 words>", "style": "<look, colours>", "keywords": ["<word>", "<word>"]}`
2. You do NOT need to make the picture yourself. Nexus attaches one automatically when the task is
   created, again at approval, and at the latest right before posting.
3. Platform media rules:

   | Platform | Image | Size Nexus renders |
   |---|---|---|
   | instagram | **required** | 1080×1350 (4:5) — 1080×1080 also valid |
   | facebook | preferred | 1200×630 |
   | linkedin | preferred | 1200×627 |
   | x | optional (added when another platform needs one) | 1600×900 |
   | youtube, blog | not handled here | — |

4. The user wants the picture now → `generate_task_image {"task_id": "..."}`.
5. The user wants it changed → `regenerate_task_image {"task_id": "...", "edits": {...}}` with ONLY the changes:
   - text: `headline`, `subline`, `cta`
   - colours (#RRGGBB): `bg` (background), `bg2` (gradient), `fg` (text), `accent` (button)
   - look: `style`; photo search words: `keywords`
   - source: `provider` = `stock` (photo + text), `card` (offline brand card), `openai`, `gemini`, `xai`, `canva`
   Example: "make it green and say 'Try it free'" →
   `{"task_id": "t-…", "edits": {"bg": "#14532D", "cta": "Try it free"}}`
6. Regenerating the image of an APPROVED post sends it back to `pending_approval`: say so.
7. Brand palette: if `get_brand_profile` → `brand_info.palette_strict` is true, use ONLY `brand_info.colors.allowed` in `image_brief`, `poster_idea` and any colour edit (Thosho Tech: blue #1E5EFF, greys #2B2F36/#6B7280/#E5E7EB, white #FFFFFF; never other accents). Nexus snaps foreign colours to the nearest brand colour.
8. Never claim a picture exists unless the tool result shows `"image"` / `"media"`.

## Files
- `system_prompt.md` — the system prompt Nexus itself uses for local models (fill-ins: date, tz, brand).
- `tools/<tool>.md` — one card per tool: when to use, steps, JSON Schema, example call.
- `schemas/<tool>.json`, `schemas/all_tools.json` — OpenAI/Hermes tool definitions.
- `examples/few_shot.md` — complete conversations in Hermes `<tool_call>` format.
