#!/usr/bin/env python3
"""
Nexus social publisher — posts queued marketing copy to live APIs.

Reads credentials from environment / .env, or from a JSON payload passed by
the local relay (browser UI → localhost:8712/publish). Never logs tokens.

Supported:
  facebook   Graph API page feed
  instagram  Graph API media container + publish (requires public image_url)
  twitter    X API v2 tweets
  linkedin   LinkedIn UGC posts (person URN)
  blogger    Blogger API v3

Usage:
  python3 agent/publish.py --platform facebook --text "Hello" --dry-run
  python3 agent/publish.py --from-json logs/publish_job.json
  python3 agent/publish.py --due          # publish scheduled queue items that are due
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "logs" / "publish_queue.json"
AUDIT = ROOT / "logs" / "publish_audit.jsonl"
SOCIAL_AGENT = ROOT / "agent" / "social_browser_agent.py"


def load_env() -> None:
    for p in (ROOT / ".env", Path.cwd() / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(platform: str, status: str, detail: str, **extra) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": now_iso(), "platform": platform, "status": status, "detail": detail[:400], **extra}
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def http_json(method: str, url: str, headers=None, data=None, form=None, timeout=30):
    headers = dict(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif data is not None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="ignore")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(err)
        except json.JSONDecodeError:
            parsed = {"error": err[:400]}
        return e.code, parsed
    except Exception as e:
        return 0, {"error": str(e)}


def creds_from(payload: dict) -> dict:
    c = payload.get("credentials") or {}
    return {
        "fb_token": c.get("fb-page-token") or os.getenv("META_ACCESS_TOKEN", ""),
        "fb_page": c.get("fb-page-id") or os.getenv("FACEBOOK_PAGE_ID", ""),
        "ig_token": c.get("ig-access-token") or os.getenv("META_ACCESS_TOKEN", ""),
        "ig_id": c.get("ig-account-id") or os.getenv("INSTAGRAM_ACCOUNT_ID", ""),
        "tw_bearer": c.get("tw-bearer-token") or os.getenv("TWITTER_BEARER_TOKEN", ""),
        "li_token": c.get("li-access-token") or os.getenv("LINKEDIN_ACCESS_TOKEN", ""),
        "li_urn": c.get("li-person-urn") or os.getenv("LINKEDIN_PERSON_URN", ""),
        "bl_token": c.get("bl-access-token") or os.getenv("BLOGGER_ACCESS_TOKEN", ""),
        "bl_id": c.get("bl-blog-id") or os.getenv("BLOGGER_BLOG_ID", ""),
    }


def post_facebook(text: str, c: dict, media: str = "") -> dict:
    if not c["fb_token"] or not c["fb_page"]:
        return {"ok": False, "error": "Missing Facebook page id or page token"}
    form = {"message": text, "access_token": c["fb_token"]}
    if media:
        form["link"] = media
    status, body = http_json("POST", f"https://graph.facebook.com/v21.0/{c['fb_page']}/feed", form=form)
    ok = status == 200 and bool(body.get("id"))
    return {"ok": ok, "status": status, "id": body.get("id"), "error": body.get("error")}


def post_instagram(text: str, c: dict, media: str = "") -> dict:
    if not c["ig_token"] or not c["ig_id"]:
        return {"ok": False, "error": "Missing Instagram account id or access token"}
    if not media:
        return {"ok": False, "error": "Instagram Graph API requires a public image_url (media field)"}
    base = f"https://graph.facebook.com/v21.0/{c['ig_id']}"
    status, body = http_json("POST", f"{base}/media", form={
        "image_url": media, "caption": text, "access_token": c["ig_token"],
    })
    creation_id = body.get("id")
    if not creation_id:
        return {"ok": False, "status": status, "error": body.get("error") or body}
    status2, body2 = http_json("POST", f"{base}/media_publish", form={
        "creation_id": creation_id, "access_token": c["ig_token"],
    })
    ok = status2 == 200 and bool(body2.get("id"))
    return {"ok": ok, "status": status2, "id": body2.get("id"), "error": body2.get("error")}


def post_twitter(text: str, c: dict, media: str = "") -> dict:
    if not c["tw_bearer"]:
        return {"ok": False, "error": "Missing Twitter/X bearer token"}
    # NOTE: X API v2 requires OAuth 1.0a user context (or OAuth 2.0 PKCE with the
    # tweet.write scope) to create a post. An app-only Bearer token can only read,
    # so a 403 here usually means "wrong auth type", not "bad token".
    tweet = text[:280]
    status, body = http_json(
        "POST",
        "https://api.twitter.com/2/tweets",
        headers={"Authorization": f"Bearer {c['tw_bearer']}"},
        data={"text": tweet},
    )
    tid = (body.get("data") or {}).get("id")
    err = body.get("errors") or body.get("detail") or body.get("error")
    if not tid and status in (401, 403):
        err = (err or "unauthorized") + " — posting requires user-context OAuth (OAuth 1.0a or OAuth 2.0 with tweet.write); an app-only bearer token cannot create posts"
    return {"ok": status in (200, 201) and bool(tid), "status": status, "id": tid, "error": err}


def post_linkedin(text: str, c: dict, media: str = "") -> dict:
    if not c["li_token"]:
        return {"ok": False, "error": "Missing LinkedIn access token"}
    author = c["li_urn"]
    if author and not author.startswith("urn:"):
        author = f"urn:li:person:{author}"
    if not author:
        return {"ok": False, "error": "Missing LINKEDIN_PERSON_URN (urn:li:person:...)"}
    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text[:3000]},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    status, body = http_json(
        "POST",
        "https://api.linkedin.com/v2/ugcPosts",
        headers={"Authorization": f"Bearer {c['li_token']}", "X-Restli-Protocol-Version": "2.0.0"},
        data=payload,
    )
    oid = body.get("id")
    return {"ok": status in (200, 201) and bool(oid), "status": status, "id": oid, "error": body.get("message") or body.get("error")}


def post_blogger(text: str, c: dict, media: str = "") -> dict:
    if not c["bl_token"] or not c["bl_id"]:
        return {"ok": False, "error": "Missing Blogger blog id or OAuth token"}
    title = text.strip().split("\n", 1)[0][:80] or "Nexus post"
    payload = {"kind": "blogger#post", "title": title, "content": text.replace("\n", "<br>")}
    status, body = http_json(
        "POST",
        f"https://www.googleapis.com/blogger/v3/blogs/{c['bl_id']}/posts",
        headers={"Authorization": f"Bearer {c['bl_token']}"},
        data=payload,
    )
    return {"ok": status in (200, 201) and bool(body.get("id")), "status": status, "id": body.get("id"), "error": body.get("error")}


def post_youtube(text: str, c: dict, media: str = "") -> dict:
    """YouTube has no text-post API — explain that instead of failing obscurely.

    The Data API v3 exposes videos.insert (a resumable video upload) and nothing
    for community/text posts, so there is no honest way to "publish" a caption.
    """
    return {
        "ok": False,
        "error": ("YouTube does not support text posts via API. Upload the video with "
                  "videos.insert (see the python-yt snippet on the Connected Accounts page) "
                  "and use this copy as the description."),
    }


PUBLISHERS = {
    "facebook": post_facebook,
    "instagram": post_instagram,
    "twitter": post_twitter,
    "x": post_twitter,           # alias — the UI and the browser flow call it "x"
    "linkedin": post_linkedin,
    "blogger": post_blogger,
    "youtube": post_youtube,     # always returns a clear explanatory error
}


# ──────────────────────────────────────────────────────────────────────────────
# API-first publishing with retries and an automatic browser fallback
# ──────────────────────────────────────────────────────────────────────────────
#
#   1. Call the official API. Retry transient failures (network error, 429,
#      5xx) with exponential backoff + jitter.
#   2. If the API is unusable — missing credentials, auth/permission errors
#      (4xx) or retries exhausted — hand the post to the browser fallback, which
#      drives a persistent, already-logged-in profile of YOUR account.
#   3. The browser route gets its own bounded retries (not for "logged out",
#      which needs a human).
#   4. One audit line per platform records the route used and every attempt.

RETRYABLE_STATUS = {0, 408, 425, 429, 500, 502, 503, 504}
BROWSER_EXIT = {0: "ok", 2: "blocked", 3: "logged_out", 4: "failed", 5: "engine_missing"}


def env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def classify_api_failure(res: dict) -> str:
    """'retry' for transient errors, 'fallback' for everything else."""
    status = res.get("status")
    if status is None:                     # never reached the network: missing creds / unsupported
        return "fallback"
    return "retry" if status in RETRYABLE_STATUS else "fallback"


def backoff_delay(attempt: int, base: float, cap: float = 30.0) -> float:
    return min(cap, base * (2 ** attempt)) * (0.75 + random.random() * 0.5)


def browser_args_for(platform: str, text: str, media: str, dry_run: bool) -> list | None:
    """Translate a publish job into a social_browser_agent.py invocation, or
    None when the platform has no browser flow."""
    local_media = media if media and Path(media).is_file() else ""
    if platform in ("twitter", "x"):
        args = ["x_post", "--text", text]
        if local_media:
            args += ["--media", local_media]
        if len(text) > 280 and env_flag("NEXUS_X_LONG_POST", False):
            args.append("--long-post")
    elif platform == "instagram":
        if not local_media:
            return None                    # IG cannot post text-only
        args = ["instagram_post", "--caption", text, "--image", local_media]
    elif platform == "facebook":
        args = ["facebook_post", "--message", text]
        if local_media:
            args += ["--image", local_media]
    else:
        return None
    if dry_run:
        if args[0] != "x_post":
            return None                    # only the X flow has a true dry-run
        args.append("--dry-run")
    return ["--allow-actions", *args]


def run_browser(args: list, timeout: int = 600) -> dict:
    """Run the browser agent and parse its one-line JSON result."""
    try:
        proc = subprocess.run([sys.executable, str(SOCIAL_AGENT), *args], capture_output=True,
                              text=True, timeout=timeout, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": 4, "error": f"browser fallback timed out after {timeout}s"}
    payload: dict = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    payload.setdefault("ok", proc.returncode == 0)
    payload["exit"] = proc.returncode
    if not payload["ok"] and not payload.get("error"):
        payload["error"] = (proc.stderr or proc.stdout)[-300:]
    return payload


def publish_with_fallback(platform: str, text: str, c: dict, media: str = "", *,
                          fallback: bool = True, browser_dry_run: bool = False,
                          api_retries: int | None = None, browser_retries: int | None = None,
                          backoff_base: float | None = None,
                          api_call=None, browser_runner=run_browser, sleep=time.sleep) -> dict:
    api_retries = env_int("NEXUS_API_RETRIES", 2) if api_retries is None else api_retries
    browser_retries = env_int("NEXUS_BROWSER_RETRIES", 1) if browser_retries is None else browser_retries
    backoff_base = float(os.environ.get("NEXUS_BACKOFF_BASE", "2")) if backoff_base is None else backoff_base
    fn = api_call or PUBLISHERS.get(platform)
    attempts: list = []

    res: dict = {"ok": False, "error": "Unsupported platform"}
    if fn:
        for i in range(api_retries + 1):
            res = dict(fn(text, c, media) or {})
            attempts.append({"route": "api", "attempt": i + 1, "ok": bool(res.get("ok")),
                             "status": res.get("status"),
                             "error": str(res.get("error"))[:200] if not res.get("ok") else None})
            if res.get("ok"):
                return {**res, "platform": platform, "route": "api", "attempts": attempts}
            if classify_api_failure(res) != "retry" or i == api_retries:
                break
            sleep(backoff_delay(i, backoff_base))

    api_error = res.get("error")
    bargs = browser_args_for(platform, text, media, browser_dry_run) if fallback else None
    if not bargs:
        why = "browser fallback disabled" if not fallback else "no browser flow for this platform/job"
        return {"ok": False, "platform": platform, "route": "api", "status": res.get("status"),
                "error": api_error, "fallback": why, "attempts": attempts}

    bres: dict = {}
    for i in range(browser_retries + 1):
        bres = browser_runner(bargs)
        outcome = BROWSER_EXIT.get(bres.get("exit"), "failed") if not bres.get("ok") else "ok"
        attempts.append({"route": "browser", "attempt": i + 1, "ok": bool(bres.get("ok")),
                         "outcome": outcome, "error": None if bres.get("ok") else str(bres.get("error"))[:200],
                         "screenshot": bres.get("screenshot")})
        if bres.get("ok") or outcome in ("logged_out", "blocked", "engine_missing") or i == browser_retries:
            break
        sleep(backoff_delay(i, backoff_base))
    return {"ok": bool(bres.get("ok")), "platform": platform, "route": "browser",
            "dry_run": bool(bres.get("dry_run")) or None, "api_error": api_error,
            "error": None if bres.get("ok") else bres.get("error"),
            "logged_out": bool(bres.get("logged_out")) or None,
            "screenshot": bres.get("screenshot"), "attempts": attempts}


def load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_queue(items: list) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(QUEUE_FILE.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    os.replace(tmp, QUEUE_FILE)          # atomic: a crash never truncates the queue


def enqueue(job: dict) -> dict:
    items = load_queue()
    job.setdefault("id", f"job-{int(datetime.now().timestamp() * 1000)}")
    job.setdefault("status", "queued")
    job.setdefault("created_at", now_iso())
    items.append(job)
    save_queue(items[-200:])
    return job


def publish_job(job: dict, dry_run: bool = False) -> dict:
    platforms = job.get("platforms") or [job.get("platform")]
    text = job.get("content") or job.get("text") or ""
    media = job.get("media") or ""
    c = creds_from(job)
    results = []
    for platform in platforms:
        if not platform:
            continue
        fn = PUBLISHERS.get(platform)
        if not fn:
            results.append({"platform": platform, "ok": False, "error": "Unsupported platform"})
            continue
        if dry_run:
            results.append({"platform": platform, "ok": True, "dry_run": True})
            continue
        res = publish_with_fallback(
            platform, text, c, media,
            fallback=job.get("fallback", env_flag("NEXUS_BROWSER_FALLBACK", True)),
            browser_dry_run=bool(job.get("browser_dry_run")))
        res = {k: v for k, v in res.items() if v is not None}
        # BUG FIX: the audit line explicitly excluded the `error` field, so
        # logs/publish_audit.jsonl recorded "error" with no reason at all —
        # useless exactly when you need it. Record the reason, never the token.
        detail = json.dumps({k: v for k, v in res.items() if k not in ("error", "attempts")}, default=str)
        if not res.get("ok"):
            reason = res.get("error")
            detail = f"{detail} reason={json.dumps(reason, default=str)[:300]}"
        audit(platform, "ok" if res.get("ok") else "error", detail,
              route=res.get("route"), attempts=res.get("attempts", []))
        results.append(res)
    job["results"] = results
    if dry_run:
        # A validation pass must not be recorded as a live publication.
        job["status"] = "validated" if all(r.get("ok") for r in results) else "failed"
    else:
        job["status"] = "published" if all(r.get("ok") for r in results) else "partial" if any(r.get("ok") for r in results) else "failed"
    job["published_at"] = now_iso()
    return job


def publish_due(dry_run: bool = False) -> dict:
    """Publish every queued/scheduled job whose time has arrived.

    BUG FIXES:
      • A naive timestamp (no timezone) used to raise TypeError when compared
        with an aware `now`, crashing the whole batch. Naive values are now
        interpreted as UTC.
      • An unparseable `schedule` used to be swallowed and the job published
        immediately — the opposite of what a schedule means. Such a job is now
        skipped and marked for attention instead.
    """
    now = datetime.now(timezone.utc)
    items = load_queue()
    ran, skipped = [], []
    for job in items:
        if job.get("status") not in ("queued", "scheduled"):
            continue
        when = job.get("schedule")
        if when:
            due = parse_when(when)
            if due is None:
                job["status"] = "invalid-schedule"
                skipped.append({"id": job.get("id"), "schedule": when})
                continue
            if due > now:
                continue
        ran.append(publish_job(job, dry_run=dry_run))
    save_queue(items)
    return {"ok": True, "processed": len(ran), "jobs": ran, "skipped": skipped}


def parse_when(value) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return due.replace(tzinfo=timezone.utc) if due.tzinfo is None else due.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nexus social publisher")
    parser.add_argument("--platform", choices=list(PUBLISHERS))
    parser.add_argument("--text", default="")
    parser.add_argument("--media", default="")
    parser.add_argument("--from-json", dest="from_json")
    parser.add_argument("--due", action="store_true")
    parser.add_argument("--list", action="store_true", help="Print the publish queue and exit")
    parser.add_argument("--dry-run", action="store_true", help="validate only; no network, no browser")
    parser.add_argument("--no-fallback", action="store_true", help="API only; never use the browser")
    parser.add_argument("--fallback-dry-run", action="store_true",
                        help="real API attempt, but a browser fallback stops before the final Post click")
    args = parser.parse_args()

    if args.list:
        items = load_queue()
        counts: dict = {}
        for j in items:
            counts[j.get("status", "unknown")] = counts.get(j.get("status", "unknown"), 0) + 1
        safe = [{k: v for k, v in j.items() if k != "credentials"} for j in items]
        print(json.dumps({"ok": True, "count": len(items), "counts": counts, "jobs": safe}, indent=2))
        return 0

    if args.due:
        print(json.dumps(publish_due(dry_run=args.dry_run), indent=2))
        return 0

    if args.from_json:
        job = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if args.no_fallback:
            job["fallback"] = False
        if args.fallback_dry_run:
            job["browser_dry_run"] = True
        print(json.dumps(publish_job(job, dry_run=args.dry_run), indent=2))
        return 0

    if not args.platform or not args.text:
        parser.error("Provide --platform and --text, or --from-json / --due")

    job = {"platforms": [args.platform], "content": args.text, "media": args.media}
    if args.no_fallback:
        job["fallback"] = False
    if args.fallback_dry_run:
        job["browser_dry_run"] = True
    print(json.dumps(publish_job(job, dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
