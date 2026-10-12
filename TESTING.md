# Manual end-to-end test (macOS)

This runs the whole path on your Mac: install, log in once, dry-runs, one real X post, a Canva export attached to an X post, and the forced browser fallback.
Allow about 20 minutes. Every command runs from the project folder.

> Prerequisites: Python 3.10+ (`python3 --version`), Google Chrome (or Microsoft Edge) installed, an X account and a Canva account you own, and one Canva design you can open.

## 0. Automated suite (optional, 1 min)
```bash
./scripts/run_tests.sh            # lint + Python + UI + headless smoke
```

## 1. Install and start the relay
```bash
cd /path/to/digital-marketing-agent
./start.sh --check                # first run creates .venv, installs deps + Chromium, validates config
./start.sh                        # starts the relay on http://127.0.0.1:8712 (leave this terminal open)
```
In a **second terminal**:
```bash
cd /path/to/digital-marketing-agent
source .venv/bin/activate
curl -s http://127.0.0.1:8712/health | python3 -m json.tool
```
Expected: `"ok": true`, `"playwright": true`, `"browser_fallback": true`.

## 2. Log in once (persistent profiles, no passwords stored)
```bash
python3 agent/social_browser_agent.py login --platform x
python3 agent/social_browser_agent.py login --platform canva
```
Each command opens a Chrome window on its own profile (`logs/profiles/x`, `logs/profiles/canva`). Log in by hand, including any 2FA or "verify you are human" prompt. The window closes on its own once it sees a signed-in page.
Nexus never sees or stores your password; only the browser profile keeps the session cookies.

## 3. Readiness check (posts nothing)
```bash
./scripts/e2e_check --canva-url "https://www.canva.com/design/<YOUR-DESIGN-ID>/edit"
```
Expected: every line ✔ and `Nothing was posted.` The X dry-run screenshot is in `shots/x_dryrun_*.png`, and the Canva export is in `output/downloads/`.
Add `--headed` to watch it run.

Publisher dry-run (validates the job; no network, no browser):
```bash
python3 agent/publish.py --platform x --text "hello" --dry-run
```

## 4. One real test post on X (delete it afterwards)
```bash
python3 agent/social_browser_agent.py --allow-actions x_post \
  --text "Testing my Nexus setup — please ignore, deleting shortly. $(date +%H:%M)"
```
Expected JSON: `"ok": true, "route": "browser"`. Open your X profile, check the post, then delete it by hand.
Long post (only if your account has Premium/Premium+):
```bash
python3 agent/social_browser_agent.py x_post --long-post --dry-run \
  --text "$(python3 -c 'print("Long-post check. " * 30)')"
```
The dry-run should report `"chars": 510`. Without Premium, the real run stops with a clear "does not appear to have X Premium" error and does not post.

## 5. Canva export attached to an X post
Dry-run first, then the real post:
```bash
python3 agent/social_browser_agent.py canva_to_x --dry-run \
  --design-url "https://www.canva.com/design/<YOUR-DESIGN-ID>/edit" --text "Canva → X test"
python3 agent/social_browser_agent.py --allow-actions canva_to_x \
  --design-url "https://www.canva.com/design/<YOUR-DESIGN-ID>/edit" \
  --text "Canva → X test via Nexus — deleting shortly"
```
Check that the post shows your design as an image, then delete it.

## 6. Forced fallback (API fails → browser posts)
Make the API unusable for this one command by blanking the X token, then publish through the normal publisher:
```bash
TWITTER_BEARER_TOKEN= NEXUS_BROWSER_FALLBACK=1 \
python3 agent/publish.py --platform x --fallback-dry-run --text "Fallback dry-run"
```
Expected: `"route": "browser"`, `"api_error": "Missing Twitter/X bearer token"`, and `"dry_run": true`.
For the real thing (it posts; delete it afterwards):
```bash
TWITTER_BEARER_TOKEN= NEXUS_BROWSER_FALLBACK=1 \
python3 agent/publish.py --platform x --text "Fallback test via Nexus — deleting shortly"
tail -1 logs/publish_audit.jsonl | python3 -m json.tool
```
In the audit line, check `"route": "browser"` and `attempts` = `[api ✘, browser ✔]`.
Through the relay (what the UI and phone use):
```bash
curl -s -X POST http://127.0.0.1:8712/publish -H 'Content-Type: application/json' \
  -d '{"content":"Relay fallback dry-run","platforms":["x"],"browser_dry_run":true}' | python3 -m json.tool
```
(Add `-H "X-Nexus-Token: <token>"` if you set one.)

## 7. Phone as the client (optional)
```bash
./start.sh --lan                  # prints the phone token; requires a 24+ char token
```
For HTTPS (needed for the installable PWA and service worker on iOS/Android), use [mkcert](https://github.com/FiloSottile/mkcert):
```bash
brew install mkcert && mkcert -install
mkdir -p certs && mkcert -cert-file certs/nexus.pem -key-file certs/nexus-key.pem "$(ipconfig getifaddr en0)" localhost
NEXUS_TLS_CERT=certs/nexus.pem NEXUS_TLS_KEY=certs/nexus-key.pem ./start.sh --lan
```
On the phone (same Wi-Fi), open `https://<mac-ip>:8712/app/`. In Settings → Local Relay, paste the token and press *Save & test relay*, then *Share → Add to Home Screen*. The phone also has to trust the mkcert root CA: AirDrop `$(mkcert -CAROOT)/rootCA.pem` to it and enable it under Settings → General → About → Certificate Trust Settings.
Posts you publish from the phone run on the Mac: API first, then the browser fallback.

## 8. Brand Setup (research → profile → 30-day plan)

1. Open the dashboard → **Brand Setup**. Enter your business name, website, Google Business Profile URL
   (a `maps.app.goo.gl` share link works), optional handles (`instagram=@you, x=@you`), location and target market. Click **Save brand**.
   The brand appears in the top-bar switcher.
2. Click **Do all three** (or Research → Build profile → Build 30-day plan). Expect 1–5 minutes.
   - The research line shows how many pages were crawled and whether Google data came from `places_api`
     (when `GOOGLE_PLACES_API_KEY` is set) or `browser`.
   - The profile chip shows `generated_by`. If it says `heuristic (AI unavailable: …)`, your AI key or provider is not working. Fix it in Settings and rebuild.
   - CLI equivalent: `python agent/brand_research.py create --name "…" --website …` then `python agent/brand_research.py run --brand <id>`.
3. Check the profile against reality: the services, USPs and prices must all appear on your site or Google profile. Nothing should be invented.
4. Edit something in the JSON editor (for example add a word to `brand_voice.avoid_words`) → **Save profile**. Edit a calendar cell → **Save plan edits**. Reload the page. Both edits persist.
5. **Export Markdown** and **Export plan CSV**. Open the CSV in Sheets/Excel: there should be about 30 days of rows with no `=` formulas executed.
6. Go to **Create Content** and generate a post. It should use your services and voice, and any avoid-word is replaced with `[removed]`.
   CLI: `python skill/digital-marketer/scripts/generate_content.py --topic "…" --platforms instagram` uses the active brand (use `--no-brand-profile` to compare).
7. Add a second brand and switch between them in the top bar. Generation follows the selected brand.
8. **Delete brand** removes `data/brands/<id>/` and the switcher falls back to the remaining brand.

## 9. Brains, Activity, links (posts nothing)
1. Antigravity: open the app, then `curl -s http://127.0.0.1:8712/antigravity/status`. In the UI, choose *Google Antigravity (live brain)* and click Generate; the text streams in.
2. Sho: set the engine to *Sho · Grok Bot*, click Generate, then run `python3 agent/brain_bridge.py list` and `answer <id> --file reply.txt`. For a post job, run `approve <id> --publish --dry-run`.
3. Dry-run each flow: `social_browser_agent.py x_post --text hi --dry-run`, `ig_post --caption hi --media img.png --dry-run`, `fb_post --text hi --page-url https://www.facebook.com/YourPage --dry-run`.
4. The Activity page lists the dry runs. Brand Setup → Saved links shows the visited pages.

## 10. Windows (when you have a laptop to test on)
Follow docs/tutorials/WINDOWS_PORTABLE.md, then run `.venv\Scripts\python.exe -m unittest discover -s tests` and `start.bat -Check`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Not logged in to x` / exit 3 | rerun step 2 for that platform |
| `human-verification page` | `login --platform <p>` and complete the check by hand |
| `none matched: …` + screenshot | the site changed its layout. Send the screenshot (`shots/`) so the selectors can be updated |
| slow connection timeouts | `NEXUS_TIMEOUT_SCALE=2` |
| use Edge instead of Chrome | `NEXUS_BROWSER_CHANNEL=msedge` |
