# Few-shot examples (Hermes format)

These are real, schema-valid exchanges. Paste one or two into a system prompt when a small
model (≤ 8B) struggles; `tests/test_nexus_skills.py` checks that every tool call in this file validates.

## 1. One post
**user:** Write an Instagram post for Friday 7pm about our new Midnight Melt burger.

**assistant:**
<tool_call>
{"name": "draft_post", "arguments": {"platforms": ["instagram"], "caption": "Double smash, double cheese, zero regrets. The Midnight Melt drops Friday 🍔 Tag the friend you're bringing!", "hashtags": ["#burger", "#smashburger", "#midnightmelt", "#foodiesofinstagram"], "poster_idea": "Cheese-pull close-up on a dark background with neon 'MIDNIGHT MELT' text.", "scheduled_at": "2026-10-23T19:00", "title": "Midnight Melt launch"}}
</tool_call>

**tool:**
<tool_response>
{"name": "draft_post", "content": {"ok": true, "task": {"id": "t-20261013-1a2b3c4d", "status": "pending_approval", "scheduled_at": "2026-10-23T19:00:00+05:30"}}}
</tool_response>

**assistant:** Drafted the Instagram post for Fri 23 Oct, 7:00 PM. It's waiting for your approval in Nexus — nothing has been posted.

## 2. Several posts → one plan_campaign
**user:** plan 3 Instagram posts for next week for an example burger brand

**assistant:**
<tool_call>
{"name": "plan_campaign", "arguments": {"name": "Burger week", "goal": "Drive weekday footfall", "start": "2026-10-19", "end": "2026-10-25", "posts": [{"scheduled_at": "2026-10-19T12:30", "platforms": ["instagram"], "title": "Monday lunch", "caption": "Monday needs a smash burger. Lunch combo till 3pm 🍔", "hashtags": ["#burger", "#lunchdeal", "#mondaymood"], "poster_idea": "Burger + fries tray on a wooden table, bold 'LUNCH COMBO' text."}, {"scheduled_at": "2026-10-21T19:00", "platforms": ["instagram"], "title": "Behind the grill", "caption": "60 seconds on a 250°C griddle = that crust. Come watch the smash 🔥", "hashtags": ["#smashburger", "#behindthescenes", "#burgerlover"], "poster_idea": "Reel: the press smashing a patty, sizzling sound."}, {"scheduled_at": "2026-10-24T19:30", "platforms": ["instagram"], "title": "Weekend tag", "caption": "Weekend plans: burgers with the crew. Tag who's paying 😏", "hashtags": ["#weekendvibes", "#burger", "#foodie"], "poster_idea": "Four friends cheersing burgers, warm evening light."}]}}
</tool_call>

**tool:**
<tool_response>
{"name": "plan_campaign", "content": {"ok": true, "campaign": {"id": "c-20261013-abc123"}, "tasks": [{"id": "t-1"}, {"id": "t-2"}, {"id": "t-3"}]}}
</tool_response>

**assistant:** Planned 3 Instagram posts (Mon 19, Wed 21, Sat 24 Oct). All are pending your approval — approve them in Chat or the Task Calendar.

## 3. Moving a post
**user:** move the Wednesday post to Thursday 6pm

**assistant:**
<tool_call>
{"name": "list_tasks", "arguments": {"status": "pending_approval", "limit": 20}}
</tool_call>

**tool:**
<tool_response>
{"name": "list_tasks", "content": {"ok": true, "tasks": [{"id": "t-20261013-2b3c4d5e", "title": "Behind the grill", "scheduled_at": "2026-10-21T19:00:00+05:30"}]}}
</tool_response>

**assistant:**
<tool_call>
{"name": "schedule_task", "arguments": {"task_id": "t-20261013-2b3c4d5e", "scheduled_at": "2026-10-22T18:00"}}
</tool_call>
