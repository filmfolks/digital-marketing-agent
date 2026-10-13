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

import re
import struct
import sys
import time
from pathlib import Path

try:
    import link_memory
except ImportError:                   # pragma: no cover
    from agent import link_memory  # type: ignore

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
X_POST_BUTTON = [C("css", "[role='dialog'] [data-testid='tweetButton']"), C("testid", "tweetButton"), C("role", "button", "Post"), C("testid", "tweetButtonInline")]
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


# ── shared helpers (lessons from live X runs) ────────────────────────────────
def front(page) -> None:
    """Bring our tab to the front: in CDP attach mode a background tab gets
    throttled and some composers ignore input until they are visible."""
    try:
        page.bring_to_front()
    except Exception:                              # noqa: BLE001
        pass


def find_attached(page, candidates, timeout_ms: int = 10000):
    """Like find_first but for elements that are attached yet hidden (file
    inputs are display:none on X, Instagram and Facebook)."""
    deadline = time.monotonic() + timeout_ms * _scale() / 1000
    while True:
        for c in candidates:
            try:
                loc = c.resolve(page).first
                if loc.count():
                    return loc
            except Exception:                      # noqa: BLE001
                continue
        if time.monotonic() >= deadline:
            raise ElementNotFound("none attached: " + ", ".join(map(str, candidates)))
        page.wait_for_timeout(300)


def _scale() -> float:
    try:
        import browser_core
        return browser_core.TIMEOUT_SCALE
    except Exception:                              # noqa: BLE001
        return 1.0


def safe_click(page, loc, *, timeout_ms: int = 8000, label: str = "") -> str:
    """Click; if an invisible overlay intercepts the pointer (X's typeahead
    layer, FB tooltips), fall back to keyboard (focus + Enter), then a DOM
    click event. Returns the route that worked."""
    try:
        loc.click(timeout=timeout_ms)
        return "click"
    except Exception as e:                         # noqa: BLE001
        log_event("click blocked; trying keyboard", label=label, error=str(e).splitlines()[0][:160] if str(e) else "")
    try:
        loc.focus()
        page.keyboard.press("Enter")
        return "keyboard"
    except Exception:                              # noqa: BLE001
        pass
    try:
        loc.dispatch_event("click")
        return "dispatch"
    except Exception as e:                         # noqa: BLE001
        raise FlowError(f"could not click {label or 'element'}: {str(e).splitlines()[0][:200]}") from e


def close_typeahead(page, text: str) -> None:
    """A caption ending in a #hashtag/@mention leaves FB/IG's suggestion list open over Next/Share.
    A trailing space closes it without picking a suggestion (Enter would, Escape may discard)."""
    if text and re.search(r"[#@][\w.]+$", text.rstrip("\n")) and not text.endswith((" ", "\n")):
        page.keyboard.type(" ")
        page.wait_for_timeout(400)


# Dialogs that would throw work away or delete something: never auto-confirmed.
DESTRUCTIVE_PROMPTS = [C("text", "Discard post?"), C("text", "Discard changes?"), C("text", "Leave page?"),
                       C("text", "Delete post?"), C("role", "button", "Discard"), C("role", "button", "Delete")]


def refuse_destructive_prompt(page) -> None:
    for c in DESTRUCTIVE_PROMPTS:
        try:
            loc = c.resolve(page).first
            if loc.count() and loc.is_visible():
                raise FlowError(f"a confirmation dialog appeared ({c}); it is never auto-confirmed — "
                                "finish or cancel it by hand")
        except FlowError:
            raise
        except Exception:                          # noqa: BLE001
            continue


def remember(platform: str, kind: str, url: str, label: str = "") -> None:
    link_memory.record(platform, kind, url or "", label)


def image_size(path: str):
    """(w, h) for PNG/JPEG without Pillow (not in requirements-posting.txt)."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    marker, = struct.unpack(">H", f.read(2))
                    seglen, = struct.unpack(">H", f.read(2))
                    if marker in (0xFFC0, 0xFFC1, 0xFFC2):
                        f.read(1)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h
                    f.seek(seglen - 2, 1)
    except Exception:                              # noqa: BLE001
        pass
    return None


# Newest-post permalinks, read from the profile/page after a successful post.
PERMALINK_JS = {
    "x": r"""() => Array.from(document.querySelectorAll('article a[href*="/status/"]'))
              .map(a => a.href).filter(h => /\/status\/\d+$/.test(h))""",
    "instagram": """() => Array.from(document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]'))
              .map(a => ({href: a.href, pinned: !!a.querySelector('svg[aria-label*="inned"]')}))
              .filter(o => !o.pinned).map(o => o.href)""",
    "facebook": """() => Array.from(document.querySelectorAll('[role="article"] a[href*="/posts/"], [role="article"] a[href*="story_fbid"], [role="article"] a[href*="/permalink/"]'))
              .map(a => a.href)""",
}


def newest_permalink(page, platform: str, profile_url: str, pacer=None) -> str | None:
    """Best effort: open the profile, read the newest post link, remember it.
    X ids are time-ordered (pinned posts are older), IG skips pinned tiles."""
    try:
        open_page(page, profile_url)
        (pacer or Pacer()).pause(2.5, 4.0)
        hrefs = [h for h in (page.evaluate(PERMALINK_JS[platform]) or []) if isinstance(h, str)]
        if not hrefs:
            return None
        if platform == "x":
            link = max(hrefs, key=lambda h: int(h.rstrip("/").rsplit("/", 1)[1]))
        else:
            link = hrefs[0]
        remember(platform, "post", link, "newest post after publish")
        return link_memory.normalize(link)
    except Exception:                              # noqa: BLE001
        return None


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

    compose = link_memory.preferred("x", "compose", X_COMPOSE_URL)
    open_page(page, compose)
    front(page)
    pacer.pause(1.5, 3.0)
    ensure_logged_in(page, "x")
    remember("x", "compose", compose)

    box = find_first(page, X_TEXTBOX, timeout_ms=20000)
    pacer.type_into(page, box, text)

    if media:
        find_attached(page, X_FILE_INPUT, timeout_ms=8000).set_input_files(media)
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
    try:
        page.bring_to_front()
    except Exception:
        pass
    # X's hashtag/mention typeahead leaves an invisible layer over the Post
    # button, so submit with X's own shortcut while the composer has focus.
    page.keyboard.press("Meta+Enter" if sys.platform == "darwin" else "Control+Enter")
    pacer.pause(2.0, 3.0)
    try:
        find_first(page, X_SENT_TOAST, timeout_ms=15000)
        confirmed = True
    except ElementNotFound:
        # Compose modal closing / navigating away is also a success signal.
        confirmed = "compose" not in (page.url or "")
    if not confirmed:
        raise FlowError("clicked Post but X showed no confirmation")
    log_event("x post sent", platform="x", chars=len(text), media=len(media))
    shot = screenshot("sent") if screenshot else None
    profile = link_memory.preferred("x", "profile", "")
    link = newest_permalink(page, "x", profile, pacer) if profile else None
    return {"ok": True, "platform": "x", "route": "browser", "chars": len(text), "media": media,
            "screenshot": shot, "permalink": link}


IG_HOME_URL = "https://www.instagram.com/"
IG_CAPTION_LIMIT = 2200
IG_HASHTAG_LIMIT = 30
IG_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov")

# Never a loose text "Create": on the logged-out page it matches "Create new account".
IG_CREATE = [C("css", "svg[aria-label='New post']"), C("role", "link", "New post"),
             C("css", "a[href='#'] svg[aria-label='New post']")]
IG_CREATE_POST = [C("css", "a:has(svg[aria-label='Post'])"), C("css", "div[role='button']:has(svg[aria-label='Post'])"),
                  C("css", "svg[aria-label='Post']"), C("role", "menuitem", "Post")]
IG_FILE_INPUT = [C("css", "[role='dialog'] input[type='file']"), C("css", "input[type='file'][accept*='image']"),
                 C("css", "input[type='file']")]
IG_NEXT = [C("role", "button", "Next"), C("css", "[role='dialog'] div[role='button']:has-text('Next')"), C("text", "Next")]
IG_CAPTION = [C("role", "textbox", "Write a caption..."), C("css", "[aria-label='Write a caption...']"),
              C("css", "[role='dialog'] div[contenteditable='true']")]
IG_SHARE = [C("role", "button", "Share"), C("css", "[role='dialog'] div[role='button']:has-text('Share')"), C("text", "Share")]
IG_SELECT_FROM_COMPUTER = [C("role", "button", "Select from computer"), C("text", "Select from computer")]
IG_SELECT_CROP = [C("css", "[role='dialog'] svg[aria-label='Select crop']"), C("role", "button", "Select crop"),
                  C("css", "[aria-label='Select crop']")]
IG_CROP_ORIGINAL = [C("css", "[role='dialog'] div[role='button']:has-text('Original')"), C("text", "Original")]
IG_CROP_45 = [C("css", "[role='dialog'] div[role='button']:has-text('4:5')"), C("text", "4:5")]
IG_SHARED = [C("text", "Your post has been shared."), C("text", "Post shared"), C("css", "img[alt*='Animated checkmark']")]


def require_logged_in_marker(page, platform_name: str, tries: int = 10) -> None:
    """Fail closed: click nothing until a positive signed-in marker is visible.
    (Absence of a login form is not proof: Instagram's 2026 logged-out page has none.)"""
    for i in range(tries):
        if is_logged_in(page, platform_name):
            return
        if i < tries - 1:
            page.wait_for_timeout(1000)
    raise LoggedOut(platform_name)


def validate_instagram(caption: str, media: list[str]) -> None:
    if not media:
        raise FlowError("Instagram needs at least one image or video")
    for m in media:
        if not Path(m).is_file():
            raise FlowError(f"media file not found: {m}")
        if not m.lower().endswith(IG_MEDIA_EXT):
            raise FlowError(f"Instagram cannot post {Path(m).suffix or 'this file'}; use {', '.join(IG_MEDIA_EXT)}")
    if len(media) > 10:
        raise FlowError("Instagram carousels allow at most 10 items")
    if len(caption) > IG_CAPTION_LIMIT:
        raise FlowError(f"Instagram caption is {len(caption)} chars; limit is {IG_CAPTION_LIMIT}")
    tags = [w for w in caption.split() if w.startswith("#")]
    if len(tags) > IG_HASHTAG_LIMIT:
        raise FlowError(f"Instagram allows {IG_HASHTAG_LIMIT} hashtags; caption has {len(tags)}")


def instagram_post(page, caption: str, media: list[str], *, dry_run: bool = False,
                   pacer: Pacer | None = None, screenshot=None) -> dict:
    """Create a feed post on instagram.com. Dry-run stops before Share."""
    pacer = pacer or Pacer()
    media = [m for m in (media or []) if m]
    validate_instagram(caption, media)

    home = link_memory.preferred("instagram", "home", IG_HOME_URL)
    open_page(page, home)
    front(page)
    pacer.pause(2.0, 3.5)
    ensure_logged_in(page, "instagram")
    require_logged_in_marker(page, "instagram")
    remember("instagram", "home", home)

    safe_click(page, find_first(page, IG_CREATE, timeout_ms=15000), label="New post")
    pacer.pause(0.8, 1.6)
    try:                                   # newer UI opens a small "Post / Live / Ad" menu first
        safe_click(page, find_first(page, IG_CREATE_POST, timeout_ms=3000), label="Post")
        pacer.pause(0.6, 1.2)
    except ElementNotFound:
        pass
    attach_media(page, IG_FILE_INPUT, IG_SELECT_FROM_COMPUTER, media)
    pacer.pause(2.5, 4.5)                  # upload → crop screen
    crop = choose_ig_crop(page, media[0], pacer)
    for step in ("crop", "edit"):          # crop → filters/edit → caption
        refuse_destructive_prompt(page)
        safe_click(page, find_first(page, IG_NEXT, timeout_ms=15000), label=f"Next ({step})")
        pacer.pause(1.2, 2.4)
    if caption:
        pacer.type_into(page, find_first(page, IG_CAPTION, timeout_ms=10000), caption)
        close_typeahead(page, caption)
    refuse_destructive_prompt(page)
    share = find_first(page, IG_SHARE, timeout_ms=10000)

    shot = screenshot("dryrun" if dry_run else "precommit") if screenshot else None
    if dry_run:
        log_event("instagram dry-run complete", platform="instagram", chars=len(caption), media=len(media))
        return {"ok": True, "platform": "instagram", "route": "browser", "dry_run": True, "crop": crop,
                "chars": len(caption), "media": media, "screenshot": shot}

    pacer.pause(0.8, 1.6)
    front(page)
    safe_click(page, share, label="Share")
    try:
        find_first(page, IG_SHARED, timeout_ms=60000)       # video processing can take a while
    except ElementNotFound as e:
        raise FlowError("clicked Share but Instagram showed no confirmation") from e
    log_event("instagram post shared", platform="instagram", chars=len(caption), media=len(media))
    shot = screenshot("sent") if screenshot else None
    profile = link_memory.preferred("instagram", "profile", "")
    link = newest_permalink(page, "instagram", profile, pacer) if profile else None
    return {"ok": True, "platform": "instagram", "route": "browser", "chars": len(caption), "media": media,
            "crop": crop, "screenshot": shot, "permalink": link}


def attach_media(page, inputs, chooser_buttons, media: list) -> str:
    """Set files on the (hidden) file input; if none is attached, open the
    native chooser via the visible button and fill it."""
    try:
        find_attached(page, inputs, timeout_ms=8000).set_input_files(media)
        return "input"
    except ElementNotFound:
        pass
    btn = find_first(page, chooser_buttons, timeout_ms=8000)
    with page.expect_file_chooser(timeout=15000) as fc:
        btn.click()
    fc.value.set_files(media)
    return "chooser"


def choose_ig_crop(page, first_media: str, pacer) -> str:
    """Instagram defaults to a 1:1 crop, which would cut a 1080x1350 poster.
    Pick "Original" (or 4:5 for a 4:5 image). Returns what was chosen."""
    size = image_size(first_media)
    want_45 = bool(size and size[1] and abs(size[0] / size[1] - 0.8) < 0.02)
    try:
        toggle = find_first(page, IG_SELECT_CROP, timeout_ms=5000)
    except ElementNotFound:
        return "default"
    safe_click(page, toggle, label="Select crop")
    pacer.pause(0.4, 0.9)
    chosen = "default"
    for name, cands in (("original", IG_CROP_ORIGINAL), ("4:5", IG_CROP_45)) if not want_45 else \
            (("4:5", IG_CROP_45), ("original", IG_CROP_ORIGINAL)):
        try:
            safe_click(page, find_first(page, cands, timeout_ms=3000), label=f"crop {name}")
            chosen = name
            break
        except ElementNotFound:
            continue
    try:                                   # close the ratio menu so Next is reachable
        safe_click(page, find_first(page, IG_SELECT_CROP, timeout_ms=2000), label="Select crop (close)")
    except ElementNotFound:
        pass
    pacer.pause(0.4, 0.9)
    return chosen


# ── Facebook Page ────────────────────────────────────────────────────────────
FB_HOME_URL = "https://www.facebook.com/"
FB_TEXT_LIMIT = 63206
FB_SWITCH = [C("role", "button", "Switch now"), C("role", "button", "Switch Profile"),
             C("role", "button", "Switch to Page"), C("text", "Switch now")]
FB_SWITCH_CONFIRM = [C("css", "[role='dialog'] [role='button']:has-text('Switch')"),
                     C("css", "[role='dialog'] button:has-text('Switch')")]
FB_CREATE = [C("role", "button", "What's on your mind"), C("css", "[role='button']:has-text(\"What's on your mind\")"),
             C("role", "button", "Create post"), C("text", "What's on your mind"), C("text", "Create post")]
FB_TEXTBOX = [C("css", "[role='dialog'] div[contenteditable='true'][role='textbox']"),
              C("css", "[role='dialog'] [aria-label*=\"What's on your mind\"][contenteditable='true']"),
              C("css", "[role='dialog'] div[contenteditable='true']")]
FB_FILE_INPUT = [C("css", "[role='dialog'] input[type='file'][accept*='image']"),
                 C("css", "[role='dialog'] input[type='file']"), C("css", "input[type='file'][accept*='image']")]
FB_PHOTO_BUTTON = [C("css", "[role='dialog'] [aria-label='Photo/video']"), C("role", "button", "Photo/video"),
                   C("text", "Photo/video")]
FB_NEXT = [C("css", "[role='dialog'] [aria-label='Next'][role='button']"), C("css", "[role='dialog'] [role='button']:has-text('Next')")]
FB_POST = [C("css", "[role='dialog'] [aria-label='Post'][role='button']"),
           C("css", "[role='dialog'] div[role='button']:has(span:text-is('Post'))"),
           C("css", "[role='dialog'] div[role='button']:text-is('Post')")]
FB_DIALOG = [C("css", "form[method='POST'] [role='dialog']"), C("css", "[role='dialog'] div[contenteditable='true']")]

FB_UPSELL_DISMISS = [C("css", "[role='dialog'] div[role='button']:has(span:text-is('Not now'))"),
                     C("css", "[role='dialog'] [aria-label='Not now']")]


def dismiss_fb_upsell(page) -> bool:
    """Close Facebook's optional upsell pop-ups (e.g. 'Speak to people directly' / Call Now)."""
    for c in FB_UPSELL_DISMISS:
        try:
            loc = c.resolve(page).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                page.wait_for_timeout(800)
                return True
        except Exception:                          # noqa: BLE001
            continue
    return False

FB_POSTED = [C("text", "Your post is now published"), C("text", "Post published"), C("text", "Just now")]


def validate_facebook(text: str, media: list[str]) -> None:
    if not text.strip() and not media:
        raise FlowError("Facebook post needs text or an image")
    if len(text) > FB_TEXT_LIMIT:
        raise FlowError(f"Facebook post is {len(text)} chars; limit is {FB_TEXT_LIMIT}")
    for m in media:
        if not Path(m).is_file():
            raise FlowError(f"media file not found: {m}")


def facebook_page_post(page, text: str, media: list[str] | None = None, *, page_url: str = "",
                       dry_run: bool = False, pacer: Pacer | None = None, screenshot=None) -> dict:
    """Post on the brand's Facebook Page as the Page. Dry-run stops before Post."""
    pacer = pacer or Pacer()
    media = [m for m in (media or []) if m]
    validate_facebook(text, media)
    url = page_url or link_memory.preferred("facebook", "page", "")
    if not url:
        raise FlowError("no Facebook Page URL: pass --page-url or set social_links.facebook.page (brand.json, or config/client.json in a client build)")

    open_page(page, url)
    front(page)
    pacer.pause(2.0, 3.5)
    ensure_logged_in(page, "facebook")
    require_logged_in_marker(page, "facebook")
    remember("facebook", "page", url)

    switched = False
    try:                                   # acting as the person → switch into the Page profile
        safe_click(page, find_first(page, FB_SWITCH, timeout_ms=4000), label="Switch now")
        pacer.pause(1.0, 2.0)
        try:
            safe_click(page, find_first(page, FB_SWITCH_CONFIRM, timeout_ms=5000), label="Switch (confirm)")
        except ElementNotFound:
            pass
        switched = True
        pacer.pause(3.0, 5.0)
        open_page(page, url)               # switching lands on the Page's home/feed
        front(page)
        pacer.pause(2.0, 3.5)
    except ElementNotFound:
        pass

    safe_click(page, find_first(page, FB_CREATE, timeout_ms=15000), label="What's on your mind")
    pacer.pause(1.2, 2.2)
    refuse_destructive_prompt(page)
    if text:
        box = find_first(page, FB_TEXTBOX, timeout_ms=10000)
        box.evaluate("e => e.focus()")        # an invisible layer blocks real clicks on the lexical editor
        page.keyboard.type(text, delay=25)
        close_typeahead(page, text)
    if media:
        attach_media(page, FB_FILE_INPUT, FB_PHOTO_BUTTON, media)
        pacer.pause(2.5, 4.5)
    try:                                   # Pages with post settings show Next before Post
        safe_click(page, find_first(page, FB_NEXT, timeout_ms=2500), label="Next")
        pacer.pause(1.0, 2.0)
    except ElementNotFound:
        pass
    refuse_destructive_prompt(page)
    dismiss_fb_upsell(page)
    button = find_first(page, FB_POST, timeout_ms=10000)
    if button.is_disabled():
        raise FlowError("Facebook disabled the Post button (image still uploading or text rejected)")

    shot = screenshot("dryrun" if dry_run else "precommit") if screenshot else None
    if dry_run:
        log_event("facebook dry-run complete", platform="facebook", chars=len(text), media=len(media))
        return {"ok": True, "platform": "facebook", "route": "browser", "dry_run": True, "switched": switched,
                "page_url": url, "chars": len(text), "media": media, "screenshot": shot}

    pacer.pause(0.8, 1.6)
    front(page)
    route = safe_click(page, button, label="Post")
    confirmed = False
    for _ in range(30):                    # composer closes or a toast appears
        page.wait_for_timeout(1000)
        if dismiss_fb_upsell(page):
            try:                           # the upsell can swallow the Post click: press Post once more
                again = find_first(page, FB_POST, timeout_ms=1500)
                if not again.is_disabled():
                    safe_click(page, again, label="Post (after upsell)")
            except ElementNotFound:
                pass
            continue
        try:
            find_first(page, FB_POSTED, timeout_ms=0)
            confirmed = True
            break
        except ElementNotFound:
            pass
        try:
            find_first(page, FB_POST, timeout_ms=0)
        except ElementNotFound:
            confirmed = True
            break
        refuse_destructive_prompt(page)
    if not confirmed:
        raise FlowError("clicked Post but the Facebook composer did not close")
    log_event("facebook page post sent", platform="facebook", chars=len(text), media=len(media), route=route)
    shot = screenshot("sent") if screenshot else None
    link = newest_permalink(page, "facebook", url, pacer)
    return {"ok": True, "platform": "facebook", "route": "browser", "switched": switched, "page_url": url,
            "chars": len(text), "media": media, "screenshot": shot, "permalink": link}


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
            remember(platform_name, "home", page.url or url)
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
