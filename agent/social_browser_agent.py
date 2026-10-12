#!/usr/bin/env python3
"""
Nexus Social Browser Agent  🤖🌐
===================================
Advanced browser automation for social media posting WITHOUT API credentials.
Uses Playwright (preferred) or Selenium/CDP as fallback to drive a real Chrome
browser session with human-like delays and behaviour patterns.

Supported workflows:
  • instagram_post    — log in & create an Instagram post (caption + image)
  • facebook_post     — log in & post to Facebook Page or profile
  • blogger_post      — log in & publish a Blogger article (title + body + labels)
  • canva_create      — open Canva, pick a template, apply text, download PNG
  • canva_publish     — open saved Canva design → Download → share to platform
  • generic_post      — navigate to URL → find text area → type → submit (fallback)
  • screenshot        — snapshot any page for visual confirmation
  • status            — report which engine is available + session state

Installation:
    pip install playwright playwright-stealth pillow
    playwright install chromium

CLI examples:
    python3 social_browser_agent.py status
    python3 social_browser_agent.py instagram_post \\
        --caption "Check out our new product! 🚀 #launch" \\
        --image /path/to/post.jpg \\
        --allow-actions

    python3 social_browser_agent.py facebook_post \\
        --message "Hello from Nexus!" \\
        --page-url "https://www.facebook.com/YourPageName" \\
        --allow-actions

    python3 social_browser_agent.py blogger_post \\
        --title "My AI Blog Post" \\
        --body "Full HTML or plain text body here" \\
        --labels "AI,Marketing" \\
        --blog-url "https://www.blogger.com/blog/posts/12345" \\
        --allow-actions

    python3 social_browser_agent.py canva_create \\
        --template "instagram post" \\
        --headline "Summer Sale 50% Off" \\
        --out shots/canva_design.png \\
        --allow-actions

Safety contract (same as browser_automation.py):
  • All mutations (post/click/type) need --allow-actions
  • Every action is written to logs/browser_audit.jsonl
  • Deny-list blocks logout / delete-account / payment operations
  • Headless mode by default; --headed for debugging
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = ROOT / "logs" / "browser_audit.jsonl"
SESSION_FILE = ROOT / "logs" / "social_session.json"
PROFILE_DIR = ROOT / "logs" / "social_profile"
SHOTS_DIR = ROOT / "shots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Safety deny-list  — refused even when --allow-actions is set
# ──────────────────────────────────────────────────────────────────────────────
DENY_PATTERNS = [
    r"delete[_ -]?(my[_ -])?account",
    r"log\s*out",
    r"sign\s*out",
    r"remove[_ -]?payment",
    r"cancel[_ -]?subscription",
    r"/admin/(delete|users)",
    r"deactivate",
]

PERMISSION_NOTE = (
    "BLOCKED: This workflow changes browser state. "
    "Pass --allow-actions after the user grants Browser Actions Permission in Nexus Settings."
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(command: str, args: dict, status: str, detail: str = "", **extra) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": now_iso(),
        "agent": "social_browser_agent",
        "command": command,
        "args": {k: v for k, v in args.items() if k not in ("func",)},
        "status": status,
        "detail": detail[:500],
        **extra,
    }
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[audit] could not write audit log: {e}", file=sys.stderr)
    print(f"[audit] {status.upper():7s} {command}: {(detail or status)[:120]}", file=sys.stderr)


def is_denied(*targets: str) -> bool:
    joined = " ".join(t.lower() for t in targets if t)
    return any(re.search(p, joined) for p in DENY_PATTERNS)


def block_denied(cmd: str, args, reason: str = "deny-list match") -> None:
    audit(cmd, vars(args) if hasattr(args, "__dict__") else {}, "blocked", reason)
    print(json.dumps({"ok": False, "error": f"Blocked by safety policy: {reason}", "blocked": True}))
    sys.exit(2)


def require_actions(args) -> bool:
    if not getattr(args, "allow_actions", False):
        audit(getattr(args, "command", "unknown"), vars(args) if hasattr(args, "__dict__") else {}, "blocked", "missing --allow-actions")
        print(json.dumps({"ok": False, "error": PERMISSION_NOTE, "blocked": True}))
        sys.exit(2)
    return True


def human_delay(lo: float = 0.8, hi: float = 2.4) -> None:
    """Mimic human reading / thinking pause to avoid bot-detection heuristics."""
    time.sleep(lo + random.random() * (hi - lo))


def human_type(page, selector: str, text: str, clear: bool = True) -> None:
    """Type text character-by-character with random delays to look human."""
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=12000)
    if clear:
        locator.triple_click()
        human_delay(0.2, 0.5)
        page.keyboard.press("Backspace")
    for ch in text:
        locator.press_sequentially(ch, delay=random.randint(45, 145))
    human_delay(0.3, 0.7)


def result_ok(data: dict) -> None:
    print(json.dumps({"ok": True, **data}))


def result_err(msg: str, detail: str = "") -> None:
    print(json.dumps({"ok": False, "error": msg, "detail": detail}))


def load_session() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_session(**kw) -> None:
    state = load_session()
    state.update(kw)
    state["updated_at"] = now_iso()
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Engine detection
# ──────────────────────────────────────────────────────────────────────────────
def detect_engine() -> str:
    try:
        import playwright  # noqa: F401
        return "playwright"
    except ImportError:
        pass
    try:
        import selenium  # noqa: F401
        return "selenium"
    except ImportError:
        pass
    return "none"


def get_playwright_page(headless: bool = True):
    """Return a (playwright_obj, context, page) tuple with a persistent profile."""
    from playwright.sync_api import sync_playwright
    try:
        from playwright_stealth import stealth_sync  # noqa: F401
        USE_STEALTH = True
    except ImportError:
        USE_STEALTH = False

    pw = sync_playwright().start()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            # Realistic Chrome UA — not a generic bot UA
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--lang=en-US",
            ],
            locale="en-US",
            timezone_id="America/New_York",
        )
    except Exception:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if USE_STEALTH:
        stealth_sync(page)
    return pw, ctx, page


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: STATUS
# ──────────────────────────────────────────────────────────────────────────────
def cmd_status(args):
    engine = detect_engine()
    session = load_session()
    result_ok({
        "engine": engine,
        "engine_ready": engine != "none",
        "playwright_available": engine == "playwright",
        "selenium_available": engine == "selenium",
        "session": session,
        "profile_dir": str(PROFILE_DIR),
        "audit_log": str(AUDIT_LOG),
        "message": (
            f"Engine: {engine}. "
            "Run: pip install playwright && playwright install chromium  to get full power."
            if engine == "none" else
            f"✅ {engine} ready. Persistent profile: {PROFILE_DIR}. "
            "You can log into social accounts in the browser and sessions will persist."
        )
    })
    audit("status", vars(args), "ok", engine)


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: INSTAGRAM POST
# ──────────────────────────────────────────────────────────────────────────────
def cmd_instagram_post(args):
    require_actions(args)
    caption = getattr(args, "caption", "") or ""
    image_path = getattr(args, "image", "") or ""
    if is_denied(caption):
        block_denied("instagram_post", args)

    if detect_engine() != "playwright":
        result_err("Playwright not installed.", "pip install playwright && playwright install chromium")
        return

    pw, ctx, page = get_playwright_page(headless=getattr(args, "headed", False) is False)
    shot = str(SHOTS_DIR / f"ig_post_{int(time.time())}.png")
    try:
        # Navigate to Instagram
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        human_delay(2, 4)

        # If not logged in — wait up to 30s for user to log in manually (non-headless)
        if "accounts/login" in page.url or page.locator("input[name='username']").count() > 0:
            if getattr(args, "headed", False):
                print("[info] Not logged in. Please log in manually. Waiting 60s…", file=sys.stderr)
                human_delay(60, 61)
            else:
                result_err(
                    "Not logged in to Instagram.",
                    "Run with --headed to log in manually, then sessions persist in the profile."
                )
                audit("instagram_post", vars(args), "error", "not logged in")
                return

        # Click the + Create button
        create_btn = page.locator("[aria-label='New post']").or_(
            page.locator("svg[aria-label='New post']")
        ).or_(page.locator("text=Create")).first
        create_btn.wait_for(state="visible", timeout=15000)
        create_btn.click()
        human_delay(1.5, 3)

        # Upload image if provided
        if image_path and Path(image_path).exists():
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(image_path)
            human_delay(2, 4)
            # Click Next through crop/filter screens
            for _ in range(3):
                next_btn = page.locator("text=Next").or_(page.locator("[aria-label='Next']")).first
                if next_btn.is_visible():
                    next_btn.click()
                    human_delay(1.5, 2.5)

        # Type caption
        caption_area = page.locator("textarea[aria-label='Write a caption…']").or_(
            page.locator("div[contenteditable='true']")
        ).first
        caption_area.wait_for(state="visible", timeout=15000)
        caption_area.click()
        human_delay(0.5, 1)
        for ch in caption:
            page.keyboard.press_sequentially(ch, delay=random.randint(40, 120))
        human_delay(1, 2)

        # Share
        share_btn = page.locator("text=Share").or_(
            page.locator("button[type='button']:has-text('Share')")
        ).first
        share_btn.wait_for(state="visible", timeout=15000)
        share_btn.click()
        human_delay(3, 6)

        page.screenshot(path=shot)
        save_session(last_platform="instagram", last_action="post", last_ts=now_iso())
        audit("instagram_post", vars(args), "ok", f"Posted: {caption[:60]}")
        result_ok({"platform": "instagram", "action": "post", "caption": caption[:60], "screenshot": shot})

    except Exception as e:
        try:
            page.screenshot(path=shot)
        except Exception:
            pass
        audit("instagram_post", vars(args), "error", str(e)[:300])
        result_err(f"Instagram post failed: {str(e)[:200]}", f"Screenshot: {shot}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: FACEBOOK POST
# ──────────────────────────────────────────────────────────────────────────────
def cmd_facebook_post(args):
    require_actions(args)
    message = getattr(args, "message", "") or ""
    page_url = getattr(args, "page_url", "") or "https://www.facebook.com/"
    image_path = getattr(args, "image", "") or ""

    if is_denied(message, page_url):
        block_denied("facebook_post", args)

    if detect_engine() != "playwright":
        result_err("Playwright not installed.", "pip install playwright && playwright install chromium")
        return

    pw, ctx, page = get_playwright_page(headless=getattr(args, "headed", False) is False)
    shot = str(SHOTS_DIR / f"fb_post_{int(time.time())}.png")
    try:
        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
        human_delay(2, 4)

        if "login" in page.url:
            if getattr(args, "headed", False):
                print("[info] Not logged in. Please log in manually. Waiting 60s…", file=sys.stderr)
                human_delay(60, 61)
            else:
                result_err("Not logged in to Facebook.", "Run with --headed to log in, sessions persist in profile.")
                audit("facebook_post", vars(args), "error", "not logged in")
                return

        # Click "What's on your mind?" post box
        post_box_selectors = [
            "div[data-testid='status-attachment-mentions-input']",
            "span:has-text(\"What's on your mind\")",
            "[data-pagelet='FeedComposer']",
            "div[role='textbox']",
            "div[aria-label=\"What's on your mind?\"]",
        ]
        clicked = False
        for sel in post_box_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible():
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Try generic approach: look for "Create post" area
            page.locator("text=Create post").first.click()
        human_delay(1, 2.5)

        # Type message
        text_area = page.locator("div[role='textbox']").first
        text_area.wait_for(state="visible", timeout=15000)
        text_area.click()
        for ch in message:
            page.keyboard.press_sequentially(ch, delay=random.randint(45, 130))
        human_delay(1, 2)

        # Upload image if provided
        if image_path and Path(image_path).exists():
            photo_btn = page.locator("[aria-label='Photo/video']").or_(
                page.locator("text=Photo/video")
            ).first
            if photo_btn.is_visible():
                photo_btn.click()
                human_delay(1, 2)
                file_input = page.locator("input[type='file']").first
                file_input.set_input_files(image_path)
                human_delay(2, 3)

        # Click Post button
        post_btn = page.locator("button[aria-label='Post']").or_(
            page.locator("div[aria-label='Post']")
        ).or_(page.locator("text=Post")).last
        post_btn.wait_for(state="visible", timeout=15000)
        post_btn.click()
        human_delay(3, 5)

        page.screenshot(path=shot)
        save_session(last_platform="facebook", last_action="post", last_ts=now_iso())
        audit("facebook_post", vars(args), "ok", f"Posted to {page_url}: {message[:60]}")
        result_ok({"platform": "facebook", "action": "post", "message": message[:60], "screenshot": shot})

    except Exception as e:
        try:
            page.screenshot(path=shot)
        except Exception:
            pass
        audit("facebook_post", vars(args), "error", str(e)[:300])
        result_err(f"Facebook post failed: {str(e)[:200]}", f"Screenshot: {shot}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: BLOGGER POST
# ──────────────────────────────────────────────────────────────────────────────
def cmd_blogger_post(args):
    require_actions(args)
    title = getattr(args, "title", "") or "Untitled Post"
    body = getattr(args, "body", "") or ""
    labels = getattr(args, "labels", "") or ""
    blog_url = getattr(args, "blog_url", "") or "https://www.blogger.com"

    if is_denied(title, body):
        block_denied("blogger_post", args)

    if detect_engine() != "playwright":
        result_err("Playwright not installed.", "pip install playwright && playwright install chromium")
        return

    pw, ctx, page = get_playwright_page(headless=getattr(args, "headed", False) is False)
    shot = str(SHOTS_DIR / f"blogger_post_{int(time.time())}.png")
    try:
        # Navigate to Blogger new post
        if "blogger.com" not in blog_url:
            blog_url = "https://www.blogger.com"
        new_post_url = "https://www.blogger.com/blog/post/create"
        page.goto(new_post_url, wait_until="domcontentloaded", timeout=30000)
        human_delay(2, 4)

        if "accounts.google" in page.url or "signin" in page.url:
            if getattr(args, "headed", False):
                print("[info] Not logged in to Google. Please log in manually. Waiting 90s…", file=sys.stderr)
                human_delay(90, 91)
            else:
                result_err("Not logged in to Google/Blogger.", "Run with --headed to log in, sessions persist.")
                audit("blogger_post", vars(args), "error", "not logged in")
                return

        # Click post title field
        title_field = page.locator("textarea[id='title']").or_(
            page.locator("input[placeholder*='Title']")
        ).or_(page.locator("h3[contenteditable]")).first
        title_field.wait_for(state="visible", timeout=15000)
        title_field.click()
        for ch in title:
            page.keyboard.press_sequentially(ch, delay=random.randint(50, 130))
        human_delay(0.5, 1)

        # Click body / compose area (the iframe or contenteditable)
        # Blogger uses an iframe for the editor
        frame = None
        try:
            page.wait_for_selector("iframe#BloggerEditor", timeout=5000)
            frame = page.frame(name="BloggerEditor") or page.frames[1]
        except Exception:
            pass

        if frame:
            body_el = frame.locator("body")
        else:
            body_el = page.locator("[contenteditable='true']").nth(1)

        body_el.click()
        human_delay(0.5, 1)
        for ch in body:
            page.keyboard.press_sequentially(ch, delay=random.randint(30, 100))
        human_delay(1, 2)

        # Add labels if provided
        if labels:
            labels_btn = page.locator("text=Labels").or_(
                page.locator("[aria-label='Labels']")
            ).first
            if labels_btn.is_visible():
                labels_btn.click()
                human_delay(0.5, 1)
                labels_input = page.locator("input[placeholder*='label']").or_(
                    page.locator("input[name='label']")
                ).first
                if labels_input.is_visible():
                    for lbl in labels.split(","):
                        labels_input.fill(lbl.strip())
                        page.keyboard.press("Enter")
                        human_delay(0.2, 0.5)

        # Publish
        publish_btn = page.locator("button:has-text('Publish')").or_(
            page.locator("[aria-label='Publish']")
        ).first
        publish_btn.wait_for(state="visible", timeout=15000)
        publish_btn.click()
        human_delay(3, 6)

        # Confirm if a dialog appears
        confirm = page.locator("button:has-text('OK')").or_(
            page.locator("button:has-text('Publish')")
        ).last
        if confirm.is_visible():
            confirm.click()
            human_delay(2, 4)

        page.screenshot(path=shot)
        save_session(last_platform="blogger", last_action="post", last_ts=now_iso())
        audit("blogger_post", vars(args), "ok", f"Published: {title[:60]}")
        result_ok({"platform": "blogger", "action": "post", "title": title[:60], "screenshot": shot})

    except Exception as e:
        try:
            page.screenshot(path=shot)
        except Exception:
            pass
        audit("blogger_post", vars(args), "error", str(e)[:300])
        result_err(f"Blogger post failed: {str(e)[:200]}", f"Screenshot: {shot}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: CANVA CREATE (open Canva, pick template, set headline, download)
# ──────────────────────────────────────────────────────────────────────────────
def cmd_canva_create(args):
    require_actions(args)
    template = getattr(args, "template", "instagram post") or "instagram post"
    headline = getattr(args, "headline", "") or ""
    out_path = getattr(args, "out", "") or str(SHOTS_DIR / f"canva_design_{int(time.time())}.png")

    if is_denied(template, headline):
        block_denied("canva_create", args)

    if detect_engine() != "playwright":
        result_err("Playwright not installed.", "pip install playwright && playwright install chromium")
        return

    pw, ctx, page = get_playwright_page(headless=getattr(args, "headed", False) is False)
    shot = str(SHOTS_DIR / f"canva_{int(time.time())}.png")
    try:
        page.goto("https://www.canva.com/create/", wait_until="domcontentloaded", timeout=30000)
        human_delay(2, 4)

        if "login" in page.url or page.locator("text=Log in").is_visible():
            if getattr(args, "headed", False):
                print("[info] Not logged in to Canva. Please log in manually. Waiting 90s…", file=sys.stderr)
                human_delay(90, 91)
            else:
                result_err("Not logged in to Canva.", "Run with --headed to log in, sessions persist.")
                audit("canva_create", vars(args), "error", "not logged in")
                return

        # Search for a template
        search = page.locator("input[placeholder*='Search']").or_(
            page.locator("[aria-label*='Search']")
        ).first
        if search.is_visible():
            human_type(page, "input[placeholder*='Search']", template)
            page.keyboard.press("Enter")
            human_delay(2, 4)

        # Click first template result
        template_card = page.locator("[data-testid='design-grid-item']").or_(
            page.locator(".design-card")
        ).first
        if template_card.is_visible():
            template_card.click()
            human_delay(3, 6)

        # Wait for editor to load
        page.wait_for_load_state("networkidle", timeout=20000)
        human_delay(2, 3)

        # Click on a text element and type the headline
        if headline:
            text_el = page.locator("[data-text-element]").or_(
                page.locator("[contenteditable='true']")
            ).first
            if text_el.is_visible():
                text_el.double_click()
                human_delay(0.5, 1)
                page.keyboard.press("Control+A")
                for ch in headline:
                    page.keyboard.press_sequentially(ch, delay=random.randint(50, 120))
                human_delay(1, 2)

        # Download
        download_btn = page.locator("button:has-text('Download')").or_(
            page.locator("[aria-label='Download']")
        ).first
        if download_btn.is_visible():
            download_btn.click()
            human_delay(1, 2)
            # Select PNG
            png_opt = page.locator("text=PNG").first
            if png_opt.is_visible():
                png_opt.click()
                human_delay(0.5, 1)
            # Click download
            dl_btn = page.locator("button:has-text('Download')").last
            with page.expect_download() as dl:
                dl_btn.click()
            download = dl.value
            download.save_as(out_path)
            human_delay(1, 2)

        page.screenshot(path=shot)
        save_session(last_platform="canva", last_action="create", last_ts=now_iso())
        audit("canva_create", vars(args), "ok", f"Template: {template}, headline: {headline[:40]}")
        result_ok({"platform": "canva", "action": "create", "template": template,
                   "design_file": out_path, "screenshot": shot})

    except Exception as e:
        try:
            page.screenshot(path=shot)
        except Exception:
            pass
        audit("canva_create", vars(args), "error", str(e)[:300])
        result_err(f"Canva create failed: {str(e)[:200]}", f"Screenshot: {shot}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: CANVA → DOWNLOAD → POST TO INSTAGRAM (full end-to-end)
# ──────────────────────────────────────────────────────────────────────────────
def cmd_canva_to_instagram(args):
    require_actions(args)
    design_url = getattr(args, "design_url", "") or ""
    caption = getattr(args, "caption", "") or ""

    if is_denied(design_url, caption):
        block_denied("canva_to_instagram", args)

    if not design_url:
        result_err("Provide --design-url (the Canva editor URL of the design to publish)")
        return

    if detect_engine() != "playwright":
        result_err("Playwright not installed.", "pip install playwright && playwright install chromium")
        return

    tmp_image = str(SHOTS_DIR / f"canva_ig_{int(time.time())}.png")
    pw, ctx, page = get_playwright_page(headless=getattr(args, "headed", False) is False)
    try:
        # Step 1: Download from Canva
        page.goto(design_url, wait_until="domcontentloaded", timeout=30000)
        human_delay(3, 5)
        page.wait_for_load_state("networkidle", timeout=20000)

        dl_btn = page.locator("button:has-text('Download')").or_(
            page.locator("[aria-label='Download']")
        ).first
        dl_btn.wait_for(state="visible", timeout=15000)
        dl_btn.click()
        human_delay(1, 2)

        png_opt = page.locator("text=PNG").first
        if png_opt.is_visible():
            png_opt.click()
            human_delay(0.5, 1)

        dl_confirm = page.locator("button:has-text('Download')").last
        with page.expect_download(timeout=60000) as dl:
            dl_confirm.click()
        download = dl.value
        download.save_as(tmp_image)
        human_delay(2, 3)

        # Step 2: Post to Instagram
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        human_delay(2, 4)

        if "accounts/login" in page.url:
            result_err("Not logged in to Instagram.", "Run with --headed to log in.")
            return

        create_btn = page.locator("[aria-label='New post']").or_(
            page.locator("text=Create")
        ).first
        create_btn.wait_for(state="visible", timeout=15000)
        create_btn.click()
        human_delay(1.5, 3)

        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(tmp_image)
        human_delay(2, 4)

        for _ in range(3):
            nb = page.locator("text=Next").or_(page.locator("[aria-label='Next']")).first
            if nb.is_visible():
                nb.click()
                human_delay(1.5, 2.5)

        cap_area = page.locator("textarea[aria-label='Write a caption…']").or_(
            page.locator("div[contenteditable='true']")
        ).first
        cap_area.wait_for(state="visible", timeout=15000)
        cap_area.click()
        for ch in caption:
            page.keyboard.press_sequentially(ch, delay=random.randint(40, 120))
        human_delay(1, 2)

        share = page.locator("text=Share").or_(
            page.locator("button:has-text('Share')")
        ).first
        share.wait_for(state="visible", timeout=15000)
        share.click()
        human_delay(4, 7)

        shot = str(SHOTS_DIR / f"canva_ig_posted_{int(time.time())}.png")
        page.screenshot(path=shot)
        save_session(last_platform="instagram", last_action="canva_post", last_ts=now_iso())
        audit("canva_to_instagram", vars(args), "ok", f"Canva design posted to Instagram: {caption[:60]}")
        result_ok({
            "action": "canva_to_instagram",
            "canva_design_url": design_url,
            "instagram_caption": caption[:60],
            "downloaded_image": tmp_image,
            "screenshot": shot,
        })

    except Exception as e:
        shot = str(SHOTS_DIR / f"canva_ig_err_{int(time.time())}.png")
        try:
            page.screenshot(path=shot)
        except Exception:
            pass
        audit("canva_to_instagram", vars(args), "error", str(e)[:300])
        result_err(f"Canva→Instagram pipeline failed: {str(e)[:200]}", f"Screenshot: {shot}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Workflow: GENERIC POST (fallback for any URL)
# ──────────────────────────────────────────────────────────────────────────────
def cmd_generic_post(args):
    require_actions(args)
    url = getattr(args, "url", "") or ""
    selector = getattr(args, "selector", "") or "textarea, [contenteditable='true'], [role='textbox']"
    text = getattr(args, "text", "") or ""
    submit_selector = getattr(args, "submit", "") or "button[type='submit']"

    if is_denied(url, selector, text):
        block_denied("generic_post", args)

    if not url:
        result_err("Provide --url for generic_post")
        return

    if detect_engine() != "playwright":
        result_err("Playwright not installed.", "pip install playwright && playwright install chromium")
        return

    pw, ctx, page = get_playwright_page(headless=getattr(args, "headed", False) is False)
    shot = str(SHOTS_DIR / f"generic_post_{int(time.time())}.png")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        human_delay(2, 4)

        text_area = page.locator(selector).first
        text_area.wait_for(state="visible", timeout=15000)
        text_area.click()
        human_delay(0.5, 1)
        for ch in text:
            page.keyboard.press_sequentially(ch, delay=random.randint(45, 130))
        human_delay(1, 2)

        if submit_selector:
            submit = page.locator(submit_selector).first
            if submit.is_visible():
                submit.click()
                human_delay(2, 4)

        page.screenshot(path=shot)
        save_session(last_platform=url, last_action="generic_post", last_ts=now_iso())
        audit("generic_post", vars(args), "ok", f"Posted to {url}: {text[:60]}")
        result_ok({"url": url, "text": text[:60], "screenshot": shot})

    except Exception as e:
        try:
            page.screenshot(path=shot)
        except Exception:
            pass
        audit("generic_post", vars(args), "error", str(e)[:300])
        result_err(f"Generic post failed: {str(e)[:200]}", f"Screenshot: {shot}")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Nexus Social Browser Agent — human-like social media posting via browser automation"
    )
    p.add_argument("--allow-actions", action="store_true", dest="allow_actions",
                   help="Enable state-changing actions (click/type/post). Required for all post workflows.")
    p.add_argument("--headed", action="store_true",
                   help="Show the browser window (useful for manual login on first run)")

    sub = p.add_subparsers(dest="command")

    sub.add_parser("status", help="Check engine status & session info")

    ig = sub.add_parser("instagram_post", help="Post to Instagram via browser")
    ig.add_argument("--caption", required=True, help="Post caption (hashtags included)")
    ig.add_argument("--image", default="", help="Path to image file")

    fb = sub.add_parser("facebook_post", help="Post to Facebook via browser")
    fb.add_argument("--message", required=True, help="Post message")
    fb.add_argument("--page-url", default="https://www.facebook.com/", dest="page_url",
                    help="Facebook Page URL (default: personal feed)")
    fb.add_argument("--image", default="", help="Path to image file")

    bl = sub.add_parser("blogger_post", help="Publish a Blogger article via browser")
    bl.add_argument("--title", required=True, help="Post title")
    bl.add_argument("--body", required=True, help="Post body (plain text or HTML)")
    bl.add_argument("--labels", default="", help="Comma-separated labels/tags")
    bl.add_argument("--blog-url", default="", dest="blog_url", help="Blogger blog URL")

    cv = sub.add_parser("canva_create", help="Create a design in Canva and download as PNG")
    cv.add_argument("--template", default="instagram post", help="Template search query")
    cv.add_argument("--headline", default="", help="Headline text to apply")
    cv.add_argument("--out", default="", help="Output PNG path")

    ci = sub.add_parser("canva_to_instagram", help="Download Canva design and post to Instagram")
    ci.add_argument("--design-url", required=True, dest="design_url", help="Canva editor URL")
    ci.add_argument("--caption", required=True, help="Instagram caption")

    gp = sub.add_parser("generic_post", help="Post to any page via CSS selectors")
    gp.add_argument("--url", required=True, help="Target URL")
    gp.add_argument("--selector", default="textarea", help="CSS selector of the text input")
    gp.add_argument("--text", required=True, help="Text to type")
    gp.add_argument("--submit", default="button[type='submit']", help="CSS selector of submit button")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "status": cmd_status,
        "instagram_post": cmd_instagram_post,
        "facebook_post": cmd_facebook_post,
        "blogger_post": cmd_blogger_post,
        "canva_create": cmd_canva_create,
        "canva_to_instagram": cmd_canva_to_instagram,
        "generic_post": cmd_generic_post,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
