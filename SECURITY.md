# Security Model

Nexus is a **local-first** application: the UI is static HTML/JS, the machine-side
work happens in local Python processes, and there is no Nexus server anywhere in
the world holding your data. That design removes a whole class of risk — and
introduces a different one, because a local app still has to defend itself
against the browser and against the network.

This document describes what is actually enforced by the code in this repository,
layer by layer, and — just as importantly — **what is not protected**.

Version: `3.0.0`

---

## 1. The local relay threat model

The browser cannot execute Python, so the UI talks to a small stdlib HTTP server
on loopback:

```
Browser UI ──HTTP──▶ agent/nexus_relay.py ──subprocess──▶ agent/*.py
 (file:// or localhost)   (127.0.0.1:8712)
```

That relay is the single most security-sensitive component in the app, because it
is reachable by *any* page your browser has open.

### 1.1 Why `Access-Control-Allow-Origin: *` was a CSRF hole

The first version of the relay answered every request with
`Access-Control-Allow-Origin: *` and no authentication of any kind.

The relay *executes things*: it drives a real browser, and it publishes to live
social accounts using credentials that it happily accepted in the request body.
Combined with a wildcard origin, the attack is trivial:

1. You have the relay running on `127.0.0.1:8712`.
2. You open any website in the same browser.
3. That website runs `fetch('http://127.0.0.1:8712/publish', {method:'POST', …})`
   with a post body of its choosing.
4. The browser sends it. The relay answers `Access-Control-Allow-Origin: *`, so
   the page can read the reply too.

This is **cross-site request forgery against a loopback service**, and because
`127.0.0.1` is reachable from the page's own browser, no firewall or router helps.
Loopback binding alone is *not* an access control boundary — the browser is the
confused deputy.

### 1.2 What is enforced now

| Control | Where | Behaviour |
|---|---|---|
| **Origin allow-list** | `ALLOWED_ORIGIN_RE` in `agent/nexus_relay.py` | Only `null` (which is what `file://` pages send), `localhost`, `127.0.0.1` and `[::1]` — with any port. Anything else gets **HTTP 403** before the route is even considered. |
| **Echoed, not wildcarded** | `_cors()` | On a permitted origin the response echoes *that exact origin* and adds `Vary: Origin`. It never sends `*`. |
| **Preflight refusal** | `do_OPTIONS` | A cross-origin `POST` with `Content-Type: application/json` forces a CORS preflight. If the origin is not allow-listed the preflight returns **403**, so the browser never sends the real request. |
| **Host header / DNS-rebinding check** | `ALLOWED_HOST_RE` in `_host_allowed()` | The `Host` header must be `localhost`, `127.0.0.1` or `[::1]`. A page that resolves `evil.example.com` to `127.0.0.1` (DNS rebinding) arrives with `Host: evil.example.com` and is refused with **403**. |
| **Loopback-only bind** | `ThreadingHTTPServer(("127.0.0.1", port), …)` | The relay binds `127.0.0.1`, never `0.0.0.0`. It is not exposed to your LAN, and cannot be reached from another machine. |
| **Optional shared token** | `NEXUS_RELAY_TOKEN` / `--token` → `X-Nexus-Token` | When set, **every non-GET request** and the `/queue` and `/tools` routes require `X-Nexus-Token`, compared with `secrets.compare_digest` (constant time). Mismatch is **HTTP 401**. |
| **Payload cap** | `_error(413, …)` | Bodies over 2 MB are rejected before parsing. |
| **Path whitelist** | `CMD_MAP` | The relay only knows a fixed set of command strings. There is no generic "run this command" route — an unknown `cmd` is a 400. |

> **Generate a token with the relay itself:**
> ```bash
> python3 agent/nexus_relay.py --generate-token
> ```
> Paste it into the UI at **Settings → Relay token** (stored only in your
> browser). Without a token the relay still refuses remote origins and foreign
> Host headers, but the token is the defence-in-depth layer that also stops a
> *local* process or a same-origin script from driving it.

### 1.3 What the origin check does *not* cover

- It cannot tell two pages on the same allowed origin apart. Anything served from
  `http://localhost:8080` is trusted by the relay.
- It does nothing against a **local process** (malware, another user account with
  access to your files, a script you ran) — the token is the only answer there.
- `null` has to be allowed because `file://` pages send it. Any page that manages
  to make a request with a `null` origin gets in. The token closes this too.

---

## 2. Browser automation

`agent/browser_automation.py` gives the agent Playwright (preferred) or raw
Chrome DevTools Protocol access to a real browser.

### 2.1 Read-only vs state-changing

| Class | Commands | Gate |
|---|---|---|
| **Read-only** | `navigate`, `snapshot`, `extract`, `screenshot`, `devtools`, `status`, `close` | None. Safe by default. |
| **State-changing** | `act --action click\|type\|press\|scroll` | Requires explicit, per-invocation permission. |

The gate exists at every layer, and all three must agree:

1. **CLI** — `act` without `--allow-actions` prints the permission notice and
   **exits with code 2** (`sys.exit(2)` in `precheck()` / `cmd_act`).
2. **Relay** — `/browser` refuses any `act-*` command unless the body carries
   `allowActions: true` (**HTTP 403**, `blocked: true`).
3. **UI** — the Browser Agent page ships a **"Grant Browser Actions Permission"**
   checkbox. Actions are blocked and the refusal is surfaced unless it is ticked;
   the tick is stored per-browser and the grant/revoke is written to the agent log.
4. **MCP** — `browser_act` returns `PERMISSION_REQUIRED` unless the caller passes
   `user_permission_granted: true` (see §6).

The permission check runs **before** the browser engine is constructed, so a
blocked request can never be masked by (or pay the cost of) launching Chromium.

### 2.2 `exit code 2` means "policy block", not "crash"

`2` is a deliberate, documented contract meaning *the agent refused, and refusing
was the correct outcome*. The relay translates it into **HTTP 403** with
`blocked: true` and passes the human-readable reason through, so the UI shows the
policy refusal instead of a generic error. Every other non-zero exit is a real
failure and becomes HTTP 500.

### 2.3 The deny-list

Even with permission granted, these targets are refused (`DENY_PATTERNS`, matched
case-insensitively against the CSS selector, the URL and the task description):

```
delete[_ -]?(my[_ -])?account     log\s*out        sign\s*out
remove[_ -]?payment               cancel[_ -]?subscription
/admin/(delete|users)
```

A deny-list hit also exits `2` and is audited as `blocked`. Destructive account
operations are not something an agent should be able to do because a user once
ticked a checkbox.

### 2.4 The persistent profile is a secret

To make `navigate → snapshot → act` work at all, the browser keeps a **real,
persistent Chrome profile** at `logs/browser_profile/`, alongside the remembered
page in `logs/browser_session.json`.

**That profile contains live cookies and session tokens for every site you log
into through the agent.** Anyone who reads that directory can reuse those
sessions. Treat it exactly like a password store:

- It is listed in `.gitignore` — **verify that it stays there** before you ever
  push this repository anywhere.
- Do not sync, back it up to cloud storage, or copy it between machines.
- Delete it (`rm -rf logs/browser_profile`) when you stop using the agent, or
  whenever you want to force a fresh login.
- `browser_automation.py close` clears the remembered *page* only — it
  deliberately **keeps** the saved logins.
- The audit trail at `logs/browser_audit.jsonl` records what the agent did
  (command, target, status, detail) but never cookie values or page content.

### 2.5 Audit trail

Every command appends one JSON line to `logs/browser_audit.jsonl` with a UTC
timestamp, engine, command, arguments, `status` (`ok` / `blocked` / `error`) and a
truncated detail string. The audit line goes to **stderr**, not stdout — stdout is
reserved for the machine-readable JSON result, and mixing the two is exactly what
broke every MCP browser tool before (§6).

---

## 3. Credential storage

### 3.1 Where credentials live

API keys and social tokens are stored in the browser's **`localStorage`**, in
plaintext, under `nexus_credentials` (and provider config under `nexus_ai_config`):

```
nexus_credentials = {
  "prov-xai-key": "xai-…",          // cloud provider API keys
  "ig-access-token": "…",           // social platform tokens
  "fb-page-token": "…",
  "tw-bearer-token": "…",
  "li-access-token": "…",
  "bl-access-token": "…",
  "canva-access-token": "…",
  "relay-token": "…",               // X-Nexus-Token for the local relay
  "browser-actions-permission": 1710000000000
}
```

There is no encryption at rest. `localStorage` is per-origin, persists until
cleared, and is readable by any JavaScript running on that origin.

### 3.2 Where credentials are sent

Two destinations — and only these two:

1. **The vendor endpoint the key belongs to.** A cloud API key is sent in the
   request to that provider's own host, and nowhere else. This is enforced by the
   provider registry: `AIClient.getProvider()` returns `null` for an unknown
   provider id and the engine **raises** rather than defaulting to some vendor's
   URL. (The previous `getCloudEndpoint()` silently returned the Gemini URL for
   anything it did not recognise, which posted an Anthropic or xAI key to Google.)
2. **The local relay, over HTTP on loopback**, when you publish. The UI builds a
   job containing the platform tokens it needs and POSTs it to
   `http://127.0.0.1:8712/publish`; the relay writes it to a job file, invokes
   `agent/publish.py`, and the publisher calls the platform API.

Social tokens are never sent to an AI provider, and AI keys are never sent to the
relay.

### 3.3 What this does and does not protect

**Protects against:**

- A cloud service or the app vendor reading your keys — there is no such service,
  and no telemetry endpoint in this codebase.
- Accidental disclosure through the repository, as long as `ui/credentials.js`,
  `.env` and the `logs/` artifacts stay git-ignored.
- Key confusion between providers — an unknown provider fails loudly instead of
  silently shipping the key to the wrong vendor.

**Does not protect against:**

- **A malicious or compromised browser extension.** Extensions with host access
  can read `localStorage` for the origin you are on, or run script inside the page.
- **Any other code running as you on this machine.** It can read the browser
  profile, `.env`, `logs/publish_queue.json` — everything.
- **XSS in the UI.** The app escapes model output before rendering
  (`renderMarkdown` escapes first, and only `http(s)` links become anchors), but a
  successful injection would have the same access to `localStorage` as the app.
- **Anyone who can read your browser profile directory** on disk.
- **Loss of the device without disk encryption.** `localStorage` is not encrypted
  by the OS for you.

**Practical guidance:** use a dedicated browser profile for Nexus rather than your
daily-driver profile, prefer **short-lived or scoped keys** over account-wide
tokens, and revoke keys from the provider console when you stop using them.

> Note on `localStorage` over `file://`: Chrome and Firefox isolate `file://`
> origins, and some browsers treat them as an opaque origin, so credentials saved
> from a page opened directly off disk may not be visible from a local HTTP
> server and vice versa. Serve the UI over `http://localhost` if storage
> behaviour looks inconsistent, and expect clipboard access to be restricted
> outside a secure context.

---

## 4. The publish queue on disk

`logs/publish_queue.json` is the **single source of truth** for scheduled posts,
owned by the relay/publisher so that a scheduled post still fires after you close
the browser.

```jsonc
[
  {
    "id": "job-1750000000000",
    "content": "…",
    "platforms": ["linkedin"],
    "media": "",
    "schedule": "2030-01-01T09:00:00.000Z",
    "status": "scheduled",
    "created_at": "…",
    "credentials": { "li-access-token": "…" }   // ← plaintext platform token
  }
]
```

**Why it stores tokens:** the queue exists so a post scheduled for tomorrow can be
published tomorrow, by a cron job, when your browser is not running. That requires
the credential to be on disk, because there is no OS keychain integration in this
codebase.

**How it is contained:**

- `GET /queue` **strips the `credentials` object** before replying
  (`queue_payload()` builds a copy without that key). The same stripping happens
  in `publish.py --list` and in the MCP `read_publish_queue` tool, and there is a
  regression test asserting the literal secret never appears in the response.
- The UI queue viewer therefore shows job status and content, never tokens.
- The relay caps the file at the last 200 jobs.

**What to do about it:** keep `logs/publish_queue.json` git-ignored (it is), and
remember that anyone with file access to your project directory can read those
tokens. Revoke platform tokens you are no longer using.

`logs/publish_job.json` is the transient hand-off file between the relay and
`publish.py`. It contains the post body and any credentials for a job in flight,
and is normally deleted after a successful run — it is git-ignored as well, and
worth deleting by hand if a run was interrupted.

---

## 5. MCP server (`agent/nexus_mcp.py`)

The MCP bridge exposes the skill set to any MCP-capable client over stdio. Three
properties are worth calling out.

### 5.1 `resources/read` is confined to the project root

```
file://../../.ssh/id_rsa   →  refused
```

The handler strips the `file://` prefix, resolves the path with `.resolve()`, and
then requires `candidate.relative_to(ROOT.resolve())` to succeed. A `ValueError`
means the resolved path escaped the project root and the request is answered with
JSON-RPC error `-32602`, *"Resource URI escapes the project root"*. This blocks
both `../` traversal and symlink escapes, because the check is on the resolved
path rather than the raw string.

### 5.2 `browser_act` requires `user_permission_granted: true`

```jsonc
{ "action": "click", "target": "#publish", "user_permission_granted": true }
```

Without that flag — or with it explicitly `false` — the tool makes **no browser
call at all** and returns:

```
PERMISSION_REQUIRED: set user_permission_granted=true only after the human
approves in Nexus Settings/Telegram.
```

The flag is not a formality: the caller is asserting a human approved. It exists
so that an autonomous agent loop cannot click its way through your accounts.

### 5.3 `publish_post` uses real credentials

`publish_post` shells out to `agent/publish.py` and **actually publishes**. It is
not a draft tool. `dry_run: true` validates the payload and every credential
without any network call, and marks the job `validated` rather than `published`.
Use it.

Credentials resolve from the job payload first, then from the process environment
/ `.env`. The publisher never writes a token into its response or its audit log —
`logs/publish_audit.jsonl` records the result and, on failure, the **reason**,
never the secret.

---

## 6. Telegram bot (`agent/telegram_bot.py`)

The bot lets you drive the whole app from your phone. It is an authenticated
command interface into your machine, so access control is the whole game.

| Control | Implementation |
|---|---|
| **Chat-id allow-list** | Only `TELEGRAM_CHAT_ID` and any ids in the comma-separated `NEXUS_TG_ALLOWLIST` may issue commands. Anything else is answered with a polite refusal, written to the audit log as `refused_unauthorized`, and (if an owner is configured) reported to the owner. The same check is applied to inline-keyboard callbacks. |
| **Approval for state changes** | `/act` and `/post` never execute on receipt. The bot stores a pending request and sends an inline **✅ Approve / ❌ Reject** keyboard. Execution happens only when the **same chat** taps Approve, and `browser_act` is then called with `user_permission_granted: true`. |
| **5-minute expiry** | `execute_pending` refuses any request older than 300 seconds, drops it, and audits `approval_expired`. A stale approval cannot be replayed later. |
| **Single-use** | The pending entry is popped on execution, rejection, or expiry. |
| **Audit log** | Every request, execution, rejection, expiry, unauthorised attempt and API error is appended to `logs/telegram_audit.jsonl` (timestamp, chat id, command, status, truncated detail). |
| **Markdown failure fallback** | If Telegram rejects a message for unbalanced Markdown, it is retried as plain text so content is never silently lost. |

### 6.1 Anyone with the bot token controls the bot

This is the blunt truth about Telegram bots: `TELEGRAM_BOT_TOKEN` **is** the
credential. Whoever holds it can read `getUpdates` — including the messages your
legitimate users send — and can send messages as the bot. The chat-id allow-list
protects *your machine from commands*, not *the bot from impersonation*.

Therefore:

- Treat the token like a root password. Never commit it, never paste it into a
  chat or a screenshot.
- Revoke it immediately with **@BotFather → `/revoke`** if it leaks.
- Keep `logs/telegram_pending.json` git-ignored; it holds pending requests and the
  chat they belong to.

---

## 7. What is NOT protected

Read this list before you trust the app with a brand account.

1. **A malicious browser extension, or any local user/process, can read
   `localStorage`** and therefore every API key and social token you have saved.
   There is no encryption and no OS keychain integration.
2. **The relay token is optional.** With no `NEXUS_RELAY_TOKEN` set, the relay
   relies on source-address binding, the Origin allow-list and the Host check
   alone. A local process — or a page on an allowed origin — can drive it.
3. **Anyone with the Telegram bot token controls the bot** (§6.1), regardless of
   the chat-id allow-list.
4. **`logs/browser_profile/` holds live session cookies** for every site you
   logged into through the agent. It is as sensitive as your password manager.
5. **`logs/publish_queue.json` holds plaintext platform tokens** so deferred posts
   can fire. The HTTP endpoint strips them; the file does not.
6. **Transport to the relay is plain HTTP.** It is loopback-only, so it does not
   cross a network, but it is not encrypted and the token travels in a header on
   every request.
7. **There is no multi-user model, no role separation and no server-side
   revocation.** Anyone who can use the app is an administrator. "Who approved
   this post?" is answered only by the local JSON audit logs, which are editable.
8. **Third-party dependencies are not vendored or pinned to hashes.** The local
   npm installs suggested for optional MCP servers (`npx @playwright/mcp@latest`,
   `npx chrome-devtools-mcp@latest`) fetch and execute code at run time.
9. **`ui/credentials.example.js` is a template, not a secret store.** Copying it
   to `ui/credentials.js` puts credentials in a file inside the web root — the
   file is git-ignored, but prefer the UI's Connected Accounts page.

---

## 8. Responsible disclosure

If you find a security issue in this project, please **do not open a public issue
with a working exploit**. Instead:

1. Report it privately to the repository maintainer (use GitHub's *Report a
   vulnerability* / private security advisory flow on the repository, or the
   contact address listed in the repository profile).
2. Include: affected version (`3.0.0` or the commit you tested), component (relay,
   browser skill, publisher, MCP bridge, Telegram bot, UI), reproduction steps, and
   the impact you believe it has.
3. Give a reasonable window to fix and release before public disclosure. A
   best-effort acknowledgement within a few days is the goal.

Please redact tokens, chat ids and cookies from anything you attach. The audit logs
are designed to be safe to share (`logs/browser_audit.jsonl`,
`logs/publish_audit.jsonl`, `logs/telegram_audit.jsonl` contain no secrets) — the
agent-log export from the UI is likewise token-free.

---

## 9. Recommended hardening checklist

Copy this into your own runbook.

**Relay**

- [ ] Always start it with a token:
      `NEXUS_RELAY_TOKEN="$(python3 agent/nexus_relay.py --generate-token)" python3 agent/nexus_relay.py`
- [ ] Store that token in **Settings → Relay token** so the UI sends `X-Nexus-Token`.
- [ ] Stop the relay when you are not using it. It has a `/shutdown` route and
      takes `Ctrl+C`.
- [ ] Never re-bind it to `0.0.0.0` and never put it behind a reverse proxy.

**Credentials**

- [ ] Use scoped, least-privilege API keys; never an account-wide token when a
      page-scoped one will do.
- [ ] Prefer short expiry and rotate on a schedule.
- [ ] Use a dedicated browser profile for the app, not your daily driver.
- [ ] Revoke tokens from the provider console as soon as you stop using them.
- [ ] Enable full-disk encryption; `localStorage` is only as safe as the disk.

**Browser automation**

- [ ] Leave "Browser Actions Permission" off unless you are actively supervising a
      run; turn it back off afterwards.
- [ ] Delete `logs/browser_profile/` when you are done with an automation session.
- [ ] Keep the audit log (`logs/browser_audit.jsonl`) for incident review.

**Telegram**

- [ ] Set `TELEGRAM_CHAT_ID` explicitly; add extra ids to `NEXUS_TG_ALLOWLIST`
      only if you really mean to.
- [ ] Revoke the bot token with `/revoke` if it is ever exposed.
- [ ] Review `logs/telegram_audit.jsonl` for `refused_unauthorized` entries.

**Repository hygiene**

- [ ] Confirm `.gitignore` still covers `logs/browser_profile/`, `logs/*.json`,
      `logs/*.jsonl`, `.env`, `ui/credentials.js` and `output/` **before your first
      push** — especially if you have been running the app in this directory.
- [ ] `git status` before every commit; if a token ever lands in a commit, rotate
      it — rewriting history does not un-leak a secret.
- [ ] Run `./scripts/run_tests.sh` after any change to the relay, the browser
      gates, the publisher or the MCP bridge; the suite includes regression tests
      for exactly the issues described in this document.

**If a token leaks**

1. Revoke/rotate it at the provider immediately — that is the only step that
   actually ends the exposure.
2. Remove the value from the working tree and from `.env`.
3. Delete `logs/browser_profile/` so no lingering session cookie remains.
4. Review the audit logs for activity you did not initiate.
5. Assume the secret is compromised even if the commit was force-pushed away.
