"""Portability: clean portable zip, Windows scripts present, no hard-coded user paths."""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT  # noqa: E402

spec = importlib.util.spec_from_file_location("make_portable", ROOT / "scripts" / "make_portable.py")
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)


class PortableZip(unittest.TestCase):
    def test_filter(self):
        ok = lambda s, b=False: mp.wanted(Path(s), b)  # noqa: E731
        for keep in ("agent/nexus_relay.py", ".env.example", "scripts/setup_windows.ps1", "ui/index.html"):
            self.assertTrue(ok(keep), keep)
        for drop in (".env", ".venv/bin/python", ".venv/Scripts/python.exe", "logs/profiles/x/Cookies",
                     "logs/browser_audit.jsonl", "shots/a.png", "agent/__pycache__/a.pyc", ".git/HEAD",
                     "data/brands/acme/brand.json", "data/antigravity/config.json", "node_modules/x/y.js",
                     "ui/._index.html", ".DS_Store"):
            self.assertFalse(ok(drop), drop)
        self.assertTrue(ok("data/brands/acme/profile.json", True))
        self.assertFalse(ok("data/brands/acme/activity_log.jsonl", True))
        self.assertFalse(ok("data/brands/acme/link_memory.json", True))

    def test_zip_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "n.zip"
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_portable.py"), "--out", str(out)],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            names = zipfile.ZipFile(out).namelist()
        self.assertIn("nexus/agent/nexus_relay.py", names)
        self.assertIn("nexus/logs/.gitkeep", names)
        self.assertFalse([n for n in names if n.startswith(("nexus/.venv/", "nexus/.git/")) or n == "nexus/.env"])


class WindowsReadiness(unittest.TestCase):
    def test_scripts_exist(self):
        for f in ("start.bat", "start.ps1", "scripts/setup_windows.ps1", "scripts/setup_windows.bat",
                  "scripts/install_daemon_windows.ps1", "scripts/uninstall_daemon_windows.ps1",
                  "scripts/make_portable.py", "scripts/make_portable.sh"):
            self.assertTrue((ROOT / f).is_file(), f)
        ps = (ROOT / "scripts" / "install_daemon_windows.ps1").read_text()
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", ps)
        self.assertIn("pythonw.exe", ps)
        self.assertIn("setup_windows.ps1", (ROOT / "start.ps1").read_text())

    def test_no_hard_coded_user_home_paths_in_code(self):
        bad = re.compile(r"/Users/[A-Za-z]|C:\\\\Users\\\\[A-Za-z]|/home/[a-z]")
        hits = []
        for folder in ("agent", "skill", "scripts", "ui"):
            for p in (ROOT / folder).rglob("*"):
                if p.suffix in (".py", ".js", ".sh", ".ps1", ".bat", ".mjs") and "node_modules" not in p.parts:
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if bad.search(line) and "UNTESTED" not in line and "e.g." not in line:
                            hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
