#!/usr/bin/env python3
"""
Shared browser plumbing for the social posting fallback.

Everything that is environment-specific lives here so the workflows stay
readable and testable:

  * stealth loader      playwright-stealth 2.x (Stealth) → 1.x (stealth_sync) → logged "off"
  * channel detection   installed Google Chrome / Microsoft Edge → bundled Chromium
  * real fingerprint    no hard-coded UA or timezone: the installed browser's own UA
                        is used untouched, timezone/locale come from the OS
  * profiles            one persistent profile PER PLATFORM under logs/profiles/<platform>
                        (log in once by hand; no passwords are stored anywhere)
  * resilient locators  ordered candidate list (role → label → text → CSS)
  * pacing              human-like jittered delays and typing
  * failure evidence    screenshot + structured audit on every failure
  * login detection     per-platform "logged out" probes with a clear message

Stdlib only at import time — Playwright is imported lazily so the module (and
the tests) load on a machine without it.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import platform as _platform
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
try:                                    # client build: profiles pinned inside this folder
    import client_config as _client
    PROFILES_DIR = _client.profiles_dir(LOGS / "profiles")
except ImportError:
    _client = None
    PROFILES_DIR = Path(os.environ.get("NEXUS_PROFILES_DIR") or (LOGS / "profiles"))
SHOTS_DIR = ROOT / "shots"
DOWNLOADS_DIR = ROOT / "output" / "downloads"

log = logging.getLogger("nexus.browser")
# Multiplier for every locator wait (slow machines/networks: >1; unit tests: 0).
TIMEOUT_SCALE = float(os.environ.get("NEXUS_TIMEOUT_SCALE", "1") or 1)


# ── structured logging ──────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            entry.update(extra)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: str | None = None) -> None:
    """JSON lines on stderr (stdout is reserved for the command's JSON result)."""
    root = logging.getLogger("nexus")
    if getattr(root, "_nexus_configured", False):
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel((level or os.environ.get("NEXUS_LOG_LEVEL") or "INFO").upper())
    root.propagate = False
    root._nexus_configured = True  # type: ignore[attr-defined]


def log_event(msg: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, msg, extra={"fields": fields})


# ── stealth ─────────────────────────────────────────────────────────────────
@dataclass
class StealthPlan:
    mode: str                                   # "v2" | "v1" | "none"
    apply: Callable[[object], None] | None = None
    reason: str = ""


def load_stealth(importer: Callable[[str], object] | None = None) -> StealthPlan:
    """Resolve the best available stealth implementation.

    2.x: ``Stealth().apply_stealth_sync(page_or_context)``
    1.x: ``stealth_sync(page)``
    Neither: returns mode="none" with a reason — and the caller logs a warning
    instead of silently proceeding (the old code swallowed the ImportError).
    """
    import importlib
    imp = importer or importlib.import_module
    try:
        mod = imp("playwright_stealth")
    except Exception as e:                       # ImportError, or pkg_resources missing in 1.x
        return StealthPlan("none", None, f"playwright-stealth not importable: {e}")

    Stealth = getattr(mod, "Stealth", None)
    if Stealth is not None:
        inst = Stealth()
        fn = getattr(inst, "apply_stealth_sync", None)
        if callable(fn):
            return StealthPlan("v2", fn, "playwright-stealth 2.x")
    legacy = getattr(mod, "stealth_sync", None)
    if callable(legacy):
        return StealthPlan("v1", legacy, "playwright-stealth 1.x")
    return StealthPlan("none", None, "playwright-stealth installed but exposes neither Stealth nor stealth_sync")


# ── environment detection ───────────────────────────────────────────────────
def system_timezone() -> str | None:
    """IANA zone of the OS, or None to let the browser inherit it."""
    tz = os.environ.get("TZ")
    if tz and "/" in tz:
        return tz
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return None      # Windows / unknown: Chrome already uses the OS zone by default


def system_locale() -> str | None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v and v not in ("C", "POSIX") and not v.startswith("C."):
            return v.split(".")[0].replace("_", "-")
    try:
        loc = locale.getlocale()[0]
        if loc and loc not in ("C", "POSIX"):
            return loc.split(".")[0].replace("_", "-")
    except Exception:
        pass
    return None


_CHANNEL_PATHS = {
    "Darwin": {
        "chrome": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "msedge": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    },
    "Windows": {
        "chrome": [r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
                   r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
                   r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
        "msedge": [r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
                   r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"],
    },
    "Linux": {
        "chrome": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"],
        "msedge": ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"],
    },
}


def detect_channel(system: str | None = None, exists: Callable[[str], bool] | None = None) -> str | None:
    """Return the Playwright channel of an installed branded browser.

    Order: $NEXUS_BROWSER_CHANNEL → Chrome → Edge → None (bundled Chromium).
    A real Chrome/Edge gives the platform its genuine UA and codecs.
    """
    forced = os.environ.get("NEXUS_BROWSER_CHANNEL", "").strip()
    if forced:
        return None if forced.lower() in ("chromium", "bundled") else forced
    system = system or _platform.system()
    exists = exists or (lambda p: Path(os.path.expandvars(p)).is_file())
    for channel in ("chrome", "msedge"):
        for p in _CHANNEL_PATHS.get(system, {}).get(channel, []):
            if exists(p):
                return channel
    if system == "Linux" and shutil.which("google-chrome"):
        return "chrome"
    return None


def profile_dir(platform_name: str, create: bool = True) -> Path:
    """Persistent, per-platform profile. Separate profiles keep one site's
    cookie/session problems from logging you out of the others."""
    safe = "".join(c for c in platform_name.lower() if c.isalnum() or c in "-_") or "default"
    p = PROFILES_DIR / safe
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


# ── pacing ──────────────────────────────────────────────────────────────────
class Pacer:
    """Jittered delays. ``NEXUS_PACE=0`` (tests / dry-run CI) disables sleeping."""

    def __init__(self, scale: float | None = None, sleep: Callable[[float], None] = time.sleep):
        env = os.environ.get("NEXUS_PACE")
        self.scale = scale if scale is not None else (float(env) if env else 1.0)
        self._sleep = sleep

    def pause(self, lo: float = 0.6, hi: float = 1.8) -> float:
        d = (lo + random.random() * (hi - lo)) * self.scale
        if d > 0:
            self._sleep(d)
        return d

    def type_into(self, page, locator, text: str) -> None:
        locator.click()
        self.pause(0.2, 0.5)
        if self.scale == 0:
            page.keyboard.insert_text(text)
            return
        for i, ch in enumerate(text):
            if ch == "\n":
                page.keyboard.press("Shift+Enter")
            else:
                page.keyboard.type(ch, delay=random.randint(35, 120))
            if i and i % random.randint(18, 40) == 0:       # occasional "thinking" pause
                self.pause(0.3, 0.9)
        self.pause(0.3, 0.8)


# ── resilient locators ──────────────────────────────────────────────────────
@dataclass
class Candidate:
    kind: str           # role | label | text | placeholder | testid | css
    value: str
    name: str | None = None

    def resolve(self, page):
        if self.kind == "role":
            return page.get_by_role(self.value, name=self.name) if self.name else page.get_by_role(self.value)
        if self.kind == "label":
            return page.get_by_label(self.value)
        if self.kind == "text":
            return page.get_by_text(self.value, exact=False)
        if self.kind == "placeholder":
            return page.get_by_placeholder(self.value)
        if self.kind == "testid":
            return page.locator(f"[data-testid='{self.value}']")
        return page.locator(self.value)

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}" + (f"[{self.name}]" if self.name else "")


class ElementNotFound(RuntimeError):
    pass


def find_first(page, candidates: Iterable[Candidate], timeout_ms: int = 12000, poll_ms: int = 300):
    """Return the first visible match among candidates, polling until timeout."""
    cands = list(candidates)
    deadline = time.monotonic() + timeout_ms * TIMEOUT_SCALE / 1000
    while True:
        for c in cands:
            try:
                loc = c.resolve(page).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue
        if time.monotonic() >= deadline:
            raise ElementNotFound("none matched: " + ", ".join(map(str, cands)))
        page.wait_for_timeout(poll_ms)


# ── login detection ─────────────────────────────────────────────────────────
LOGGED_OUT_PROBES: dict[str, dict] = {
    "x": {
        "url_markers": ["/i/flow/login", "/login", "/i/flow/signup"],
        "candidates": [Candidate("testid", "loginButton"), Candidate("role", "link", "Sign in"),
                       Candidate("role", "link", "Log in")],
    },
    "canva": {
        "url_markers": ["/login", "/signup"],
        "candidates": [Candidate("role", "button", "Log in"), Candidate("role", "link", "Log in"),
                       Candidate("role", "button", "Sign up")],
    },
    # The logged-out landing page changes often (2026: a Meta sign-up form with no
    # username field and no /accounts/login URL), so check several signs.
    "instagram": {"url_markers": ["/accounts/login", "/accounts/emailsignup", "/accounts/signup"],
                  "candidates": [Candidate("css", "input[name='username']"),
                                 Candidate("text", "Create new account"),
                                 Candidate("text", "Get started on Instagram"),
                                 Candidate("role", "button", "Log in")]},
    "facebook": {"url_markers": ["/login"], "candidates": [Candidate("css", "input[name='email']")]},
    "linkedin": {"url_markers": ["/login", "/authwall", "/uas/login"], "candidates": [Candidate("css", "#username")]},
}


class LoggedOut(RuntimeError):
    def __init__(self, platform_name: str):
        self.platform = platform_name
        cdp = resolve_cdp_url()
        if cdp:
            super().__init__(
                f"Not logged in to {platform_name} in the Chrome attached at {cdp}. Log in there by hand "
                f"(a normal tab in that window), then run this again. Nexus never types passwords.")
            return
        super().__init__(
            f"Not logged in to {platform_name}. Log in once by hand with:\n"
            f"  python3 agent/social_browser_agent.py login --platform {platform_name}\n"
            f"The session is kept in {profile_dir(platform_name, create=False)} and reused by every later run.")


# Positive "you are logged in" markers. A login check only passes when one of
# these is visible — absence of a login button alone is not proof (an error or
# bot-check page has neither).
LOGGED_IN_PROBES: dict[str, list] = {
    "x": [Candidate("testid", "SideNav_NewTweet_Button"), Candidate("testid", "AppTabBar_Profile_Link"),
          Candidate("testid", "tweetTextarea_0")],
    "canva": [Candidate("role", "button", "Create a design"), Candidate("css", "[aria-label*='Your account']"),
              Candidate("css", "[data-testid='header-avatar']"), Candidate("role", "button", "Share")],
    "instagram": [Candidate("css", "svg[aria-label='Home']"), Candidate("css", "[aria-label='New post']")],
    "facebook": [Candidate("css", "[aria-label='Your profile']"), Candidate("role", "navigation", "Shortcuts")],
    "linkedin": [Candidate("css", "#global-nav"), Candidate("css", ".global-nav__me")],
}

CHALLENGE_TEXT = ["Verify you are human", "We’ll have you designing again soon",
                  "We'll have you designing again soon", "Checking if the site connection is secure",
                  "Just a moment...", "unusual activity", "Confirm it's you"]


class HumanCheckRequired(RuntimeError):
    def __init__(self, platform_name: str, marker: str):
        self.platform = platform_name
        super().__init__(
            f"{platform_name} is showing a human-verification page ({marker!r}). Complete it once by hand:\n"
            f"  python3 agent/social_browser_agent.py login --platform {platform_name}\n"
            "Nexus never tries to solve these automatically.")


def challenge_marker(page) -> str | None:
    for marker in CHALLENGE_TEXT:
        try:
            loc = page.get_by_text(marker, exact=False).first
            if loc.count() and loc.is_visible():
                return marker
        except Exception:
            continue
    return None


def is_logged_in(page, platform_name: str) -> bool:
    for c in LOGGED_IN_PROBES.get(platform_name, []):
        try:
            loc = c.resolve(page).first
            if loc.count() and loc.is_visible():
                return True
        except Exception:
            continue
    return False


def is_logged_out(page, platform_name: str) -> bool:
    probe = LOGGED_OUT_PROBES.get(platform_name)
    if not probe:
        return False
    url = (page.url or "").lower()
    if any(m in url for m in probe["url_markers"]):
        return True
    for c in probe["candidates"]:
        try:
            loc = c.resolve(page).first
            if loc.count() and loc.is_visible():
                return True
        except Exception:
            continue
    return False


def ensure_logged_in(page, platform_name: str) -> None:
    marker = challenge_marker(page)
    if marker:
        raise HumanCheckRequired(platform_name, marker)
    if is_logged_out(page, platform_name):
        raise LoggedOut(platform_name)


# ── CDP attach mode ─────────────────────────────────────────────────────────
_CDP_OFF = {"", "0", "off", "no", "none", "false", "launch"}


class CDPUnavailable(RuntimeError):
    """The Chrome we were told to attach to is not answering."""


def resolve_cdp_url(explicit: str | None = None) -> str:
    """CDP endpoint to attach to, or "" to launch our own browser.

    Precedence: explicit (--cdp-url) > NEXUS_CDP_URL > client.json "cdp_url".
    "off"/"0"/"" disables attach mode; a bare port means 127.0.0.1:<port>."""
    v = explicit
    if v is None:
        v = os.environ.get("NEXUS_CDP_URL") or None      # empty (e.g. "NEXUS_CDP_URL=" in .env) = unset
    if v is None and _client is not None:
        try:
            v = _client.load_client().get("cdp_url")
        except Exception:                                    # noqa: BLE001
            v = None
    v = str(v or "").strip()
    if v.lower() in _CDP_OFF:
        return ""
    if v.isdigit():
        v = f"http://127.0.0.1:{v}"
    elif "://" not in v:
        v = "http://" + v
    return v.rstrip("/")


# ── session ─────────────────────────────────────────────────────────────────
@dataclass
class BrowserSession:
    platform: str
    headless: bool = True
    channel: str | None = None
    stealth: StealthPlan | None = None
    pw: object = None
    ctx: object = None
    page: object = None
    downloads: Path = field(default_factory=lambda: DOWNLOADS_DIR)
    # Attach mode: None = resolve from --cdp-url/NEXUS_CDP_URL/client.json; "" = force launch.
    cdp_url: str | None = None
    browser: object = None
    attached: bool = False

    def launch_kwargs(self) -> dict:
        kw: dict = {
            "user_data_dir": str(profile_dir(self.platform)),
            "headless": self.headless,
            "accept_downloads": True,
            "viewport": {"width": 1280, "height": 900},
            "args": ["--no-first-run", "--no-default-browser-check"],
        }
        if self.channel:
            kw["channel"] = self.channel
        tz, loc = system_timezone(), system_locale()
        if tz:
            kw["timezone_id"] = tz
        if loc:
            kw["locale"] = loc
        # Deliberately NO user_agent: the installed browser reports its own,
        # which always matches its real version and platform. (Headless mode
        # reports "HeadlessChrome"; _fix_headless_ua strips just that token.)
        return kw

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright
        url = resolve_cdp_url(self.cdp_url)
        if url:
            self.pw = sync_playwright().start()
            try:
                return self._attach(url)
            except BaseException:
                self._stop_driver()
                raise
        self.channel = self.channel if self.channel is not None else detect_channel()
        self.stealth = self.stealth or load_stealth()
        if self.stealth.mode == "none":
            log_event("stealth unavailable — continuing without it", logging.WARNING,
                      platform=self.platform, reason=self.stealth.reason)
        self.pw = sync_playwright().start()
        kw = self.launch_kwargs()
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(**kw)
        except Exception as e:
            if not kw.get("channel"):
                raise
            log_event("branded browser failed to launch; using bundled Chromium", logging.WARNING,
                      channel=kw["channel"], error=str(e)[:200])
            kw.pop("channel")
            self.channel = None
            self.ctx = self.pw.chromium.launch_persistent_context(**kw)
        if self.headless:
            self._fix_headless_ua()
        if self.stealth.mode == "v2":
            self.stealth.apply(self.ctx)          # 2.x accepts a context: covers every page
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        if self.stealth.mode == "v1":
            self.stealth.apply(self.page)         # 1.x is per-page
        log_event("browser started", platform=self.platform, channel=self.channel or "chromium",
                  headless=self.headless, stealth=self.stealth.mode)
        return self

    def _attach(self, url: str) -> "BrowserSession":
        """Attach to the owner's running Chrome. Reuse its default (logged-in)
        context, open ONE new tab, and never touch anything else. No stealth
        and no UA override: it is a real, headed, user-launched browser."""
        try:
            self.browser = self.pw.chromium.connect_over_cdp(url, timeout=15000)
        except Exception as e:                               # noqa: BLE001
            raise CDPUnavailable(
                f"no Chrome answering at {url} ({str(e).splitlines()[0][:160]}). Start Chrome with "
                f"--remote-debugging-port=<port>, or set NEXUS_CDP_URL=off to launch Nexus's own browser.") from e
        ctxs = list(getattr(self.browser, "contexts", []) or [])
        if not ctxs:
            raise CDPUnavailable(f"Chrome at {url} exposes no browser context to attach to")
        self.ctx = ctxs[0]
        self.page = self.ctx.new_page()
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        self.attached = True
        self.cdp_url = url
        self.stealth = StealthPlan("none", reason="attach mode: real user browser")
        log_event("browser attached", platform=self.platform, cdp_url=url, tabs=len(self.ctx.pages))
        return self

    def _stop_driver(self) -> None:
        try:
            self.pw and self.pw.stop()
        except Exception:                                    # noqa: BLE001
            pass

    def _fix_headless_ua(self) -> None:
        try:
            probe = self.ctx.new_page()
            ua = probe.evaluate("navigator.userAgent")
            probe.close()
            if "HeadlessChrome" in ua:
                real = ua.replace("HeadlessChrome", "Chrome")
                self.ctx.set_extra_http_headers({"User-Agent": real})
                self.ctx.add_init_script(
                    "Object.defineProperty(Navigator.prototype,'userAgent',{get:()=>%s});" % json.dumps(real))
        except Exception:
            pass

    def __exit__(self, *exc) -> None:
        if self.attached:
            # Close only the tab we opened. Never ctx.close()/browser.close():
            # that is the owner's browser and their other tabs. Stopping the
            # driver just drops the CDP connection; Chrome keeps running.
            try:
                if self.page is not None and not self.page.is_closed():
                    self.page.close()
            except Exception:                                # noqa: BLE001
                pass
            self._stop_driver()
            return
        for closer in (getattr(self.ctx, "close", None), getattr(self.pw, "stop", None)):
            try:
                closer and closer()
            except Exception:
                pass

    def screenshot(self, tag: str) -> str | None:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOTS_DIR / f"{self.platform}_{tag}_{int(time.time())}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            return None
