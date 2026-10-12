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
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))
