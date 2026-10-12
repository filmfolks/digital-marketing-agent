"""Scheduler with a fake clock and a fake publisher: due/missed/never-double-post/restart."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401
import nexus_scheduler as ns  # noqa: E402
import nexus_tasks as nt  # noqa: E402

IST = nt.user_tz("Asia/Kolkata")


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 20, 18, 0, tzinfo=IST)

    def __call__(self):
        return self.t


def setup(grace=60):
    clock = Clock()
    s = nt.TaskStore(Path(tempfile.mkdtemp()), clock=clock, log=lambda *a, **k: None)
    posted = []

    def runner(task):
        posted.append(task["id"])
        return {"ok": True, "permalinks": [f"https://x.com/i/{task['id']}"]}
    return clock, s, posted, ns.Scheduler(s, runner=runner, clock=clock, grace_minutes=grace, dry_run=False)


def approved(s, when, **kw):
    t = s.create({"title": "p", "content": "hello", "platforms": ["x"], "scheduled_at": when, **kw})
    return s.approve(t["id"])


class SchedulerTests(unittest.TestCase):
    def test_runs_only_approved_and_due(self):
        clock, s, posted, sch = setup()
        a = approved(s, "2026-10-20T18:30")
        pending = s.create({"title": "p", "content": "x", "platforms": ["x"], "scheduled_at": "2026-10-20T17:00"})
        self.assertEqual(sch.tick()["ran"], [])
        clock.t += timedelta(minutes=31)
        r = sch.tick()
        self.assertEqual(r["ran"], [a["id"]])
        self.assertEqual(posted, [a["id"]])
        done = s.get(a["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["results"][0]["permalinks"], [f"https://x.com/i/{a['id']}"])
        self.assertEqual(s.get(pending["id"])["status"], "pending_approval")
        self.assertEqual(sch.tick()["ran"], [])            # never twice
        self.assertEqual(posted, [a["id"]])

    def test_missed_beyond_grace_is_not_posted_late(self):
        clock, s, posted, sch = setup(grace=60)
        old = approved(s, "2026-10-20T15:00")               # 3h ago, relay was down
        recent = approved(s, "2026-10-20T17:30")            # 30 min ago: within grace
        r = sch.tick()
        self.assertEqual(r["missed"], [old["id"]])
        self.assertEqual(r["ran"], [recent["id"]])
        self.assertEqual(posted, [recent["id"]])
        self.assertEqual(s.get(old["id"])["status"], "failed")
        self.assertIn("missed", s.get(old["id"])["error"])

    def test_failure_is_recorded(self):
        clock, s, _, _ = setup()
        sch = ns.Scheduler(s, runner=lambda t: {"ok": False, "error": "X API 403"}, clock=clock, dry_run=False)
        t = approved(s, "2026-10-20T17:59")
        self.assertEqual(sch.tick()["failed"], [t["id"]])
        self.assertEqual(s.get(t["id"])["error"], "X API 403")

    def test_runner_exception_does_not_kill_loop(self):
        clock, s, _, _ = setup()

        def boom(task):
            raise RuntimeError("browser crashed")
        sch = ns.Scheduler(s, runner=boom, clock=clock, dry_run=False)
        t = approved(s, "2026-10-20T17:59")
        sch.tick()
        self.assertIn("browser crashed", s.get(t["id"])["error"])

    def test_two_schedulers_never_double_post(self):
        clock, s, posted, sch1 = setup()
        gate = threading.Event()

        def slow(task):
            gate.wait(1)
            posted.append(task["id"])
            return {"ok": True}
        sch1.runner = slow
        sch2 = ns.Scheduler(nt.TaskStore(s.root, clock=clock, log=lambda *a, **k: None), runner=slow, clock=clock,
                            dry_run=False)
        for i in range(5):
            approved(s, "2026-10-20T17:59")
        th = [threading.Thread(target=x.tick) for x in (sch1, sch2)]
        [t.start() for t in th]
        gate.set()
        [t.join() for t in th]
        self.assertEqual(len(posted), 5)
        self.assertEqual(len(set(posted)), 5)

    def test_restart_marks_interrupted_running_tasks_failed(self):
        clock, s, posted, sch = setup()
        t = approved(s, "2026-10-20T17:59")
        s.claim(t["id"])                                  # crashed mid-post
        self.assertEqual(sch.recover(), [t["id"]])
        self.assertEqual(s.get(t["id"])["status"], "failed")
        sch.tick()
        self.assertEqual(posted, [])

    def test_dry_run_changes_nothing(self):
        clock, s, posted, _ = setup()
        sch = ns.Scheduler(s, runner=lambda t: {"ok": True, "status": "dry_run"}, clock=clock, dry_run=True)
        t = approved(s, "2026-10-20T17:59")
        r = sch.tick()
        self.assertEqual(r["ran"][0]["id"], t["id"])
        self.assertEqual(s.get(t["id"])["status"], "approved")

    def test_background_thread_and_single_instance_lock(self):
        clock, s, posted, sch = setup()
        sch.interval_s = 0.05
        approved(s, "2026-10-20T17:59")
        self.assertTrue(sch.start())
        other = ns.Scheduler(s, runner=lambda t: {"ok": True}, clock=clock)
        self.assertFalse(other.start())
        for _ in range(100):
            if posted:
                break
            threading.Event().wait(0.02)
        sch.stop()
        self.assertEqual(len(posted), 1)
        self.assertTrue(other.start())                    # lock released on stop
        other.stop()

    def test_publish_runner_builds_publish_py_call(self):
        seen = {}

        class P:
            returncode = 0
            stdout = '{"status": "ok", "results": [{"platform": "x", "ok": true, "url": "https://x.com/a/status/1"}]}'
            stderr = ""

        def run(args, **kw):
            seen["args"] = args
            seen["payload"] = Path(args[args.index("--from-json") + 1]).read_text()
            return P()
        r = ns.publish_runner({"id": "t-1", "platforms": ["x"], "content": "hi", "media": []}, dry_run=True, run=run)
        self.assertTrue(r["ok"])
        self.assertIn("--dry-run", seen["args"])
        self.assertIn('"platforms": ["x"]', seen["payload"])
        self.assertEqual(r["permalinks"], ["https://x.com/a/status/1"])


if __name__ == "__main__":
    unittest.main()
