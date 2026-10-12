#!/usr/bin/env python3
"""
Browser posting flows used as the FALLBACK route when an official API call fails.

Each flow takes an already-open page (see browser_core.BrowserSession) so it can
be unit-tested with a fake page, and returns a plain dict result. In dry-run a
flow does everything a real run does — opens the site, checks the login,
fills the composer, attaches media — and stops right before the final
"Post" click, taking a screenshot as evidence.
"""

from __future__ import annotations

import time
from pathlib import Path

try:                                  # package-less repo: works as script or module
    from browser_core import (Candidate as C, ElementNotFound, LoggedOut, Pacer, HumanCheckRequired,
                              DOWNLOADS_DIR, ensure_logged_in, find_first, is_logged_in, log_event)
except ImportError:                   # pragma: no cover
    from agent.browser_core import (Candidate as C, ElementNotFound, LoggedOut, Pacer,  # type: ignore
                                    HumanCheckRequired, DOWNLOADS_DIR, ensure_logged_in, find_first,
                                    is_logged_in, log_event)

X_COMPOSE_URL = "https://x.com/compose/post"
X_HOME_URL = "https://x.com/home"
X_STANDARD_LIMIT = 280
X_PREMIUM_LIMIT = 25_000

X_TEXTBOX = [C("testid", "tweetTextarea_0"), C("role", "textbox", "Post text"),
             C("css", "div[role='textbox'][contenteditable='true']")]
X_FILE_INPUT = [C("css", "input[data-testid='fileInput']"), C("css", "input[type='file'][accept*='image']"),
                C("css", "input[type='file']")]
X_POST_BUTTON = [C("testid", "tweetButton"), C("role", "button", "Post"), C("testid", "tweetButtonInline")]
X_SENT_TOAST = [C("text", "Your post was sent"), C("role", "alert")]

CANVA_SHARE = [C("role", "button", "Share"), C("css", "button[aria-label*='Share']")]
CANVA_DOWNLOAD_ENTRY = [C("role", "button", "Download"), C("role", "menuitem", "Download"),
                        C("role", "link", "Download"), C("text", "Download")]
CANVA_FILETYPE = [C("role", "combobox", "File type"), C("css", "[aria-label*='File type']")]
CANVA_DOWNLOAD_CONFIRM = [C("css", "[role='dialog'] button:has-text('Download')"),
                          C("css", "aside button:has-text('Download')"), C("role", "button", "Download")]


CANVA_ERROR_TEXT = ["We couldn't load this page", "This design isn't available",
                    "You don't have access"]


class FlowError(RuntimeError):
    pass


def open_page(page, url: str, timeout: int = 45000) -> None:
    """goto with a one-line, human-readable error (site down, blocked, offline)."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as e:                         # noqa: BLE001
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        raise FlowError(f"could not open {url}: {first}") from e


def validate_x_text(text: str, long_post: bool) -> None:
    if not text.strip():
        raise FlowError("X post text is empty")
    limit = X_PREMIUM_LIMIT if long_post else X_STANDARD_LIMIT
    if len(text) > limit:
        hint = "" if long_post else " — pass --long-post if the account has X Premium/Premium+"
        raise FlowError(f"X post is {len(text)} chars; limit is {limit}{hint}")


def x_post(page, text: str, media: list[str] | None = None, *, long_post: bool = False,
           dry_run: bool = False, pacer: Pacer | None = None, screenshot=None) -> dict:
    """Compose and (unless dry_run) publish a post on x.com."""
    pacer = pacer or Pacer()
    media = [m for m in (media or []) if m]
    validate_x_text(text, long_post)
    for m in media:
        if not Path(m).is_file():
            raise FlowError(f"media file not found: {m}")

    open_page(page, X_COMPOSE_URL)
    pacer.pause(1.5, 3.0)
    ensure_logged_in(page, "x")

    box = find_first(page, X_TEXTBOX, timeout_ms=20000)
    pacer.type_into(page, box, text)

    if media:
        find_first(page, X_FILE_INPUT, timeout_ms=8000).set_input_files(media)
        pacer.pause(2.0, 4.0)                      # upload + processing

    button = find_first(page, X_POST_BUTTON, timeout_ms=10000)
    if button.is_disabled():
        if len(text) > X_STANDARD_LIMIT:
            raise FlowError("X disabled the Post button for a long post — this account does not "
                            "appear to have X Premium/Premium+ long-post access")
        raise FlowError("X disabled the Post button (media still uploading or text rejected)")

    shot = screenshot("dryrun" if dry_run else "precommit") if screenshot else None
    if dry_run:
        log_event("x dry-run complete", platform="x", chars=len(text), media=len(media))
        return {"ok": True, "platform": "x", "route": "browser", "dry_run": True,
                "chars": len(text), "media": media, "screenshot": shot}

    pacer.pause(0.8, 1.6)
    button.click()
    try:
        find_first(page, X_SENT_TOAST, timeout_ms=15000)
        confirmed = True
    except ElementNotFound:
        # Compose modal closing / navigating away is also a success signal.
        confirmed = "compose" not in (page.url or "")
    if not confirmed:
        raise FlowError("clicked Post but X showed no confirmation")
    log_event("x post sent", platform="x", chars=len(text), media=len(media))
    return {"ok": True, "platform": "x", "route": "browser", "chars": len(text), "media": media,
            "screenshot": screenshot("sent") if screenshot else None}


def canva_export(page, design_url: str, out_path: str | None = None, *, file_type: str = "PNG",
                 pacer: Pacer | None = None, screenshot=None) -> dict:
    """Open a Canva design and download it. Not a publish action, so it runs
    the same in dry-run (that is how the e2e check proves the export works)."""
    pacer = pacer or Pacer()
    if "canva.com/design/" not in design_url:
        raise FlowError("design_url must be a Canva editor link (https://www.canva.com/design/...)")
    ext = {"PNG": ".png", "JPG": ".jpg", "PDF": ".pdf"}.get(file_type.upper(), ".png")
    out = Path(out_path) if out_path else DOWNLOADS_DIR / f"canva_{int(time.time())}{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)

    open_page(page, design_url, timeout=60000)
    pacer.pause(3.0, 5.0)
    ensure_logged_in(page, "canva")
    for marker in CANVA_ERROR_TEXT:
        try:
            if page.get_by_text(marker, exact=False).first.is_visible():
                raise FlowError(f"Canva could not open the design ({marker!r}) — check the link and that "
                                "this account can access it")
        except FlowError:
            raise
        except Exception:
            pass

    # Current Canva: Share ▸ Download. Older layouts expose Download directly.
    try:
        find_first(page, CANVA_SHARE, timeout_ms=20000).click()
        pacer.pause(0.8, 1.6)
    except ElementNotFound:
        pass
    find_first(page, CANVA_DOWNLOAD_ENTRY, timeout_ms=10000).click()
    pacer.pause(1.0, 2.0)

    try:
        ft = find_first(page, CANVA_FILETYPE, timeout_ms=3000)
        ft.click()
        pacer.pause(0.4, 0.8)
        find_first(page, [C("role", "option", file_type), C("text", file_type)], timeout_ms=3000).click()
        pacer.pause(0.4, 0.8)
    except ElementNotFound:
        pass                                       # default (PNG) is fine

    confirm = find_first(page, CANVA_DOWNLOAD_CONFIRM, timeout_ms=10000)
    with page.expect_download(timeout=120000) as dl:
        confirm.click()
    dl.value.save_as(str(out))
    if not out.is_file() or out.stat().st_size == 0:
        raise FlowError("Canva download finished but the file is empty")
    log_event("canva export saved", platform="canva", path=str(out), bytes=out.stat().st_size)
    return {"ok": True, "platform": "canva", "file": str(out), "bytes": out.stat().st_size,
            "screenshot": screenshot("exported") if screenshot else None}


def check_login(page, platform_name: str, url: str, pacer: Pacer | None = None) -> dict:
    pacer = pacer or Pacer()
    try:
        open_page(page, url)
    except FlowError as e:
        return {"platform": platform_name, "logged_in": False, "message": str(e)}
    pacer.pause(2.0, 3.5)
    try:
        ensure_logged_in(page, platform_name)
    except (LoggedOut, HumanCheckRequired) as e:
        return {"platform": platform_name, "logged_in": False, "message": str(e)}
    # Wait (bounded) for a positive marker: SPAs render the signed-in shell late.
    for _ in range(10):
        if is_logged_in(page, platform_name):
            return {"platform": platform_name, "logged_in": True}
        page.wait_for_timeout(1000)
    return {"platform": platform_name, "logged_in": False,
            "message": f"Could not confirm a signed-in {platform_name} session (no logged-in marker found). "
                       f"Run: python3 agent/social_browser_agent.py login --platform {platform_name}"}


LOGIN_URLS = {
    "x": "https://x.com/home",
    "canva": "https://www.canva.com/",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
    "linkedin": "https://www.linkedin.com/feed/",
}
