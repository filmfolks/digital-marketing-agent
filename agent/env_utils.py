"""Environment loading utilities for Nexus agents."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent


def env_value(raw: str) -> str:
    """Value part of a .env line: quotes removed, unquoted inline \"  # comment\" dropped
    (.env.example has lines like `NEXUS_API_RETRIES=2   # extra attempts`)."""
    v = raw.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        return v[1:end] if end > 0 else v[1:]
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def load_env(paths: Optional[Iterable[Path | str]] = None) -> None:
    """Read .env files into os.environ. Real environment variables win; empty values are skipped."""
    for p in paths or (ROOT / ".env", Path.cwd() / ".env"):
        p = Path(p)
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), env_value(v)
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
        except OSError:
            continue
