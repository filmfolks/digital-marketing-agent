"""Antigravity brain bridge: discovery, project registration, conversations,
transcript streaming and the relay SSE endpoint — all with mocked subprocesses."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

ag = load_agent("antigravity_bridge")

PS = """  545 /Applications/Antigravity.app/Contents/Resources/bin/language_server --standalone --https_server_port 0 --csrf_token abc-123 --app_data_dir antigravity
  777 /usr/bin/python3 something_else
"""
LSOF = """COMMAND   PID  USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
language_ 545 apple    6u  IPv4 0x1      0t0  TCP 127.0.0.1:49155 (LISTEN)
language_ 545 apple    7u  IPv4 0x2      0t0  TCP 127.0.0.1:49156 (LISTEN)
"""


def step(i, typ, content="", tool_calls=None, status="DONE"):
    d = {"step_index": i, "type": typ, "status": status, "content": content}
    if tool_calls:
        d["tool_calls"] = tool_calls
    return json.dumps(d) + "\n"


class FakeCLI:
    """Stands in for subprocess.run(agentapi ...); writes transcript lines like Antigravity."""

    def __init__(self, case, reply="pong", fail_first_port=False):
        self.case, self.reply, self.calls, self.fail_first_port = case, reply, [], fail_first_port
        self.n = 0

    def __call__(self, args, **kw):
        self.calls.append((args, kw["env"]))
        if self.fail_first_port and kw["env"]["ANTIGRAVITY_LS_ADDRESS"].endswith("49155"):
            return subprocess.CompletedProcess(args, 1, json.dumps({"response": {}, "error": "dial tcp: connection refused"}), "")
        cmd = args[args.index("agentapi") + 1] if "agentapi" in args else args[1]
        if cmd == "new-conversation":
            self.n += 1
            cid = f"conv-{self.n}"
            self.case.write(cid, step(0, "USER_INPUT", args[-1]) + step(1, "PLANNER_RESPONSE", self.reply))
            out = {"response": {"newConversation": {"conversationId": cid}}}
        else:
            cid = args[-2]
            self.case.write(cid, step(5, "USER_INPUT", args[-1]) + step(6, "PLANNER_RESPONSE", self.reply.upper()))
            out = {"response": {}}
        return subprocess.CompletedProcess(args, 0, json.dumps(out), "")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            "NEXUS_GEMINI_HOME": str(t / "gemini"), "NEXUS_ANTIGRAVITY_DIR": str(t / "state"),
            "NEXUS_ANTIGRAVITY_FOLDER": str(t / "nexus"), "NEXUS_ACTIVITY_LOG": str(t / "activity.jsonl"),
            "ANTIGRAVITY_CONVERSATION_ID": "foreign", "ANTIGRAVITY_PROJECT_ID": "foreign", "SECRET_X": "nope"})
        self.env.start()
        (t / "nexus").mkdir()
        (t / "gemini" / "config" / "projects").mkdir(parents=True)
        self.patches = [mock.patch.object(ag, "find_language_servers", lambda ps_output=None: ag.__dict__["_real_find"](PS)),
                        mock.patch.object(ag, "listening_ports", lambda pid, lsof_output=None: ag.__dict__["_real_ports"](pid, LSOF)),
                        mock.patch.object(ag, "cli_path", lambda: ["/fake/language_server", "agentapi"])]
        for p in self.patches:
            p.start()
        ag._good_address.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def write(self, cid, text):
        p = ag.transcript_path(cid)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(text)

    def stream(self, msg, sid, runner, **kw):
        return list(ag.chat_stream(msg, sid, runner=runner, settle=0, poll=0.01, timeout=5, **kw))


ag._real_find = ag.find_language_servers
ag._real_ports = ag.listening_ports


class DiscoveryTests(Base):
    def test_parse_ps_and_lsof(self):
        self.assertEqual(ag._real_find(PS), [(545, "abc-123")])
        self.assertEqual(ag._real_ports(545, LSOF), [49155, 49156])

    def test_not_running(self):
        with mock.patch.object(ag, "find_language_servers", lambda ps_output=None: []):
            with self.assertRaisesRegex(ag.AntigravityError, "not running"):
                ag.discover()
            self.assertFalse(ag.status()["available"])

    def test_sanitized_env(self):
        env = ag.sanitized_env("localhost:1", "tok", "pid")
        self.assertNotIn("SECRET_X", env)
        self.assertNotIn("ANTIGRAVITY_CONVERSATION_ID", env)
        self.assertEqual(env["ANTIGRAVITY_PROJECT_ID"], "pid")
        self.assertTrue(set(env) <= {"PATH", "HOME", "USER", "LANG", "ANTIGRAVITY_LS_ADDRESS",
                                     "ANTIGRAVITY_CSRF_TOKEN", "ANTIGRAVITY_PROJECT_ID"})


class ProjectTests(Base):
    def test_register_once_and_reuse(self):
        a = ag.ensure_project()
        b = ag.ensure_project()
        self.assertEqual(a, b)
        files = list(ag.projects_dir().glob("*.json"))
        self.assertEqual(len(files), 1)
        d = json.loads(files[0].read_text())
        self.assertEqual(d["name"], "Nexus")
        self.assertEqual(d["projectResources"]["resources"][0]["folderUri"], "file://" + str(ag.nexus_folder()))
        self.assertEqual(d["permissionGrants"], {"permissionGrants": {"allow": []}})
        self.assertIs(d["isWorkspaceOnly"], False)

    def test_reuses_existing_project_for_folder(self):
        (ag.projects_dir() / "existing.json").write_text(json.dumps({
            "id": "existing", "name": "X", "projectResources": {"resources": [{"folderUri": "file://" + str(ag.nexus_folder())}]}}))
        self.assertEqual(ag.ensure_project(), "existing")
        self.assertEqual(len(list(ag.projects_dir().glob("*.json"))), 1)


class ChatTests(Base):
    def test_new_then_follow_up(self):
        cli = FakeCLI(self)
        ev = self.stream("Reply with the single word: pong", "s1", cli, model="flash")
        self.assertEqual(ev[0]["type"], "meta")
        self.assertTrue(ev[0]["new"])
        self.assertEqual([e["text"] for e in ev if e["type"] == "text"], ["pong"])
        self.assertEqual(ev[-1]["type"], "done")
        args, env = cli.calls[0]
        self.assertIn("new-conversation", args)
        self.assertIn("--model=flash", args)
        self.assertTrue(any(a.startswith("--title=") and "\n" not in a for a in args))
        self.assertEqual(env["ANTIGRAVITY_CSRF_TOKEN"], "abc-123")
        self.assertNotEqual(env["ANTIGRAVITY_PROJECT_ID"], "foreign")
        ev2 = self.stream("again", "s1", cli)
        self.assertFalse(ev2[0]["new"])
        self.assertEqual(ev2[0]["conversation_id"], "conv-1")
        self.assertEqual([e["text"] for e in ev2 if e["type"] == "text"], ["PONG"])   # old steps skipped
        self.assertIn("send-message", cli.calls[1][0])
        log = [json.loads(l) for l in Path(os.environ["NEXUS_ACTIVITY_LOG"]).read_text().splitlines()]
        self.assertEqual([l["status"] for l in log], ["ok", "ok"])
        self.assertEqual(log[0]["conversation_id"], "conv-1")
        self.assertEqual(log[0]["brain"], "antigravity")

    def test_port_fallback(self):
        cli = FakeCLI(self, fail_first_port=True)
        ev = self.stream("hi", "s2", cli)
        self.assertEqual(ev[-1]["type"], "done")
        self.assertEqual(ag._good_address["abc-123"], "localhost:49156")

    def test_concurrent_same_session_one_conversation(self):
        cli = FakeCLI(self)
        out = []
        ths = [threading.Thread(target=lambda: out.append(self.stream("hi", "dup", cli))) for _ in range(3)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        news = [c for c, _ in cli.calls if "new-conversation" in c]
        self.assertEqual(len(news), 1)
        self.assertEqual(len(out), 3)

    def test_auth_error_is_clear(self):
        def runner(args, **kw):
            return subprocess.CompletedProcess(args, 1, json.dumps({"error": "UNAUTHENTICATED: please sign in"}), "")
        with self.assertRaisesRegex(ag.AntigravityError, "signed in"):
            self.stream("hi", "s3", runner)
        log = Path(os.environ["NEXUS_ACTIVITY_LOG"]).read_text()
        self.assertIn('"failed"', log)

    def test_timeout(self):
        def runner(args, **kw):
            return subprocess.CompletedProcess(args, 0, json.dumps({"response": {"newConversation": {"conversationId": "silent"}}}), "")
        with self.assertRaises(TimeoutError):
            list(ag.chat_stream("hi", "s4", runner=runner, timeout=0.2, poll=0.01))

    def test_validation(self):
        with self.assertRaises(ValueError):
            list(ag.chat_stream("", "s"))
        with self.assertRaises(ValueError):
            list(ag.chat_stream("x", "bad id!"))


class TranscriptTests(Base):
    def test_tool_calls_then_final(self):
        cid = "t1"
        self.write(cid, step(0, "USER_INPUT", "q")
                   + step(1, "PLANNER_RESPONSE", "", tool_calls=[{"name": "search_web"}])
                   + step(2, "SEARCH_WEB")
                   + step(3, "PLANNER_RESPONSE", "Thinking", status="RUNNING")
                   + step(4, "PLANNER_RESPONSE", 'Answer.\n```json\n{"name": "x", "args": {}}\n```\nDone.'))
        ev = list(ag.tail_answer(cid, 0, settle=0, poll=0.01, timeout=2))
        texts = [e["text"] for e in ev if e["type"] == "text"]
        self.assertEqual(texts, ["Answer.\n\nDone."])

    def test_checkpoint_after_answer_does_not_block_completion(self):
        cid = "t3"
        self.write(cid, step(0, "USER_INPUT", "q") + step(1, "CONVERSATION_HISTORY", "h")
                   + step(2, "PLANNER_RESPONSE", "pong"))
        t = [0.0]

        def sleep(_):
            t[0] += 1.0
            if t[0] == 1.0:
                self.write(cid, step(3, "CHECKPOINT", "{{ CHECKPOINT 0 }}"))
        ev = list(ag.tail_answer(cid, 0, settle=2, poll=1, timeout=30, sleep=sleep, clock=lambda: t[0]))
        self.assertEqual(ev[-1], {"type": "done"})
        self.assertLess(t[0], 5)

    def test_partial_line_waits(self):
        cid = "t2"
        p = ag.transcript_path(cid); p.parent.mkdir(parents=True)
        p.write_text(step(1, "PLANNER_RESPONSE", "ok")[:-10])
        steps, off = ag.read_steps(p)
        self.assertEqual((steps, off), ([], 0))

    def test_clean_prose_inline_json(self):
        self.assertEqual(ag.clean_prose('Hi\n{"name": "t", "args": {}}\nBye'), "Hi\nBye")


class RelaySSETests(unittest.TestCase):
    TOKEN = "test-token-abcdefghijklmnop"

    @classmethod
    def setUpClass(cls):
        cls.relay = load_agent("nexus_relay")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.relay.Handler)
        cls.server.token = cls.TOKEN
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def post(self, body, token=TOKEN):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}/antigravity/chat",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"X-Nexus-Token": token} if token else {})})
        return urllib.request.urlopen(req, timeout=10)

    def test_streams_events(self):
        def fake(msg, sid, model=None):
            yield {"type": "meta", "conversation_id": "c9", "new": True}
            yield {"type": "text", "text": "pong"}
            yield {"type": "done"}
        with mock.patch.object(self.relay.antigravity_bridge, "chat_stream", fake):
            with self.post({"message": "hi", "session_id": "s"}) as r:
                self.assertTrue(r.headers["Content-Type"].startswith("text/event-stream"))
                body = r.read().decode()
        self.assertIn("event: meta", body)
        self.assertIn('event: text\ndata: {"text": "pong"}', body)
        self.assertIn("event: done", body)

    def test_error_event(self):
        def fake(msg, sid, model=None):
            raise self.relay.antigravity_bridge.AntigravityError("Google Antigravity is not running.")
            yield  # pragma: no cover
        with mock.patch.object(self.relay.antigravity_bridge, "chat_stream", fake):
            with self.post({"message": "hi", "session_id": "s"}) as r:
                body = r.read().decode()
        self.assertIn("event: error", body)
        self.assertIn("not running", body)

    def test_requires_token_and_fields(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"message": "hi", "session_id": "s"}, token="wrong")
        self.assertEqual(e.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"message": "hi"})
        self.assertEqual(e.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"message": "hi", "session_id": "s", "model": "gpt"})
        self.assertEqual(e.exception.code, 400)



class WindowsDiscoveryTests(unittest.TestCase):
    """Parsers for the Windows discovery path (pure functions; real Windows is UNTESTED)."""

    def test_netstat_ports(self):
        out = ("  Proto  Local Address          Foreign Address        State           PID\n"
               "  TCP    127.0.0.1:53210        0.0.0.0:0              LISTENING       4242\n"
               "  TCP    0.0.0.0:53211          0.0.0.0:0              LISTENING       4242\n"
               "  TCP    127.0.0.1:53210        127.0.0.1:60000        ESTABLISHED     4242\n"
               "  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       1\n"
               "  TCP    [::1]:53212            [::]:0                 LISTENING       4242\n")
        self.assertEqual(ag.netstat_ports(4242, out), [53210, 53211, 53212])

    def test_wmic_csv_feeds_the_ps_parser(self):
        csv_out = ("\nNode,CommandLine,ProcessId\n"
                   "PC,C:\\Users\\me\\AppData\\Local\\Programs\\Antigravity\\resources\\bin\\language_server.exe "
                   "--csrf_token abc123 --x 1,4242\n")
        self.assertEqual(ag.find_language_servers(ag.parse_wmic(csv_out)), [(4242, "abc123")])

    def test_windows_candidates_and_wrappers(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ag, "IS_WINDOWS", True), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": d, "NEXUS_GEMINI_HOME": d}):
            os.environ.pop("NEXUS_ANTIGRAVITY_LS", None)
            cands = ag.app_language_servers()
            self.assertIn(Path(d) / "Programs" / "Antigravity" / "resources" / "bin" / "language_server.exe", cands)
            self.assertEqual([w.name for w in ag.agentapi_wrappers()][:2], ["agentapi.exe", "agentapi.cmd"])
            wrapper = Path(d) / "antigravity" / "bin" / "agentapi.cmd"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("@echo off\n")
            with mock.patch.object(ag, "app_language_servers", lambda: []):
                argv = ag.cli_path()
            self.assertEqual(argv[1:], ["/c", str(wrapper)])

    def test_override_env(self):
        with mock.patch.dict(os.environ, {"NEXUS_ANTIGRAVITY_LS": "/opt/x/language_server"}):
            self.assertEqual(ag.app_language_servers(), [Path("/opt/x/language_server")])

if __name__ == "__main__":
    unittest.main()
