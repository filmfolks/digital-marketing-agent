"""Brand contacts + post signature, and BrandStore contact persistence."""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

bs = load_agent("brand_signature")
br = load_agent("brand_research")

BRAND = {
    "id": "acme",
    "business_name": "Acme Dental",
    "website": "https://www.acmedental.in/",
    "mobile": "+91 98765 43210",
    "email": "hello@acmedental.in",
    "social_links": {"instagram": {"profile": "https://instagram.com/acmedental"},
                     "facebook": "https://facebook.com/acmedental", "twitter": "https://x.com/acmedental"},
}


class _BrandsDir(unittest.TestCase):
    """Points NEXUS_BRANDS_DIR at a throwaway folder and restores it."""

    def setUp(self):
        self._old = os.environ.get("NEXUS_BRANDS_DIR")
        self.dir = Path(tempfile.mkdtemp(prefix="nexus-brands-"))
        os.environ["NEXUS_BRANDS_DIR"] = str(self.dir)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("NEXUS_BRANDS_DIR", None)
        else:
            os.environ["NEXUS_BRANDS_DIR"] = self._old


class NormalisationTests(_BrandsDir):
    def test_clean_phone_email_and_wa(self):
        self.assertEqual(bs.clean_phone("(+91) 98765 43210"), "+91 98765 43210")
        self.assertEqual(bs.wa_number("+91 98765 43210"), "+919876543210")
        self.assertEqual(bs.clean_email("Sales <HELLO@Acme.IN>"), "hello@acme.in")
        self.assertEqual(bs.clean_email("not an email"), "")
        self.assertEqual(bs.clean_email("mailto:hi@acme.in"), "hi@acme.in")

    def test_social_links_clean_ordered_and_complete(self):
        out = bs.clean_social_links({"twitter": "@acme", "instagram": {"url": "https://www.instagram.com/acme/"},
                                     "junk": "https://x.com/y"})
        self.assertEqual(list(out), ["instagram", "x"])
        self.assertEqual(out["instagram"], "https://www.instagram.com/acme/")
        self.assertEqual(out["x"], "https://x.com/acme")

    def test_normalise_contacts_is_partial(self):
        out = bs.normalise_contacts({"mobile": "+1 (555) 010-9999"}, existing={"whatsapp": "keep me"})
        self.assertEqual(out, {"mobile": "+1 555 010-9999"})
        self.assertEqual(bs.normalise_contacts({}), {})

    def test_signature_settings_merge_not_clobber(self):
        existing = {"signature": {"enabled": True, "emoji": False}}
        out = bs.normalise_contacts({"signature": {"include": "phone,website"}}, existing=existing)
        self.assertEqual(out["signature"], {"enabled": True, "emoji": False, "include": "phone,website"})


class SignatureTests(_BrandsDir):
    def test_build_with_emoji(self):
        text = bs.signature_text(BRAND, "instagram")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Acme Dental")
        self.assertIn("📞 +91 98765 43210", text)
        self.assertIn("hello@acmedental.in", text)
        self.assertIn("🌐 acmedental.in", text)
        self.assertIn("IG instagram.com/acmedental", text)
        self.assertIn("X x.com/acmedental", text)

    def test_emoji_policy_none_uses_plain_prefix(self):
        brand = {**BRAND, "brand_voice": {"emoji_policy": "none"}}
        text = bs.signature_text(brand, "instagram")
        self.assertIn("Call: +91 98765 43210", text)
        self.assertNotIn("📞", text)
        self.assertIn("acmedental.in", text)

    def test_single_line_for_x_is_compact(self):
        line = bs.signature_text(BRAND, "x")
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("Acme Dental", line)
        self.assertIn("acmedental.in", line)

    def test_disabled_and_custom(self):
        self.assertEqual(bs.signature_text({**BRAND, "signature": {"enabled": False}}), "")
        self.assertEqual(bs.signature_text({**BRAND, "signature": {"custom": "Acme\nPune"}}), "Acme\nPune")
        self.assertEqual(bs.signature_text({}), "")

    def test_apply_is_idempotent(self):
        once = bs.apply_signature("Fresh smile tips.", "instagram", brand=BRAND)
        twice = bs.apply_signature(once, "instagram", brand=BRAND)
        self.assertEqual(once, twice)
        self.assertTrue(once.startswith("Fresh smile tips."))

    def test_apply_trims_content_not_signature_for_x(self):
        body = "word " * 200
        out = bs.apply_signature(body.strip(), "x", brand=BRAND)
        self.assertLessEqual(len(out), 280)
        self.assertIn("Acme Dental", out)
        self.assertTrue(out.rstrip()[-1].isalnum(), "signature must not be cut")

    def test_apply_max_len_zero_means_unlimited(self):
        body = "word " * 200
        out = bs.apply_signature(body.strip(), "x", brand=BRAND, max_len=0)
        self.assertGreater(len(out), 280)

    def test_apply_without_brand_returns_original(self):
        self.assertEqual(bs.apply_signature("hi", "instagram", brand={}), "hi")


class LogoTests(_BrandsDir):
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    def test_save_detect_and_remove(self):
        data_url = "data:image/png;base64," + base64.b64encode(self.PNG).decode()
        out = bs.save_logo("acme", data_url)
        self.assertTrue(out["ok"], out)
        self.assertTrue(Path(out["logo"]).is_file())
        self.assertEqual(bs.detect_image(self.PNG), ".png")
        self.assertEqual(bs.logo_for("acme"), out["logo"])
        self.assertTrue(bs.remove_logo("acme")["removed"])
        self.assertEqual(bs.logo_for("acme"), "")

    def test_rejects_bad_input(self):
        self.assertFalse(bs.save_logo("../etc", "data:image/png;base64,AAAA")["ok"])
        self.assertFalse(bs.save_logo("acme", "data:image/png;base64,not base64!!")["ok"])
        self.assertFalse(bs.save_logo("acme", "data:text/plain;base64,"
                                      + base64.b64encode(b"hello").decode())["ok"])


class BrandStoreContactsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nexus-brands-"))
        self.store = br.BrandStore(self.dir)

    def test_create_persists_contacts_and_patch_merges(self):
        brand = self.store.create({"business_name": "Acme Dental", "website": "https://acmedental.in",
                                   "mobile": "+91 98765 43210", "email": "hello@acmedental.in",
                                   "social_links": {"instagram": "@acmedental"}})
        self.assertEqual(brand["mobile"], "+91 98765 43210")
        self.assertEqual(brand["email"], "hello@acmedental.in")
        self.assertEqual(brand["social_links"], {"instagram": "https://www.instagram.com/acmedental/"})

        patched = self.store.patch(brand["id"], {"logo": "/tmp/logo.png"})
        self.assertEqual(patched["logo"], "/tmp/logo.png")
        self.assertEqual(patched["mobile"], "+91 98765 43210", "patch must not drop other fields")

        reread = self.store.read(brand["id"], "brand")
        self.assertEqual(reread["logo"], "/tmp/logo.png")
        self.assertEqual(reread["email"], "hello@acmedental.in")


class PublishSignatureTests(unittest.TestCase):
    def test_publish_signed_text_uses_active_brand(self):
        pub = load_agent("publish")
        old = os.environ.get("NEXUS_BRANDS_DIR")
        d = Path(tempfile.mkdtemp(prefix="nexus-brands-"))
        os.environ["NEXUS_BRANDS_DIR"] = str(d)
        try:
            br.BrandStore(d).create({"business_name": "Acme Dental", "website": "https://acmedental.in",
                                     "mobile": "+91 98765 43210"})
            out = pub.signed_text("Big news!", "instagram")
            self.assertIn("Acme Dental", out)
            self.assertIn("Big news!", out)
        finally:
            if old is None:
                os.environ.pop("NEXUS_BRANDS_DIR", None)
            else:
                os.environ["NEXUS_BRANDS_DIR"] = old


if __name__ == "__main__":
    unittest.main()
