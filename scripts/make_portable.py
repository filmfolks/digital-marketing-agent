#!/usr/bin/env python3
"""Make a clean, portable copy of Nexus (zip) to move it to another computer,
e.g. a Windows laptop via pendrive.

    python3 scripts/make_portable.py                  # → dist/nexus-portable-YYYYMMDD-HHMM.zip
    python3 scripts/make_portable.py --out E:\\nexus.zip
    python3 scripts/make_portable.py --include-brands # also copy data/brands (profiles/plans, no logs)
    python3 scripts/make_portable.py --list           # print what would be packed

Left out on purpose: .venv (machine-specific, recreate with scripts/setup_windows.bat
or ./start.sh), .env (API keys / tokens — copy secrets separately), logs/ (browser
profiles with logged-in sessions, audit logs), shots/, output/, caches, .git,
node_modules, link/activity logs. Empty logs/ and shots/ folders are recreated.
Works the same on macOS, Linux and Windows (pathlib + zipfile only).
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
                "logs", "shots", "output", "dist", ".idea", ".vscode", "agent-tools"}
EXCLUDE_FILES = [".env", ".env.*.local", "*.pyc", "*.pyo", ".DS_Store", "._*", "*.log", "*.jsonl",
                 "link_memory.json", "credentials.js", "*.pem", "*.key", "*.p12", "Thumbs.db"]
KEEP_FILES = {".env.example"}
EMPTY_DIRS = ("logs", "shots")


def wanted(rel: Path, include_brands: bool) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return False
    if parts[0] == "data" and not (include_brands and len(parts) > 2 and parts[1] == "brands"):
        return False
    if parts[0] == "data" and parts[-1] in ("activity_log.jsonl", "link_memory.json"):
        return False
    name = parts[-1]
    if name in KEEP_FILES:
        return True
    return not any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILES)


def collect(include_brands: bool = False) -> list:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(ROOT)
            if wanted(rel, include_brands):
                out.append(rel)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="")
    ap.add_argument("--include-brands", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    files = collect(a.include_brands)
    if a.list:
        print("\n".join(rel.as_posix() for rel in files))
        return 0
    out = Path(a.out) if a.out else ROOT / "dist" / time.strftime("nexus-portable-%Y%m%d-%H%M.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    top = "nexus"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(ROOT / rel, f"{top}/{rel.as_posix()}")
        for d in EMPTY_DIRS:
            z.writestr(f"{top}/{d}/.gitkeep", "")
    print(f"→ {out}  ({len(files)} files, {out.stat().st_size // 1024} KB)")
    print("Next on the target computer: unzip, then Windows: scripts\\setup_windows.bat · macOS/Linux: ./start.sh")
    print("Copy your .env (API keys) separately and log in to the social sites again there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
