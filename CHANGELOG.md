# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — chat, tasks, calendar, campaigns

### Added
- `agent/brain_router.py`: one `chat(messages, tools|schema)` interface for every brain.
  - Modes: native OpenAI tools, Hermes `<tool_call>` prompt, or JSON schema.
  - Includes a tolerant parser, validation and a repair/retry loop.
- `agent/nexus_actions.py`: the action schema (10 tools) plus a zero-dependency validator and
  executor. There is no publish or approve tool.
- `agent/nexus_tasks.py`: thread- and process-safe task store with a status machine, campaigns,
  calendar and ICS export.
- `agent/nexus_scheduler.py`: runs approved tasks in the relay.
  - Uses a grace window and compare-and-set claims.
  - Recovers tasks that were interrupted mid-post.
- `agent/nexus_chat.py` (agent loop) and `agent/nexus_task_routes.py`.
  - New relay endpoints: `/nexus/chat` (SSE), `/tasks…`, `/calendar`, `/calendar.ics`,
    `/scheduler`.
- UI: Nexus Chat, Task Calendar and Tasks pages (`ui/nexus-tasks.js`).
- `skills/nexus/` skill set (SKILL.md, system prompt, tool cards, schemas, few-shot examples)
  and `agent/nexus_tasks_mcp.py` (stdio MCP).
- Docs: `docs/research/HERMES_INTEGRATION.md`, `docs/tutorials/CHAT_TASKS_CALENDAR.md`,
  `docs/tutorials/HERMES.md`.

## [3.0.0]

This release is the "make it actually work, then make it safe" release. The
publishing path had never been connected end to end, the retry/fail-over engine
that the documentation promised was unreachable dead code, and the local relay
was an unauthenticated wildcard-CORS service on loopback. All three are fixed,
the test suite below now locks the fixes in, and the surface area has grown to
cover the whole campaign lifecycle: generate → guardrail → approve → schedule →
publish → measure.

### Fixed

**Relay and publishing — the publish path never worked**

- **The relay had no `/publish` route.** `ui/script.js` posted every "Publish
  Now" click to `/publish`, got a `404`, fell into its `catch` block, and
  reported *"relay offline"* even while the relay was running and answering
  `/health`. Publishing was unreachable by construction. `/publish` now exists,
  validates the payload, writes the job, and returns real per-platform results.
- **Publish failures were logged with no reason.** `publish.py` built its audit
  detail as `{k: v for k, v in res.items() if k != "error"}` — deliberately
  dropping the *only* field that explained the failure, so
  `logs/publish_audit.jsonl` recorded that a post failed without recording why.
  The reason is now appended to the detail (and the token still never is).
- **`publish_due` crashed on naive timestamps.** A `schedule` without a timezone
  was compared against an aware `datetime.now(timezone.utc)`, raising `TypeError`
  and aborting the whole batch. Naive values are now interpreted as UTC.
- **`publish_due` published immediately when a schedule failed to parse.** An
  unparseable `schedule` was swallowed, `due` was never set, and the job was
  published on the spot — the exact opposite of what a schedule means. Such a job
  is now skipped, marked `invalid-schedule`, and reported in `skipped`.
- **`generate_content.py --url <bare base URL>` posted to the wrong path.**
  `lm_studio_run.sh` passes `--url http://localhost:1234/v1`, but the provider
  table stores the full `/v1/chat/completions` path, so the script POSTed to the
  bare base URL and got a `404`. `normalize_base_url()` now accepts either form
  for any OpenAI-compatible server, and appends `/messages` for Anthropic.
- **Canva Studio claimed success while sending nothing.** The old
  `publishCanvaDesign()` waited about a second and then printed
  *"Design link shared + caption published ✓"* for every platform that merely had
  a token stored. No network call was ever made. It now hands the job to
  `agent/publish.py` through the relay and reports the true per-platform result —
  including the fact that Instagram is excluded from the hand-off because the
  Graph API needs a direct public image URL.
- **The Canva OAuth flow could never complete.** It sent a hard-coded PKCE
  `code_challenge` with no `code_verifier` stored anywhere, so the token exchange
  could not succeed. It now generates a random verifier, derives the S256
  challenge with `crypto.subtle`, keeps the verifier in `sessionStorage` for the
  round-trip, validates the returned `state` to reject CSRF, and reports a missing
  verifier (wrong tab/session) instead of failing mutely.
- **Trends rendered invented metrics as if they were live data.** The page
  displayed fabricated figures ("1.2M mentions", "+45%") with no data source. It
  now runs an "Opportunity Scan" that ranks angles from text you paste in and
  labels the page as analysis of supplied input rather than a live feed.
- **Same-day, same-topic generator runs overwrote each other.** `save_content()`
  named output by date and topic only. The filename now includes `%H%M%S`, so two
  runs cannot silently destroy each other's output.
- **The service worker was cache-first for HTML forever.** `index.html` was served
  from cache indefinitely, so after any update the user kept running an old UI with
  no way to refresh out of it, and one bug poisoned the cache with an opaque
  response. Navigation/HTML is now network-first with a cache fallback.
- **The service worker cached cross-origin API responses.** Every `GET` was cached,
  including authenticated AI provider and Telegram traffic. Cross-origin requests
  now pass through untouched, and requests carrying `Authorization` or
  `X-Nexus-Token` are never cached.
- **`generate_content.py` reported success while writing an error into a file.**
  A provider failure was formatted into the output markdown as if it were copy,
  and the process still exited `0`. Provider failures now raise `GenerationError`,
  failures are collected in `failures`, and the process exits non-zero when every
  platform failed, so cron and the relay can tell "generated" from "failed".
- **Telegram `/queue`, `/approve` and `/reject` were documented but not
  implemented** — `/queue` answered *"Unknown command"*. All three handlers now
  exist, and `/queue` reads the real publish queue through the MCP tool.
- **Telegram `/post` returned a fake success note.** `execute_pending` returned a
  hard-coded `{"ok": True, "note": "Queued…"}` and published nothing, so the bot
  reported success for a post that did not exist. It now calls the real
  `publish_post` tool and reports what actually happened.
- **Telegram `/plan` silently ate words.** The parser unconditionally dropped the
  last two tokens as days and budget, so `"q3 launch push"` became `"q3"` and
  `"holiday sale campaign 10"` mis-read `10` as a budget. Only genuinely numeric
  trailing tokens are stripped now, and they are interpreted in the documented
  `[days] [budget]` order.
- **Telegram lost messages that contained unbalanced Markdown.** AI copy
  frequently contains stray `*`, `_`, `[` or `` ` ``, which makes Telegram reject
  the message with HTTP 400; only an `api_error` audit line remained and the user
  never saw the content. The message is now retried as plain text.
- **The browser skill's `[audit]` line was printed to stdout.** `nexus_mcp.run_py`
  parsed stdout as JSON, so `{…json…}` followed by `[audit] OK …` raised
  `Extra data` and **every** MCP browser tool reported failure even when the
  underlying command had succeeded. Audit output now goes to stderr, stdout is
  reserved for the machine-readable result, and both parsers use a
  largest-valid-JSON scanner instead of `find("{")`.
- **MCP `render_video` did not escape quotes in the ffmpeg concat file.** A raw
  `'` in a filename corrupted the list or allowed directive injection into the
  concat demuxer. Single quotes are now escaped, and newlines are stripped.
- **MCP `generate_content` returned a placeholder string.** It emitted
  `"[INSTAGRAM] topic — tone…"`, which looked like output but contained no
  marketing value. It now shells out to `generate_content.py` and returns real
  copy, the resolved provider and the model.
- **`renderOutputTabs` crashed on an empty result set.** It indexed `platforms[0]`
  (undefined) and rendered a broken tab and a blank panel. It now renders an
  explicit "no content was returned, check Agent Logs" message.
- **`animateCounters` and `animateBars` were dead code.** Every KPI card carried a
  `data-count` attribute and every platform bar had a width to animate, but
  nothing ever called the functions, so the numbers never animated.
  `initDashboard()` now calls both.
- **`navigator.clipboard` was used with no fallback.** The README recommends
  opening `ui/index.html` directly, where `navigator.clipboard` is undefined
  outside a secure context, so every copy action threw. `copyToClipboard()` now
  checks `window.isSecureContext` and falls back to a hidden `<textarea>` plus
  `document.execCommand('copy')`, and reports failure to the user rather than
  throwing.

**AI engine — wrong vendor, dead code, and lost settings**

- **`AIClient.getCloudEndpoint()` silently defaulted unknown providers to the
  Gemini URL.** Any provider id it did not recognise — including newly added ones
  — was routed to Google, so an Anthropic, xAI or OpenRouter key was POSTed to
  `generativelanguage.googleapis.com`. The function is gone; unknown provider ids
  now resolve to `null` and the engine raises a typed error. A regression test
  asserts each provider points at its own host.
- **The retry/fail-over engine in `ui/script.js` was dead code.** `runAgent`,
  `callProvider` and `callAnthropic` existed but nothing ever called them, so the
  retries, fail-over and Anthropic support the documentation promised never ran.
  All provider routing now lives in one place, `ui/ai-client.js`, which is the
  single source of truth and is actually invoked by the UI.
- **"Save Key for Provider" wrote where generation did not read.** The button
  stored the key at `nexus_credentials["prov-<id>-key"]` while generation read
  `nexus_ai_config.cloudApiKey`, so a saved key was ignored until you also pressed
  "Save Settings" — and the engine silently fell back to offline templates. Both
  now read and write the shared `nexus_credentials` store through one write path
  (`saveProviderCredentials()`), and saving a key for the active provider updates
  the engine immediately.
- **Switching cloud provider in Settings did not update the engine.** The engine
  kept the previously selected provider until "Save Settings" was pressed, so
  generating right after switching sent the request *and the key* to the old
  vendor. The `change` handler now calls `NexusAI.saveConfig({cloudProvider})`
  synchronously and prefills the saved key and model for the new provider.
- **Provider selection could not fail over usefully.** A cloud engine hard-coded a
  two-provider chain and ignored the user's own selection order. `resolveChain()`
  now puts the selected provider first and appends the ordered fail-over list,
  de-duplicated.
- **Playwright installed without a browser produced a confusing crash.**
  `make_engine()` only caught `ImportError`, so a missing Chromium binary raised a
  non-`ImportError` and every browser command died with *"Executable doesn't
  exist"*. `_playwright_ready()` now probes for the actual binary and the fallback
  message names the exact fix.
- **Every browser CLI/relay invocation launched and closed its own browser.** This
  made the core workflow impossible: `navigate` → `snapshot` → `act` always saw a
  blank `about:blank` page on the second command, because the first command's
  browser was already gone. There is now a persistent profile plus session state
  (§ Added), so successive invocations operate on the same document.
- **The permission gate ran after the browser was constructed.** A blocked `act`
  wasted a full Chromium launch or, with no browser installed, crashed with an
  engine error instead of the clean policy message. `precheck()` now runs before
  `make_engine()`, and the test suite asserts a blocked request never mentions a
  missing engine.
- **The markdown renderer emitted one `<ul>` per list item and mangled
  asterisks.** Each `- item` line opened and closed its own list, and `*` used for
  both bullets and emphasis produced `<em> bullet` and broken bold. The renderer
  now tracks block structure: one list per run, correct `<ol start>`, fenced code
  taken verbatim, and inline emphasis that does not swallow list bullets.

- **Switching cloud provider deleted that provider's saved API key.** The
  provider `<select>` passes an empty key when it changes, and `saveConfig()`
  mirrored `cloudApiKey` into the credential store where an empty value means
  "delete" — so selecting a provider you had already configured wiped its key,
  and the field then refilled blank. Credential writes are now explicit and
  non-destructive: `saveProviderKey(id, '')` is a no-op and only
  `saveProviderKey(id, null)` clears a key.
- **`saveConfig()` no longer writes credentials at all.** It mutated the shared
  credential store as a side effect, which is what made the bug above possible.
  Settings and secrets now have one writer each.
- **A provider could inherit another provider's key.** `getProvider()` resolves
  the deprecated top-level `cloudApiKey` only when the configured provider
  matches, and that rule is now covered by a test.
- **Cancel and log routing were shared across concurrent generations.** One
  `AbortController` and one log hook lived on the instance, so a second
  `generate()` (a hub tool started while the main form was running) replaced the
  first run's controller and hook — "Cancel" could abort the wrong batch, or
  nothing. Each run now owns its controller and log sink, `abort()` cancels every
  in-flight run and reports how many, and `activeRuns` exposes the count.
- **YouTube was offered as a publish target but cannot be one.** The Data API has
  no text/community-post endpoint — only `videos.insert`. The publish picker no
  longer lists it, `publish.py` returns an actionable explanation instead of
  "Unsupported platform", and the Telegram `/post` command no longer accepts it.
- **Publish hand-off files accumulated.** `nexus_relay.py` replied *and* returned
  `None` from the same helper, so the caller's `isinstance(result, dict)` guard
  was always false and `logs/publish_job.json` was never deleted. Execution and
  response are now separate, the temp file is removed in a `finally`, and the
  finished job is mirrored into the queue (without credentials).
- **The MCP server still advertised version `2.0.0`** while the relay, UI and
  browser skill all reported `3.0.0`.
- **Stale "11 tools" counts.** `SKILL.md`, `TUTORIAL.md`, `mcp-config-examples.json`
  and the UI catalogue all predated `publish_post` and `read_publish_queue`; they
  now report the real count of 13.
- **`TUTORIAL.md` linked two reference files that do not exist**
  (`meta_ads_skillset.md`, `google_ads_skillset.md`), and `telegram_bot.py`
  pointed at a `nexus_server.py` that was never in the repo. Both corrected.
- **`AI_PROVIDER` was documented in `.env.example` but never read.**
  `generate_content.py` now uses it as the default for `--provider`, so a cron
  job can pin a provider through the environment.
- **`CANVA_REDIRECT_URI` was documented as an environment variable** although the
  OAuth redirect is handled in the browser; it is now documented as the Canva
  Studio form field it actually is.

### Added

**Publishing pipeline**

- A real publish queue with `/publish`, `/queue`, `/publish-due`, `/health` and
  `/tools` relay routes. The queue is persisted at `logs/publish_queue.json` so a
  scheduled post survives a browser restart, capped at the last 200 jobs.
- A UI queue viewer that reads real relay state, with per-status counts, and a
  pre-flight checker that flags platform-limit violations before you publish:
  character limits per platform, Instagram's public-image-URL requirement, and the
  LinkedIn in-body-link reach penalty.
- A dry-run validation path ("Validate") that checks the payload and credentials
  without making a single network call, and records the job as `validated` rather
  than `published`.
- `agent/publish.py` as a standalone CLI with `--platform/--text`, `--from-json`,
  `--due`, `--list` and `--dry-run`, for direct use and for cron.

**AI engine**

- A 7-provider cloud registry (Gemini, Groq, DeepSeek, Qwen/DashScope, OpenAI, xAI
  Grok, OpenRouter) plus 2 local engines (LM Studio, Ollama), with **native
  Anthropic Messages API support** as a distinct dialect (its own payload shape,
  `x-api-key` header, `anthropic-version`, and the direct-browser-access header).
  A `claude` alias resolves to `anthropic` so existing configs and Settings markup
  keep working.
- Retries with exponential backoff and jitter, a per-attempt timeout, and a
  typed fatal/retryable decision: 4xx other than 408/409/429 is not retried.
- Ordered fail-over down the provider chain, with every attempt recorded through
  the `onLog` hook so the Agent Logs page shows the real story.
- Bounded-concurrency parallel generation (default 3 workers) with per-platform
  error isolation, so one failing platform never sinks the batch, plus working
  **abort** (`cancelDispatch()` → `NexusAI.abort()`).
- Usage and latency accounting per piece in `NexusAI.lastRun`, including
  completion tokens and which provider served each platform.
- Provider connection tests that use a real request for native dialects, because a
  `/models` GET is not part of the Anthropic API.

**Content quality and campaign management**

- **Brand guardrails** applied to model output before it reaches you: banned-word
  replacement, a hashtag cap, an emoji policy, and a required disclaimer — with an
  auditable adjustment report returned as warnings and written to the agent log,
  so a compliance pass is visible rather than silent.
- A **UTM campaign link builder** page with per-platform presets, required-field
  validation, preservation of existing query parameters, and a one-click "add a
  tracked link to this output" action.
- **Performance analytics**: CSV/TSV/semicolon import with RFC4180-style quoted
  fields and column-alias mapping, computing engagement rate, CTR, CPC, CPM, CPA
  and ROAS per channel plus a blended summary. Missing columns produce `n/a`
  rather than a guess, and the insights are deterministic comparisons of your own
  numbers.
- **Content repurposing** from any generated asset into native copy for the other
  platforms, using the full engine rather than truncating the source.
- An **approval workflow** on calendar posts with a strict forward-only status
  flow (`draft → in_review → approved → scheduled → published`), a per-post
  history, and a publish guard that refuses to hand an unapproved post to the
  publisher.
- A **command palette** on ⌘K / Ctrl+K (and `/` when not typing) exposing
  navigation, dispatch, cancel, copy, repurpose, calendar, publisher, dry-run
  validation, queue processing, relay health, analytics import and log export.
- **Workspace export/import** of all local data as a JSON backup, with an explicit
  opt-in for credentials that stamps a plaintext warning into the file, plus a
  guarded clear-all that deliberately leaves credentials alone.
- A trend **Opportunity Scan** that ranks angles from pasted data, replacing the
  fabricated live metrics.

**Browser automation**

- A **persistent browser profile** at `logs/browser_profile/` (via
  `launch_persistent_context`) so cookies and logins survive between invocations,
  with an automatic fall back to an ephemeral context in locked-down environments.
- Remembered-page session state at `logs/browser_session.json`, restored at launch
  so multi-step workflows operate on the same document.
- `status` and `close` subcommands. `status` reports engine availability, the
  profile directory, the remembered page and the deny-list without needing a
  browser at all; `close` forgets the page but keeps the saved logins.

**Testing and CI**

- A test suite of **103 Python tests** across six files, **36 front-end tests**
  and **43 headless-browser assertions**, covering the relay contract over real
  HTTP (token auth, origin allow-list, Host validation, 400/403/404 shapes), queue
  credential stripping, the publisher, schedule parsing, the CLI permission gates
  and exit code 2, MCP path-traversal refusal and permission gating, the markdown
  renderer, the provider registry, the credential store, run isolation, and the
  brand guardrails.
- `scripts/smoke_ui.mjs` — a headless Chrome runner that boots the real
  `ui/index.html`, drives it, and asserts on the live DOM. It catches what no
  syntax check can: the run found a temporal-dead-zone `ReferenceError` in the
  init sequence that aborted `init()` before the first page was shown, and a
  `initChrome()` that was defined but never called (so the sidebar toggle, mobile
  drawer and notification bell had no listeners). It skips cleanly when Chrome is
  absent and writes screenshots to `shots/`.
- `scripts/run_tests.sh` as a single entry point with `--lint`, `--python`,
  `--ui`, `--smoke` and `--fast` modes. The lint pass compiles every Python source, syntax-checks the
  front-end bundles, and cross-checks that every `getElementById` target and every
  inline `onclick` handler actually exists — catching whole features that would
  otherwise die silently at runtime.
- A GitHub Actions workflow at `.github/workflows/ci.yml` running the suite on
  Ubuntu and macOS across Python 3.10 and 3.12, plus a relay job that boots the
  relay and asserts its security contract end to end, and a headless-browser job
  that uploads its screenshots as artifacts.

### Changed

- **Provider routing has one home.** `ui/ai-client.js` owns the registry, the
  credential store, retries and fail-over; `ui/script.js` keeps only thin adapters
  so the existing Settings markup and inline handlers keep working. The duplicate
  divergent provider table is gone.
- **Settings became a single write path.** `saveProviderCredentials()` (in
  `script.js`) and `AIClient.saveProviderKey()` (in `ai-client.js`) both write the
  same `nexus_credentials` store, so saving a key in the Settings page is
  immediately visible to the engine. `AIClient.saveConfig()` deliberately does
  **not** touch credentials — it only persists engine settings — because writing
  secrets as a side effect is what silently deleted them.
- **The relay is documented as an API.** `/tools` returns the live command list,
  `/health` is deliberately unauthenticated so the UI can show relay status, and
  every other route requires the token when one is configured.
- **Browser gating is uniform across three layers.** The CLI `--allow-actions`
  flag, the relay's `allowActions` body field and the MCP
  `user_permission_granted` argument all express the same permission, with the UI
  checkbox as the source of truth. A refusal always reports as a refusal.
- **`logs/browser_audit.jsonl` records `ok` / `blocked` / `error`** for every
  browser command with the engine name, so policy refusals are distinguishable
  from failures during an incident review.
- **Front-end output rendering** routes through one `renderMarkdown` shared by the
  output tabs, the calendar detail drawer and the publisher, instead of ad-hoc
  escaping.
- **Service worker strategy** is now network-first for HTML, stale-while-
  revalidate for same-origin assets, and pass-through for everything cross-origin
  or state-changing.

### Security

- **The relay is no longer a wildcard-CORS service.** `Access-Control-Allow-Origin:
  *` with no authentication let any website you had open POST to
  `127.0.0.1:8712` and drive your logged-in browser or publish to your accounts —
  a genuine CSRF hole against a loopback service. The relay now enforces an Origin
  allow-list (`null`, `localhost`, `127.0.0.1`, `[::1]`), echoes the specific
  origin instead of `*` with `Vary: Origin`, and refuses non-allow-listed
  preflights with HTTP 403.
- **DNS-rebinding protection.** The `Host` header must be a loopback name; a page
  that resolves its own hostname to `127.0.0.1` is refused with HTTP 403.
- **Optional relay authentication.** `NEXUS_RELAY_TOKEN` (or `--token`) requires
  `X-Nexus-Token` on every non-GET request and on `/queue` and `/tools`, compared
  with `secrets.compare_digest`. `--generate-token` prints a fresh
  `secrets.token_urlsafe(32)`.
- **The relay still binds `127.0.0.1` only** and never `0.0.0.0`, so it is not
  reachable from your network even with no token configured.
- **Queue credentials are stripped on the way out.** `GET /queue`,
  `publish.py --list` and the MCP `read_publish_queue` tool all remove the
  `credentials` object before replying, so platform tokens persisted for deferred
  publishing never travel back over HTTP. Regression tests assert the literal
  secret is absent from the response.
- **MCP `resources/read` is confined to the project root.** The handler resolves
  the path and requires it to remain inside `ROOT`, so `../../.ssh/id_rsa` and
  symlink escapes are refused with JSON-RPC `-32602`.
- **`browser_act` requires `user_permission_granted: true`** and makes no browser
  call without it, so an autonomous agent loop cannot click through your accounts.
- **Payload size caps and a fixed command whitelist** on the relay: bodies over
  2 MB are rejected with HTTP 413, and an unknown command is a 400 — there is no
  generic "execute this" route.
- **The publisher never logs a token.** `logs/publish_audit.jsonl` records the
  result and the failure reason, never the credential.
- **`generate_content.py` narrates to stderr in `--json` mode**, keeping stdout as
  a pure machine interface so the relay and MCP cannot mis-parse a successful run.
- **Model output cannot inject markup.** `renderMarkdown` HTML-escapes before
  formatting, and only `http(s)` URLs become anchors, so `javascript:` and `data:`
  URLs stay inert text.
- **The service worker never caches credentials or authenticated responses** —
  cross-origin requests and anything carrying `Authorization` or `X-Nexus-Token`
  is passed through untouched.

See [SECURITY.md](SECURITY.md) for the full threat model, the explicit list of
what is *not* protected, and the hardening checklist.
