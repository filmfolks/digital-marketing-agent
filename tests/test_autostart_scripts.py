"""macOS autostart scripts: syntax + the properties that matter (KeepAlive, volume wait, no sudo, no kill)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT  # noqa: E402

S = ROOT / "scripts"


class AutostartScripts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash missing")
    def test_syntax(self):
        for n in ("install_daemon.sh", "install_autostart_mac.sh", "uninstall_daemon.sh"):
            r = subprocess.run(["bash", "-n", str(S / n)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{n}: {r.stderr}")

    def test_relay_agent(self):
        t = (S / "install_daemon.sh").read_text()
        for must in ("<key>KeepAlive</key><true/>", "<key>RunAtLoad</key><true/>", ".venv/bin/python",
                     "os.execv(py, [py, \"agent/nexus_relay.py\"])", "NEXUS_VOLUME_WAIT_S", "os.path.isdir",
                     "Full Disk Access", "REALPY", "another relay is serving",
                     "Application Support/Nexus", "daemon.err.log"):
            self.assertIn(must, t)
        self.assertNotIn("sudo", t)

    def test_chrome_and_awake_agents(self):
        t = (S / "install_autostart_mac.sh").read_text()
        self.assertIn("--remote-debugging-port=$PORT", t)
        self.assertIn("/json/version", t)                      # skip when the port already serves
        self.assertNotRegex(t, r"\b(kill|pkill|killall)\b")    # never kills the running Chrome
        self.assertIn("<string>/usr/bin/caffeinate</string><string>-s</string><string>-i</string>", t)
        self.assertNotIn("sudo ", t)
        self.assertIn("Profile 21", t)

    def test_uninstall_all(self):
        self.assertIn("--all", (S / "uninstall_daemon.sh").read_text())


if __name__ == "__main__":
    unittest.main()
