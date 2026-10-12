# Nexus Social Media Manager — Full Tutorial

> The app also ships an **in-app tutorial** (sidebar → 🎓 Tutorial) with the same walkthrough.

## 0. Prerequisites
| You want… | You need |
|---|---|
| Just explore the UI | A browser. Open `ui/index.html` (or `python3 -m http.server 8000` in `ui/`) |
| Real AI generation (cloud) | An API key: xAI **Grok**, **Groq**, **OpenRouter** (Hermes 4), **Google Gemini**, or **Anthropic Claude** |
| Real AI generation (local, free/private) | **LM Studio** (serve a Hermes/Llama GGUF with its local OpenAI-compatible server on port 1234) or **Ollama** (port 11434) |
| Agentic automation | **Google Antigravity** (or any agent runtime that reads `skill/digital-marketer/SKILL.md`) |
| Live posting | Platform tokens from Meta Business Suite, YouTube Data API v3, X OAuth 2.0, LinkedIn Community Mgmt API, Blogger API v3, Canva Connect API |

## 1. First launch & brand setup
1. Open the dashboard → click **Setup Agent**.
2. Fill Brand Name, Niche, Audience, Tone → **Save Settings** (persisted in your browser).
3. Pick the **AI Engine**:
   - **Google Antigravity** — agentic handoff: Nexus writes prompts you feed to the agent running this repo's skill.
   - **Cloud APIs** — choose provider (xAI Grok, Gemini, OpenRouter/Hermes, Groq, Claude), paste key, click **Save Key for Provider**, then **Test Provider**.
   - **Local Models** — LM Studio (`http://localhost:1234/v1`) or Ollama (`http://localhost:11434/v1`). Start the server first!

## 2. Connect accounts (sidebar → 🔌 Connected Accounts)
Paste each platform's token and hit **Save Credentials**. Keys never leave your browser (localStorage). Use **Test Connections** to validate Instagram/Facebook tokens via Graph API.

## 3. Dispatch tasks to the agent (sidebar → ✨ AI Agent)
Write one directive ("Launch a campaign for our new feature"), tick platforms, pick tone + content type, **Dispatch Task to Agent**.
- ✅ If your engine works → output comes from the model.
- ⚠️ If it fails (offline, bad key, rate limit) → Nexus **retries with exponential backoff**, **fails over** down the chain (e.g. Grok → OpenRouter → Groq), **documents the failure in Agent Logs**, and still returns built-in template content so you're never blocked.

## 4. Specialist hubs
Instagram (carousels/reels/stories), Facebook (ads/groups/events), YouTube (titles/SEO scripts/Shorts), LinkedIn (authority posts/newsletters), Blog & SEO (outlines/meta keywords), Canva Studio (design briefs + Canva AI prompts), Content Studio (captions, hooks, hashtags, emails, A/B tests), Trending Now.

## 5. Calendar & scheduling
Add events on the calendar page. For hands-free daily runs use the copyable command in Settings:
```bash
# LM Studio / Ollama route
cd skill/digital-marketer/scripts && ./daily_run.sh "your topic"
```
or give the scheduler prompt to Google Antigravity (it executes `generate_content.py`, which auto-detects your local server).

## 6. Ads skill sets
- **Meta Ads** reference: `skill/digital-marketer/references/meta_ads_skillset.md` (campaign structure, Advantage+, audiences, CPM/CPC/ROAS benchmarks, placements, tracking).
- **Google Ads** reference: `skill/digital-marketer/references/google_ads_skillset.md` (Search/PMax/Demand Gen, Quality Score, negatives, bidding strategies).
Ask the agent: *"Design a Meta Ads campaign for X with $30/day budget"* — the skill system prompt forces full funnel output.

## 7. Error reporting & documentation (sidebar → 🚨 Agent Logs)
Every dispatch, provider retry, failover, publish attempt, connection test and even uncaught runtime errors are written to a persistent log (last 500 events). Filter by Errors/Warnings/Successes, expand row context, and **Export JSON** to attach to bug reports. The red badge shows open issues.

## 8. Open-source components used
- **Phosphor Icons** (MIT) — icon set; **Google Fonts** — typography.
- **LM Studio / Ollama** — local model serving (OpenAI-compatible API).
- **OpenRouter** — gateway to Nous Research **Hermes 4**, Llama, DeepSeek etc.
- **xAI Grok API**, **Groq**, **Google Gemini**, **Anthropic** — cloud reasoning.
- **postiz.io** (AGPL) — recommended self-hosted multi-platform publishing backend when you outgrow browser-only demos.
- **n8n** (fair-code) — optional workflow automation glue (RSS→post, analytics pulls).

## 9. Troubleshooting matrix
| Symptom | Likely cause | Fix (and where it's logged) |
|---|---|---|
| "Connection refused" in logs | Local server off | Start LM Studio Developer Server / `ollama serve`; enable CORS in LM Studio |
| HTTP 401 | Wrong/expired key | Re-paste key → Save Key for Provider → Test Provider |
| HTTP 429 | Rate limit | Engine auto-retries with backoff; consider fallback provider |
| HTTP 404 model not found | Model name changed | Set exact model id in Accounts page provider-model field or provider console |
| Empty output tab | Model returned no choices | Logged as error; check provider quota |
| Publish says "No credentials" | Token missing | Connect account; failure is documented in Agent Logs |
