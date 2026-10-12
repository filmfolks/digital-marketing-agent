"""Shared helpers for the Nexus test-suite.

Run everything with:  python3 -m unittest discover -s tests -v
                  or:  ./scripts/run_tests.sh
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"
SKILL_SCRIPTS = ROOT / "skill" / "digital-marketer" / "scripts"


def load_module(name: str, path: Path):
    """Import a script by file path (the repo has no installable package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_agent(name: str):
    return load_module(f"nexus_{name}", AGENT / f"{name}.py")

# Tests must never launch a real browser as a publish fallback, and never sleep
# for human pacing. Individual tests opt back in with explicit arguments.
import os as _os  # noqa: E402
_os.environ.setdefault("NEXUS_BROWSER_FALLBACK", "0")
_os.environ.setdefault("NEXUS_PACE", "0")
_os.environ.setdefault("NEXUS_BACKOFF_BASE", "0")
# The generic suite tests the multi-brand app. The client build's own behaviour
# (config/client.json) is covered by tests/test_client_build.py, which points
# NEXUS_CLIENT_CONFIG back at the real file.
CLIENT_CONFIG = ROOT / "config" / "client.json"
_os.environ.setdefault("NEXUS_CLIENT_CONFIG", str(ROOT / "tests" / "no-client-config.json"))
_os.environ.setdefault("NEXUS_BRANDS_DIR", str(ROOT / "logs" / "test-brands-unused"))
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))
_os.environ.pop("NEXUS_CDP_URL", None)   # attach mode is opt-in per test
# Tests (and the CLIs they spawn) must never write the real audit/activity logs.
_os.environ.setdefault("NEXUS_AUDIT_LOG", _os.path.join(__import__("tempfile").mkdtemp(prefix="nexus-audit-"), "browser_audit.jsonl"))
_os.environ.setdefault("NEXUS_ACTIVITY_LOG", _os.path.join(__import__("tempfile").mkdtemp(prefix="nexus-activity-"), "activity_log.jsonl"))
# Task store / calendar data (data/tasks) goes to a throwaway folder too.
_os.environ.setdefault("NEXUS_DATA_DIR", __import__("tempfile").mkdtemp(prefix="nexus-data-"))
_os.environ.setdefault("NEXUS_TZ", "Asia/Kolkata")
_os.environ.setdefault("NEXUS_LINK_MEMORY", _os.path.join(__import__("tempfile").mkdtemp(prefix="nexus-links-"), "link_memory.json"))
