# Brains: who writes the content

Pick the brain in **Settings → AI engine** or the top-bar engine menu. Every brain gets the active brand profile (Brand Setup) and its guardrails.

| Engine (UI value) | What it is | Needs | Status |
|---|---|---|---|
| **Google Antigravity — live brain** (`antigravity`) | The relay drives the Antigravity desktop app's own `agentapi` (`agent/antigravity_bridge.py`). It streams the answer to the UI over SSE (`POST /antigravity/chat`). `GET /antigravity/status` reports whether the app was found. Each brand gets its own conversation, and Nexus registers its folder as an Antigravity project once. | Antigravity app open and signed in. The relay running. | macOS tested. Windows discovery has been written but **not tested** (see below). |
| Antigravity hand-off (no app running) | If the live bridge is unavailable, Nexus falls back to the skill directive / prompt hand-off. | nothing | works everywhere |
| **Local AI: LM Studio / Ollama** (`local`) | OpenAI-compatible local server. **Hermes 3** (`hermes-3-llama-3.1-8b`, the default `LOCAL_MODEL_NAME`) is the recommended model. Ollama: `hermes3:8b`. | LM Studio server on `:1234` or Ollama on `:11434` | works. Each is the other's fail-over. |
| **Cloud APIs** (`cloud`) | xAI Grok, Gemini, Groq, DeepSeek, Qwen, OpenAI, OpenRouter (Hermes 4 405B), Anthropic Claude, with fail-over. | API key in Settings or `.env` | works |
| **Sho · Grok Bot, external agent** (`sho`) | Nexus calls no LLM. Each request becomes a job in `data/brain/inbox/<id>.json`. An external agent (Sho / Grok Bot, or any agent you run) answers by writing `data/brain/outbox/<id>.json` or calling `POST /brain/result`. `agent/brain_bridge.py list/show/answer/approve/reject`. | relay with `NEXUS_RELAY_TOKEN` set (required for `/brain/result` and `/brain/approve`) | works. This is the job-queue mechanism, and an agent with access to the folder or relay can pick jobs up. |
| Built-in (`builtin`) | Offline templates | nothing | works |

## About "Hermes"
Neither this repo nor the client builds contain a separate Hermes *agent* integration. Hermes is used as a **model**:
- Hermes 3 through LM Studio or Ollama (engine `local`; `skill/digital-marketer/scripts/lm_studio_run.sh`). The generator adds a Hermes-specific system intro when the model name contains "hermes".
- Hermes 4 405B through OpenRouter (engine `cloud`, provider `openrouter`).
- LM Studio can also load the Nexus MCP server, so Hermes tool-calling sees the Nexus tools (`mcp-config-examples.json` → `lm_studio_hermes`).

To run your own Hermes agent as the brain, use the **Sho** job queue: that agent polls `GET /brain/jobs?status=pending` and posts its answer to `/brain/result`.

## Approval rule (all brains)
No brain publishes by itself. A brain's `post` job is created as `pending_approval`, and only the owner moves it forward: `brain_bridge.py approve <id> [--publish] [--dry-run]` or `POST /brain/approve`. In the UI, publishing is always a click by the user. Browser flows have `--dry-run`, which stops before the final Post/Share click.

## Antigravity on Windows (UNTESTED)
`antigravity_bridge.py` looks for the app's `language_server` under `%LOCALAPPDATA%\Programs\Antigravity\resources\bin\` and `%ProgramFiles%\Antigravity\…`, or for the wrapper `~/.gemini/antigravity/bin/agentapi(.exe/.cmd)`. It finds the running server with `psutil` (if installed) or PowerShell `Get-CimInstance Win32_Process`, falling back to `wmic`, and finds its ports with `netstat -ano`. If the binary lives somewhere else, set `NEXUS_ANTIGRAVITY_LS=C:\path\to\language_server.exe`. These parsers have unit tests, but nobody has run them against a real Windows Antigravity install yet.
