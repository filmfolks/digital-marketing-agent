"""Regression tests for the Python content generator."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import SKILL_SCRIPTS, load_module  # noqa: E402

expected = load_module("nexus_generate_content",
                       SKILL_SCRIPTS / "generate_content.py")


class TestBaseUrlNormalisation(unittest.TestCase):
    def test_bare_base_url_gets_the_endpoint_appended(self):
        """lm_studio_run.sh passes `--url http://localhost:1234/v1`; the old code
        POSTed straight to it and LM Studio answered 404."""
        self.assertEqual(
            expected.normalize_base_url("http://localhost:1234/v1", "lmstudio"),
            "http://localhost:1234/v1/chat/completions")
        self.assertEqual(
            expected.normalize_base_url("https://api.example.com/v1/", "openai"),
            "https://api.example.com/v1/chat/completions")

    def test_full_endpoint_is_left_alone(self):
        full = "http://localhost:11434/v1/chat/completions"
        self.assertEqual(expected.normalize_base_url(full, "ollama"), full)

    def test_claude_uses_the_messages_endpoint(self):
        self.assertEqual(
            expected.normalize_base_url("https://api.anthropic.com/v1", "claude"),
            "https://api.anthropic.com/v1/messages")

    def test_empty_url_passes_through(self):
        self.assertEqual(expected.normalize_base_url("", "openai"), "")


class TestProviderTable(unittest.TestCase):
    def test_every_advertised_provider_exists(self):
        for provider in ("lmstudio", "ollama", "gemini", "groq", "claude",
                         "deepseek", "openai", "xai", "openrouter", "qwen"):
            self.assertIn(provider, expected.PROVIDERS, provider)

    def test_every_provider_declares_url_and_model(self):
        for name, cfg in expected.PROVIDERS.items():
            self.assertTrue(cfg.get("url"), name)
            self.assertTrue(cfg.get("default_model"), name)
            if name not in ("lmstudio", "ollama"):
                self.assertTrue(cfg.get("api_key_env"), name)

    def test_cli_provider_choices_match_the_table(self):
        """A provider that exists in the table but not in --choices is unusable
        from the documented command line."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--provider", default="auto",
                            choices=["auto", "lmstudio", "ollama", "gemini", "groq",
                                     "claude", "deepseek", "openai", "xai", "openrouter", "qwen"])
        parsed = parser.parse_args(["--provider", "xai"])
        self.assertEqual(parsed.provider, "xai")


class TestGenerationError(unittest.TestCase):
    def test_provider_failures_raise_instead_of_returning_error_text(self):
        """Error strings used to be written into output/*.md as if they were copy."""
        self.assertTrue(issubclass(expected.GenerationError, RuntimeError))

    def test_unreachable_provider_raises_generation_error(self):
        with self.assertRaises(expected.GenerationError):
            expected.generate_for_platform(
                topic="t", platform="instagram", tone="professional",
                brand="B", provider_key="lmstudio",
                custom_url="http://127.0.0.1:9/v1",       # nothing listens here
            )

    def test_claude_without_a_key_raises(self):
        with self.assertRaises(expected.GenerationError):
            expected.generate_claude("t", "instagram", "professional", "B", "", "claude-x")


class TestOutputNaming(unittest.TestCase):
    def test_filenames_include_a_timestamp(self):
        """Same-day, same-topic runs used to overwrite each other silently."""
        import inspect
        source = inspect.getsource(expected.save_content)
        self.assertIn("%H%M%S", source)


class TestBrandProfileIsFedIntoPrompts(unittest.TestCase):
    def setUp(self):
        self.saved = (expected.BRAND_CONTEXT, expected.BRAND_GUARDRAILS, expected.http_post_json)

    def tearDown(self):
        expected.BRAND_CONTEXT, expected.BRAND_GUARDRAILS, expected.http_post_json = self.saved

    def test_brand_context_reaches_the_model(self):
        sent = {}

        def fake_post(url, payload, headers=None, timeout=60):
            sent["payload"] = payload
            return 200, {"choices": [{"message": {"content": "ok"}}]}
        expected.http_post_json = fake_post
        expected.BRAND_CONTEXT = "BRAND PROFILE — Acme Dental\nProven USPs (only claim these): 15 years"
        expected.generate_for_platform("smile tips", "instagram", "professional", "Acme", "lmstudio")
        user = sent["payload"]["messages"][1]["content"]
        self.assertIn("BRAND PROFILE — Acme Dental", user)
        self.assertIn("Stay strictly on-brand", user)

    def test_no_profile_means_no_block(self):
        expected.BRAND_CONTEXT = ""
        self.assertEqual(expected.brand_block(), "")

    def test_brand_guardrails_remove_avoid_words_and_emoji(self):
        out = expected.apply_brand_guardrails("Cheap whitening 😁 today", {"bannedWords": ["cheap"], "allowEmoji": False})
        self.assertEqual(out.strip(), "[removed] whitening  today".strip())
        self.assertEqual(expected.apply_brand_guardrails("x", {}), "x")


if __name__ == "__main__":
    unittest.main()
