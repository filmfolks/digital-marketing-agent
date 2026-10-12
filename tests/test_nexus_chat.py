"""Agent loop with fake brains: a tidy cloud-like model and a messy local one."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401
import brain_router as br  # noqa: E402
import nexus_actions as na  # noqa: E402
import nexus_chat  # noqa: E402
import nexus_tasks as nt  # noqa: E402

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=nt.user_tz("Asia/Kolkata"))
BRAND = {"id": "burger-co", "brand": {"business_name": "Burger Co", "location": "Bengaluru"},
         "profile": {"voice": "playful", "pillars": [{"name": "Menu"}], "hashtags": ["#burgerco"]}}


def store():
    return nt.TaskStore(Path(tempfile.mkdtemp()), clock=lambda: NOW, log=lambda *a, **k: None)


def plan_args(n=3):
    return {"name": "Burger week", "start": "2026-10-19", "end": "2026-10-25", "posts": [
        {"scheduled_at": f"2026-10-{19 + 2 * i}T19:00", "platforms": ["instagram"], "caption": f"Post {i} 🍔",
         "hashtags": ["burger", "#bengalurufood"], "poster_idea": "burger photo", "title": f"P{i}"} for i in range(n)]}


class ChatTests(unittest.TestCase):
    def run_turn(self, fn, msg="plan 3 Instagram posts for next week for an example burger brand"):
        s = store()
        cfg = br.BrainConfig("fake", fake=fn)
        events = list(nexus_chat.run_turn([], msg, cfg, store=s, brand_loader=lambda b=None: BRAND, now=NOW))
        return s, events

    def test_tidy_brain_plans_campaign_pending_approval(self):
        replies = [{"text": "", "tool_calls": [{"id": "1", "name": "plan_campaign", "arguments": plan_args()}]},
                   {"text": "Planned 3 posts; approve them in Nexus.", "tool_calls": []}]
        s, events = self.run_turn(lambda **k: replies.pop(0))
        tasks = s.list()
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(t["status"] == "pending_approval" for t in tasks))
        self.assertTrue(all(t["brand"] == "burger-co" for t in tasks))
        self.assertTrue(all(t["campaign_id"] for t in tasks))
        self.assertIn("#burger", tasks[0]["content"])           # hashtags appended to the caption
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds.count("task"), 3)
        self.assertEqual(events[-1], {"type": "done", "tasks_created": 3, "steps": 2,
                                      "task_ids": [e["task"]["id"] for e in events if e["type"] == "task"]})

    def test_messy_local_model_is_repaired(self):
        """Hermes-8B-style output: <think>, single quotes, string lists, no closing tag, bad platform first."""
        seen = []
        posts = ", ".join("{'scheduled_at': '2026-10-%dT19:00', 'platform': 'Instagram', 'text': 'Post %d', 'hashtags': '#burger #yum'}" % (19 + i, i)
                          for i in range(3))
        replies = [
            "<think>I should plan.</think>\n<tool_call>\n{'name': 'nexus_plan_campaign', 'arguments': {'name': 'Week', 'posts': [" + posts.replace("Instagram", "tiktok", 1) + "]}}\n",
            "<tool_call>\n{'name': 'plan_campaign', 'arguments': {'name': 'Week', 'posts': [" + posts + "],}}\n</tool_call>",
            "Done! 3 posts await approval.",
        ]

        def fn(messages, tools, schema, mode):
            seen.append(messages[-1]["content"])
            return {"text": replies.pop(0), "tool_calls": []}
        s, events = self.run_turn(fn)
        self.assertEqual(len(s.list(status="pending_approval")), 3)
        self.assertIn("tiktok", seen[1])                          # error fed back to the model
        self.assertTrue(any(e["type"] == "warning" and "repaired" in e["text"] for e in events))
        self.assertEqual(events[-2]["text"], "Done! 3 posts await approval.")

    def test_model_cannot_approve_or_publish(self):
        replies = [{"text": "", "tool_calls": [{"id": "1", "name": "publish_post", "arguments": {"text": "hi"}}]},
                   "<tool_call>{\"name\": \"update_task\", \"arguments\": {\"task_id\": \"t-x\", \"status\": \"approved\"}}</tool_call>",
                   "I can't do that."]
        s, events = self.run_turn(lambda **k: (lambda r: r if isinstance(r, dict) else {"text": r, "tool_calls": []})(replies.pop(0)))
        self.assertEqual(s.list(), [])
        self.assertEqual(events[-2]["text"], "I can't do that.")
        self.assertFalse(any(e["type"] == "tool_call" for e in events))

    def test_duplicate_calls_are_not_executed_twice(self):
        call = {"id": "1", "name": "draft_post", "arguments": {"platforms": ["x"], "caption": "hi"}}
        replies = [{"text": "", "tool_calls": [call, dict(call, id="2")]}, {"text": "ok", "tool_calls": []}]
        s, _ = self.run_turn(lambda **k: replies.pop(0))
        self.assertEqual(len(s.list()), 1)

    def test_system_prompt_has_date_tz_brand(self):
        p = nexus_chat.system_prompt(BRAND, now=NOW)
        self.assertIn("2026-10-13", p)
        self.assertIn("Asia/Kolkata", p)
        self.assertIn("2026-10-19", p)                           # next Monday
        self.assertIn("burger-co", p)
        self.assertNotRegex(p, r"\{(today|timezone|brand_block)\}")

    def test_step_limit(self):
        n = iter(range(100))
        s, events = self.run_turn(lambda **k: {"text": "", "tool_calls": [
            {"id": "x", "name": "list_tasks", "arguments": {"limit": next(n) + 1}}]})
        self.assertTrue(any(e["type"] == "warning" and "stopped" in e["text"] for e in events))


class ActionTests(unittest.TestCase):
    def test_plan_with_bad_date_creates_nothing(self):
        s = store()
        ex = na.Executor(s, brand_loader=lambda b=None: BRAND)
        args = plan_args(2)
        args["posts"][1]["scheduled_at"] = "someday"
        r = ex.run("plan_campaign", args)
        self.assertFalse(r["ok"])
        self.assertEqual(s.list(), [])
        self.assertEqual(s.campaigns(), [])

    def test_calendar_and_brand_tools(self):
        s = store()
        ex = na.Executor(s, brand_loader=lambda b=None: BRAND)
        ex.run("draft_post", {"platforms": ["x"], "caption": "hi", "scheduled_at": "2026-10-14T09:00"})
        cal = ex.run("get_calendar", {"start": "2026-10-01", "end": "2026-10-31"})
        self.assertEqual(list(cal["days"]), ["2026-10-14"])
        self.assertEqual(ex.run("get_brand_profile", {})["brand"], "burger-co")
        self.assertFalse(ex.run("schedule_task", {"task_id": "t-nope", "scheduled_at": "2026-10-14"})["ok"])

    def test_every_schema_is_valid_openai_shape(self):
        for t in na.openai_tools():
            self.assertEqual(t["type"], "function")
            self.assertEqual(t["function"]["parameters"]["type"], "object")
            json.dumps(t)
            self.assertRegex(t["function"]["name"], re.compile(r"^[a-z_]+$"))


if __name__ == "__main__":
    unittest.main()
