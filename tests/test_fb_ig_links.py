"""Facebook Page flow, Instagram crop/upload review, and link memory (fake page, no browser)."""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT, load_agent  # noqa: E402
from test_browser_flows import IG_OK, FakePage, core, flows, nopace  # noqa: E402

lm = flows.link_memory
PAGE = "https://www.facebook.com/EXAMPLEBRAND"
# Throw-away client config (the main app ships none): curated social links.
_CLIENT_DIR = tempfile.mkdtemp(prefix="nexus-client-")
CLIENT_JSON = str(Path(_CLIENT_DIR) / "client.json")
Path(CLIENT_JSON).write_text(json.dumps({"client_id": "examplebrand", "social_links": {
    "x": {"handle": "@ExampleBrand", "profile": "https://x.com/ExampleBrand", "compose": "https://x.com/compose/post"},
    "instagram": {"handle": "@examplebrand", "profile": "https://www.instagram.com/examplebrand/",
                  "home": "https://www.instagram.com/"},
    "facebook": {"page": PAGE, "your_pages": "https://www.facebook.com/pages/?category=your_pages"}}}))


def png(path: Path, w: int, h: int) -> str:
    raw = b"".join(b"\x00" + b"\x00" * w * 3 for _ in range(1))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.write_bytes(data)
    return str(path)


class TempMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"NEXUS_LINK_MEMORY": str(Path(self.tmp.name) / "lm.json")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def mem(self):
        return json.loads(Path(os.environ["NEXUS_LINK_MEMORY"]).read_text())


FB_OK = {
    "[aria-label='Your profile']": {},
    "role:button:What's on your mind": {},
    "[role='dialog'] div[contenteditable='true'][role='textbox']": {},
    "[role='dialog'] input[type='file'][accept*='image']": {},
    "[role='dialog'] [aria-label='Post'][role='button']": {},
}


def composer_closes(p):
    p.elements.pop("[role='dialog'] [aria-label='Post'][role='button']", None)


class FacebookPageFlow(TempMemory):
    def setUp(self):
        super().setUp()
        self.img = png(Path(self.tmp.name) / "poster.png", 1080, 1350)

    def test_dry_run_types_attaches_and_stops_before_post(self):
        page = FakePage(dict(FB_OK))
        r = flows.facebook_page_post(page, "Party packs 🪔", [self.img], page_url=PAGE, dry_run=True,
                                     pacer=nopace(), screenshot=lambda t: f"/s/{t}.png")
        self.assertTrue(r["dry_run"])
        self.assertEqual(page.typed, "Party packs 🪔")
        self.assertEqual(page.uploads, [self.img])
        self.assertNotIn("[role='dialog'] [aria-label='Post'][role='button']", page.clicks)
        self.assertEqual(r["screenshot"], "/s/dryrun.png")
        self.assertGreaterEqual(page.fronted, 1)
        self.assertFalse(r["switched"])
        self.assertEqual(self.mem()["platforms"]["facebook"][0]["url"], PAGE)

    def test_page_url_comes_from_client_config(self):
        page = FakePage(dict(FB_OK))
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": CLIENT_JSON}):
            r = flows.facebook_page_post(page, "hi", dry_run=True, pacer=nopace())
        self.assertEqual(page.visits[0], PAGE)
        self.assertEqual(r["page_url"], PAGE)

    def test_no_page_url_is_a_clear_error(self):
        with self.assertRaises(flows.FlowError):
            flows.facebook_page_post(FakePage(dict(FB_OK)), "hi", dry_run=True, pacer=nopace())

    def test_switches_into_the_page_profile_first(self):
        els = dict(FB_OK)
        els["role:button:Switch now"] = {"on_click": lambda p: (
            p.elements.pop("role:button:Switch now"),
            p.elements.__setitem__("[role='dialog'] [role='button']:has-text('Switch')", {}))}
        page = FakePage(els)
        r = flows.facebook_page_post(page, "hi", page_url=PAGE, dry_run=True, pacer=nopace())
        self.assertTrue(r["switched"])
        self.assertIn("role:button:Switch now", page.clicks)
        self.assertIn("[role='dialog'] [role='button']:has-text('Switch')", page.clicks)
        self.assertEqual(page.visits, [PAGE, PAGE])

    def test_blocked_post_click_falls_back_to_keyboard_and_confirms(self):
        els = dict(FB_OK)
        els["[role='dialog'] [aria-label='Post'][role='button']"] = {"blocked": True, "on_click": composer_closes}
        page = FakePage(els)
        r = flows.facebook_page_post(page, "hi", [self.img], page_url=PAGE, pacer=nopace())
        self.assertTrue(r["ok"])
        self.assertIn("Enter", page.presses)
        self.assertIn("key:[role='dialog'] [aria-label='Post'][role='button']", page.clicks)

    def test_real_post_records_the_new_permalink(self):
        els = dict(FB_OK)
        els["[role='dialog'] [aria-label='Post'][role='button']"] = {"on_click": composer_closes}
        page = FakePage(els)
        page.eval_result = ["https://www.facebook.com/EXAMPLEBRAND/posts/pfbid0abc?__cft__=x"]
        r = flows.facebook_page_post(page, "hi", page_url=PAGE, pacer=nopace())
        self.assertEqual(r["permalink"], "https://www.facebook.com/EXAMPLEBRAND/posts/pfbid0abc")
        kinds = {i["kind"] for i in self.mem()["platforms"]["facebook"]}
        self.assertEqual(kinds, {"page", "post"})

    def test_unconfirmed_post_fails(self):
        page = FakePage(dict(FB_OK))
        with self.assertRaises(flows.FlowError):
            flows.facebook_page_post(page, "hi", page_url=PAGE, pacer=nopace())

    def test_destructive_dialog_is_never_confirmed(self):
        els = dict(FB_OK)
        els["text:Discard post?"] = {}
        els["role:button:Discard"] = {}
        page = FakePage(els)
        with self.assertRaises(flows.FlowError):
            flows.facebook_page_post(page, "hi", page_url=PAGE, dry_run=True, pacer=nopace())
        self.assertNotIn("role:button:Discard", page.clicks)
        self.assertNotIn("[role='dialog'] [aria-label='Post'][role='button']", page.clicks)

    def test_logged_out_clicks_nothing(self):
        page = FakePage(dict(FB_OK), url_after_goto="https://www.facebook.com/login/")
        with self.assertRaises(core.LoggedOut):
            flows.facebook_page_post(page, "hi", page_url=PAGE, dry_run=True, pacer=nopace())
        self.assertEqual(page.clicks, [])

    def test_photo_button_file_chooser_when_no_input_is_attached(self):
        els = dict(FB_OK)
        els.pop("[role='dialog'] input[type='file'][accept*='image']")
        els["role:button:Photo/video"] = {}
        page = FakePage(els)
        flows.facebook_page_post(page, "hi", [self.img], page_url=PAGE, dry_run=True, pacer=nopace())
        self.assertEqual(page.uploads, [self.img])
        self.assertIn("role:button:Photo/video", page.clicks)

    def test_validation_and_publish_routing(self):
        with self.assertRaises(flows.FlowError):
            flows.validate_facebook("", [])
        with self.assertRaises(flows.FlowError):
            flows.validate_facebook("hi", ["/nope.png"])
        pub = load_agent("publish")
        args = pub.browser_args_for("facebook", "cap", self.img, True)
        self.assertEqual(args, ["--allow-actions", "fb_post", "--text", "cap", "--media", self.img, "--dry-run"])

    def test_cli_has_fb_post_and_links(self):
        sba = load_agent("social_browser_agent")
        a = sba.build_parser().parse_args(["fb_post", "--text-file", "t.txt", "--media", "p.png", "--dry-run"])
        self.assertEqual((a.command, a.text_file, a.media, a.dry_run), ("fb_post", "t.txt", ["p.png"], True))
        a = sba.build_parser().parse_args(["ig_post", "--caption-file", "c.txt", "--media", "p.png"])
        self.assertEqual(a.caption_file, "c.txt")


IG_CROP = {"[role='dialog'] svg[aria-label='Select crop']": {},
           "[role='dialog'] div[role='button']:has-text('Original')": {},
           "[role='dialog'] div[role='button']:has-text('4:5')": {}}


class InstagramReview(TempMemory):
    def test_4x5_poster_picks_4x5_crop_and_closes_menu(self):
        img = png(Path(self.tmp.name) / "p.png", 1080, 1350)
        page = FakePage({**IG_OK, **IG_CROP})
        r = flows.instagram_post(page, "cap", [img], dry_run=True, pacer=nopace())
        self.assertEqual(r["crop"], "4:5")
        self.assertEqual(page.clicks.count("[role='dialog'] svg[aria-label='Select crop']"), 2)
        self.assertLess(page.clicks.index("[role='dialog'] div[role='button']:has-text('4:5')"),
                        page.clicks.index("role:button:Next"))
        self.assertNotIn("role:button:Share", page.clicks)
        self.assertGreaterEqual(page.fronted, 1)

    def test_other_ratio_keeps_original(self):
        img = png(Path(self.tmp.name) / "w.png", 1600, 900)
        r = flows.instagram_post(FakePage({**IG_OK, **IG_CROP}), "cap", [img], dry_run=True, pacer=nopace())
        self.assertEqual(r["crop"], "original")

    def test_image_size_reads_png_and_jpeg_headers(self):
        self.assertEqual(flows.image_size(png(Path(self.tmp.name) / "a.png", 1080, 1350)), (1080, 1350))
        jpg = Path(self.tmp.name) / "a.jpg"
        jpg.write_bytes(b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00"
                        + b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", 900, 1600) + b"\x03")
        self.assertEqual(flows.image_size(str(jpg)), (1600, 900))
        self.assertIsNone(flows.image_size("/nope"))

    def test_discard_prompt_stops_before_share(self):
        img = png(Path(self.tmp.name) / "p.png", 1080, 1080)
        page = FakePage({**IG_OK, "text:Discard post?": {}})
        with self.assertRaises(flows.FlowError):
            flows.instagram_post(page, "cap", [img], pacer=nopace())
        self.assertNotIn("role:button:Share", page.clicks)

    def test_hidden_input_missing_uses_select_from_computer(self):
        img = png(Path(self.tmp.name) / "p.png", 1080, 1080)
        els = dict(IG_OK)
        els.pop("[role='dialog'] input[type='file']")
        els["role:button:Select from computer"] = {}
        page = FakePage(els)
        flows.instagram_post(page, "cap", [img], dry_run=True, pacer=nopace())
        self.assertEqual(page.uploads, [img])

    def test_blocked_share_uses_keyboard(self):
        img = png(Path(self.tmp.name) / "p.png", 1080, 1080)
        els = dict(IG_OK)
        els["role:button:Share"] = {"blocked": True,
                                    "on_click": lambda p: p.elements.__setitem__("text:Your post has been shared.", {})}
        r = flows.instagram_post(FakePage(els), "cap", [img], pacer=nopace())
        self.assertTrue(r["ok"])


class LinkMemory(TempMemory):
    def test_record_normalizes_dedupes_and_counts(self):
        lm.record("x", "profile", "https://X.com/ExampleBrand/?s=20#top")
        lm.record("x", "profile", "https://x.com/ExampleBrand")
        lm.record("x", "post", "https://x.com/ExampleBrand/status/1")
        links = self.mem()["platforms"]["x"]
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["url"], "https://x.com/ExampleBrand")
        self.assertEqual(links[0]["count"], 2)
        self.assertTrue(links[0]["first_seen"] <= links[0]["last_seen"])
        self.assertEqual(lm.normalize("https://www.facebook.com/permalink.php?story_fbid=9&id=7&ref=x"),
                         "https://www.facebook.com/permalink.php?story_fbid=9&id=7")
        self.assertIsNone(lm.record("x", "post", "javascript:alert(1)"))

    def test_memory_file_is_merged_not_overwritten(self):
        Path(os.environ["NEXUS_LINK_MEMORY"]).write_text(json.dumps(
            {"brand": "examplebrand", "platforms": {"instagram": [{"url": "https://www.instagram.com/examplebrand",
             "kind": "profile", "label": "", "first_seen": "a", "last_seen": "a", "count": 1}]}}))
        lm.record("x", "compose", "https://x.com/compose/post")
        self.assertEqual(set(self.mem()["platforms"]), {"instagram", "x"})

    def test_preferred_config_then_memory_then_default(self):
        self.assertEqual(lm.preferred("x", "compose", "D"), "D")
        lm.record("x", "compose", "https://x.com/compose/tweet")
        self.assertEqual(lm.preferred("x", "compose", "D"), "https://x.com/compose/tweet")
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": CLIENT_JSON}):
            self.assertEqual(lm.preferred("x", "compose", "D"), "https://x.com/compose/post")
            self.assertEqual(lm.preferred("facebook", "page"), PAGE)
            v = lm.view()
            self.assertEqual(v["platforms"]["facebook"][0]["source"], "config")
            self.assertIn("instagram", v["platforms"])

    def test_bad_path_never_breaks_a_flow(self):
        with mock.patch.dict(os.environ, {"NEXUS_LINK_MEMORY": "/proc/forbidden/lm.json"}):
            self.assertIsNone(lm.record("x", "profile", "https://x.com/a"))

    def test_x_flow_records_compose_and_newest_post_ignoring_pinned_order(self):
        page = FakePage({"[data-testid='tweetTextarea_0']": {}, "[data-testid='tweetButton']": {}})
        sent = lambda p: p.elements.__setitem__("text:Your post was sent", {})   # noqa: E731
        page.on_key = {"Meta+Enter": sent, "Control+Enter": sent}
        page.eval_result = ["https://x.com/ExampleBrand/status/2109719434294346122",   # pinned, older
                            "https://x.com/ExampleBrand/status/2109732111422988710"]
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": CLIENT_JSON}):
            r = flows.x_post(page, "hi", pacer=nopace())
        self.assertEqual(r["permalink"], "https://x.com/ExampleBrand/status/2109732111422988710")
        self.assertEqual(page.visits, ["https://x.com/compose/post", "https://x.com/ExampleBrand"])
        kinds = {(i["kind"], i["url"]) for i in self.mem()["platforms"]["x"]}
        self.assertIn(("compose", "https://x.com/compose/post"), kinds)

    def test_check_login_remembers_the_home_page(self):
        page = FakePage({"[aria-label='Your profile']": {}})
        r = flows.check_login(page, "facebook", "https://www.facebook.com/", pacer=nopace())
        self.assertTrue(r["logged_in"])
        self.assertEqual(self.mem()["platforms"]["facebook"][0]["kind"], "home")


class LinksEndpoint(TempMemory):
    def test_relay_serves_links_with_token(self):
        import test_relay_http as trh
        lm.record("x", "profile", "https://x.com/ExampleBrand")

        class Case(trh.RelayServerCase):
            def runTest(self):
                pass
        Case.setUpClass()
        try:
            c = Case()
            code, _ = c.request("GET", "/links")
            self.assertEqual(code, 401)
            code, body = c.request("GET", "/links", token=trh.TOKEN)
            self.assertEqual(code, 200)
            self.assertEqual(body["platforms"]["x"][0]["url"], "https://x.com/ExampleBrand")
        finally:
            Case.tearDownClass()



class BrandSocialLinks(TempMemory):
    """Main app: links come from the active brand (brand.json), not from code."""

    def test_links_from_active_brand_handles_and_explicit_social_links(self):
        cc = load_agent("client_config")
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {
                "NEXUS_BRANDS_DIR": d, "NEXUS_CLIENT_CONFIG": str(Path(d) / "none.json")}):
            os.environ.pop("NEXUS_BRAND", None)   # a real .env may pin another brand
            (Path(d) / "acme").mkdir()
            (Path(d) / "acme" / "brand.json").write_text(json.dumps(
                {"id": "acme", "handles": {"twitter": "@AcmeCo", "facebook": "AcmePage"}}))
            (Path(d) / "active.txt").write_text("acme")
            self.assertEqual(cc.active_brand_id(), "acme")
            self.assertEqual(lm.preferred("x", "profile"), "https://x.com/AcmeCo")
            self.assertEqual(lm.preferred("facebook", "page"), "https://www.facebook.com/AcmePage")
            (Path(d) / "acme" / "brand.json").write_text(json.dumps(
                {"id": "acme", "social_links": {"facebook": {"page": "https://www.facebook.com/acme.official"}}}))
            self.assertEqual(lm.preferred("facebook", "page"), "https://www.facebook.com/acme.official")
            os.environ.pop("NEXUS_LINK_MEMORY")
            self.assertEqual(lm.memory_path(), Path(d) / "acme" / "link_memory.json")

if __name__ == "__main__":
    unittest.main()
