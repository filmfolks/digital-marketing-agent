""".env parsing: inline comments (as shipped in .env.example) and quotes."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_agent  # noqa: E402

relay = load_agent("nexus_relay")


class TestEnvValue(unittest.TestCase):
    def test_inline_comment_dropped(self):
        self.assertEqual(relay.env_value("2          # extra API attempts"), "2")

    def test_quoted_value_keeps_hash(self):
        self.assertEqual(relay.env_value('"a # b"  # note'), "a # b")
        self.assertEqual(relay.env_value("'x'"), "x")

    def test_hash_without_space_is_value(self):
        self.assertEqual(relay.env_value("abc#def"), "abc#def")

    def test_example_file_loads_clean_ints(self):
        ex = Path(__file__).resolve().parent.parent / ".env.example"
        keys = ("NEXUS_API_RETRIES", "NEXUS_BROWSER_RETRIES", "NEXUS_BACKOFF_BASE")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            relay.load_env([ex])
            for k in keys:
                float(os.environ[k])
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
