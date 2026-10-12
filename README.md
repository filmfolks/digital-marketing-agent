# 🚀 Digital Marketing Agent (Nexus Command Center)

An enterprise-grade, local-first **Autonomous Digital Marketing & Social Media AI Agent**. Runs across **Google Antigravity**, **Local LLMs (Hermes 3, Llama 3, Mistral via LM Studio or Ollama)**, and **Cloud APIs (Gemini, Groq, Claude, DeepSeek, Qwen, OpenAI, xAI Grok, OpenRouter)** — with a real publish queue, approval workflow, browser automation and an MCP bridge to any agent host.

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-3.0.0-7c3aed)
![License](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12-3776ab)
[![Security](https://img.shields.io/badge/security-model-SECURITY.md-red)](SECURITY.md)

---

## 🆕 What's new in v3.0

v3.0 fixed the things that made the app look finished while quietly not working, then built the rest of the campaign lifecycle on top.

**The big three fixes**

1. **Publishing had never worked at all.** The relay had no `/publish` route, so every "Publish Now" click 404'd and reported *"relay offline"* even with the relay running. There is now a real publish queue behind `/publish`, `/queue`, `/publish-due`, `/health` and `/tools`.
2. **Your API key could be sent to the wrong vendor.** `AIClient.getCloudEndpoint()` silently defaulted every unknown provider to the **Gemini** URL, so an Anthropic or xAI key was POSTed to Google. Unknown providers now raise instead of substituting.
3. **The relay was a CSRF hole.** `Access-Control-Allow-Origin: *` with no auth meant any website you had open could drive your local browser or publish to your accounts. The relay now enforces an Origin allow-list, a Host-header (DNS-rebinding) check, and an optional `X-Nexus-Token`.

**Also fixed**

- The entire retry/fail-over engine in `ui/script.js` was dead code — nothing called it. All routing now lives in `ui/ai-client.js` and is actually wired to the UI.
- **"Save Key for Provider" wrote to a key generation never read**, so saved keys were ignored until you also pressed "Save Settings". One credential store now, one write path.
- Switching cloud provider in Settings didn't update the engine until Save, so the key went to the previous vendor.
- The browser skill printed its `[audit]` line to **stdout**, breaking the MCP JSON parser so *every* MCP browser tool reported failure.
- Every browser command launched and closed its own browser, so `navigate → snapshot → act` could never work. There is now a persistent profile and session restore.
- Canva Studio printed *"published ✓"* for every platform with a stored token while sending **nothing** — and Canva OAuth used a hard-coded PKCE challenge with no verifier, so it could never complete.
- The Trends page rendered invented metrics (`1.2M mentions`, `+45%`) as live data.
- `publish_due` crashed on naive timestamps **and** published immediately when a schedule failed to parse.
- Publish failures were logged **with no reason** (the audit line excluded the `error` field).
- The service worker was cache-first for HTML forever and cached cross-origin API responses.
- Markdown lists rendered one `<ul>` per item and asterisks were mangled; copy broke on `file://` (which this README recommends).

**Added**

- Publish queue + UI queue viewer + pre-flight checker + dry-run validation
- 7-provider cloud registry with **native Anthropic Messages API** support, retries with backoff + jitter, ordered fail-over, bounded-concurrency parallel generation, abort, and usage/latency accounting
- **Brand guardrails** (banned words, hashtag cap, emoji policy, required disclaimer) with an auditable adjustment report
- **UTM campaign link builder**
- **Performance analytics**: CSV/TSV import computing engagement rate, CTR, CPC, CPM, CPA and ROAS per channel, plus deterministic insights
- **Content repurposing** across platforms
- **Approval workflow** (`draft → in_review → approved → scheduled → published`) with history and a publish guard
- **Command palette** (⌘K / Ctrl+K / `/`)
- **Workspace export/import** backup (credentials optional) and clear-all
- Trend **Opportunity Scan** that ranks angles from pasted data instead of faking metrics
- Persistent browser profile plus `status` / `close` subcommands
- **103 Python tests + 36 front-end tests + 43 in-browser assertions**, `scripts/run_tests.sh`, and GitHub Actions CI

Full detail: [CHANGELOG.md](CHANGELOG.md) · threat model: [SECURITY.md](SECURITY.md)

---

## 🌟 Supported AI Harnesses

| Engine | Best For | Typical Models | Requirements |
|---|---|---|---|
| **Google Antigravity** | Autonomous multi-step workflows, cron scheduling | Gemini 2.0 / 1.5 Pro | Antigravity IDE / CLI |
| **Hermes 3 (Local)** | 100% private, zero-cost copywriting | `Hermes-3-Llama-3.1-8B` | 8GB+ RAM (LM Studio or Ollama) |
| **LM Studio (Local)** | Visual model management, local OpenAI endpoint | `Mistral-7B`, `Llama-3-8B`, `Phi-3` | Local Server on `localhost:1234` |
| **Ollama (Local)** | Command-line local model execution | `hermes3:8b`, `llama3.1:8b` | Ollama on `localhost:11434` |
| **Cloud APIs** | Ultra-fast inference, large context | Gemini, Groq, Claude, DeepSeek, Qwen, OpenAI, Grok, OpenRouter | API key in UI or `.env` |
| **Built-in offline engine** | Never blocked — always produces a draft | deterministic templates | none |

---

## ⚡ Quick Start

### 1. Running the Nexus Web UI

Open directly in your browser or serve locally:

```bash
# Option A: Open directly in your browser
open ui/index.html

# Option B: Run via local HTTP server (recommended)
cd ui
python3 -m http.server 8080
# Navigate to: http://localhost:8080
```

> **Prefer Option B.** `navigator.clipboard` does not exist in a `file://` context
> and `file://` origins are isolated by some browsers, which can make saved
> credentials look like they vanished. Over `http://localhost` everything behaves.

Inside the UI:

1. Select your AI engine from **Settings** (☁️ Cloud APIs / 💻 Local Models / 🚀 Google Antigravity).
2. Click **Test Provider** to verify it before generating.
3. Use ⌘K / Ctrl+K for the command palette — every action is keyboard-reachable.

### 2. Running the relay (needed for publishing & browser automation)

The browser can't execute Python, so a small stdlib HTTP bridge does it. Full
endpoint list and the auth model are in [Running the relay](#-running-the-relay).

```bash
# Terminal 1 — relay with a token (recommended; prints a new one each run)
python3 agent/nexus_relay.py --generate-token          # copy the token
NEXUS_RELAY_TOKEN="<paste-it>" python3 agent/nexus_relay.py

# Terminal 2 — the UI
cd ui && python3 -m http.server 8080
```

Paste the token into **Settings → Relay token**. The header chip shows
`🟢 Relay online · v3.0.0 · queue N` when it's reachable.

---

### 3. Using with Google Antigravity (Agentic Mode)

Install the skill into your Antigravity environment:

```bash
# macOS / Linux
cp -r skill/digital-marketer ~/.gemini/config/skills/

# Windows (PowerShell)
Copy-Item -Recurse .\skill\digital-marketer $env:USERPROFILE\.gemini\config\skills\
```

In Antigravity chat, trigger your agent:

```
Tell me about your digital marketing skills.
Activate digital-marketer skill and generate today's content based on campaign.json.
```

#### Scheduled Autonomous Runs via Antigravity

```
# Daily morning content run (9 AM)
/schedule CronExpression="0 9 * * *" Prompt="Activate digital-marketer skill. Check campaign.json and generate today's social media content."

# Weekly content calendar planning (Monday 8 AM)
/schedule CronExpression="0 8 * * 1" Prompt="Activate digital-marketer skill. Generate a 7-day multi-platform content calendar."
```

You can also register the MCP bridge so Antigravity can call Nexus tools directly
— see `mcp-config-examples.json` and the [MCP section](#-mcp-bridge).

---

### 4. Using Local Models (Hermes 3, LM Studio & Ollama)

#### Option A: LM Studio (Recommended for 8GB RAM Mac / PC)

1. Download [LM Studio](https://lmstudio.ai).
2. Download a recommended 8GB-friendly model (Q4_K_M quantization):
   - **`NousResearch/Hermes-3-Llama-3.1-8B-GGUF`** — best instruction following
   - **`mistral-7b-instruct-v0.3.Q4_K_M.gguf`** — fast and reliable
   - **`Phi-3-mini-4k-instruct.Q4_K_M.gguf`** — ultra-lightweight (~2.2 GB)
3. In LM Studio: **Local Server** → load model → **Start Server** (`http://localhost:1234/v1`).
4. Generate via the Python CLI:

   ```bash
   python3 skill/digital-marketer/scripts/generate_content.py \
     --provider lmstudio \
     --topic "5 AI marketing automations for solopreneurs" \
     --platforms instagram linkedin
   ```

#### Option B: Ollama

1. Pull a model:
   ```bash
   ollama pull hermes3:8b
   ```
2. Generate:
   ```bash
   python3 skill/digital-marketer/scripts/generate_content.py \
     --provider ollama \
     --model hermes3:8b \
     --topic "Product launch teaser"
   ```

Both accept either a base URL or a full endpoint path: `--url http://localhost:1234/v1`
and `--url http://localhost:1234/v1/chat/completions` behave identically.

---

### 5. Automated Daily Cron Runs (macOS / Linux)

```bash
# Make the runners executable (once)
chmod +x skill/digital-marketer/scripts/daily_run.sh
chmod +x skill/digital-marketer/scripts/lm_studio_run.sh
chmod +x scripts/run_tests.sh

# Open crontab
crontab -e

# Daily content run at 9:00 AM
0 9 * * * /path/to/digital-marketing-agent/skill/digital-marketer/scripts/daily_run.sh

# Every 15 minutes: fire any scheduled post whose time has arrived
*/15 * * * * cd /path/to/digital-marketing-agent && python3 agent/publish.py --due >> logs/publish_cron.log 2>&1
```

`daily_run.sh` reads `campaign.json` (`brand_name`, `daily_topic`, `tone`,
`platforms`, `active`) and honours `"active": false` as a pause switch. Every
generated package is timestamped and written to `output/`.

---

## 📁 Project Architecture

```
digital-marketing-agent/
├── README.md                       ← you are here
├── SECURITY.md                     ← threat model, what is NOT protected, hardening checklist
├── CHANGELOG.md                    ← Keep a Changelog history (v3.0.0)
├── TUTORIAL.md                     ← long-form walkthrough (mirrors the in-app tutorial)
├── LICENSE
├── .env.example                    ← Environment & API key templates
├── .gitignore                      ← Protects credentials, profiles and local logs
├── requirements.txt                ← OPTIONAL Python SDKs (the agent layer is stdlib-only)
├── campaign.json                   ← Daily campaign goals, audience, tone, platforms
├── mcp-config-examples.json        ← Register the MCP bridge in Claude Desktop / Antigravity / LM Studio
├── .github/
│   └── workflows/
│       └── ci.yml                  ← Test matrix (Ubuntu + macOS, py3.10/3.12),
│                                        relay security contract job, headless browser job
├── agent/                          ← machine-side skills (stdlib only)
│   ├── nexus_relay.py              ← Local HTTP bridge: 127.0.0.1:8712, Origin/token gated
│   ├── publish.py                  ← Real social publisher (FB, IG, X, LinkedIn, Blogger) + queue
│   ├── browser_automation.py       ← Playwright / CDP browser skill, permission-gated + deny-list
│   ├── nexus_mcp.py                ← MCP stdio server — 13 tools, path-confined resources
│   └── telegram_bot.py             ← Phone control with tap-to-approve + chat allow-list
├── skill/
│   └── digital-marketer/
│       ├── SKILL.md                ← Core Antigravity agent instructions
│       ├── references/
│       │   ├── copywriting_swipe_file.md   ← 50+ hooks, formulas & CTAs
│       │   ├── platform_best_practices.md  ← Algorithmic growth rules
│       │   └── hashtag_categories.md       ← Niche hashtag banks by industry
│       ├── examples/
│       │   └── sample_campaign.md          ← Complete 5-day product launch
│       └── scripts/
│           ├── generate_content.py         ← Universal multi-provider content generator
│           ├── daily_run.sh                ← campaign.json cron runner
│           └── lm_studio_run.sh            ← Local-model cron runner (auto-detects 1234/11434)
├── ui/
│   ├── index.html                  ← Nexus Marketing Command Center (20 pages)
│   ├── ai-client.js                ← THE engine: provider registry, retries, fail-over, guardrails, markdown
│   ├── script.js                   ← App logic: routing, hubs, calendar, queue, UTM, analytics, palette
│   ├── style.css                   ← Glassmorphism interface styles
│   ├── sw.js                       ← Service worker (network-first HTML, never caches credentials)
│   ├── manifest.json               ← PWA manifest
│   ├── favicon.svg
│   └── credentials.example.js      ← Safe local credential placeholders (copy → credentials.js)
├── tests/
│   ├── _support.py                 ← Shared module loader
│   ├── test_relay.py               ← Origin/Host rules, queue credential stripping, command map
│   ├── test_publish.py             ← Publisher, dry-run, schedule parsing, session round-trip
│   ├── test_mcp.py                 ← 13 tools, path traversal, permission gate, JSON-RPC loop
│   ├── test_relay_http.py          ← live HTTP contract: auth, origin, Host, 403/400/404
│   ├── test_cli_gates.py           ← exit code 2 contract across CLI → relay → MCP
│   ├── test_generate_content.py    ← Provider table, URL normalisation, error semantics
│   └── test_ui.mjs                 ← Markdown renderer, provider registry, guardrails, credential store, concurrent runs (36 cases)
├── SECURITY.md                     ← Threat model + hardening checklist (also listed above)
├── scripts/
│   ├── run_tests.sh                ← One-command suite: --lint / --python / --ui / --smoke / --fast
│   ├── smoke_ui.mjs                ← Headless Chrome runner (43 assertions + screenshots)
│   └── smoke_harness.html          ← Same-origin harness page the runner serves from memory
├── output/                         ← Timestamped generated markdown (git-ignored)
├── logs/                           ← Audit trails, browser profile, publish queue (git-ignored)
└── shots/                          ← Screenshots from the browser skill (PNGs git-ignored)
```

### UI pages

Dashboard · AI Agent · Content Studio · Content Calendar · Instagram · Facebook ·
YouTube · LinkedIn · Blog & SEO · Connected Accounts · Canva Studio · Trending Now ·
Hashtag Tool · UTM Builder · Analytics · Browser Agent · MCP & Integrations ·
Agent Logs · Tutorial · Settings

---

## 🔌 Running the relay

```bash
python3 agent/nexus_relay.py                                  # 127.0.0.1:8712, no token
python3 agent/nexus_relay.py --port 9000 --token mysecret      # custom port + token
NEXUS_RELAY_PORT=9000 NEXUS_RELAY_TOKEN=mysecret python3 agent/nexus_relay.py
python3 agent/nexus_relay.py --generate-token                  # print a fresh random token
```

It is **stdlib-only** — no `pip install` required to run the relay itself.

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | **none** (by design) | Liveness, version, whether auth is on, queue depth |
| `GET` | `/queue` | token | Publish queue + per-status counts — **credentials stripped** |
| `GET` | `/tools` | token | Live list of accepted command names |
| `POST` | `/browser` | token | Run a `browser_automation.py` command |
| `POST` | `/publish` | token | Publish now, or enqueue when `schedule` is set |
| `POST` | `/publish-due` | token | Fire every queued/scheduled job whose time has arrived |
| `POST` | `/mcp` | token | Generic MCP tool call through `nexus_mcp.py` |
| `POST` | `/shutdown` | token | Graceful stop |

`/tools` accepts these `/browser` commands: `navigate`, `snapshot`, `extract`,
`screenshot`, `devtools-console`, `devtools-perf`, `devtools-network`,
`act-click`, `act-type`, `status`, `close`.

### Auth model

- **Binds `127.0.0.1` only** — never `0.0.0.0`, so it is not reachable from your network.
- **Origin allow-list** — only `null` (what `file://` pages send), `localhost`,
  `127.0.0.1` and `[::1]`, any port. The response echoes that exact origin and adds
  `Vary: Origin`; it never sends `*`. Anything else is **403**.
- **Host-header check** — the `Host` must be a loopback name, which blocks
  **DNS-rebinding** attacks. Failure is **403**.
- **`X-Nexus-Token`** — when `NEXUS_RELAY_TOKEN` / `--token` is set, every
  non-`GET` request *and* `/queue`/`/tools` must carry it, compared in constant
  time. Mismatch is **401**.
- **`act-*` commands** additionally require `"allowActions": true` in the body,
  which the UI sets only while **Browser Actions Permission** is ticked. Refusal
  is **403** with `blocked: true`.
- Bodies over 2 MB are rejected with **413**.

### Copy-pasteable examples

```bash
# 1. Health — deliberately unauthenticated, safe to poll
curl -s http://127.0.0.1:8712/health
# → {"ok":true,"service":"nexus-relay","version":"3.0.0","auth":false,"queue":0,...}

# 2. Discover the route list (needs the token when one is configured)
curl -s -H 'X-Nexus-Token: mysecret' http://127.0.0.1:8712/tools

# 3. Read the queue — platform tokens are stripped from the response
curl -s -H 'X-Nexus-Token: mysecret' http://127.0.0.1:8712/queue

# 4. Read-only browser command: open a page
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{"cmd":"navigate","url":"https://example.com"}' \
  http://127.0.0.1:8712/browser

# 5. Text snapshot of the remembered page (works because of session restore)
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{"cmd":"snapshot"}' http://127.0.0.1:8712/browser

# 6. State-changing action — 403 unless allowActions is true
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{"cmd":"act-click","selector":"button.publish"}' \
  http://127.0.0.1:8712/browser
# → 403   (add "allowActions": true to actually click)

# 7. Validate a post with ZERO network calls (dry run)
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{"content":"Launch teaser","platforms":["facebook"],"dry_run":true}' \
  http://127.0.0.1:8712/publish
# → {"status":"validated","results":[{"platform":"facebook","ok":true,"dry_run":true}],...}

# 8. Schedule a real post (queued, not sent)
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{"content":"Tomorrow at 9","platforms":["linkedin"],"schedule":"2030-01-01T09:00:00Z"}' \
  http://127.0.0.1:8712/publish
# → {"status":"scheduled","job":{...},"results":[{"platform":"linkedin","scheduled":true}]}

# 9. Fire everything that is due
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{}' http://127.0.0.1:8712/publish-due

# 10. Call any MCP tool through the relay
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{"tool":"plan_campaign","arguments":{"goal":"launch week","days":5,"budget_usd":500}}' \
  http://127.0.0.1:8712/mcp

# 11. Stop the relay
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Nexus-Token: mysecret' \
  -d '{}' http://127.0.0.1:8712/shutdown
```

> The relay refuses anything that is not a local origin or a loopback `Host`, so
> these `curl` calls work (no `Origin` header) while a random website's `fetch`
> does not. See [SECURITY.md](SECURITY.md) for why that matters.

---

## 🔁 API first, browser fallback

`agent/publish.py` (and so the relay's `/publish`, the UI, and the phone) always tries the official API first:

1. **API**: network errors, 429, and 5xx are retried with exponential backoff and jitter (`NEXUS_API_RETRIES`, `NEXUS_BACKOFF_BASE`).
2. **Browser fallback**: missing keys, 4xx auth errors, or retries running out hand the post to `agent/social_browser_agent.py`. It drives your own logged-in, persistent per-platform profile (`logs/profiles/<platform>`). It retries a bounded number of times (`NEXUS_BROWSER_RETRIES`), never when you are logged out.
3. **Audit**: each line in `logs/publish_audit.jsonl` records `route` (`api`/`browser`) and every attempt.

Browser flows: `x_post` (x.com composer, media, `--long-post` for Premium/Premium+), `canva_export` (Share ▸ Download), `canva_to_x`, `instagram_post`, `facebook_post`. Every flow supports `--dry-run`, which does everything except the final Post click.
Platform behaviour:
- Selectors are role/text based with fallbacks, so a small layout change doesn't break a flow.
- Pacing is human-like.
- Any failure leaves a screenshot in `shots/`.
- A logged-out session or a bot-check page stops the run with the exact `login` command to run.

Browser environment:
- The installed Chrome or Edge is detected automatically (`NEXUS_BROWSER_CHANNEL` to override).
- Its real user agent is used, and the OS timezone and locale.
- Stealth uses playwright-stealth 2.x, falls back to 1.x, and logs a warning if neither is available.

```bash
python3 agent/social_browser_agent.py login --platform x        # once, by hand
./scripts/e2e_check --canva-url https://www.canva.com/design/<id>/edit   # verifies, never posts
python3 agent/publish.py --platform x --text "hi" --no-fallback          # API only
python3 agent/publish.py --platform x --text "hi" --fallback-dry-run     # fallback stops before Post
```

## 📱 Phone as a client

Browser automation runs on a desktop (macOS, Windows, Linux). On Android and iOS the **PWA is the client**: it sends jobs to the relay on your desktop (or a hosted relay), and the desktop runs the API call and, if needed, the browser fallback.

```bash
./start.sh --lan              # macOS/Linux  (Windows: start.bat -Lan  or  .\start.ps1 -Lan)
```
- LAN mode **refuses to start without a token of 24+ characters**. `start.sh --lan` generates one and saves it to `.env`.
- The relay serves the PWA itself at `http(s)://<desktop-ip>:8712/app/`. Open that on the phone, paste the token in Settings → Local Relay, and *Add to Home Screen*.
- For HTTPS, pass `--tls-cert/--tls-key` (or `NEXUS_TLS_CERT/NEXUS_TLS_KEY`, e.g. from mkcert), or put the relay behind an HTTPS reverse proxy. For a hosted UI on another origin, list it in `NEXUS_ALLOWED_ORIGINS`.
- `capacitor.config.json` is included if you want to wrap `ui/` as a native shell (`npx cap add ios|android`).

Full step-by-step manual test: **[TESTING.md](TESTING.md)**.

## 📤 Publish pipeline

Publishing is a four-stage pipeline, deliberately not a single button.

```
write / generate copy
        │
        ▼
①  Pre-flight check      ← client-side: char limits, Instagram media rule, LinkedIn link rule
        │
        ▼
②  Validate (dry run)    ← plugin-free: credentials + payload, ZERO network calls
        │
        ▼
③  Publish               ← POST /publish → logs/publish_job.json → agent/publish.py → platform API
        │
        ▼
④  Queue                 ← logs/publish_queue.json (credentials stored locally, stripped over HTTP)
        │
        ▼
    --due cron           ← python3 agent/publish.py --due   (fires jobs whose time has arrived)
```

**① Pre-flight check** — click **Pre-flight check** on *Connected Accounts*. It
reports, per platform, whether the post will be rejected: the platform's character
limit, Instagram's mandatory public image URL, and LinkedIn suppressing reach when
an external link sits in the post body. Markdown is stripped first, so the count is
what the API will actually receive.

**② Validate (dry run)** — click **Validate**. This runs the *real* publisher with
`--dry-run`: credentials are resolved and the payload is shaped exactly as it would
be for a live call, but **no network request is made**. The job is recorded as
`validated`, never as `published`.

**③ Publish** — click **Publish Now**. The UI builds a job containing only the
credentials the selected platforms need and POSTs it to `/publish`. The relay writes
`logs/publish_job.json`, invokes `agent/publish.py --from-json`, and returns real
per-platform results — including per-platform failures, so one bad token doesn't
hide the rest.

Supported live platforms: `facebook`, `instagram`, `twitter`/X, `linkedin`,
`blogger`.

**④ Queue** — give the post a schedule and it is written to
`logs/publish_queue.json` with the platform token it needs, because the post must be
able to fire later without your browser open. The **queue viewer** on *Connected
Accounts* reads real relay state with per-status counts. `GET /queue`,
`publish.py --list` and the MCP `read_publish_queue` tool all **strip credentials**
before replying.

**`--due`** — the cron entry from the Quick Start fires due jobs:

```bash
python3 agent/publish.py --due              # publish everything due
python3 agent/publish.py --due --dry-run    # rehearse it
python3 agent/publish.py --list             # inspect the queue (no credentials)
```

A job whose `schedule` cannot be parsed is **skipped** and marked
`invalid-schedule` — it is never published early. Timestamps without a timezone are
treated as UTC rather than crashing the batch.

### ⛔ Twitter/X posting note

Creating a post requires **user-context** OAuth (OAuth 1.0a, or OAuth 2.0 with the
`tweet.write` scope). An app-only **Bearer token can only read** — a `403` from X
usually means "wrong auth type", not "bad token". The publisher says so explicitly
in its error and in `logs/publish_audit.jsonl`.

---

## 🧠 Providers

The cloud registry lives in `ui/ai-client.js` (`NEXUS_PROVIDERS`) and mirrors
`generate_content.py`'s `PROVIDERS` table. Every entry declares its own host,
default model and dialect.

### Cloud

| Provider id | Label | Default model | Endpoint | Dialect | Env var |
|---|---|---|---|---|---|
| `gemini` | Google Gemini | `gemini-2.5-flash` | `generativelanguage.googleapis.com/v1beta/openai` | OpenAI-compatible | `GEMINI_API_KEY` |
| `groq` | Groq | `llama-3.3-70b-versatile` | `api.groq.com/openai/v1` | OpenAI-compatible | `GROQ_API_KEY` |
| `anthropic` | **Anthropic Claude** | `claude-sonnet-4-20250514` | `api.anthropic.com/v1` | **native Messages API** | `ANTHROPIC_API_KEY` |
| `deepseek` | DeepSeek | `deepseek-chat` | `api.deepseek.com/v1` | OpenAI-compatible | `DEEPSEEK_API_KEY` |
| `qwen` | Qwen (DashScope) | `qwen-plus` | `dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI-compatible | `QWEN_API_KEY` |
| `openai` | OpenAI | `gpt-4o-mini` | `api.openai.com/v1` | OpenAI-compatible | `OPENAI_API_KEY` |
| `xai` | xAI Grok | `grok-4` | `api.x.ai/v1` | OpenAI-compatible | `XAI_API_KEY` |
| `openrouter` | OpenRouter | `nousresearch/hermes-4-405b` | `openrouter.ai/api/v1` | OpenAI-compatible (+ attribution headers) | `OPENROUTER_API_KEY` |

**Anthropic is a native dialect, not an OpenAI path.** It uses `POST /v1/messages`,
an `x-api-key` header, `anthropic-version: 2023-06-01`, and
`anthropic-dangerous-direct-browser-access: true` for direct browser calls. Its
connection test sends a real (tiny) request, because `/models` is not part of that
API and would report a false failure. `claude` is kept as an alias for `anthropic`
so older saved configs still work.

### Local

| Provider id | Label | Default model | Endpoint | Key |
|---|---|---|---|---|
| `lmstudio` | LM Studio (local) | `hermes-3-llama-3.1-8b` | `http://localhost:1234/v1` | none |
| `ollama` | Ollama (local) | `hermes3:8b` | `http://localhost:11434/v1` | none |

Both speak the OpenAI chat-completions protocol. The local engine chains the
configured server first and the other local server as fail-over.

### Built-in + agentic

| Engine | Behaviour |
|---|---|
| `builtin` | **Deterministic offline copywriting engine.** Never calls the network; used automatically as the last resort so a batch is never left empty. |
| `antigravity` | **Prompt hand-off, no API call.** Produces a formatted agent directive you paste into Google Antigravity. |

### `--provider auto` and unknown providers

`--provider auto` (the default for the CLI) probes in this order:

1. `http://localhost:1234/v1/models` → **LM Studio**
2. `http://localhost:11434/api/tags` → **Ollama**
3. the first cloud key present in the environment, in the order
   `gemini → groq → deepseek → openai → claude → xai → openrouter → qwen`
4. fall back to **LM Studio**

So `auto` on a machine that is running both LM Studio *and* has `OPENAI_API_KEY`
set will pick **LM Studio** — it prefers a local server over a cloud key. Pass
`--provider <id>` explicitly (or set `NEXUS_PROVIDER`) whenever the choice matters;
the resolved provider is always reported in the output, and `generate_content.py
--test` prints what it found.

**Unknown provider ids now raise instead of silently switching vendor.** The old
`getCloudEndpoint()` returned the Gemini URL for anything it did not recognise,
which POSTed your Anthropic/xAI/OpenRouter key to Google. `NexusAI.getProvider()`
returns `null` for an unknown id (asserted by the test suite) and the CLI
`--provider` choices are checked against the provider table.

### Reliability behaviour

- **Retries** — up to `retries` (default 2) with exponential backoff and jitter,
  capped at 8 s. `408`/`409`/`429` and 5xx are retried; other 4xx are treated as
  fatal and fail over immediately.
- **Fail-over** — the selected provider is tried first, then the ordered cloud
  chain, de-duplicated. Every attempt is written to **Agent Logs** with the reason.
- **Bounded concurrency** — generation runs 3 platforms at a time by default
  (configurable 1–6), because a local server cannot serve six parallel requests.
  One failing platform never sinks the batch.
- **Abort** — the Cancel button aborts in-flight requests, including during a
  backoff sleep.
- **Guardrails** — banned words, hashtag cap, emoji policy and required disclaimer
  are applied to model output *and* reported as an adjustment list, so a compliance
  pass is auditable rather than invisible.
- **Last resort** — if every provider fails, the built-in engine still returns a
  draft, and the UI tells you how many pieces were degraded instead of pretending
  the model answered.

---

## 🧪 Testing

One command runs everything:

```bash
./scripts/run_tests.sh              # everything: lint + 103 Python + 36 front-end + 43 browser assertions
./scripts/run_tests.sh --fast       # skip the headless browser test (~2s instead of ~40s)
./scripts/run_tests.sh --python     # Python suites only
./scripts/run_tests.sh --ui         # front-end suite only (needs node)
./scripts/run_tests.sh --smoke      # headless browser test only (needs Chrome)
./scripts/run_tests.sh --lint       # syntax + integrity checks only
```

Under the hood, if you prefer the raw commands:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
node tests/test_ui.mjs
node scripts/smoke_ui.mjs
```

### The browser smoke test

`scripts/smoke_ui.mjs` loads the real `ui/index.html` in headless Chrome and
asserts on the live DOM — because a syntax check cannot catch a temporal-dead-zone
error, a missing event listener or a broken selector. It writes screenshots to
`shots/smoke-*.png`.

It works by serving `scripts/smoke_harness.html` from the same origin as the app,
which boots the app in an iframe, drives it, and writes a JSON report into its own
DOM; Chrome's `--dump-dom` then prints that report. Load-time errors are read back
from the app's own Agent Log, so a clean report means the app really did boot.

It **skips with exit 0** when Chrome is not installed, so it is safe on any machine.
(The harness page is served from memory, so nothing is written into `ui/`.)

### What it checks

**Lint / integrity** (no dependencies needed)

- every Python source compiles
- `ui/script.js`, `ui/ai-client.js` and `ui/sw.js` parse (`node --check`)
- **every `getElementById(...)` target exists in `index.html`** — catches whole
  features that would otherwise die silently at runtime
- **every inline `onclick`/`onchange` handler resolves to a real function**

**Python suite — 103 tests**

| File | Covers |
|---|---|
| `test_relay.py` (16) | Origin allow-list accept/reject, Host-header DNS-rebinding block, queue credential stripping, largest-JSON extraction from noisy stdout, the command map the UI dispatches |
| `test_publish.py` (18) | Every publisher's missing-credential error, Instagram media requirement, dry run makes no network call and is never recorded as published, naive vs aware timestamp handling, unparseable schedules are skipped, browser session round-trip |
| `test_mcp.py` (20) | All 13 tools advertised, `browser_act` permission refusal (absent *and* explicitly false), `resources/read` path-traversal refusal, JSON-RPC loop survives bad JSON, unknown tool/method errors |
| `test_cli_gates.py` (15) | The `exit code 2` policy contract end-to-end across CLI → relay → MCP; the gate runs *before* the engine is constructed; `[audit]` never pollutes stdout |
| `test_generate_content.py` (11) | Provider table completeness, bare base URL → `/chat/completions`, Anthropic → `/messages`, provider failures raise instead of returning error text, timestamped filenames |

**Front-end suite — 36 tests** (`node tests/test_ui.mjs`, no browser needed)

- the markdown renderer: escaping, one `<ul>` for a multi-item list, `<ol start>`,
  list/paragraph separation, bullets vs emphasis, fenced code, blockquotes, and
  `javascript:` URLs staying inert
- the provider registry: every Settings dropdown entry is registered, each provider
  points at its own host, unknown ids resolve to `null`, the `claude` alias, and
  chain ordering/de-duplication
- brand guardrails: banned-term replacement, hashtag capping, emoji stripping only
  when forbidden, disclaimer appended exactly once, no-op when disabled
- token budgets for long-form vs short-form platforms

**CI** — `.github/workflows/ci.yml` runs the above on Ubuntu and macOS across
Python 3.10 and 3.12, plus a **relay smoke job** that boots the relay and asserts
its security contract: `/health` is unauthenticated, `/queue` is 401 without the
token and 200 with it, a remote Origin preflight is 403 while `localhost` is 204, a
browser action without permission is 403, and a publish dry run returns
`"validated"`.

> The agent layer is **stdlib-only by design**, so CI installs nothing.

---

## 🔒 Security & Privacy Notice

- API keys and social tokens are stored **strictly in your local browser
  (`localStorage`)** and are sent only to the vendor endpoint they belong to.
- No credentials are ever transmitted to any third-party tracking server.
- When running LM Studio or Ollama, **100% of your data stays on your machine**.
- The relay binds **`127.0.0.1` only**, enforces an Origin allow-list and a
  Host-header check, and supports a shared `X-Nexus-Token`.
- The browser skill requires explicit permission for any state-changing action and
  refuses destructive targets even then.

This is a summary, not the whole story. **[Read SECURITY.md](SECURITY.md)** for the
threat model and — importantly — the explicit list of what is **not** protected
(`localStorage` is readable by a malicious extension; the relay token is optional;
`logs/browser_profile/` holds live session cookies and must be treated as a secret;
`logs/publish_queue.json` holds platform tokens on disk so scheduled posts can
fire).

---

## 🔌 MCP bridge

`agent/nexus_mcp.py` exposes the whole skill set to any MCP client over stdio — no
dependencies. It registers **13 tools**: `generate_content`, `plan_campaign`,
`hashtag_research`, `browser_navigate`, `browser_snapshot`, `browser_screenshot`,
`browser_act`, `devtools_inspect`, `render_video`, `telegram_notify`,
`read_agent_logs`, `publish_post`, `read_publish_queue`.

```bash
python3 agent/nexus_mcp.py --self-test      # verify the server and the permission gate
```

Register it with the snippets in `mcp-config-examples.json` (Claude Desktop,
Google Antigravity, LM Studio/Hermes, Cursor, or an SSE gateway for Grok).

Two properties worth knowing:

- `resources/read` is **confined to the project root** — `../../.ssh/id_rsa` is
  refused.
- `browser_act` requires **`user_permission_granted: true`** and makes no browser
  call without it.

---

## 📲 Telegram bot

```bash
export TELEGRAM_BOT_TOKEN="<from @BotFather>"
export TELEGRAM_CHAT_ID="<your chat id>"
python3 agent/telegram_bot.py
```

`/status` · `/generate <topic>` · `/plan <goal> [days] [budget]` · `/hashtags <niche>`
· `/browse <url>` · `/shot <url>` · `/act click <selector>` · `/post <platform> <text>`
· `/queue` · `/approve <id>` · `/reject <id>` · `/logs [n]` · `/help`

State-changing commands (`/act`, `/post`) never execute on receipt: the bot sends an
inline **✅ Approve / ❌ Reject** keyboard, execution happens only after the same chat
approves, and the approval **expires after 5 minutes**. Only `TELEGRAM_CHAT_ID` plus
any ids in `NEXUS_TG_ALLOWLIST` may issue commands. Everything is written to
`logs/telegram_audit.jsonl`.

---

## 🛠️ Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **Relay returns 401** | Token mismatch | `X-Nexus-Token` doesn't match `NEXUS_RELAY_TOKEN`. Copy the token printed at relay start into **Settings → Relay token** (or clear both to run untokened). The UI chip shows *"Relay online but the token is wrong"*. |
| **Relay returns 403** | Policy block **or** origin refused | Either the request came from a non-allow-listed origin / a foreign `Host` (serve the UI from `http://localhost`, not a LAN IP or a custom hostname), or it's a deliberate block: a browser `act-*` without `"allowActions": true` / without **Browser Actions Permission**, or a deny-listed target. The response body carries the reason and `blocked: true`. |
| **"Copy blocked" / nothing copies** | Insecure context | `navigator.clipboard` needs a secure context. Serve over HTTP instead of `file://`: `cd ui && python3 -m http.server 8080`. The app falls back to `execCommand`, but some browsers still refuse on `file://` — the toast then says to select the text manually. |
| **`Executable doesn't exist`** | Playwright installed, Chromium not downloaded | `pip install playwright && playwright install chromium`. Or use the CDP path: `pip install websocket-client`, launch Chrome with `--remote-debugging-port=9222`, then pass `--force-cdp`. `python3 agent/browser_automation.py status` reports which engine is actually usable. |
| **`--provider auto` picked the wrong thing** | `auto` prefers a local server over a cloud key | It probes LM Studio (1234) → Ollama (11434) → cloud env keys in `gemini, groq, deepseek, openai, claude, xai, openrouter, qwen` order → LM Studio. Pass `--provider <id>` or set `NEXUS_PROVIDER`. Run with `--test` to see what it detected. |
| **Saved API key is ignored** | Stale config from an older version | In v3 a key saved with **Save Key for Provider** takes effect immediately. If you're on an older build, use the current one — keys are now read from `nexus_credentials`, the same store the button writes. Press **Test Provider** to confirm. |
| **LM Studio connection fails** | Server not running | Load a model and turn the **Local Server** toggle green on port `1234`. `curl http://localhost:1234/v1/models` should return JSON. |
| **Ollama connection fails** | Server not running | Run `ollama serve`, or check `http://localhost:11434/api/tags`. |
| **8GB RAM out of memory** | Model too large / context too long | Use `Q4_K_M` or `Q4_0` quantizations and lower the context length to 4096 tokens in LM Studio. |
| **Generation says "used the offline fallback"** | Every provider failed | Open **Agent Logs** — the exact provider and HTTP reason is recorded per attempt. Usually a missing/invalid key, a rate limit, or a model id that doesn't exist for that account. |
| **Twitter/X publish returns 403** | App-only bearer token | Posting needs user-context OAuth (OAuth 1.0a or OAuth 2.0 with `tweet.write`). A bearer token can only read. |
| **Instagram publish fails** | No public image URL | The Graph API requires a publicly reachable `image_url`. Host the image, then set the **Media URL** field. Facebook and LinkedIn still accept a plain link. |
| **Publish says "Queued locally — start the relay"** | Relay not running | Start `python3 agent/nexus_relay.py` (with the token from Settings), then retry. The job is already saved in the browser queue. |
| **Scheduled post never went out** | No `--due` runner | Add the cron line from the Quick Start, or click **Process scheduled posts that are due**. A job with an unparseable schedule is marked `invalid-schedule` and will never auto-fire. |
| **Skill not detected in Antigravity** | Wrong install path | Verify `~/.gemini/config/skills/digital-marketer/SKILL.md` exists. |
| **UI styles or scripts blocked** | `file://` restrictions | Open via `python3 -m http.server 8080` in the `ui` folder. |
| **Old UI after an update** | Service worker cache | The service worker is now network-first for HTML; if you still see a stale build, hard-reload once (⇧⌘R) or unregister the worker in DevTools → Application. |

---

## 📄 License

See [LICENSE](LICENSE). The markdown renderer, provider registry and guardrails in
`ui/ai-client.js` are plain ES2020 with no runtime dependencies; Phosphor Icons and
Google Fonts are used under their own licenses. Referenced open-source projects
(Playwright, playwright-mcp, chrome-devtools-mcp, browser-use, Selenium) are
independent projects and are not vendored here.
