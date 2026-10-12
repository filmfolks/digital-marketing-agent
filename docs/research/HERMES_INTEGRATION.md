# Hermes + local models in Nexus: research notes

Researched on 13 Oct 2026. Sources are linked next to each finding. This is the background
for `agent/brain_router.py`, `agent/nexus_actions.py`, `skills/nexus/` and `agent/nexus_tasks_mcp.py`.

## 1. How Hermes models call tools

### Hermes 3 (Llama 3.1 base, ChatML)
- The prompt format is **ChatML** (`<|im_start|>system … <|im_end|>`).
- Tools go in the **system prompt** inside `<tools></tools>` as OpenAI-style function JSON,
  followed by a pydantic `FunctionCall` schema (`{"name", "arguments"}`) and the instruction to
  return each call inside `<tool_call></tool_call>` XML tags.
- Tool results go back in a `tool` turn wrapped in `<tool_response></tool_response>`,
  as `{"name": ..., "content": ...}`.
- The reference agent prompt asks for one call at a time, no invented results, and (optionally)
  `<scratch_pad>` reasoning before the call.
- Sources: [Hermes-3-Llama-3.1-8B model card](https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B),
  [NousResearch/Hermes-Function-Calling](https://github.com/NousResearch/Hermes-Function-Calling),
  [sys_prompt.yml](https://github.com/NousResearch/Hermes-Function-Calling/blob/main/prompt_assets/sys_prompt.yml),
  [vLLM hermes tool template](https://github.com/vllm-project/vllm/blob/main/examples/tool_chat_template_hermes.jinja).

### Hermes 4 (Llama 3.1 70B/405B and smaller)
- Uses the **Llama-3 chat format** (`<|start_header_id|>…<|eot_id|>`), not ChatML.
- **Hybrid reasoning:** it may emit `<think>…</think>` before answering. Reasoning mode is
  switched on with `thinking=True` in the chat template or a "deep thinking" system prompt.
- Tool calls use the same `<tools>` / `<tool_call>{…}</tool_call>` / `<tool_response>`
  convention. Calls come after the reasoning, and several can appear in one turn. `<tool_call>`
  is an added token, so parsing during streaming is easy. vLLM's `hermes` tool parser handles it.
- Trained for **schema adherence and repairing malformed JSON**. That's why our retry loop
  feeds exact validation errors back to the model.
- Suggested sampling: `temperature=0.6, top_p=0.95, top_k=20`.
- Sources: [Hermes-4-70B model card](https://huggingface.co/NousResearch/Hermes-4-70B),
  [Hermes-4-405B](https://huggingface.co/NousResearch/Hermes-4-405B),
  [Hermes 4 Technical Report](https://arxiv.org/abs/2508.18255).

**What Nexus does with this:** `brain_router.hermes_system_prompt()` reproduces the official
Hermes function-calling system prompt. `to_hermes_messages()` turns the neutral history
(assistant `tool_calls`, `tool` results) into `<tool_call>` and `<tool_response>` text.
`strip_think()` removes `<think>` blocks, including an unclosed block or a block whose opening
tag was already consumed by the template.

## 2. Hermes Agent (Nous Research's agent harness)
- It exists: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent),
  docs at [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/docs/).
- **Skills.** Each skill is a folder with a `SKILL.md` (YAML frontmatter + Markdown) and
  optional `references/`, `templates/`, `scripts/`, `examples/` and `assets/` folders. User
  skills live in `~/.hermes/skills/<category>/<name>/SKILL.md`.
  - Required fields: `name` and `description`. `name` is lowercase with hyphens, up to 64
    characters, and must match the folder name.
  - Recommended fields: `version`, `author`, `license`, `platforms`,
    `metadata.hermes.{tags, related_skills}`.
  - New skills must keep the description under 60 characters, written as one sentence ending
    with a period, because it is the routing signal in the skill index.
  - The format is compatible with the [Agent Skills spec](https://agentskills.io/specification).
  - Sources: [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills),
    [Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills),
    [skill_manager_tool.py validator](https://github.com/NousResearch/hermes-agent/blob/ee472a7f/tools/skill_manager_tool.py).
- **MCP.** There is a native MCP client (stdio and HTTP). Configure it under `mcp_servers:` in
  `~/.hermes/config.yaml` with `command`, `args`, `env`, `timeout` and an optional
  `tools.include` list. Tools are discovered at startup and registered as `mcp_<server>_<tool>`.
  Run `/reload-mcp` after changes. Only safe environment variables plus the ones you list are
  passed to the server.
  - Sources: [MCP docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp),
    [native-mcp SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/mcp/native-mcp/SKILL.md),
    [PR #301](https://github.com/NousResearch/hermes-agent/pull/301).
- **Local models.** Use `provider: lmstudio` (default `http://localhost:1234/v1`), or
  `provider: custom` with `base_url` for any OpenAI-compatible server such as Ollama at
  `http://localhost:11434/v1`.
  - Source: [Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers).

**What Nexus does with this:** `skills/nexus/SKILL.md` follows the Hermes and Agent Skills
frontmatter rules, and a test enforces them. `agent/nexus_tasks_mcp.py` is a stdio MCP server
that exposes only the safe Nexus actions, so Hermes Agent sees them as `mcp_nexus_<tool>`.
Setup steps are in [docs/tutorials/HERMES.md](../tutorials/HERMES.md).

## 3. LM Studio's OpenAI-compatible server (port 1234)
- The server is at `http://localhost:1234/v1`. It provides `/v1/models`, `/v1/chat/completions`,
  `/v1/responses`, `/v1/embeddings` and `/v1/completions`.
  - Source: [OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat).
- **Tools:** send the standard `tools` array. When LM Studio can parse the model's tool tokens,
  calls come back in `choices[0].message.tool_calls` with `finish_reason: "tool_calls"`.
  Otherwise the raw text stays in `message.content`, and the docs warn that smaller models may
  not follow the format. So we always parse the text as a fallback.
  - Source: [Tool Use](https://lmstudio.ai/docs/developer/openai-compat/tools).
- **Structured output:** `response_format: {type: "json_schema", json_schema: {name, strict, schema}}`.
  The JSON comes back as a string in `message.content`. The docs note that models under about
  7B are less reliable.
  - Source: [Structured Output](https://lmstudio.ai/docs/developer/openai-compat/structured-output).
- **MCP inside LM Studio:** add servers to `~/.lmstudio/mcp.json` (Windows:
  `%USERPROFILE%\.lmstudio\mcp.json`) in Cursor-style `mcpServers` format, or use the in-app
  "Edit mcp.json". Available since 0.3.17.
  - Sources: [Use MCP Servers](https://lmstudio.ai/docs/app/mcp),
    [0.3.17 release](https://lmstudio.ai/blog/lmstudio-v0.3.17).

## 4. Ollama (port 11434)
- Native API: `POST /api/chat` with `tools` (OpenAI-style function definitions). Calls come back
  in `message.tool_calls`. Structured output uses `format: "json"` or a JSON Schema object.
  The docs recommend also putting the schema in the prompt and using temperature 0.
- The OpenAI-compatible endpoint `http://localhost:11434/v1/chat/completions` also accepts
  `tools` and `response_format`.
- Models without tool support return an error containing `does not support tools`. The router
  catches that and switches to the Hermes prompt mode.
- Sources: [api.md](https://github.com/ollama/ollama/blob/main/docs/api.md),
  [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
  [blog](https://ollama.com/blog/structured-outputs),
  [Hermes + Ollama tools notebook](https://github.com/NousResearch/Hermes-Function-Calling/blob/HEAD/examples/ollama_openai_tools.ipynb).

## 5. Best practices for small local models, and where Nexus applies them
| Practice | Why | Where in Nexus |
|---|---|---|
| Few, small, flat tools with enums | Fewer choices mean fewer wrong calls | 10 tools, `platforms` enum, `additionalProperties: false` |
| Strict JSON Schema plus server-side validation | Never trust model JSON | `nexus_actions.validate()` on every call, from every brain |
| Grammar-constrained output when available | The sampler can't emit invalid JSON | `mode="json"` sends `response_format: json_schema` (LM Studio/OpenAI/Ollama `/v1`) |
| Tolerant parsing | 8B models drop closing tags, use single quotes or Python literals, add trailing commas, wrap calls in code fences, or pass `arguments` as a string | `brain_router.loads_lenient()` / `parse_tool_calls()` |
| Coercion of obvious slips | `"5"` becomes 5, `"Instagram, twitter"` becomes `["instagram","x"]`, `text` becomes `caption` | `nexus_actions.coerce()` / `check_call()` aliases |
| Validate, then feed back exact errors and retry (bounded) | Hermes 4 is trained to repair objects; the bound prevents loops | `brain_router.chat(max_repairs=2)` |
| Short steps, step limit, duplicate-call guard | Small models loop | `nexus_chat.MAX_STEPS=6`, identical calls run once |
| Concrete dates in the prompt | Models can't know "today" | System prompt includes today, timezone and next Monday |
| Few-shot examples | Big gains for under-8B models | `skills/nexus/examples/few_shot.md` (validated by tests) |
| Low temperature for tool use | More deterministic | Router default 0.3 |
| Safety by construction | Models make mistakes | There is no publish or approve tool. Posting needs human approval and the scheduler |
