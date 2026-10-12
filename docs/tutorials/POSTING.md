# Posting flows (X, Instagram, Facebook), activity log and link memory

**Rule: nothing goes out without your approval.** Run every flow with `--dry-run` first: it fills the composer and stops before the final Post/Share click. Real posts happen only when you run them without `--dry-run`, or when you approve a brain job (`brain_bridge.py approve <id> --publish`).

Log in once per platform (persistent profile, Nexus never stores passwords):
```bash
.venv/bin/python agent/social_browser_agent.py login --platform x        # instagram | facebook
.venv/bin/python agent/social_browser_agent.py check_logins --platforms x,instagram,facebook
```
Windows: use `.venv\Scripts\python.exe` and backslashes.

Attach mode: to use **your own Chrome** instead of Nexus's profile browser, start Chrome with `--remote-debugging-port=9222` and pass `--cdp-url http://127.0.0.1:9222`, or set `NEXUS_CDP_URL`. Nexus brings its tab to the front and closes only that tab.

## X
`x_post --text "…" [--media img.png] [--dry-run]`. The text is submitted with Cmd/Ctrl+Enter, because the typeahead overlay can cover the Post button. Afterwards Nexus opens your profile and records the newest post's permalink, ignoring the pinned post.

## Instagram
`ig_post --caption-file cap.txt --media img.png [--dry-run]`. It handles Create → Post (svg selectors), the crop step (4:5 / original), Next → Next, and caption. It fails closed unless it sees that you are signed in. After Share, it scrapes the new post's permalink from your profile.

## Facebook Page
`fb_post --text-file post.txt --media img.png [--page-url https://www.facebook.com/YourPage] [--dry-run]`. It switches into the Page profile first, focuses the composer and types with the keyboard, then clicks the exact **Post** button. It never answers destructive dialogs. Without `--page-url`, it takes the Page URL from your brand's social links (Brand Setup handles or `social_links.facebook.page` in `data/brands/<id>/brand.json`), then from link memory.

`publish.py --platform instagram|facebook|x … --fallback-dry-run` goes through the same flows when an API fails.

## Activity log
Every agent action (posts above all) is appended to `data/brands/<brand>/activity_log.jsonl`, or to `NEXUS_ACTIVITY_LOG` if set. Entries are never rewritten. Each one records time, brain, platform, status (`ok | failed | blocked | dry_run`), content, media, permalink and screenshot. Where to see it:
- UI: **Activity** page (filter by platform/status, thumbnails, permalinks)
- relay: `GET /activity?platform=x&status=ok`, `GET /activity/media?path=…`
- CLI: `python agent/activity_log.py list --platform x`, `… backfill` (imports `logs/browser_audit.jsonl`. Real permalinks/notes can come from an optional `data/brands/<brand>/activity_backfill.json`)

## Link memory
Flows record each page they visit (profile, compose, Page, permalinks) in `data/brands/<brand>/link_memory.json`, or in `NEXUS_LINK_MEMORY` if set, and open known URLs first next time. Curated links win over remembered ones. They come from the brand's `social_links`, which a client build can set in `config/client.json`. See them under **Brand Setup → Saved links**, `GET /links`, or `social_browser_agent.py links`.

Tests never touch your real logs: `tests/_support.py` points `NEXUS_AUDIT_LOG`, `NEXUS_ACTIVITY_LOG` and `NEXUS_LINK_MEMORY` at temp files.
