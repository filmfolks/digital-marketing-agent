"""Brain router: tolerant parsing, validation + repair loop, per-provider wire formats."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401
import brain_router as br  # noqa: E402
import nexus_actions as na  # noqa: E402

TOOLS = na.openai_tools()
NAMES = list(na.TOOLS)


class ParserTests(unittest.TestCase):
    def test_hermes_tags(self):
        text = 'Sure!\n<tool_call>\n{"name": "list_tasks", "arguments": {"status": "approved"}}\n</tool_call>'
        calls, prose, _ = br.parse_tool_calls(text, NAMES)
        self.assertEqual(calls[0]["name"], "list_tasks")
        self.assertEqual(calls[0]["arguments"], {"status": "approved"})
        self.assertEqual(prose, "Sure!")

    def test_messy_local_model_output(self):
        text = ("<think>The user wants a post. I'll call draft_post.</think>\n"
                "<tool_call>\n{'name': 'draft_post', 'arguments': {'platforms': 'Instagram, twitter', "
                "'caption': \"Burgers!\nBest in town\", 'hashtags': '#burger #food', 'scheduled_at': '2026-10-20T19:00',}}\n")
        calls, _, notes = br.parse_tool_calls(text, NAMES)
        self.assertEqual(len(calls), 1)
        canon, args, errs = na.check_call(calls[0]["name"], calls[0]["arguments"])
        self.assertEqual(errs, [])
        self.assertEqual(args["platforms"], ["instagram", "x"])
        self.assertIn("unclosed-tag", notes)

    def test_arguments_as_string_and_function_wrapper(self):
        text = '<tool_call>{"function": {"name": "get_calendar", "arguments": "{\\"start\\": \\"2026-11-01\\"}"}}</tool_call>'
        calls, _, _ = br.parse_tool_calls(text, NAMES)
        self.assertEqual(calls[0]["arguments"], {"start": "2026-11-01"})

    def test_untagged_fenced_json_and_multiple_calls(self):
        text = 'Here you go:\n```json\n{"name": "list_tasks", "arguments": {}}\n```'
        calls, prose, notes = br.parse_tool_calls(text, NAMES)
        self.assertEqual(calls[0]["name"], "list_tasks")
        self.assertIn("untagged-json", notes)
        two = "<tool_call>{\"name\":\"list_tasks\",\"arguments\":{}}</tool_call><tool_call>{\"name\":\"get_calendar\",\"arguments\":{}}</tool_call>"
        self.assertEqual([c["name"] for c in br.parse_tool_calls(two, NAMES)[0]], ["list_tasks", "get_calendar"])

    def test_plain_answer_has_no_calls(self):
        calls, prose, _ = br.parse_tool_calls("Hello! What brand is this for? {not a call}", NAMES)
        self.assertEqual(calls, [])
        self.assertIn("Hello", prose)

    def test_truncated_json_is_closed(self):
        v, rep = br.loads_lenient('{"name": "list_tasks", "arguments": {"status": "approved"')
        self.assertEqual(v["arguments"]["status"], "approved")
        self.assertIn("unclosed", rep)

    def test_unclosed_think_means_no_answer(self):
        self.assertEqual(br.strip_think("<think>still thinking..."), "")
        self.assertEqual(br.strip_think("reasoning</think>answer"), "answer")


class ChatLoopTests(unittest.TestCase):
    def fake(self, replies):
        seen = []

        def fn(messages, tools, schema, mode):
            seen.append({"messages": messages, "mode": mode})
            r = replies.pop(0)
            return r if isinstance(r, dict) else {"text": r, "tool_calls": []}
        return br.BrainConfig("fake", fake=fn), seen

    def test_invalid_call_is_repaired_with_feedback(self):
        cfg, seen = self.fake([
            '<tool_call>{"name": "draft_post", "arguments": {"platforms": ["tiktok"]}}</tool_call>',
            '<tool_call>{"name": "draft_post", "arguments": {"platforms": ["instagram"], "caption": "Hi"}}</tool_call>'])
        r = br.chat([{"role": "user", "content": "post"}], tools=TOOLS, cfg=cfg)
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(r["tool_calls"][0]["arguments"]["caption"], "Hi")
        feedback = seen[1]["messages"][-1]["content"]
        self.assertIn("caption", feedback)
        self.assertIn("tiktok", feedback)

    def test_gives_up_after_max_repairs(self):
        cfg, _ = self.fake(["<tool_call>{broken</tool_call>"] * 3)
        r = br.chat([{"role": "user", "content": "x"}], tools=TOOLS, cfg=cfg)
        self.assertTrue(r["failed_validation"])
        self.assertEqual(r["tool_calls"], [])
        self.assertEqual(r["attempts"], 3)

    def test_json_mode_validates_schema(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
        cfg, _ = self.fake(['```json\n{"n": "3",}\n```'])
        r = br.chat([{"role": "user", "content": "x"}], schema=schema, cfg=cfg)
        self.assertEqual(r["data"], {"n": 3})
        self.assertEqual(r["mode"], "json")


class WireFormatTests(unittest.TestCase):
    def test_openai_native_tools_payload_and_parse(self):
        captured = {}

        def post(url, payload, headers, timeout):
            captured.update(url=url, payload=payload)
            return 200, {"model": "hermes-3", "choices": [{"message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "list_tasks", "arguments": "{\"limit\": \"5\"}"}}]}}]}
        cfg = br.BrainConfig("lmstudio", model="hermes-3-llama-3.1-8b")
        r = br.chat([{"role": "user", "content": "what is planned?"}], tools=TOOLS, cfg=cfg, post=post)
        self.assertEqual(captured["url"], "http://localhost:1234/v1/chat/completions")
        self.assertEqual(len(captured["payload"]["tools"]), len(TOOLS))
        self.assertEqual(r["tool_calls"][0]["arguments"], {"limit": 5})
        self.assertEqual(r["mode"], "native")

    def test_lmstudio_text_tool_call_is_parsed(self):
        def post(url, payload, headers, timeout):
            return 200, {"choices": [{"message": {"content": '<tool_call>\n{"name": "list_tasks", "arguments": {}}\n</tool_call>'}}]}
        r = br.chat([{"role": "user", "content": "x"}], tools=TOOLS, cfg=br.BrainConfig("lmstudio", model="m"), post=post)
        self.assertEqual(r["tool_calls"][0]["name"], "list_tasks")

    def test_ollama_without_tool_support_switches_to_hermes_prompt(self):
        calls = []

        def post(url, payload, headers, timeout):
            calls.append(payload)
            if "tools" in payload:
                return 400, {"error": {"message": "registry.ollama.ai/library/x does not support tools"}}
            return 200, {"choices": [{"message": {"content": '<tool_call>{"name":"list_tasks","arguments":{}}</tool_call>'}}]}
        r = br.chat([{"role": "user", "content": "x"}], tools=TOOLS, cfg=br.BrainConfig("ollama", model="tiny"), post=post)
        self.assertEqual(r["mode"], "hermes")
        self.assertIn("<tools>", calls[1]["messages"][0]["content"])
        self.assertEqual(calls[0]["model"], "tiny")
        self.assertTrue(r["mode_switches"])

    def test_hermes_history_conversion(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "list_tasks", "arguments": {}}]},
                {"role": "tool", "tool_call_id": "1", "name": "list_tasks", "content": json.dumps({"ok": True})}]
        out = br.to_hermes_messages(msgs, TOOLS)
        self.assertTrue(out[0]["content"].startswith("S"))
        self.assertIn("<tool_call>", out[2]["content"])
        self.assertEqual(out[3]["role"], "user")
        self.assertIn("<tool_response>", out[3]["content"])

    def test_cloud_without_key_fails_clearly(self):
        import os
        os.environ.pop("OPENAI_API_KEY", None)
        with self.assertRaises(br.BrainError):
            br.chat([{"role": "user", "content": "x"}], cfg=br.BrainConfig("openai"), post=lambda *a: (200, {}))

    def test_unknown_brain(self):
        with self.assertRaises(br.BrainError):
            br.BrainConfig("gpt-9000")


if __name__ == "__main__":
    unittest.main()
