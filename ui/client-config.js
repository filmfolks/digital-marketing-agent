/* Optional client build settings, mirrored from config/client.json.
   The generic multi-brand app ships with NEXUS_CLIENT = null. A client build
   (one hard-wired brand, its own relay port) replaces null with the object
   from agent/client_config.public_view(), e.g.
     {"client_id": "acme", "business_name": "Acme", "app_title": "Nexus · Acme",
      "website": "https://acme.example/", "handles": {"x": "acme"}, "x_handle": "@acme",
      "lock_brand": true, "relay_port": 8722}
   tests/test_client_build.py checks the two stay in sync. */
window.NEXUS_CLIENT = null;
