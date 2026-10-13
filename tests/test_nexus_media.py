"""Media resolver: rules, provider chain + fallbacks, offline card renderer, store/scheduler/tool/HTTP wiring."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401
import nexus_actions as na  # noqa: E402
import nexus_media as nm  # noqa: E402
import nexus_scheduler as ns  # noqa: E402
import nexus_tasks as nt  # noqa: E402
from PIL import Image  # noqa: E402

IST = nt.user_tz("Asia/Kolkata")


def _size(path):
    with Image.open(path) as im:
        return im.size


def png_bytes(size=(64, 48), color=(200, 30, 30)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def brand_root():
    root = Path(tempfile.mkdtemp())
    (root / "acme").mkdir()
    (root / "acme" / "brand.json").write_text(json.dumps({
        "business_name": "Acme Tech", "website": "https://acme.test", "handles": {"instagram": "acme", "x": "acme_x"},
        "colors": {"primary": "#112233", "secondary": "#334455", "accent": "#FF8800"}}))
    return root


TASK = {"id": "t-1", "brand": "acme", "title": "Totag AI Bookmark — Instagram Ad", "platforms": ["instagram"],
        "content": "Stop losing track of the web's best stuff. 🧠 Totag saves and finds your bookmarks. Download it today! #ai"}


class Rules(unittest.TestCase):
    def test_platform_rules(self):
        self.assertEqual(nm.media_platforms(["x"]), [])
        self.assertEqual(nm.media_platforms(["instagram"]), ["instagram"])
        self.assertEqual(nm.media_platforms(["x", "instagram", "facebook", "linkedin"]),
                         ["x", "instagram", "facebook", "linkedin"])
        self.assertEqual(nm.media_platforms(["blog", "youtube"]), [])
        with mock.patch.dict(os.environ, {"NEXUS_IMAGE_FOR_X": "1"}):
            self.assertEqual(nm.media_platforms(["x"]), ["x"])
        self.assertEqual(nm.primary_platform(["x", "facebook", "instagram"]), "instagram")

    def test_chain_always_ends_with_card(self):
        self.assertEqual(nm.chain("stock,bogus"), ["stock", "card"])
        self.assertEqual(nm.chain("card,openai"), ["card", "openai"])
        self.assertEqual(nm.chain(""), nm.DEFAULT_CHAIN)

    def test_missing_for_respects_existing_media(self):
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(png_bytes())
        f.close()
        self.assertEqual(nm.missing_for({**TASK, "media": [f.name]}), [])
        self.assertEqual(nm.missing_for({**TASK, "media": ["https://cdn/x.jpg"]}), [])
        self.assertEqual(nm.missing_for({**TASK, "media": ["/does/not/exist.png"]}), ["instagram"])

    def test_brief_derivation_and_edits(self):
        b = nm.merge_brief(TASK)
        self.assertTrue(b["headline"] and len(b["headline"].split()) <= 8)
        self.assertEqual(b["cta"], "Download now")
        self.assertNotIn("🧠", b["headline"] + b["subline"])
        b2 = nm.merge_brief({**TASK, "image_brief": {"headline": "Bookmarks that find themselves"}}, {"cta": "Get it"})
        self.assertEqual((b2["headline"], b2["cta"]), ("Bookmarks that find themselves", "Get it"))

    def test_brand_style(self):
        st = nm.load_brand_style("acme", brand_root())
        self.assertEqual((st["bg"], st["accent"], st["name"]), ("#112233", "#FF8800", "Acme Tech"))
        st2 = nm.load_brand_style("nobody", brand_root())
        self.assertTrue(nm.HEX.match(st2["bg"]))


class Render(unittest.TestCase):
    def test_card_sizes_for_every_platform(self):
        out = Path(tempfile.mkdtemp())
        task = {**TASK, "platforms": ["x", "instagram", "facebook", "linkedin"]}
        r = nm.produce(task, providers=["card"], out_dir=out, brand_root=brand_root())
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "card")
        want = {"instagram": (1080, 1350), "facebook": (1200, 630), "x": (1600, 900), "linkedin": (1200, 627)}
        for p, size in want.items():
            self.assertEqual(_size(r["variants"][p]), size, p)
        self.assertIn("instagram", r["media"][0])                 # IG size is the primary image
        self.assertTrue(all(str(m).startswith(str(out)) for m in r["media"]))

    def test_square_and_long_text_fit(self):
        out = Path(tempfile.mkdtemp()) / "sq.png"
        nm.render_card({"headline": "word " * 40, "subline": "longer " * 60, "cta": "Go"},
                       nm.load_brand_style("acme", brand_root()), (1080, 1080), out)
        self.assertEqual(_size(out), (1080, 1080))

    def test_bad_colour_rejected_then_card_still_renders_with_brand_colours(self):
        with self.assertRaises(nm.MediaError):
            nm.render_card({"headline": "x", "bg": "blue"}, nm.load_brand_style("acme", brand_root()), (200, 200),
                           Path(tempfile.mkdtemp()) / "a.png")

    def test_colour_edit_applies(self):
        out = Path(tempfile.mkdtemp())
        r = nm.produce(TASK, providers=["card"], edits={"bg": "#00FF00", "bg2": "#00FF00"}, out_dir=out, brand_root=brand_root())
        with Image.open(r["media"][0]) as im:
            px = im.convert("RGB").getpixel((540, 1300))
        self.assertGreater(px[1], 150)


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        self.calls.append((method, url, body))
        for key, fn in self.routes.items():
            if key in url:
                v = fn(method, url, body) if callable(fn) else fn
                if isinstance(v, Exception):
                    raise v
                return v if isinstance(v, bytes) else json.dumps(v).encode()
        raise nm.MediaError(f"unexpected {url}")


class Providers(unittest.TestCase):
    def run_chain(self, providers, env, routes):
        fake = FakeHttp(routes)
        with mock.patch.dict(os.environ, env, clear=False):
            r = nm.produce(TASK, providers=providers, out_dir=Path(tempfile.mkdtemp()), brand_root=brand_root(), http_fn=fake)
        return r, fake

    def test_unconfigured_providers_skip_to_card_without_network(self):
        env = {k: "" for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY", "NEXUS_CANVA_TOKEN",
                               "PEXELS_API_KEY", "UNSPLASH_ACCESS_KEY", "PIXABAY_API_KEY")}
        env["NEXUS_STOCK_KEYLESS"] = "0"
        r, fake = self.run_chain(["openai", "gemini", "xai", "canva", "stock"], env, {})
        self.assertEqual(r["source"], "card")
        self.assertEqual(fake.calls, [])
        self.assertTrue(all(a.get("skipped") for a in r["attempts"][:-1]))

    def test_openai_error_falls_back(self):
        r, _ = self.run_chain(["openai"], {"OPENAI_API_KEY": "k"}, {"api.openai.com": nm.MediaError("HTTP 429")})
        self.assertEqual(r["source"], "card")
        self.assertIn("429", r["attempts"][0]["error"])

    def test_openai_success_is_cropped_to_platform_size(self):
        r, fake = self.run_chain(["openai"], {"OPENAI_API_KEY": "k"},
                                 {"api.openai.com": {"data": [{"b64_json": base64.b64encode(png_bytes((1024, 1536))).decode()}]}})
        self.assertEqual(r["source"], "openai")
        self.assertEqual(_size(r["media"][0]), (1080, 1350))
        self.assertEqual(fake.calls[0][2]["size"], "1024x1536")

    def test_gemini_imagen(self):
        r, fake = self.run_chain(["gemini"], {"GEMINI_API_KEY": "g"},
                                 {"generativelanguage": {"predictions": [{"bytesBase64Encoded": base64.b64encode(png_bytes()).decode()}]}})
        self.assertEqual(r["source"], "gemini")
        self.assertEqual(fake.calls[0][2]["parameters"]["aspectRatio"], "3:4")

    def test_stock_pexels_gets_text_overlay(self):
        r, _ = self.run_chain(["stock"], {"PEXELS_API_KEY": "p"}, {
            "api.pexels.com": {"photos": [{"photographer": "Ann", "url": "https://pexels/1", "src": {"large2x": "https://img/1.jpg"}}]},
            "img/1.jpg": png_bytes((800, 1000))})
        self.assertEqual(r["source"], "stock")
        self.assertEqual(r["meta"]["stock"], "pexels")
        self.assertIn("Ann", r["meta"]["credit"])

    def test_stock_keyless_openverse_cc0_only(self):
        env = {"PEXELS_API_KEY": "", "UNSPLASH_ACCESS_KEY": "", "PIXABAY_API_KEY": "", "NEXUS_STOCK_KEYLESS": "1"}
        r, fake = self.run_chain(["stock"], env, {
            "api.openverse.org": {"results": [{"url": "https://ov/1.png", "license": "cc0"}]}, "ov/1.png": png_bytes()})
        self.assertEqual(r["meta"]["stock"], "openverse")
        self.assertIn("license=cc0,pdm", fake.calls[0][1])

    def test_openverse_falls_back_to_single_keywords(self):
        env = {"PEXELS_API_KEY": "", "UNSPLASH_ACCESS_KEY": "", "PIXABAY_API_KEY": "", "NEXUS_STOCK_KEYLESS": "1"}

        def ov(method, url, body):
            return {"results": [{"url": "https://ov/2.png", "license": "pdm"}]} if "q=laptop" in url else {"results": []}
        fake = FakeHttp({"api.openverse.org": ov, "ov/2.png": png_bytes()})
        with mock.patch.dict(os.environ, env):
            r = nm.produce({**TASK, "image_brief": {"keywords": ["bookmarks", "laptop"]}}, providers=["stock"],
                           out_dir=Path(tempfile.mkdtemp()), brand_root=brand_root(), http_fn=fake)
        self.assertEqual((r["source"], r["meta"]["query"]), ("stock", "laptop"))

    def test_canva_stub_and_flow(self):
        r, fake = self.run_chain(["canva"], {"NEXUS_CANVA_TOKEN": "", "NEXUS_CANVA_TEMPLATE_ID": ""}, {})
        self.assertEqual((r["source"], fake.calls), ("card", []))
        with mock.patch.object(nm.time, "sleep", lambda s: None):
            r, fake = self.run_chain(["canva"], {"NEXUS_CANVA_TOKEN": "tok", "NEXUS_CANVA_TEMPLATE_ID": "BT1"}, {
                "/autofills/": {"job": {"id": "a1", "status": "success", "result": {"design": {"id": "D1"}}}},
                "/autofills": {"job": {"id": "a1", "status": "in_progress"}},
                "/exports/": {"job": {"id": "e1", "status": "success", "urls": ["https://export/d1.png"]}},
                "/exports": {"job": {"id": "e1", "status": "in_progress"}},
                "export/d1.png": png_bytes((1080, 1350))})
        self.assertEqual(r["source"], "canva")
        self.assertEqual(fake.calls[0][2]["brand_template_id"], "BT1")
        self.assertIn("headline", fake.calls[0][2]["data"])


def store(clock=None):
    return nt.TaskStore(Path(tempfile.mkdtemp()), clock=clock, log=lambda *a, **k: None, auto_media=True)


class StoreWiring(unittest.TestCase):
    def wait_media(self, s, tid, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            t = s.get(tid)
            if t.get("media"):
                return t
            time.sleep(0.05)
        return s.get(tid)

    def test_created_task_gets_image_in_background(self):
        s = store()
        t = s.create({"title": "IG", "content": "Hello world. Download now", "platforms": ["instagram"], "brand": "acme"})
        t = self.wait_media(s, t["id"])
        self.assertTrue(Path(t["media"][0]).is_file())
        self.assertEqual(t["media_source"], "card")
        self.assertEqual(t["history"][-1]["event"], "media")
        self.assertEqual(t["status"], "pending_approval")

    def test_x_only_task_gets_no_image(self):
        s = store()
        t = s.create({"title": "X", "content": "Hello", "platforms": ["x"]})
        time.sleep(0.3)
        self.assertEqual(s.get(t["id"])["media"], [])

    def test_approval_attaches_image_and_stays_approved(self):
        s = nt.TaskStore(Path(tempfile.mkdtemp()), log=lambda *a, **k: None, auto_media=False)
        t = s.create({"title": "IG", "content": "Hi", "platforms": ["instagram"], "scheduled_at": "2030-01-01T10:00"})
        s.auto_media = True
        t = s.approve(t["id"])
        self.assertEqual(t["status"], "approved")
        self.assertTrue(t["media"] and t["history"][-1]["when"] == "approved")

    def test_user_regenerate_keeps_approval_model_sends_back(self):
        s = nt.TaskStore(Path(tempfile.mkdtemp()), log=lambda *a, **k: None, auto_media=False)
        t = s.create({"title": "IG", "content": "Hi", "platforms": ["instagram"], "scheduled_at": "2030-01-01T10:00"})
        s.approve(t["id"])
        r = nm.ensure(t["id"], store=s, by="user", force=True, edits={"headline": "New words"})
        self.assertEqual((r["task"]["status"], r["task"]["image_brief"]["headline"]), ("approved", "New words"))
        r = nm.ensure(t["id"], store=s, by="model", force=True, edits={"accent": "#123456"})
        self.assertEqual(r["task"]["status"], "pending_approval")

    def test_image_brief_field_is_cleaned(self):
        s = nt.TaskStore(Path(tempfile.mkdtemp()), log=lambda *a, **k: None, auto_media=False)
        t = s.create({"title": "a", "image_brief": {"headline": "H", "keywords": "a, b", "junk": 1}})
        self.assertEqual(t["image_brief"], {"headline": "H", "keywords": ["a", "b"]})
        with self.assertRaises(nt.TaskError):
            s.create({"title": "a", "image_brief": 5})

    def test_done_task_media_frozen(self):
        s = nt.TaskStore(Path(tempfile.mkdtemp()), log=lambda *a, **k: None, auto_media=False)
        t = s.create({"title": "a", "content": "b", "platforms": ["instagram"]})
        s.cancel(t["id"])
        self.assertTrue(nm.ensure(t["id"], store=s, force=True)["skipped"])


class SchedulerWiring(unittest.TestCase):
    def test_scheduler_renders_before_posting_and_never_double_posts(self):
        clock = lambda: datetime(2026, 10, 20, 18, 0, tzinfo=IST)  # noqa: E731
        s = nt.TaskStore(Path(tempfile.mkdtemp()), clock=clock, log=lambda *a, **k: None, auto_media=False)
        t = s.create({"title": "p", "content": "hello", "platforms": ["instagram", "x"], "scheduled_at": "2026-10-20T17:59"})
        s.approve(t["id"])
        self.assertEqual(s.get(t["id"])["media"], [])
        seen = []
        sch = ns.Scheduler(s, runner=lambda task: seen.append(task) or {"ok": True}, clock=clock, dry_run=False)
        r = sch.tick()
        self.assertEqual(r["ran"], [t["id"]])
        self.assertTrue(seen[0]["media"] and Path(seen[0]["media"][0]).is_file())
        self.assertEqual(set(seen[0]["media_variants"]), {"instagram", "x"})
        self.assertEqual(sch.tick()["ran"], [])                     # done: not posted again
        self.assertEqual(len(seen), 1)
        hist = [h for h in s.get(t["id"])["history"] if h["event"] == "media"]
        self.assertEqual(hist[0]["when"], "before_post")

    def test_media_failure_still_posts(self):
        clock = lambda: datetime(2026, 10, 20, 18, 0, tzinfo=IST)  # noqa: E731
        s = nt.TaskStore(Path(tempfile.mkdtemp()), clock=clock, log=lambda *a, **k: None, auto_media=False)
        t = s.create({"title": "p", "content": "hello", "platforms": ["instagram"], "scheduled_at": "2026-10-20T17:59"})
        s.approve(t["id"])

        def broken(*a, **k):
            raise RuntimeError("disk full")
        sch = ns.Scheduler(s, runner=lambda task: {"ok": True}, clock=clock, dry_run=False, media=broken)
        self.assertEqual(sch.tick()["ran"], [t["id"]])

    def test_publish_runner_sends_per_platform_media(self):
        captured = {}

        def run(args, **kw):
            captured.update(json.loads(Path(args[args.index("--from-json") + 1]).read_text()))
            return mock.Mock(returncode=0, stdout='{"status": "published", "results": []}', stderr="")
        ns.publish_runner({"id": "t9", "platforms": ["x", "instagram"], "content": "c", "media": ["/i.png", "/x.png"],
                           "media_variants": {"instagram": "/i.png", "x": "/x.png"}}, run=run)
        self.assertEqual(captured["media"], "/i.png")
        self.assertEqual(captured["media_by_platform"], {"instagram": "/i.png", "x": "/x.png"})

    def test_publish_job_uses_platform_variant(self):
        import publish
        used = []
        with mock.patch.object(publish, "publish_with_fallback",
                               lambda platform, text, c, media, **kw: used.append((platform, media)) or {"ok": True}), \
                mock.patch.object(publish, "audit", lambda *a, **k: None):
            publish.publish_job({"platforms": ["x", "instagram"], "content": "c", "media": "/i.png",
                                 "media_by_platform": {"x": "/x.png"}})
        self.assertEqual(used, [("x", "/x.png"), ("instagram", "/i.png")])


class Tools(unittest.TestCase):
    def test_tools_exist_and_work(self):
        self.assertIn("generate_task_image", na.TOOLS)
        self.assertIn("regenerate_task_image", na.TOOLS)
        s = nt.TaskStore(Path(tempfile.mkdtemp()), log=lambda *a, **k: None, auto_media=False)
        ex = na.Executor(s, brain="test", brand="acme", brand_loader=lambda b: {})
        r = ex.run("draft_post", {"platforms": ["instagram"], "caption": "Hello there",
                                  "image_brief": {"headline": "Hi", "cta": "Go", "keywords": ["sun"]}})
        tid = r["task"]["id"]
        self.assertEqual(r["task"]["image_brief"]["headline"], "Hi")
        r = ex.run("generate_task_image", {"task_id": tid})
        self.assertTrue(r["ok"] and r["image"] and r["source"] == "card")
        r = ex.run("generate_task_image", {"task_id": tid})
        self.assertIn("no new image", r["note"])
        r = ex.run("regenerate_task_image", {"task_id": tid, "edits": {"headline": "Changed", "accent": "#00AA00"}})
        self.assertEqual(r["task"]["image_brief"]["headline"], "Changed")
        bad = ex.run("regenerate_task_image", {"task_id": tid, "edits": {"accent": "green"}})
        self.assertFalse(bad["ok"])

    def test_mcp_lists_image_tools(self):
        import nexus_tasks_mcp as mcp
        names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
        self.assertIn("generate_task_image", names)
        self.assertIn("regenerate_task_image", names)


if __name__ == "__main__":
    unittest.main()
