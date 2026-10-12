"""Regression tests for agent/publish.py and agent/browser_automation.py."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

publish = load_agent("publish")
browser = load_agent("browser_automation")


class TestScheduleParsing(unittest.TestCase):
    def test_iso_with_z_is_utc(self):
        got = publish.parse_when("2030-01-01T09:00:00.000Z")
        self.assertEqual(got, datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc))

    def test_offset_is_honoured(self):
        got = publish.parse_when("2030-01-01T09:00:00+02:00")
        self.assertEqual(got.hour, 7)          # normalised to UTC

    def test_naive_timestamp_is_treated_as_utc(self):
        """A naive value used to raise TypeError when compared with an aware
        `now`, crashing the whole --due batch."""
        got = publish.parse_when("2030-01-01T09:00:00")
        self.assertIsNotNone(got)
        self.assertEqual(got.tzinfo, timezone.utc)

    def test_nonsense_returns_none_instead_of_publishing_early(self):
        for bad in ("tomorrow", "", None, "not-a-date", 42):
            self.assertIsNone(publish.parse_when(bad), repr(bad))


class TestCredentialResolution(unittest.TestCase):
    def test_payload_wins_over_env(self):
        c = publish.creds_from({"credentials": {"fb-page-token": "from-payload",
                                                "fb-page-id": "123"}})
        self.assertEqual(c["fb_token"], "from-payload")
        self.assertEqual(c["fb_page"], "123")

    def test_missing_credentials_produce_empty_strings_not_none(self):
        c = publish.creds_from({})
        for key, value in c.items():
            self.assertIsInstance(value, str, key)


class TestPublishersFailClosed(unittest.TestCase):
    def test_each_publisher_reports_a_clear_error_without_credentials(self):
        c = publish.creds_from({})
        cases = {
            "facebook": publish.post_facebook,
            "instagram": publish.post_instagram,
            "twitter": publish.post_twitter,
            "linkedin": publish.post_linkedin,
            "blogger": publish.post_blogger,
        }
        for name, fn in cases.items():
            res = fn("hello", c)
            self.assertFalse(res.get("ok"), name)
            self.assertTrue(res.get("error"), name)

    def test_instagram_requires_media_url(self):
        c = publish.creds_from({"credentials": {"ig-access-token": "t", "ig-account-id": "1"}})
        res = publish.post_instagram("caption", c, media="")
        self.assertFalse(res["ok"])
        self.assertIn("image_url", res["error"])

    def test_dry_run_never_claims_to_have_published(self):
        job = {"platforms": ["facebook"], "content": "x", "credentials": {}}
        result = publish.publish_job(job, dry_run=True)
        self.assertEqual(result["status"], "validated")

    def test_unknown_platform_is_reported(self):
        result = publish.publish_job({"platforms": ["myspace"], "content": "x"}, dry_run=True)
        self.assertEqual(result["status"], "failed")


class TestBrowserSafetyGate(unittest.TestCase):
    def test_deny_list_catches_destructive_targets(self):
        for target in ("button#delete-account", "a.logout", "Sign Out",
                       "remove payment method", "cancel subscription",
                       "/admin/delete/users"):
            self.assertTrue(browser.is_denied(target), target)

    def test_deny_list_allows_normal_targets(self):
        for target in ("button.publish", "#caption", "input[name=subject]",
                       "https://example.com/pricing"):
            self.assertFalse(browser.is_denied(target), target)

    def test_deny_list_checks_task_text_too(self):
        self.assertTrue(browser.is_denied("button.ok", "task: delete account"))

    def test_permission_note_explains_the_gate(self):
        self.assertIn("BLOCKED", browser.PERMISSION_NOTE)
        self.assertIn("--allow-actions", browser.PERMISSION_NOTE)


class TestSessionPersistence(unittest.TestCase):
    def test_session_round_trip(self):
        """The browser agent must remember which page it was on, otherwise
        navigate → snapshot/act always operated on a blank tab."""
        original = browser.SESSION_FILE
        browser.SESSION_FILE = Path(__file__).resolve().parent / "_tmp_session.json"
        try:
            browser.SESSION_FILE.unlink(missing_ok=True)
            self.assertEqual(browser.load_session(), {})
            browser.save_session(url="https://example.com", title="Example")
            state = browser.load_session()
            self.assertEqual(state["url"], "https://example.com")
            self.assertEqual(state["title"], "Example")
            self.assertIn("updated_at", state)

            browser.save_session(title="Changed")
            merged = browser.load_session()
            self.assertEqual(merged["url"], "https://example.com", "update must merge, not replace")
            self.assertEqual(merged["title"], "Changed")

            browser.clear_session()
            self.assertEqual(browser.load_session(), {})
        finally:
            browser.SESSION_FILE.unlink(missing_ok=True)
            browser.SESSION_FILE = original


if __name__ == "__main__":
    unittest.main()


class TestYouTubeIsHandledHonestly(unittest.TestCase):
    """The publish UI used to offer YouTube, but the Data API has no text-post
    endpoint. Asking for it must produce an actionable explanation, not a silent
    'Unsupported platform'."""

    def test_youtube_publisher_explains_itself(self):
        res = publish.post_youtube("caption", publish.creds_from({}))
        self.assertFalse(res["ok"])
        self.assertIn("videos.insert", res["error"])

    def test_youtube_is_a_registered_platform(self):
        self.assertIn("youtube", publish.PUBLISHERS)

    def test_youtube_job_reports_failure(self):
        result = publish.publish_job({"platforms": ["youtube"], "content": "x"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("videos.insert", result["results"][0]["error"])
