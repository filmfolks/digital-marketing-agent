"""Task store: status machine, approval rules, campaigns, concurrency, ICS."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401
import nexus_tasks as nt  # noqa: E402

IST = nt.user_tz("Asia/Kolkata")


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def store(clock=None, log=None):
    d = tempfile.mkdtemp(prefix="nexus-tasks-")
    return nt.TaskStore(Path(d), clock=clock or Clock(datetime(2026, 10, 13, 9, 0, tzinfo=IST)),
                        log=log or (lambda *a, **k: None))


POST = {"title": "Burger", "content": "Eat burgers", "platforms": ["Instagram", "twitter"],
        "scheduled_at": "2026-10-20T19:00"}


class TimeTests(unittest.TestCase):
    def test_naive_is_user_tz_and_date_only_is_10am(self):
        self.assertEqual(nt.parse_when("2026-10-20T19:00").utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(nt.parse_when("2026-10-20").hour, 10)
        self.assertEqual(nt.parse_when("2026-10-20 07:15").minute, 15)
        self.assertEqual(nt.parse_when("2026-10-20T13:30:00Z").utcoffset(), timedelta(0))
        with self.assertRaises(ValueError):
            nt.parse_when("next tuesday")

    def test_fixed_offset_fallback_when_no_tz_database(self):
        tz = nt.user_tz("Asia/Kolkata")
        self.assertEqual(datetime(2026, 1, 1, tzinfo=tz).utcoffset(), timedelta(hours=5, minutes=30))


class StoreTests(unittest.TestCase):
    def test_create_normalises_and_starts_pending(self):
        events = []
        s = store(log=lambda action, task, **k: events.append(action))
        t = s.create(POST)
        self.assertEqual(t["status"], "pending_approval")
        self.assertEqual(t["platforms"], ["instagram", "x"])
        self.assertTrue(t["scheduled_at"].endswith("+05:30"))
        self.assertEqual(events, ["task_created"])
        with self.assertRaises(nt.TaskError):
            s.create(POST, status="approved")
        with self.assertRaises(nt.TaskError):
            s.create({"title": "x", "platforms": ["myspace"]})

    def test_approval_rules(self):
        s = store()
        t = s.create({"title": "no content", "platforms": ["x"], "scheduled_at": "2026-10-20T10:00"})
        with self.assertRaises(nt.TaskError):
            s.approve(t["id"])                          # needs content
        t2 = s.create({"title": "no time", "content": "hi", "platforms": ["x"]})
        with self.assertRaises(nt.TaskError):
            s.approve(t2["id"])                         # needs a time
        t3 = s.create(POST)
        with self.assertRaises(nt.TaskError):
            s.transition(t3["id"], "approved", by="model")
        self.assertEqual(s.approve(t3["id"])["status"], "approved")
        with self.assertRaises(nt.TaskError):
            s.transition(t3["id"], "done")              # must go through running

    def test_model_edit_of_approved_post_needs_reapproval_but_reschedule_does_not(self):
        s = store()
        t = s.approve(s.create(POST)["id"])
        t = s.update(t["id"], {"scheduled_at": "2026-10-21T19:00"}, by="model")
        self.assertEqual(t["status"], "approved")
        t = s.update(t["id"], {"content": "changed"}, by="model")
        self.assertEqual(t["status"], "pending_approval")
        with self.assertRaises(nt.TaskError):
            s.update(t["id"], {"auto_approve": True}, by="model")

    def test_claim_is_compare_and_set(self):
        s = store()
        t = s.approve(s.create(POST)["id"])
        results = []
        threads = [threading.Thread(target=lambda: results.append(s.claim(t["id"]))) for _ in range(8)]
        [th.start() for th in threads]
        [th.join() for th in threads]
        self.assertEqual(sum(1 for r in results if r), 1)
        self.assertEqual(s.get(t["id"])["status"], "running")

    def test_concurrent_creates_lose_nothing(self):
        s = store()
        threads = [threading.Thread(target=lambda i=i: s.create({**POST, "title": f"t{i}"})) for i in range(20)]
        [th.start() for th in threads]
        [th.join() for th in threads]
        self.assertEqual(len(s.list()), 20)
        s2 = nt.TaskStore(s.root, log=lambda *a, **k: None)          # second instance = other process view
        self.assertEqual(len(s2.list()), 20)

    def test_campaign_auto_approve_is_explicit(self):
        s = store()
        c = s.create_campaign({"name": "Oct"})
        self.assertEqual(s.create(POST, campaign_id=c["id"])["status"], "pending_approval")
        with self.assertRaises(nt.TaskError):
            s.set_campaign_auto_approve(c["id"], True, by="model")
        s.set_campaign_auto_approve(c["id"], True, by="user")
        t = s.create(POST, campaign_id=c["id"])
        self.assertEqual(t["status"], "approved")
        res = s.approve_many(campaign_id=c["id"])
        self.assertEqual(len(res["approved"]), 1)

    def test_list_filters_and_calendar(self):
        s = store()
        s.create(POST)
        s.create({**POST, "scheduled_at": "2026-11-02T09:00"})
        oct_ = s.list(start=nt.parse_when("2026-10-01T00:00"), end=nt.parse_when("2026-11-01T00:00"))
        self.assertEqual(len(oct_), 1)
        cal = s.calendar(nt.parse_when("2026-10-01T00:00"), nt.parse_when("2026-12-01T00:00"))
        self.assertEqual(sorted(cal["days"]), ["2026-10-20", "2026-11-02"])

    def test_corrupt_store_is_moved_aside(self):
        s = store()
        s.root.mkdir(parents=True, exist_ok=True)
        s.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(s.list(), [])
        self.assertTrue(list(s.root.glob("tasks.corrupt-*.json")))

    def test_ics(self):
        s = store()
        t = s.create({**POST, "content": "Line1\nLine2, with; specials " + "x" * 120})
        ics = nt.to_ics(s.list(), stamp=datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn(f"UID:{t['id']}@nexus", ics)
        self.assertIn("DTSTART:20261020T133000Z", ics)
        self.assertIn("\\n", ics)
        self.assertIn("\\,", ics)
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))
        self.assertIn("STATUS:TENTATIVE", ics)


if __name__ == "__main__":
    unittest.main()
