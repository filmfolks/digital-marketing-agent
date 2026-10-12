# Hermes and local models with Nexus (LM Studio, Ollama, Hermes Agent)

There are three ways to use Hermes. Pick one.

| Way | Who runs the loop | What you need |
|---|---|---|
| **A. Nexus Chat with a local brain** (recommended) | Nexus (`agent/brain_router.py`) | LM Studio or Ollama with a Hermes model |
| **B. Hermes Agent harness** | Hermes Agent, which calls Nexus over MCP | Hermes Agent + `skills/nexus` + `agent/nexus_tasks_mcp.py` |
| **C. LM Studio chat + MCP** | LM Studio's own chat UI | LM Studio ≥ 0.3.17 + `mcp.json` |

In all three cases a model can only create **pending_approval** tasks. You approve them in the
Nexus app, and the scheduler posts them at their time.

## A. Nexus Chat with LM Studio or Ollama

### LM Studio (Mac and Windows)
1. Install LM Studio from lmstudio.ai. Download **Hermes 3 Llama 3.1 8B** (GGUF Q4_K_M, about
   5 GB, fine with 8 GB RAM) or a Hermes 4 GGUF if you have the memory.
2. Go to **Developer → Start Server**. The default port is **1234**. Load the model. A context
   length of 8k or more is recommended, because a monthly plan is long.
3. Check it:
   - macOS: `curl http://localhost:1234/v1/models`
   - Windows PowerShell: `Invoke-RestMethod http://localhost:1234/v1/models`
4. In Nexus, open **Settings → AI engine → Local AI → LM Studio** (or pick *LM Studio* in the Nexus
   Chat brain menu). If you leave the model empty, Nexus uses the loaded model and prefers one
   with "hermes" in the name.

### Ollama (Mac and Windows)
1. Install Ollama from ollama.com, then run `ollama pull hermes3:8b`.
2. Ollama listens on **11434** (`ollama serve` if it isn't already running as an app).
3. Check it: `curl http://localhost:11434/api/tags` (PowerShell: `Invoke-RestMethod …`).
4. In Nexus, choose **Local AI → Ollama** or *Ollama* in the chat menu.

### Tips for small models
- **Temperature:** the router uses 0.3. Hermes 4's card suggests 0.6 for chat.
- **Plans fail validation?** Ask for fewer posts per message, for example "plan week 1" and then
  "plan week 2".
- **Force a mode:**
  - `python3 agent/nexus_chat.py "…" --brain lmstudio --mode hermes` uses the prompt + `<tool_call>`
    tags, which is good for models whose template LM Studio can't parse.
  - `--mode json` uses grammar-constrained JSON.
- **Repairs:** every repair shows up as a yellow "repaired model output" line in the chat.
  Persistent failures are shown with the exact validation errors.

## B. Hermes Agent (Nous Research)
1. Install Hermes Agent by following its README:
   [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
2. Point it at your local model in `~/.hermes/config.yaml`:
   ```yaml
   model:
     provider: lmstudio            # or: custom
     base_url: http://localhost:1234/v1   # Ollama: http://localhost:11434/v1
     default: hermes-3-llama-3.1-8b
   ```
3. Add the Nexus MCP server. Use absolute paths and your Nexus `.venv` Python.
   ```yaml
   mcp_servers:
     nexus:
       command: /Users/you/Documents/Agents/digital-marketing-agent/.venv/bin/python3
       args: ["/Users/you/Documents/Agents/digital-marketing-agent/agent/nexus_tasks_mcp.py"]
       env:
         NEXUS_TZ: "Asia/Kolkata"
       timeout: 60
   ```
   On Windows, use
   `command: C:\\Users\\you\\nexus\\.venv\\Scripts\\python.exe` and
   `args: ["C:\\Users\\you\\nexus\\agent\\nexus_tasks_mcp.py"]`.
   Then run `/reload-mcp`. The tools appear as `mcp_nexus_draft_post`, `mcp_nexus_plan_campaign`
   and so on.
4. Install the skill by copying (or symlinking) the folder:
   - macOS/Linux: `cp -R skills/nexus ~/.hermes/skills/marketing/nexus`
   - Windows: `xcopy /E /I skills\nexus %USERPROFILE%\.hermes\skills\marketing\nexus`
5. Ask: "plan 3 Instagram posts for next week". The tasks appear in Nexus → Task Calendar as
   *Needs approval*.

## C. LM Studio's own chat with MCP
1. Open LM Studio → **Program → Install → Edit mcp.json**. The file is `~/.lmstudio/mcp.json`
   (Windows: `%USERPROFILE%\.lmstudio\mcp.json`).
2. Add:
   ```json
   {"mcpServers": {"nexus": {"command": "/ABS/PATH/.venv/bin/python3",
     "args": ["/ABS/PATH/agent/nexus_tasks_mcp.py"]}}}
   ```
3. Enable the `mcp/nexus` plugin in the chat and paste `skills/nexus/system_prompt.md` as the
   system prompt. Replace `{today}` and the other placeholders, or ask the MCP prompt
   `nexus_system` for a filled-in copy.

## The skill set (`skills/nexus/`)
| File | Purpose |
|---|---|
| `SKILL.md` | Hermes Agent / Agent Skills format (name `nexus`, ≤60-character description) |
| `system_prompt.md` | The system prompt Nexus Chat uses. Placeholders are filled at runtime. |
| `tools/<tool>.md` | One card per tool: when to use it, steps, JSON Schema, example `<tool_call>` |
| `schemas/*.json` | OpenAI/Hermes tool definitions. `all_tools.json` is the full list. |
| `examples/few_shot.md` | Full Hermes-format conversations. A test validates every call. |

Regenerate the cards and schemas after editing `agent/nexus_actions.py`:
`python3 scripts/gen_nexus_skills.py`. The tests fail if they are stale.

Check the MCP server: `python3 agent/nexus_tasks_mcp.py --self-test`.
