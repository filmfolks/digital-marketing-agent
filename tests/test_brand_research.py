"""Brand research: crawler, GBP, AI profile/plan, exports, store (network + AI mocked)."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

br = load_agent("brand_research")

SITE = "https://acme-dental.example"
PAGES = {
    f"{SITE}/robots.txt": "User-agent: *\nDisallow: /private\nCrawl-delay: 2\n",
    f"{SITE}/": """<html lang="en-IN"><head><title>Acme Dental | Family Dentist in Pune</title>
        <meta name="description" content="Gentle family dentistry in Kothrud, Pune.">
        <meta name="keywords" content="dentist pune, teeth whitening, root canal">
        <script type="application/ld+json">{"@type":"Dentist","name":"Acme Dental","telephone":"+91 20 1234 5678",
        "priceRange":"₹₹"}</script></head><body>
        <h1>Gentle dentistry for the whole family</h1>
        <h2>Teeth Whitening</h2><h2>Root Canal Treatment</h2><h2>Kids Dentistry</h2>
        <p>We have helped Pune families smile with confidence using modern, painless techniques.</p>
        <ul><li>15 years of experience</li><li>Same-day emergency appointments</li></ul>
        <a href="/about">About</a> <a href="/services/whitening">Whitening</a> <a href="/pricing">Pricing</a>
        <a href="/private/admin">Admin</a> <a href="https://facebook.com/acme">FB</a>
        <a href="/brochure.pdf">PDF</a> <a href="mailto:hi@acme-dental.example">Mail</a>
        </body></html>""",
    f"{SITE}/about": "<title>About us</title><p>Founded by Dr. Rao, our clinic focuses on anxiety-free care for families.</p>",
    f"{SITE}/services/whitening": "<title>Whitening</title><h2>Laser whitening</h2><p>Brighter teeth in one visit, safe and effective.</p>",
    f"{SITE}/pricing": "<title>Pricing</title><p>Consultation from ₹500 per visit. Whitening packages ₹8,000.</p>",
}


class FakeWeb:
    def __init__(self, pages=PAGES, redirects=None, statuses=None):
        self.pages, self.redirects, self.statuses = pages, redirects or {}, statuses or {}
        self.calls = []

    def __call__(self, url, timeout=15):
        self.calls.append(url)
        if url in self.redirects:
            return {"status": 200, "url": self.redirects[url], "content_type": "text/html", "text": ""}
        if url in self.statuses:
            return {"status": self.statuses[url], "url": url, "content_type": "text/html", "text": ""}
        if url in self.pages:
            ctype = "text/plain" if url.endswith("robots.txt") else "text/html; charset=utf-8"
            return {"status": 200, "url": url, "content_type": ctype, "text": self.pages[url]}
        return {"status": 404, "url": url, "content_type": "text/html", "text": ""}


AI_PROFILE = {
    "summary": "Family dental clinic in Kothrud, Pune.",
    "category": "Dental clinic",
    "services": [{"name": "Teeth whitening", "price_hint": "₹8,000"}, "Root canal treatment", {"description": "no name"}],
    "usps": ["15 years of experience", "Same-day emergency appointments"],
    "value_proposition": "For anxious Pune families, Acme offers painless dentistry.",
    "personas": [{"name": "Busy parent Priya", "age_range": "30-40", "pains": "fear of pain; no time",
                  "goals": ["healthy kids"]}],
    "brand_voice": {"adjectives": ["warm", "reassuring"], "do": ["explain simply"], "dont": ["use jargon"],
                    "avoid_words": ["cheap", "guaranteed"], "emoji_policy": "LIGHT"},
    "competitors_to_research": ["dentists in Kothrud"],
    "local_seo_keywords": ["Dentist in Kothrud", "dentist in kothrud", "teeth whitening pune cost"],
    "content_pillars": [{"name": "Oral health tips", "share_pct": 2}, {"name": "Meet the team", "share_pct": 2},
                        {"name": "Smile stories", "share_pct": 4}],
    "hashtag_sets": {"local": ["Pune Dentist", "#kothrud"], "branded": ["#AcmeSmiles", "acmesmiles"]},
}


def fake_llm(payloads):
    calls = []

    def llm(system, user):
        calls.append((system, user))
        p = payloads.pop(0)
        if isinstance(p, Exception):
            raise p
        return ("Here you go:\n```json\n" + json.dumps(p) + "\n```", "mock:model")
    llm.calls = calls
    return llm


class Crawler(unittest.TestCase):
    def crawl(self, **kw):
        sleeps = []
        res = br.crawl_site(SITE, fetch=kw.pop("fetch", FakeWeb()), sleep=sleeps.append, **kw)
        return res, sleeps

    def test_respects_robots_same_site_and_skips_assets(self):
        web = FakeWeb()
        res, sleeps = self.crawl(fetch=web)
        urls = [p["url"] for p in res["pages"]]
        self.assertIn(f"{SITE}/pricing", urls)
        self.assertNotIn(f"{SITE}/private/admin", web.calls)          # robots.txt
        self.assertFalse(any("facebook.com" in u for u in web.calls))  # same-site only
        self.assertFalse(any(u.endswith(".pdf") for u in web.calls))
        self.assertTrue(any(s["reason"] == "robots.txt" for s in res["skipped"]))

    def test_crawl_delay_from_robots_is_honoured(self):
        res, sleeps = self.crawl()
        self.assertEqual(res["crawl_delay_s"], 2.0)
        self.assertTrue(sleeps and all(s == 2.0 for s in sleeps))

    def test_page_cap(self):
        res, _ = self.crawl(max_pages=2)
        self.assertEqual(len(res["pages"]), 2)

    def test_priority_pages_first(self):
        res, _ = self.crawl(max_pages=3)
        kinds = [p["kind"] for p in res["pages"]]
        self.assertEqual(kinds[0], "home")
        self.assertTrue(set(kinds[1:]) <= {"about", "services", "pricing"})

    def test_robots_forbidden_blocks_everything(self):
        web = FakeWeb(statuses={f"{SITE}/robots.txt": 403})
        res, _ = self.crawl(fetch=web)
        self.assertEqual(res["pages"], [])
        self.assertFalse(res["ok"])

    def test_offsite_redirect_is_dropped(self):
        web = FakeWeb(redirects={f"{SITE}/": "https://parked-domain.example/"})
        res, _ = self.crawl(fetch=web)
        self.assertEqual(res["pages"], [])

    def test_js_shell_is_rendered_with_playwright_fallback(self):
        shell = '<html><head><title>App</title><script src=a.js></script><script src=b.js></script><script>x</script></head><body><div id=root></div></body></html>'
        web = FakeWeb(pages={f"{SITE}/robots.txt": "", f"{SITE}/": shell})
        rendered = []

        def render(url):
            rendered.append(url)
            return "<title>Rendered</title><h1>Real content</h1><p>" + "Plenty of server-rendered marketing copy. " * 20 + "</p>"
        res, _ = self.crawl(fetch=web, render=render)
        self.assertEqual(rendered, [f"{SITE}/"])
        self.assertTrue(res["pages"][0].get("rendered"))
        self.assertEqual(res["pages"][0]["title"], "Rendered")


class Extraction(unittest.TestCase):
    def test_summary_finds_services_prices_usps_meta_and_jsonld(self):
        res = br.crawl_site(SITE, fetch=FakeWeb(), sleep=lambda s: None)
        s = br.summarize_site(res)
        self.assertIn("Teeth Whitening", s["service_candidates"])
        self.assertIn("₹500 per visit", s["pricing_hints"])
        self.assertIn("15 years of experience", s["usp_candidates"])
        self.assertIn("dentist pune", s["meta_keywords"])
        self.assertEqual(s["contact"]["telephone"], "+91 20 1234 5678")
        self.assertIn("hi@acme-dental.example", s["contact"]["emails"])
        self.assertIn("anxiety-free", s["about_text"])
        self.assertEqual(s["language"], "en-IN")

    def test_malformed_html_does_not_crash(self):
        p = br.parse_page(SITE, "<html><h1>Broken <p>unclosed <li>item one</ul></div></span>")
        self.assertIsInstance(p["headings"], list)


class GoogleBusinessProfile(unittest.TestCase):
    BRAND = {"business_name": "Acme Dental", "location": "Pune",
             "gbp_url": "https://www.google.com/maps/place/Acme+Dental+Clinic/@18.5,73.8,17z"}

    def test_query_from_maps_url_and_q_param(self):
        self.assertEqual(br.gbp_query_from_url(self.BRAND["gbp_url"], "x"), "Acme Dental Clinic")
        self.assertEqual(br.gbp_query_from_url("https://maps.google.com/?q=Acme+Pune", "x"), "Acme Pune")
        self.assertEqual(br.gbp_query_from_url("", "fallback"), "fallback")

    def test_share_link_is_resolved(self):
        web = FakeWeb(redirects={"https://maps.app.goo.gl/abc": self.BRAND["gbp_url"]})
        self.assertEqual(br.resolve_share_link("https://maps.app.goo.gl/abc", web), self.BRAND["gbp_url"])

    def test_places_api_used_when_key_set(self):
        sent = {}

        def post(url, payload, headers):
            sent.update(url=url, payload=payload, headers=headers)
            return 200, {"places": [{"displayName": {"text": "Acme Dental"}, "primaryTypeDisplayName": {"text": "Dentist"},
                                     "formattedAddress": "Kothrud, Pune", "rating": 4.8, "userRatingCount": 212,
                                     "regularOpeningHours": {"weekdayDescriptions": ["Monday: 9–7"]},
                                     "reviews": [{"rating": 5, "text": {"text": "Very friendly staff and painless"}},
                                                 {"rating": 2, "text": {"text": "Long wait time, slow"}}]}]}
        os.environ["GOOGLE_PLACES_API_KEY"] = "test-key"
        try:
            res = br.research_gbp(self.BRAND, post=post, browser=lambda u: self.fail("browser used"))
        finally:
            del os.environ["GOOGLE_PLACES_API_KEY"]
        self.assertEqual(sent["payload"]["textQuery"], "Acme Dental Clinic")
        self.assertEqual(sent["headers"]["X-Goog-Api-Key"], "test-key")
        self.assertIn("places.rating", sent["headers"]["X-Goog-FieldMask"])
        self.assertEqual((res["name"], res["rating"], res["review_count"]), ("Acme Dental", 4.8, 212))
        themes = {t["theme"]: t for t in res["review_themes"]}
        self.assertEqual(themes["speed / wait time"]["negative"], 1)
        self.assertEqual(themes["service quality"]["mentions"], 1)

    def test_browser_fallback_when_no_key_or_api_fails(self):
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        res = br.research_gbp(self.BRAND, browser=lambda u: {"ok": True, "source": "browser", "name": "Acme", "reviews": []})
        self.assertEqual(res["source"], "browser")
        os.environ["GOOGLE_PLACES_API_KEY"] = "k"
        try:
            res = br.research_gbp(self.BRAND, post=lambda *a: (403, {"error": "denied"}),
                                  browser=lambda u: {"ok": True, "source": "browser", "name": "Acme"})
        finally:
            del os.environ["GOOGLE_PLACES_API_KEY"]
        self.assertEqual(res["source"], "browser")
        self.assertIn("403", res["places_api_error"])

    def test_no_gbp_is_skipped_cleanly(self):
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        res = br.research_gbp({"business_name": "x"})
        self.assertTrue(res["skipped"])


class Profile(unittest.TestCase):
    BRAND = {"id": "acme-dental", "business_name": "Acme Dental", "website": SITE, "location": "Kothrud, Pune",
             "target_market": "families"}

    def research(self):
        crawl = br.crawl_site(SITE, fetch=FakeWeb(), sleep=lambda s: None)
        return {"site_summary": br.summarize_site(crawl), "gbp": {"name": "Acme", "category": "Dentist",
                "review_themes": [{"theme": "service quality", "mentions": 3, "negative": 0}]}}

    def test_ai_profile_is_normalised(self):
        llm = fake_llm([AI_PROFILE])
        p = br.build_profile(self.BRAND, self.research(), llm)
        self.assertEqual(p["generated_by"], "mock:model")
        self.assertEqual([s["name"] for s in p["services"]], ["Teeth whitening", "Root canal treatment"])
        self.assertEqual(p["personas"][0]["pains"], ["fear of pain", "no time"])
        self.assertEqual(p["brand_voice"]["emoji_policy"], "light")
        self.assertEqual(round(sum(c["share_pct"] for c in p["content_pillars"])), 100)
        self.assertEqual(p["hashtag_sets"]["local"], ["#punedentist", "#kothrud"])
        self.assertEqual(p["hashtag_sets"]["branded"], ["#acmesmiles"])                  # de-duplicated
        self.assertEqual(p["local_seo_keywords"][:2], ["dentist in kothrud", "teeth whitening pune cost"])
        self.assertEqual(p["competitors_to_research"][0]["name_or_search"], "dentists in Kothrud")
        self.assertEqual(p["gbp"]["category"], "Dentist")
        # research reached the model as data, and the system prompt forbids inventing facts
        system, user = llm.calls[0]
        self.assertIn("Never invent", system)
        self.assertIn("Teeth Whitening", user)

    def test_heuristic_profile_when_ai_unavailable(self):
        p = br.build_profile(self.BRAND, self.research(), fake_llm([RuntimeError("no provider")]))
        self.assertTrue(p["generated_by"].startswith("heuristic"))
        self.assertTrue(p["services"])
        self.assertIn("#kothrud", p["hashtag_sets"]["local"])
        self.assertTrue(any("kothrud" in k for k in p["local_seo_keywords"]))
        self.assertTrue(p["gaps_and_assumptions"])

    def test_non_json_ai_reply_falls_back(self):
        p = br.build_profile(self.BRAND, self.research(), lambda s, u: ("sorry, I can't", "mock"))
        self.assertTrue(p["generated_by"].startswith("heuristic"))


class Plan(unittest.TestCase):
    PROFILE = br.normalize_profile(AI_PROFILE, Profile.BRAND)

    def test_ai_plan_normalised_and_dated(self):
        cal = [{"day": d, "platform": "Twitter" if d % 2 else "instagram", "pillar": "smile stories",
                "idea": f"idea {d}", "hashtags": ["Pune Smiles"]} for d in range(1, 31)]
        cal.append({"day": 45, "platform": "x", "idea": "out of range"})
        llm = fake_llm([{"goals": [{"goal": "More bookings", "kpi": "Calls", "target": "+10%"}],
                         "cadence": {"Instagram": 4, "twitter": "5"}, "calendar": cal,
                         "campaigns": [{"name": "Whitening month", "start_day": 5, "end_day": 99, "channels": ["Twitter"]}]}])
        plan = br.build_plan(self.PROFILE, start=date(2026, 11, 1), llm=llm)
        self.assertEqual(plan["calendar"][0]["date"], "2026-11-01")
        self.assertEqual(plan["end_date"], "2026-11-30")
        self.assertEqual(len(plan["calendar"]), 30)
        self.assertEqual(plan["calendar"][0]["platform"], "x")
        self.assertEqual(plan["calendar"][0]["pillar"], "Smile stories")              # fuzzy-matched
        self.assertEqual(plan["calendar"][0]["hashtags"], ["#punesmiles"])
        self.assertEqual(plan["cadence"], {"instagram": 4, "x": 5})
        self.assertEqual(plan["campaigns"][0]["end_day"], 30)
        self.assertEqual(plan["campaigns"][0]["channels"], ["x"])

    def test_thin_ai_calendar_is_topped_up(self):
        llm = fake_llm([{"goals": [], "calendar": [{"day": 1, "platform": "instagram", "idea": "only one"}]}])
        plan = br.build_plan(self.PROFILE, start=date(2026, 11, 1), llm=llm)
        days = {c["day"] for c in plan["calendar"]}
        self.assertGreaterEqual(len(days), 20)
        self.assertTrue(any(c.get("auto_filled") for c in plan["calendar"]))
        self.assertTrue(plan["goals"])                                                 # default goals filled

    def test_heuristic_plan_follows_cadence_and_pillars(self):
        plan = br.build_plan(self.PROFILE, start=date(2026, 11, 1), llm=fake_llm([RuntimeError("x")]),
                             cadence={"instagram": 7, "linkedin": 0})
        self.assertTrue(plan["generated_by"].startswith("heuristic"))
        self.assertEqual({c["platform"] for c in plan["calendar"]}, {"instagram"})
        self.assertEqual(len(plan["calendar"]), 30)
        names = {p["name"] for p in self.PROFILE["content_pillars"]}
        self.assertTrue({c["pillar"] for c in plan["calendar"]} <= names)


class ContextAndGuardrails(unittest.TestCase):
    def test_context_block_carries_voice_usps_and_tags(self):
        p = br.normalize_profile(AI_PROFILE, Profile.BRAND)
        ctx = br.brand_context(p)
        self.assertIn("Acme Dental", ctx)
        self.assertIn("Proven USPs", ctx)
        self.assertIn("warm", ctx)
        self.assertIn("#kothrud", ctx)
        self.assertLessEqual(len(br.brand_context(p, max_chars=100)), 100)
        self.assertEqual(br.brand_context(None), "")

    def test_guardrails_from_voice(self):
        g = br.brand_guardrails(br.normalize_profile(AI_PROFILE, Profile.BRAND))
        self.assertEqual(g["bannedWords"], ["cheap", "guaranteed"])
        self.assertTrue(g["allowEmoji"])
        self.assertFalse(br.brand_guardrails({"brand_voice": {"emoji_policy": "none"}})["allowEmoji"])


class Exports(unittest.TestCase):
    def test_markdown_and_csv(self):
        prof = br.normalize_profile(AI_PROFILE, Profile.BRAND)
        plan = br.build_plan(prof, start=date(2026, 11, 1), llm=fake_llm([RuntimeError("x")]))
        plan["calendar"][0]["idea"] = "=HYPERLINK(\"http://evil\") | pipe"
        md = br.to_markdown(prof, plan)
        self.assertIn("# Acme Dental — Brand Profile", md)
        self.assertIn("## Content calendar", md)
        self.assertIn("\\|", md)                                         # table-safe
        rows = list(csv.DictReader(io.StringIO(br.plan_to_csv(plan))))
        self.assertEqual(len(rows), len(plan["calendar"]))
        self.assertTrue(rows[0]["idea"].startswith("'="))                 # formula injection neutralised
        self.assertEqual(list(rows[0].keys()), br.CSV_FIELDS)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = br.BrandStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_multi_brand_create_list_switch_delete(self):
        a = self.store.create({"business_name": "Acme Dental", "website": "acme-dental.example", "handles": {"instagram": "@acme"}})
        b = self.store.create({"business_name": "Bean Café", "website": "https://bean.example"})
        self.assertEqual(a["website"], "https://acme-dental.example")
        self.assertEqual(a["handles"], {"instagram": "acme"})
        self.assertEqual(self.store.active(), "acme-dental")            # first brand becomes active
        self.store.set_active(b["id"])
        self.assertEqual(self.store.active(), "bean-caf")
        self.assertEqual([x["id"] for x in self.store.list()], ["acme-dental", "bean-caf"])
        self.store.delete("bean-caf")
        self.assertEqual(self.store.active(), "acme-dental")            # falls back to remaining brand

    def test_same_name_updates_different_name_same_slug_gets_suffix(self):
        self.store.create({"business_name": "Acme"})
        self.store.create({"business_name": "Acme", "location": "Pune"})
        self.store.create({"business_name": "ACME!"})
        ids = [x["id"] for x in self.store.list()]
        self.assertEqual(ids, ["acme", "acme-2"])

    def test_validation_and_path_safety(self):
        with self.assertRaises(ValueError):
            self.store.create({"business_name": ""})
        with self.assertRaises(ValueError):
            self.store.create({"business_name": "x", "website": "not a url"})
        with self.assertRaises(ValueError):
            self.store.read("../../etc", "brand")
        with self.assertRaises(ValueError):
            self.store.set_active("nope")

    def test_full_pipeline_and_owner_edits(self):
        b = self.store.create({"business_name": "Acme Dental", "website": SITE, "location": "Kothrud, Pune",
                               "gbp_url": "https://www.google.com/maps/place/Acme"})
        br.run_research(self.store, b["id"], fetch=FakeWeb(), render=None, sleep=lambda s: None,
                        gbp=lambda brand: {"ok": True, "name": "Acme", "review_themes": []})
        self.assertTrue(self.store.read(b["id"], "research")["pages"])
        br.run_profile(self.store, b["id"], fake_llm([AI_PROFILE]))
        br.run_plan(self.store, b["id"], fake_llm([RuntimeError("x")]), start=date(2026, 11, 1))
        prof = self.store.read(b["id"], "profile")
        prof["brand_voice"]["avoid_words"] = "cheap, best in town"           # edited as a string in the UI
        prof["services"].append({"name": ""})                                  # junk row
        saved = br.save_edited(self.store, b["id"], "profile", prof)
        self.assertEqual(saved["brand_voice"]["avoid_words"], ["cheap", "best in town"])
        self.assertTrue(saved["edited_by_owner"])
        self.assertEqual(saved["generated_by"], "mock:model")
        plan = self.store.read(b["id"], "plan")
        plan["calendar"][0]["status"] = "approved"
        saved_plan = br.save_edited(self.store, b["id"], "plan", plan)
        self.assertEqual(saved_plan["calendar"][0]["status"], "approved")
        self.assertEqual(br.active_profile(self.store)["business_name"], "Acme Dental")

    def test_plan_requires_profile(self):
        b = self.store.create({"business_name": "x"})
        with self.assertRaises(ValueError):
            br.run_plan(self.store, b["id"], fake_llm([{}]))


class ProviderChain(unittest.TestCase):
    def test_default_llm_uses_configured_cloud_provider(self):
        sent = {}

        def post(url, payload, headers):
            sent.update(url=url, payload=payload, headers=headers)
            return 200, {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}
        os.environ["GROQ_API_KEY"] = "gk"
        try:
            text, label = br.default_llm("sys", "user", provider="groq", post=post)
        finally:
            del os.environ["GROQ_API_KEY"]
        self.assertEqual(text, '{"summary": "ok"}')
        self.assertTrue(label.startswith("groq:"))
        self.assertEqual(sent["headers"]["Authorization"], "Bearer gk")
        self.assertEqual(sent["payload"]["response_format"], {"type": "json_object"})

    def test_missing_key_raises_so_caller_can_fall_back(self):
        os.environ.pop("OPENAI_API_KEY", None)
        with self.assertRaises(RuntimeError):
            br.default_llm("s", "u", provider="openai", post=lambda *a: (200, {}))


if __name__ == "__main__":
    unittest.main()
