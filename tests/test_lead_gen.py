"""Lead generation: normalisation, Places pagination, enrichment, store, export."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

lg = load_agent("lead_gen")

SITE = "https://acme-dental.example"
PAGES = {
    f"{SITE}/": f'<html><head><title>Acme Dental</title><meta name="description" content="Family dentist in Pune">'
                f'</head><body><h1>Acme Dental</h1><p>Gentle family dentistry.</p>'
                f'<a href="mailto:hello@acme-dental.example">Email us</a>'
                f'<a href="tel:+912012345678">Call us</a>'
                f'<a href="/contact">Contact</a></body></html>',
    f"{SITE}/contact": '<html><head><title>Contact</title></head><body>'
                       '<p>Reach us at info@acme-dental.example or sales@acme-dental.example</p>'
                       '<a href="https://wa.me/919876543210">WhatsApp</a></body></html>',
}


def fake_fetch(url, timeout=15):
    if url in PAGES:
        return {"status": 200, "url": url, "content_type": "text/html", "text": PAGES[url]}
    return {"status": 404, "url": url, "content_type": "text/html", "text": ""}


def places_page(names, token=""):
    body = {"places": [{"id": f"p{i}", "displayName": {"text": n},
                        "formattedAddress": f"{i} Main St, Pune", "primaryTypeDisplayName": {"text": "Dentist"},
                        "nationalPhoneNumber": "+91 20 0000 0000", "websiteUri": f"https://{n}.example",
                        "googleMapsUri": f"https://maps.google.com/?q={n}", "rating": 4.5,
                        "userRatingCount": 10} for i, n in enumerate(names)]}
    if token:
        body["nextPageToken"] = token
    return 200, body


class _LeadsDir(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("NEXUS_LEADS_DIR")
        self.dir = Path(tempfile.mkdtemp(prefix="nexus-leads-"))
        os.environ["NEXUS_LEADS_DIR"] = str(self.dir)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("NEXUS_LEADS_DIR", None)
        else:
            os.environ["NEXUS_LEADS_DIR"] = self._old


class NormaliseTests(_LeadsDir):
    def test_slug_domain_and_stable_id(self):
        self.assertEqual(lg.slugify("Dental Clinics — Pune!"), "dental-clinics-pune")
        self.assertEqual(lg.domain_of("https://www.Acme.in/x"), "acme.in")
        a = lg.lead_id("Acme", "https://www.acme.in", "")
        b = lg.lead_id("Acme Dental", "https://acme.in/contact", "")
        self.assertEqual(a, b, "same domain → same id")

    def test_best_email_prefers_own_domain_then_role(self):
        self.assertEqual(lg.best_email(["someone@gmail.com", "info@acme.in"], "acme.in"), "info@acme.in")
        self.assertEqual(lg.best_email(["sales@acme.in", "info@acme.in"], "acme.in"), "info@acme.in")
        self.assertEqual(lg.best_email(["noreply@acme.in", "hi@acme.in"], "acme.in"), "hi@acme.in")
        self.assertEqual(lg.best_email(["noreply@acme.in"], "acme.in"), "")

    def test_normalise_contact(self):
        lead = lg.normalise_contact({"name": "Acme", "website": "https://www.acme.in",
                                     "phone": "  +91  20  1 ", "emails": ["info@acme.in"]})
        self.assertEqual(lead["domain"], "acme.in")
        self.assertEqual(lead["phone"], "+91 20 1")
        self.assertEqual(lead["email"], "info@acme.in")
        self.assertEqual(lead["status"], "new")

    def test_merge_keeps_richer_and_never_loses_email(self):
        leads = []
        lg.merge_lead(leads, lg.normalise_contact({"name": "Acme", "website": "https://acme.in"}))
        lg.merge_lead(leads, lg.normalise_contact({"name": "Acme", "website": "https://www.acme.in",
                                                   "phone": "+91 1", "emails": ["hi@acme.in"]}))
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["phone"], "+91 1")
        self.assertEqual(leads[0]["email"], "hi@acme.in")

    def test_merge_does_not_reset_contacted_status(self):
        leads = [lg.normalise_contact({"name": "Acme", "website": "https://acme.in", "status": "contacted"})]
        lg.merge_lead(leads, lg.normalise_contact({"name": "Acme", "website": "https://acme.in", "phone": "+1"}))
        self.assertEqual(leads[0]["status"], "contacted")


class PlacesTests(_LeadsDir):
    def test_pagination_and_limit(self):
        calls = []

        def post(url, payload, headers):
            calls.append(payload)
            if "pageToken" not in payload:
                return places_page([f"biz{i}" for i in range(20)], token="TOK")
            return places_page([f"biz{i}" for i in range(20, 26)])

        out = lg.places_search("dentists pune", "KEY", limit=25, post=post, sleep=lambda s: None)
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["leads"]), 25)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["maxResultCount"], 5)
        self.assertEqual(calls[0]["textQuery"], "dentists pune")

    def test_reports_http_error(self):
        out = lg.places_search("x", "KEY", post=lambda *a: (403, {"error": "bad key"}), sleep=lambda s: None)
        self.assertFalse(out["ok"])
        self.assertIn("403", out["error"])


class EnrichTests(_LeadsDir):
    def test_enrich_lead_pulls_emails_and_phone(self):
        lead = lg.normalise_contact({"name": "Acme", "website": SITE})
        out = lg.enrich_lead(lead, fetch=fake_fetch, sleep=lambda s: None, max_pages=4)
        self.assertIn("info@acme-dental.example", out["emails"])
        self.assertEqual(out["email"], "info@acme-dental.example")
        self.assertTrue(out["phone"])
        self.assertIsNotNone(out["enriched_at"])

    def test_enrich_marks_no_contact_without_a_site(self):
        lead = lg.normalise_contact({"name": "No Site", "phone": ""})
        self.assertEqual(lg.enrich_lead(lead, fetch=fake_fetch), lead)


class ScanTests(_LeadsDir):
    def test_scan_with_browser_fallback_and_enrich(self):
        def browser(query, limit):
            return {"ok": True, "source": "maps_browser", "error": "",
                    "leads": [lg.normalise_contact({"name": "Acme", "website": SITE}),
                              lg.normalise_contact({"name": "Beta", "phone": "+91 2"})]}

        out = lg.scan("dental clinics", "Pune", campaign="pune-dentists", browser=browser,
                      fetch=fake_fetch, sleep=lambda s: None, delay=0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["campaign"], "pune-dentists")
        self.assertEqual(out["stats"]["total"], 2)
        self.assertGreaterEqual(out["stats"]["with_email"], 1)
        self.assertIn("best-effort", out["note"])
        # persisted
        self.assertEqual(lg.load_campaign("pune-dentists")["count"], 2)

    def test_scan_requires_query(self):
        with self.assertRaises(ValueError):
            lg.scan("", "Pune")


class StoreTests(_LeadsDir):
    def test_save_list_delete_and_suppression(self):
        camp = lg.empty_campaign("pune", "dentists", "Pune")
        camp["leads"].append(lg.normalise_contact({"name": "Acme", "website": "https://acme.in", "emails": ["a@acme.in"]}))
        lg.save_campaign(camp)
        names = [c["id"] for c in lg.list_campaigns()]
        self.assertIn("pune", names)
        self.assertTrue(lg.delete_campaign("pune"))
        self.assertEqual(lg.load_campaign("pune")["count"], 0)

        self.assertEqual(lg.suppress("Stop@Acme.in"), {"ok": True, "email": "stop@acme.in", "count": 1})
        lg.suppress("stop@acme.in")
        self.assertEqual(lg.load_suppression(), {"stop@acme.in"})
        self.assertFalse(lg.suppress("")["ok"])


class ExportTests(_LeadsDir):
    def test_csv_header_filter_and_formula_neutralised(self):
        camp = lg.empty_campaign("x")
        camp["leads"] = [
            lg.normalise_contact({"name": "=cmd()", "email": "a@acme.in", "website": "https://acme.in"}),
            lg.normalise_contact({"name": "NoEmail", "phone": "+91 1"}),
        ]
        text = lg.export_csv(camp)
        self.assertTrue(text.startswith(",".join(lg.CSV_FIELDS)))
        self.assertIn("'=cmd()", text)
        only = lg.export_csv(camp, only_with_email=True)
        self.assertNotIn("NoEmail", only)


if __name__ == "__main__":
    unittest.main()
