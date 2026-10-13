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

## One router for every brain (Nexus Chat, tasks, campaigns)
`agent/brain_router.py` gives every brain the same call: `chat(messages, tools=…, schema=…)`.
That is what **Nexus Chat** uses.

| Brain | How tools are called |
|---|---|
| LM Studio, Ollama | Native OpenAI `tools`. If the model answers in text, Nexus parses `<tool_call>` itself. If Ollama replies "does not support tools", Nexus switches to the Hermes prompt automatically. |
| Cloud (OpenAI, Groq, DeepSeek, xAI, OpenRouter, Gemini, Qwen) | Native `tools` |
| Claude | Anthropic `tool_use` |
| Antigravity | Hermes `<tools>`/`<tool_call>` prompt through the live bridge |
| Sho | Hermes prompt in a `chat` job. The answer can be text with `<tool_call>` tags or `{"tool_calls": [...], "text": "..."}` |

Every reply goes through the same tolerant parser, schema validation and repair loop (up to
2 retries with the exact errors). You can force a mode with `brain.mode`: `native`, `hermes`
(prompt + tags) or `json` (`response_format: json_schema`).
See [CHAT_TASKS_CALENDAR.md](CHAT_TASKS_CALENDAR.md) and [HERMES.md](HERMES.md).

## About "Hermes"
- **Hermes 3** (LM Studio/Ollama) and **Hermes 4** (OpenRouter) are used as models. The router
  speaks their native function-calling format. Research notes:
  [docs/research/HERMES_INTEGRATION.md](../research/HERMES_INTEGRATION.md).
- **Hermes Agent** (Nous Research's harness) can drive Nexus. Load `skills/nexus/` as a skill and
  add `agent/nexus_tasks_mcp.py` as an MCP server. It can only create pending_approval tasks.
  See [HERMES.md](HERMES.md).
- The older `agent/nexus_mcp.py` still exposes direct publishing tools. Give unattended models
  `nexus_tasks_mcp.py` instead.

## Images (all brains)
Every brain is told (through `skills/nexus/system_prompt.md`, which Antigravity, LM Studio/Ollama, cloud and Sho all receive, and through `skills/nexus/SKILL.md` for Hermes Agent) to attach an `image_brief` (headline ≤ 8 words, subline, CTA, style, stock keywords) to every post. It also learns the media rules (Instagram required; Facebook/LinkedIn preferred; X optional) and the tools for images: `generate_task_image` and `regenerate_task_image` (with `edits`). Small models never have to produce a picture themselves: `agent/nexus_media.py` attaches one at creation, at approval and before posting. Its chain is AI image API → Canva → stock → offline Pillow card. See [MEDIA.md](MEDIA.md).
Image generation by provider: OpenAI (`gpt-image-1`), Gemini Imagen, xAI (`grok-2-image`) through their APIs when the keys are set. Local LLMs (LM Studio/Hermes) cannot generate images, so with them Nexus uses stock photos or the brand card. Antigravity's own `generate_image` output can be attached with `POST /tasks/<id>/update {"media": [path]}`.

## Approval rule (all brains)
No brain publishes by itself. Tasks from Nexus Chat start as `pending_approval`, and only approved tasks are posted by the scheduler at their time. A brain's `post` job is created as `pending_approval`, and only the owner moves it forward: `brain_bridge.py approve <id> [--publish] [--dry-run]` or `POST /brain/approve`. In the UI, publishing is always a click by the user. Browser flows have `--dry-run`, which stops before the final Post/Share click.

## Antigravity on Windows (UNTESTED)
`antigravity_bridge.py` looks for the app's `language_server` under `%LOCALAPPDATA%\Programs\Antigravity\resources\bin\` and `%ProgramFiles%\Antigravity\…`, or for the wrapper `~/.gemini/antigravity/bin/agentapi(.exe/.cmd)`. It finds the running server with `psutil` (if installed) or PowerShell `Get-CimInstance Win32_Process`, falling back to `wmic`, and finds its ports with `netstat -ano`. If the binary lives somewhere else, set `NEXUS_ANTIGRAVITY_LS=C:\path\to\language_server.exe`. These parsers have unit tests, but nobody has run them against a real Windows Antigravity install yet.
