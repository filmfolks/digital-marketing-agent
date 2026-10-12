"""Sho external brain: job files, relay endpoints, approval gate, providers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import AGENT, SKILL_SCRIPTS, load_agent, load_module  # noqa: E402
import test_relay_http as _rh  # noqa: E402  (module import: avoids re-running its tests)
TOKEN = _rh.TOKEN


bb = load_agent("brain_bridge")
PROFILE = {"business_name": "Acmebites", "brand_voice": {"avoid_words": ["cheap"]}}


class BrainDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"NEXUS_BRAIN_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Jobs(BrainDir):
    def test_job_file_carries_task_and_brand_profile(self):
        job = bb.create_job("generate", {"topic": "Mumbai mini burgers"}, brand_id="acmebites", brand_profile=PROFILE)
        on_disk = json.loads((Path(self.tmp.name) / "inbox" / f"{job['id']}.json").read_text())
        self.assertEqual(on_disk["status"], "pending")
        self.assertEqual(on_disk["brand_profile"], PROFILE)
        self.assertEqual(on_disk["task"]["topic"], "Mumbai mini burgers")
        self.assertIn("instructions", on_disk)

    def test_outbox_file_is_picked_up(self):
        job = bb.create_job("generate", {"topic": "t"}, brand_profile=PROFILE)
        out = Path(self.tmp.name) / "outbox" / f"{job['id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"result": {"content": {"x": "hello"}}}))
        got = bb.get_job(job["id"])
        self.assertEqual(got["status"], "answered")
        self.assertEqual(bb.list_jobs("answered")[0]["id"], job["id"])

    def test_wait_times_out_and_marks_expired(self):
        job = bb.create_job("generate", {"topic": "t"}, brand_profile=PROFILE)
        t = [0.0]
        with self.assertRaises(TimeoutError):
            bb.wait_for_result(job["id"], 5, poll_s=2, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
        self.assertEqual(bb.get_job(job["id"])["status"], "expired")
        with self.assertRaises(ValueError):            # a late answer to an expired job is refused
            bb.submit_result(job["id"], "late")

    def test_bad_ids_are_rejected(self):
        for bad in ("../../etc/passwd", "a", "UPPER-CASE-ID", ""):
            with self.assertRaises(ValueError):
                bb.get_job(bad)

    def test_post_stays_pending_approval_until_approved(self):
        job = bb.create_job("post", {"platform": "x", "topic": "launch"}, brand_profile=PROFILE)
        with self.assertRaises(ValueError):
            bb.approve(job["id"])                       # nothing to approve yet
        bb.submit_result(job["id"], {"platform": "x", "text": "Mini burgers, big flavour."})
        self.assertEqual(bb.get_job(job["id"])["status"], "pending_approval")
        with self.assertRaises(PermissionError):
            bb.publish_approved(job["id"], run=lambda *a, **k: self.fail("must not publish"))
        bb.approve(job["id"], by="test")
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            payload = json.loads(Path(args[args.index("--from-json") + 1]).read_text())
            self.assertEqual(payload["content"], "Mini burgers, big flavour.")
            return subprocess.CompletedProcess(args, 0, json.dumps({"status": "validated"}), "")
        res = bb.publish_approved(job["id"], dry_run=True, run=fake_run)
        self.assertTrue(res["ok"] and res["dry_run"])
        self.assertIn("--dry-run", calls[0])
        self.assertEqual(bb.get_job(job["id"])["status"], "approved")   # a dry run publishes nothing

    def test_generate_jobs_never_need_approval(self):
        job = bb.create_job("generate", {"topic": "t"}, brand_profile=PROFILE)
        bb.submit_result(job["id"], "text")
        with self.assertRaises(ValueError):
            bb.approve(job["id"])

    def test_cli_list_show_answer_approve(self):
        env = {**os.environ, "NEXUS_BRAIN_DIR": self.tmp.name}
        cli = [sys.executable, str(AGENT / "brain_bridge.py")]
        job = bb.create_job("post", {"platform": "x", "topic": "t"}, brand_profile=PROFILE)
        run = lambda *a: subprocess.run(cli + list(a), capture_output=True, text=True, env=env, timeout=60)  # noqa: E731
        self.assertIn(job["id"], run("list").stdout)
        ans = Path(self.tmp.name) / "a.json"
        ans.write_text(json.dumps({"platform": "x", "text": "hi"}))
        self.assertEqual(run("answer", job["id"], "--file", str(ans)).returncode, 0)
        self.assertEqual(json.loads(run("show", job["id"]).stdout)["status"], "pending_approval")
        self.assertEqual(json.loads(run("approve", job["id"]).stdout)["status"], "approved")
        self.assertEqual(run("approve", job["id"]).returncode, 2)          # cannot approve twice


class Providers(BrainDir):
    def test_generator_sho_provider_round_trip(self):
        gen = load_module("nexus_generate_content_sho", SKILL_SCRIPTS / "generate_content.py")
        self.assertIn("sho", gen.PROVIDERS)

        def answer(_s):
            for j in bb.list_jobs("pending"):
                bb.submit_result(j["id"], {"content": {"x": "Byte-sized burgers 🍔"}})
        text = gen.generate_via_sho("burgers", "x", "casual", "Acmebites", timeout_s=10, sleep=answer)
        self.assertEqual(text, "Byte-sized burgers 🍔")

    def test_generator_sho_timeout_is_reported_not_raised(self):
        gen = load_module("nexus_generate_content_sho2", SKILL_SCRIPTS / "generate_content.py")
        t = [0.0]
        text = gen.generate_via_sho("burgers", "x", "casual", "B", timeout_s=1,
                                    sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
        self.assertTrue(text.startswith("⚠️ Error"))

    def test_brand_research_profile_via_sho(self):
        br = load_agent("brand_research")

        def answer(_s):
            for j in bb.list_jobs("pending"):
                self.assertEqual(j["kind"], "profile")
                bb.submit_result(j["id"], {"summary": "Veg mini burgers", "services": ["Mini burgers"]})
        text, by = br.sho_llm(br.PROFILE_SYSTEM, "user prompt", sleep=answer)
        self.assertEqual(by, "sho:brain")
        self.assertEqual(json.loads(text)["summary"], "Veg mini burgers")


class BrainRelay(_rh.RelayServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp2 = tempfile.TemporaryDirectory()
        cls._env = mock.patch.dict(os.environ, {"NEXUS_BRAIN_DIR": cls._tmp2.name})
        cls._env.start()

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        cls._tmp2.cleanup()
        super().tearDownClass()

    def test_job_lifecycle_over_http(self):
        code, body = self.request("POST", "/brain/job", body={"kind": "post", "task": {"platform": "x", "topic": "t"}},
                                  token=TOKEN)
        self.assertEqual(code, 200, body)
        jid = body["job"]["id"]
        self.assertEqual(self.request("GET", "/brain/jobs")[0], 401)
        code, body = self.request("GET", "/brain/jobs?status=pending", token=TOKEN)
        self.assertIn(jid, [j["id"] for j in body["jobs"]])
        self.assertEqual(self.request("POST", "/brain/result", body={"id": jid, "result": "x"})[0], 401)
        code, body = self.request("POST", "/brain/result", body={"id": jid, "result": {"text": "hi"}}, token=TOKEN)
        self.assertEqual(body["job"]["status"], "pending_approval")
        code, body = self.request("GET", f"/brain/jobs/{jid}", token=TOKEN)
        self.assertFalse(body["job"]["approved"])
        code, body = self.request("POST", "/brain/approve", body={"id": jid}, token=TOKEN)
        self.assertEqual((code, body["job"]["status"]), (200, "approved"))
        self.assertEqual(self.request("GET", "/brain/jobs/..%2f..%2fetc", token=TOKEN)[0], 404)

    def test_result_and_approve_refused_when_relay_has_no_token(self):
        saved = self.server.token
        self.server.token = ""
        try:
            code, body = self.request("POST", "/brain/approve", body={"id": "20260101-000000-post-abcdef"})
            self.assertEqual(code, 401)
            self.assertIn("NEXUS_RELAY_TOKEN", body["error"])
        finally:
            self.server.token = saved


if __name__ == "__main__":
    unittest.main()
