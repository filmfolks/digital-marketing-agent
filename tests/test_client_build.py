"""Client builds: an optional config/client.json drives brand lock, ports and profiles.

The main app ships WITHOUT config/client.json (generic multi-brand). These tests
use a throw-away example client so no real client data lives in this repo."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import ROOT, load_agent  # noqa: E402
import test_relay_http as _rh  # noqa: E402  (module import: avoids re-running its tests)
TOKEN = _rh.TOKEN
relay = _rh.relay


cc = load_agent("client_config")
br = load_agent("brand_research")
_CFG_DIR = tempfile.mkdtemp(prefix="nexus-client-")
EXAMPLE_CONFIG = Path(_CFG_DIR) / "client.json"
EXAMPLE_CONFIG.write_text(json.dumps({
    "client_id": "acme", "business_name": "Acme", "app_title": "Nexus · Acme",
    "website": "https://acme.example/", "handles": {"x": "@AcmeCo"}, "x_handle": "@AcmeCo",
    "lock_brand": True, "relay_port": 8799, "cdp_port": 9299, "profiles_dir": "logs/profiles",
}), encoding="utf-8")
REAL = {"NEXUS_CLIENT_CONFIG": str(EXAMPLE_CONFIG)}


class ClientConfig(unittest.TestCase):
    def test_main_app_ships_generic(self):
        self.assertFalse((ROOT / "config" / "client.json").exists(),
                         "config/client.json is client data; the main app must not ship one")
        js = (ROOT / "ui" / "client-config.js").read_text()
        self.assertRegex(js, r"window\.NEXUS_CLIENT = null;")
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": str(ROOT / "config" / "client.json")}):
            self.assertEqual(cc.load_client(), cc.DEFAULTS)

    def test_public_view_matches_ui_shape(self):
        with mock.patch.dict(os.environ, REAL):
            view = cc.public_view()
        self.assertEqual((view["client_id"], view["handles"], view["relay_port"]), ("acme", {"x": "AcmeCo"}, 8799))
        self.assertNotIn("profiles_dir", view)

    def test_port_precedence_env_then_client_then_default(self):
        with mock.patch.dict(os.environ, {**REAL, "NEXUS_RELAY_PORT": ""}):
            self.assertEqual(cc.relay_port(), 8799)
        with mock.patch.dict(os.environ, {**REAL, "NEXUS_RELAY_PORT": "9100"}):
            self.assertEqual(cc.relay_port(), 9100)
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": "/nonexistent.json", "NEXUS_RELAY_PORT": ""}):
            self.assertEqual(cc.relay_port(), 8712)
            self.assertEqual(cc.load_client()["lock_brand"], False)

    def test_profiles_pinned_by_client_config(self):
        elsewhere = str(Path(tempfile.gettempdir()) / "nexus-main" / "logs" / "profiles")
        with mock.patch.dict(os.environ, {**REAL, "NEXUS_PROFILES_DIR": elsewhere}):
            self.assertEqual(cc.profiles_dir(Path("x")), ROOT / "logs" / "profiles")
        with mock.patch.dict(os.environ, {"NEXUS_CLIENT_CONFIG": "/nonexistent.json", "NEXUS_PROFILES_DIR": elsewhere}):
            self.assertEqual(cc.profiles_dir(Path("x")), Path(elsewhere))

    def test_ensure_client_brand_creates_and_activates(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, REAL):
            store = br.BrandStore(d)
            other = store.create({"business_name": "Other"})
            store.set_active(other["id"])
            self.assertEqual(cc.ensure_client_brand(store), "acme")
            self.assertEqual(store.active(), "acme")
            self.assertEqual(store.read("acme", "brand")["handles"], {"x": "AcmeCo"})


class LockedRelay(_rh.RelayServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._env = mock.patch.dict(os.environ, REAL)
        cls._env.start()
        cls.br = relay.brand_module()
        cls._bdir = tempfile.TemporaryDirectory()
        cls._saved = cls.br.BRANDS_DIR
        cls.br.BRANDS_DIR = Path(cls._bdir.name)

    @classmethod
    def tearDownClass(cls):
        cls.br.BRANDS_DIR = cls._saved
        cls._bdir.cleanup()
        cls._env.stop()
        super().tearDownClass()

    def brand(self, **body):
        return self.request("POST", "/brand", body=body, token=TOKEN)

    def test_client_endpoint_names_the_client(self):
        code, body = self.request("GET", "/client")
        self.assertEqual(code, 200)
        self.assertEqual((body["client_id"], body["x_handle"], body["app_title"]),
                         ("acme", "@AcmeCo", "Nexus · Acme"))

    def test_brand_is_locked(self):
        code, body = self.brand(action="list")
        self.assertEqual(body["active"], "acme")
        self.assertEqual([b["id"] for b in body["brands"]], ["acme"])
        self.assertEqual(self.brand(action="delete")[0], 403)
        self.assertEqual(self.brand(action="activate", brand_id="someone-else")[0], 403)
        code, body = self.brand(action="create", brand={"business_name": "Hijack", "location": "Bengaluru"})
        self.assertEqual(code, 200, body)
        self.assertEqual((body["brand"]["id"], body["brand"]["business_name"], body["brand"]["location"]),
                         ("acme", "Acme", "Bengaluru"))


class UiBranding(unittest.TestCase):
    def test_generic_title_switcher_and_client_hooks(self):
        html = (ROOT / "ui" / "index.html").read_text()
        js = (ROOT / "ui" / "script.js").read_text()
        self.assertIn("<title>Nexus — Marketing Command Center</title>", html)
        self.assertLess(html.index('src="client-config.js"'), html.index('src="script.js"'))
        self.assertIn('<option value="">No brand</option>', html)
        self.assertIn("relay_port) || 8712}", js)
        self.assertIn("function brandLocked()", js)
        self.assertIn("function applyClientBranding()", js)
        self.assertIn("./client-config.js", (ROOT / "ui" / "sw.js").read_text())


if __name__ == "__main__":
    unittest.main()
