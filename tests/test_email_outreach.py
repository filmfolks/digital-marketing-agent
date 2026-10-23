"""Email outreach: config, rendering, footer, send loop, caps and suppression."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

eo = load_agent("email_outreach")
lg = load_agent("lead_gen")

CFG = {"host": "smtp.example.com", "port": 587, "tls": "starttls", "user": "u", "password": "p",
       "from": "me@nexus.example", "from_name": "Nexus", "reply_to": "", "postal": "1 Main St, Pune",
       "unsubscribe": "", "daily_cap": 50, "min_delay": 0, "configured": True}


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = dict(os.environ)
        self.dir = Path(tempfile.mkdtemp(prefix="nexus-leads-"))
        os.environ["NEXUS_LEADS_DIR"] = str(self.dir)
        for k in list(os.environ):
            if k.startswith("NEXUS_SMTP") or k.startswith("NEXUS_EMAIL"):
                os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old)

    def campaign(self, cid="pune", leads=None):
        camp = lg.empty_campaign(cid, "dentists", "Pune")
        camp["leads"] = leads if leads is not None else [
            lg.normalise_contact({"name": "Acme", "emails": ["info@acme.in"], "website": "https://acme.in"}),
            lg.normalise_contact({"name": "Beta", "phone": "+91 1"}),
        ]
        lg.save_campaign(camp)
        return camp


class ConfigTests(_Env):
    def test_tls_inference_and_configured_flag(self):
        os.environ.update({"NEXUS_SMTP_HOST": "smtp.gmail.com", "NEXUS_SMTP_PORT": "465",
                           "NEXUS_SMTP_USER": "me@gmail.com", "NEXUS_SMTP_PASS": "pw"})
        c = eo.smtp_config()
        self.assertEqual(c["tls"], "ssl")
        self.assertEqual(c["port"], 465)
        self.assertTrue(c["configured"])
        self.assertEqual(c["from"], "me@gmail.com")

        os.environ["NEXUS_SMTP_PORT"] = "25"
        self.assertEqual(eo.smtp_config()["tls"], "none")

    def test_unconfigured_reports_reason(self):
        c = eo.smtp_config()
        self.assertFalse(c["configured"])
        self.assertIn("NEXUS_SMTP_HOST", c["error"])
        s = eo.status()
        self.assertFalse(s["ok"])
        self.assertNotIn("password", s)
        self.assertIn("password_set", s)


class RenderTests(_Env):
    def test_render_and_footer(self):
        brand = {"business_name": "Nexus", "website": "https://nexus.example", "city": "Pune",
                 "services": ["SEO", "Ads"], "phone": "+91 1", "email": "hi@nexus.example"}
        lead = {"name": "Acme", "category": "Dentist", "city": "Pune"}
        out = eo.render(eo.DEFAULT_BODY, lead, brand)
        self.assertIn("Acme", out)
        self.assertIn("Nexus", out)
        self.assertNotIn("{business_name}", out)

        footed = eo.add_footer("Body", CFG, brand)
        self.assertIn("1 Main St, Pune", footed)
        self.assertIn("Reply with 'STOP'", footed)

    def test_render_keeps_unknown_placeholders(self):
        self.assertIn("{unknown}", eo.render("a {unknown} b {city}", {}, {}))


class DeliverTests(_Env):
    def test_rejects_invalid_recipient_and_subject(self):
        self.assertFalse(eo.deliver("not an email", "S", "B", CFG)["ok"])
        self.assertFalse(eo.deliver("a@b.co", "", "B", CFG)["ok"])

    def test_unconfigured_is_refused(self):
        out = eo.deliver("a@b.co", "S", "B", {"configured": False, "error": "no host"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no host")

    def test_send_fn_is_used(self):
        calls = []
        out = eo.deliver("a@b.co", "Hi", "Body", CFG, send_fn=lambda *a: calls.append(a) or {"ok": True})
        self.assertTrue(out["ok"])
        self.assertEqual(calls[0][0], "a@b.co")


class SendCampaignTests(_Env):
    def test_dry_run_sends_nothing(self):
        self.campaign()
        out = eo.send_campaign("pune", send=False, cfg=CFG)
        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["sent_count"], 0)
        self.assertEqual(len(out["previews"]), 1)
        self.assertEqual(out["would_send"], 1)
        self.assertEqual(eo.sent_last_24h(), 0)

    def test_send_updates_status_and_logs(self):
        self.campaign()
        sent = []
        out = eo.send_campaign("pune", send=True, cfg=CFG, sleep=lambda s: None,
                               send_fn=lambda to, s, b, c: sent.append(to) or {"ok": True})
        self.assertEqual(out["sent_count"], 1)
        self.assertEqual(sent, ["info@acme.in"])
        self.assertEqual(eo.sent_last_24h(), 1)
        camp = lg.load_campaign("pune")
        acme = next(l for l in camp["leads"] if l["name"] == "Acme")
        self.assertEqual(acme["status"], "contacted")
        self.assertTrue(acme["last_contacted_at"])

    def test_suppression_and_only_email(self):
        self.campaign()
        lg.suppress("info@acme.in")
        out = eo.send_campaign("pune", send=False, cfg=CFG)
        self.assertEqual(out["would_send"], 0)
        self.assertEqual(out["previews"], [])

    def test_daily_cap_blocks(self):
        self.campaign()
        cfg = {**CFG, "daily_cap": 1}
        eo.log_send({"campaign": "pune", "email": "x@acme.in", "subject": "s", "status": "sent"})
        out = eo.send_campaign("pune", send=True, cfg=cfg, send_fn=lambda *a: {"ok": True})
        self.assertEqual(out["sent_count"], 0)
        self.assertIn("daily cap", out["reason"])
        self.assertEqual(out["daily_remaining"], 0)

    def test_contacted_leads_are_not_emailed_twice(self):
        self.campaign()
        cfg = {**CFG, "daily_cap": 0, "min_delay": 0}
        first = eo.send_campaign("pune", send=True, cfg=cfg, sleep=lambda s: None, send_fn=lambda *a: {"ok": True})
        self.assertEqual(first["sent_count"], 1)
        second = eo.send_campaign("pune", send=True, cfg=cfg, sleep=lambda s: None, send_fn=lambda *a: {"ok": True})
        self.assertEqual(second["sent_count"], 0)

    def test_empty_campaign_errors(self):
        out = eo.send_campaign("missing", cfg=CFG)
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
