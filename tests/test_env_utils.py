"""Unit tests for env_utils.py."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import env_utils as eu


class TestEnvUtils(unittest.TestCase):
    def test_env_value_strips_quotes_and_comments(self):
        self.assertEqual(eu.env_value("plain"), "plain")
        self.assertEqual(eu.env_value("'single_quoted'"), "single_quoted")
        self.assertEqual(eu.env_value('"double_quoted"'), "double_quoted")
        self.assertEqual(eu.env_value("val   # inline comment"), "val")
        self.assertEqual(eu.env_value("'val # not comment'"), "val # not comment")

    def test_load_env_reads_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".env"
            p.write_text("TEST_KEY_ONE=apple\n# comment\nTEST_KEY_TWO='banana'\n", encoding="utf-8")
            os.environ.pop("TEST_KEY_ONE", None)
            os.environ.pop("TEST_KEY_TWO", None)
            eu.load_env([p])
            self.assertEqual(os.environ.get("TEST_KEY_ONE"), "apple")
            self.assertEqual(os.environ.get("TEST_KEY_TWO"), "banana")
            # Existing keys are not overwritten
            p.write_text("TEST_KEY_ONE=cherry\n", encoding="utf-8")
            eu.load_env([p])
            self.assertEqual(os.environ.get("TEST_KEY_ONE"), "apple")
            os.environ.pop("TEST_KEY_ONE", None)
            os.environ.pop("TEST_KEY_TWO", None)


if __name__ == "__main__":
    unittest.main()
