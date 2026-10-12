#!/usr/bin/env python3
"""
Unified brain router
====================
One call for every brain Nexus can use::

    reply = brain_router.chat(messages, tools=openai_tools, cfg=BrainConfig("lmstudio"))
    reply["text"], reply["tool_calls"]  # [{"id","name","arguments"}]

Providers and the tool-calling mode each one gets:

    provider                      mode (auto)
    ----------------------------  -------------------------------------------------
    openai groq deepseek xai      native OpenAI ``tools`` (+ text fallback parse)
    openrouter gemini qwen
    lmstudio                      native ``tools`` (LM Studio parses Hermes/Qwen/Llama
                                  tool tags itself); if the model answers in text we
                                  parse <tool_call> ourselves
    ollama                        native ``tools`` via /v1; a "does not support tools"
                                  error switches to Hermes prompt mode automatically
    claude                        Anthropic tool_use blocks
    antigravity                   Hermes prompt mode through agent/antigravity_bridge.py
    sho                           Hermes prompt mode through agent/brain_bridge.py jobs
    fake (tests)                  whatever the injected function returns

    mode="hermes"   tools in a <tools> system block, answers parsed from <tool_call>
    mode="json"     no tools: response_format json_schema (LM Studio/OpenAI) and the
                    reply is parsed + validated against ``schema``

Small local models make mistakes, so every reply goes through a tolerant parser
(<think> stripping, unclosed tags, code fences, single quotes, trailing commas,
Python literals, arguments-as-string) and the caller's validator. Invalid calls
are sent back to the model with the exact errors, up to ``max_repairs`` times.
"""
from __future__ import annotations

import ast
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

OPENAI_COMPAT = ("openai", "groq", "deepseek", "xai", "openrouter", "gemini", "qwen", "lmstudio", "ollama")
LOCAL = ("lmstudio", "ollama")
BRAINS = OPENAI_COMPAT + ("claude", "antigravity", "sho")
DEFAULT_MODELS = {"lmstudio": "hermes-3-llama-3.1-8b", "ollama": "hermes3:8b", "antigravity": "flash",
                  "sho": "sho", "openrouter": "nousresearch/hermes-4-405b"}


class BrainError(RuntimeError):
    pass


class BrainConfig:
    def __init__(self, provider: str = "", model: str = "", base_url: str = "", api_key: str = "",
                 mode: str = "auto", temperature: float = 0.3, max_tokens: int = 3000, timeout: float = 180,
                 session_id: str = "", max_repairs: int = 2, fake: Optional[Callable] = None):
        self.provider = (provider or os.environ.get("NEXUS_BRAIN") or os.environ.get("NEXUS_PROVIDER") or "lmstudio").lower()
        if self.provider not in BRAINS and self.provider != "fake":
            raise BrainError(f"unknown brain '{self.provider}'. Use one of: {', '.join(BRAINS)}")
        self.model = model or os.environ.get("NEXUS_BRAIN_MODEL", "")
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.mode = mode if mode in ("auto", "native", "hermes", "json") else "auto"
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout
        self.session_id = session_id or "nexus-chat"
        self.max_repairs = max_repairs
        self.fake = fake

    def label(self) -> str:
        return f"{self.provider}:{self.model or DEFAULT_MODELS.get(self.provider, 'default')}"

    @classmethod
    def from_dict(cls, d: dict) -> "BrainConfig":
        d = d or {}
        return cls(provider=d.get("provider") or d.get("brain") or "", model=d.get("model") or "",
                   base_url=d.get("base_url") or d.get("url") or "", api_key=str(d.get("api_key") or ""), mode=d.get("mode") or "auto",
                   session_id=d.get("session_id") or "", temperature=float(d.get("temperature", 0.3)),
                   max_tokens=int(d.get("max_tokens", 3000)))


# =============================================================================
# Tolerant parsing
# =============================================================================
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
TAG_RE = re.compile(r"<(tool_call|function_call|toolcall)>\s*(.*?)\s*(?:</\1>|(?=<\1>)|$)", re.S | re.I)
FENCE_RE = re.compile(r"```(?:json|tool_call|tool_code|javascript)?\s*(.*?)```", re.S | re.I)


def strip_think(text: str) -> str:
    text = THINK_RE.sub("", text or "")
    if "</think>" in text:                       # opening tag eaten by the template
        text = text.split("</think>", 1)[1]
    if re.match(r"\s*<think>", text):            # never closed: everything is reasoning
        return ""
    return text


def _balanced(s: str, start: int) -> Optional[str]:
    """Substring from s[start] ('{' or '[') to its matching bracket, string-aware."""
    open_ch = s[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, None, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_str:
                in_str = None
            continue
        if c in "\"'":
            in_str = c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _close_open(s: str) -> str:
    """Append the closers a truncated JSON (or Python-literal) value is missing."""
    stack, quote, esc = [], None, False
    for c in s:
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]" and stack:
            stack.pop()
    return s + (quote or "") + "".join(reversed(stack))


def _escape_newlines_in_strings(s: str) -> str:
    out, quote, esc = [], None, False
    for c in s:
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = None
            elif c == "\n":
                out.append("\\n")
                continue
            elif c == "\t":
                out.append("\\t")
                continue
        elif c in "\"'":
            quote = c
        out.append(c)
    return "".join(out)


def loads_lenient(text: str) -> Tuple[Any, List[str]]:
    """Parse JSON the way a forgiving human would. Returns (value, repairs_applied).
    Raises ValueError when nothing works."""
    repairs: List[str] = []
    s = (text or "").strip()
    try:
        return json.loads(s), repairs
    except ValueError:
        pass
    m = FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
        repairs.append("fence")
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    first = min([i for i in (s.find("{"), s.find("[")) if i >= 0] or [-1])
    if first > 0:
        s = s[first:]
        repairs.append("leading-text")
    if first >= 0:
        bal = _balanced(s, 0)
        if bal and bal != s:
            s = bal
            repairs.append("trailing-text")
    candidates = []
    t = re.sub(r",\s*([}\]])", r"\1", s)
    candidates.append(("trailing-comma", t))
    t2 = _escape_newlines_in_strings(t)
    candidates.append(("raw-newline", t2))
    candidates.append(("unclosed", _close_open(re.sub(r",\s*$", "", t2.rstrip()))))
    for name, c in candidates:
        try:
            v = json.loads(c)
            if c != s:
                repairs.append(name)
            return v, repairs
        except ValueError:
            continue
    # Python-literal dicts: {'name': 'x', 'arguments': {...}, 'ok': True}
    for cand in (t, t2, candidates[-1][1]):
        try:
            py = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", cand)))
            v = ast.literal_eval(py)
            if isinstance(v, (dict, list)):
                repairs.append("python-literal")
                return json.loads(json.dumps(v)), repairs
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
    raise ValueError("could not parse JSON")


def _normalize_call(obj: Any) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):
        obj = {**obj["function"], "id": obj.get("id")}
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
    if not isinstance(name, str) or not name.strip():
        return None
    args = None
    for k in ("arguments", "parameters", "args", "input", "params"):
        if k in obj:
            args = obj[k]
            break
    if args is None:
        args = {k: v for k, v in obj.items() if k not in ("name", "tool", "tool_name", "function", "id", "type")}
    if isinstance(args, str):
        try:
            args, _ = loads_lenient(args) if args.strip() else ({}, [])
        except ValueError:
            args = {"_unparsed": args}
    return {"id": obj.get("id") or f"call_{secrets.token_hex(4)}", "name": name.strip(), "arguments": args}


def parse_tool_calls(text: str, tool_names: Optional[List[str]] = None) -> Tuple[List[dict], str, List[str]]:
    """Find tool calls in free text. Returns (calls, remaining_prose, notes)."""
    notes: List[str] = []
    body = strip_think(text)
    calls: List[dict] = []
    spans = list(TAG_RE.finditer(body))
    if spans:
        for m in spans:
            if "{" not in m.group(2) and "[" not in m.group(2):
                continue                         # the tag was only mentioned in prose
            try:
                v, rep = loads_lenient(m.group(2))
                notes += rep
                if not m.group(0).rstrip().lower().endswith(f"</{m.group(1).lower()}>"):
                    notes.append("unclosed-tag")
            except ValueError:
                notes.append("unparseable tool_call")
                calls.append({"id": f"call_{secrets.token_hex(4)}", "name": "_invalid", "arguments": {"_raw": m.group(2)[:2000]}})
                continue
            for item in (v if isinstance(v, list) else [v]):
                c = _normalize_call(item)
                if c:
                    calls.append(c)
        prose = TAG_RE.sub("", body)
        return calls, prose.strip(), notes
    # No tags: a fenced or bare JSON object that looks like a call.
    known = set(tool_names or [])
    for chunk in [m.group(1) for m in FENCE_RE.finditer(body)] + [body]:
        idx = chunk.find("{")
        while idx >= 0:
            frag = _balanced(chunk, idx) or chunk[idx:]
            try:
                v, rep = loads_lenient(frag)
            except ValueError:
                v, rep = None, []
            items = v if isinstance(v, list) else ([v] if v is not None else [])
            if isinstance(v, dict) and isinstance(v.get("tool_calls"), list):
                items = v["tool_calls"]
            found = [c for c in (_normalize_call(i) for i in items) if c and (not known or _canon(c["name"]) in known)]
            if found:
                calls += found
                notes += rep + ["untagged-json"]
                body = body.replace(frag, "")
                break
            idx = chunk.find("{", idx + 1)
        if calls:
            break
    body = FENCE_RE.sub(lambda m: "" if calls and "{" in m.group(1) else m.group(0), body)
    return calls, body.strip(), notes


def _canon(name: str) -> str:
    try:
        import nexus_actions
        return nexus_actions.resolve_name(name) or name
    except Exception:                                          # noqa: BLE001
        return name


# =============================================================================
# Prompt builders
# =============================================================================
FUNCTION_CALL_SCHEMA = ('{"title": "FunctionCall", "type": "object", "properties": {"name": {"title": "Name", '
                        '"type": "string"}, "arguments": {"title": "Arguments", "type": "object"}}, '
                        '"required": ["name", "arguments"]}')


def hermes_system_prompt(tools: List[dict], preamble: str = "") -> str:
    """The Nous Research Hermes function-calling system prompt (Hermes-Function-Calling repo)."""
    return (
        (preamble.strip() + "\n\n" if preamble else "")
        + "You are a function calling AI model. You are provided with function signatures within "
        "<tools></tools> XML tags. You may call one or more functions to assist with the user query. "
        "Don't make assumptions about what values to plug into functions. Here are the available tools: "
        "<tools> " + json.dumps(tools, ensure_ascii=False) + " </tools> "
        "Use the following pydantic model json schema for each tool call you will make: " + FUNCTION_CALL_SCHEMA + " "
        "For each function call return a json object with function name and arguments within "
        "<tool_call></tool_call> XML tags as follows:\n<tool_call>\n{\"name\": <function-name>, \"arguments\": <args-dict>}\n</tool_call>\n"
        "Results come back inside <tool_response></tool_response> tags. If no tool is needed, answer normally "
        "without any <tool_call> tag."
    )


def to_hermes_messages(messages: List[dict], tools: Optional[List[dict]]) -> List[dict]:
    """Neutral history → plain system/user/assistant messages in Hermes text format."""
    out: List[dict] = []
    sys_parts = [m["content"] for m in messages if m["role"] == "system"]
    if tools:
        out.append({"role": "system", "content": hermes_system_prompt(tools, "\n\n".join(sys_parts))})
    elif sys_parts:
        out.append({"role": "system", "content": "\n\n".join(sys_parts)})
    pending_tool: List[str] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "tool":
            pending_tool.append("<tool_response>\n" + json.dumps({"name": m.get("name"), "content": _jsonish(m.get("content"))},
                                                                 ensure_ascii=False) + "\n</tool_response>")
            continue
        if pending_tool:
            out.append({"role": "user", "content": "\n".join(pending_tool)})
            pending_tool = []
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = "\n".join("<tool_call>\n" + json.dumps({"name": c["name"], "arguments": c["arguments"]}, ensure_ascii=False)
                              + "\n</tool_call>" for c in m["tool_calls"])
            out.append({"role": "assistant", "content": ((m.get("content") or "") + "\n" + calls).strip()})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    if pending_tool:
        out.append({"role": "user", "content": "\n".join(pending_tool)})
    return out


def _jsonish(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def to_openai_messages(messages: List[dict]) -> List[dict]:
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or "",
                        "tool_calls": [{"id": c["id"], "type": "function",
                                        "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                                       for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            c = m.get("content")
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or "", "name": m.get("name") or "",
                        "content": c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def flatten(messages: List[dict]) -> str:
    """One prompt string for single-message brains (Antigravity, Sho)."""
    parts = []
    for m in messages:
        role = m["role"].upper()
        parts.append(f"### {role}\n{m.get('content') or ''}")
    return "\n\n".join(parts) + "\n\n### ASSISTANT\n"


# =============================================================================
# Transports
# =============================================================================
def http_post_json(url: str, payload: dict, headers: Optional[dict] = None, timeout: float = 180) -> Tuple[int, dict]:
    import urllib.error
    import urllib.request
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"error": raw[:500]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": str(e)}


def _provider_table() -> dict:
    try:
        import importlib.util
        p = ROOT / "skill" / "digital-marketer" / "scripts" / "generate_content.py"
        spec = importlib.util.spec_from_file_location("nexus_generate_content_router", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                          # type: ignore[union-attr]
        return mod.PROVIDERS
    except Exception:                                          # noqa: BLE001
        return {}


def endpoint(cfg: BrainConfig) -> Tuple[str, dict, str]:
    """(chat_completions_url, headers, model) for an OpenAI-compatible provider."""
    table = _provider_table()
    entry = table.get(cfg.provider, {})
    if cfg.base_url:
        url = cfg.base_url if cfg.base_url.endswith("/chat/completions") else cfg.base_url + "/chat/completions"
    else:
        url = entry.get("url") or {"lmstudio": "http://localhost:1234/v1/chat/completions",
                                   "ollama": "http://localhost:11434/v1/chat/completions"}.get(cfg.provider, "")
    if cfg.provider == "ollama" and url.rstrip("/").endswith(":11434"):
        url = url.rstrip("/") + "/v1/chat/completions"
    key = cfg.api_key or os.getenv(entry.get("api_key_env", "") or "_", "")
    if cfg.provider not in LOCAL and not key:
        raise BrainError(f"{cfg.provider}: API key not set ({entry.get('api_key_env', 'API key')})")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    model = cfg.model or (entry.get("default_model") if cfg.provider not in LOCAL else "") or DEFAULT_MODELS.get(cfg.provider, "")
    if cfg.provider in LOCAL and not cfg.model:
        model = detect_local_model(cfg, url) or model
    return url, headers, model


def detect_local_model(cfg: BrainConfig, chat_url: str, get: Optional[Callable] = None) -> str:
    """First loaded model from /v1/models (LM Studio lists loaded/downloaded models; prefer Hermes)."""
    import urllib.request
    base = chat_url.rsplit("/chat/completions", 1)[0]
    try:
        if get:
            data = get(base + "/models")
        else:
            with urllib.request.urlopen(base + "/models", timeout=3) as r:
                data = json.loads(r.read().decode())
        ids = [m.get("id", "") for m in data.get("data", []) if "embed" not in m.get("id", "")]
        herm = [i for i in ids if "hermes" in i.lower()]
        return (herm or ids or [""])[0]
    except Exception:                                          # noqa: BLE001
        return ""


def call_openai(cfg: BrainConfig, messages: List[dict], tools, schema, mode: str, post: Callable) -> dict:
    url, headers, model = endpoint(cfg)
    payload: Dict[str, Any] = {"model": model, "temperature": cfg.temperature, "max_tokens": cfg.max_tokens, "stream": False}
    if mode == "native" and tools:
        payload["messages"] = to_openai_messages(messages)
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    elif mode == "hermes":
        payload["messages"] = to_hermes_messages(messages, tools)
    else:
        payload["messages"] = to_hermes_messages(messages, None)
    if mode == "json" and schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "nexus_output", "strict": True, "schema": schema}}
    status, body = post(url, payload, headers, cfg.timeout)
    if status != 200 or not body.get("choices"):
        err = body.get("error")
        err = err.get("message") if isinstance(err, dict) else err
        raise BrainError(f"{cfg.provider} HTTP {status}: {str(err or body)[:300]}")
    msg = body["choices"][0].get("message") or {}
    calls = []
    for tc in msg.get("tool_calls") or []:
        c = _normalize_call(tc)
        if c:
            calls.append(c)
    return {"text": msg.get("content") or "", "tool_calls": calls, "model": body.get("model") or model,
            "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or ""}


def call_claude(cfg: BrainConfig, messages: List[dict], tools, schema, mode: str, post: Callable) -> dict:
    key = cfg.api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise BrainError("claude: ANTHROPIC_API_KEY not set")
    model = cfg.model or "claude-sonnet-4-5"
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    conv = [m for m in to_hermes_messages(messages, None) if m["role"] != "system"]
    payload: Dict[str, Any] = {"model": model, "max_tokens": cfg.max_tokens, "system": system, "messages": conv}
    if tools:
        payload["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                             "input_schema": t["function"]["parameters"]} for t in tools]
    status, body = post("https://api.anthropic.com/v1/messages", payload,
                        {"x-api-key": key, "anthropic-version": "2023-06-01"}, cfg.timeout)
    if status != 200:
        raise BrainError(f"claude HTTP {status}: {str(body)[:300]}")
    text, calls = [], []
    for block in body.get("content") or []:
        if block.get("type") == "text":
            text.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append({"id": block.get("id"), "name": block.get("name"), "arguments": block.get("input") or {}})
    return {"text": "\n".join(text), "tool_calls": calls, "model": model}


def call_antigravity(cfg: BrainConfig, messages: List[dict], tools, schema, mode: str, post: Callable) -> dict:
    import antigravity_bridge as ag
    msgs = to_hermes_messages(messages, tools)
    if schema and not tools:
        msgs.append({"role": "user", "content": "Reply with ONLY a JSON object matching this JSON schema, no prose:\n"
                     + json.dumps(schema)})
    prompt = ("You are the planning brain for the Nexus marketing app. Do not edit files, run commands or "
              "browse; answer in this chat only.\n\n" + flatten(msgs))
    model = cfg.model if cfg.model in ag.MODELS else None
    res = ag.chat(prompt, cfg.session_id, model, timeout=cfg.timeout)
    return {"text": res.get("text", ""), "tool_calls": [], "model": f"antigravity:{model or 'default'}",
            "conversation_id": res.get("conversation_id")}


def call_sho(cfg: BrainConfig, messages: List[dict], tools, schema, mode: str, post: Callable) -> dict:
    import brain_bridge as bb
    task = {"messages": to_hermes_messages(messages, tools), "tools": tools or [], "schema": schema or None,
            "reply_format": "Answer as text. To call a tool, include <tool_call>{\"name\": ..., \"arguments\": {...}}</tool_call> "
                            "or return {\"tool_calls\": [{\"name\": ..., \"arguments\": {...}}], \"text\": \"...\"}."}
    job = bb.ask("chat", task, timeout_s=cfg.timeout)
    r = job.get("result")
    if isinstance(r, dict):
        calls = [c for c in (_normalize_call(x) for x in (r.get("tool_calls") or [])) if c]
        text = r.get("text") or ("" if calls else json.dumps(r, ensure_ascii=False))
        return {"text": text, "tool_calls": calls, "model": "sho", "job_id": job["id"]}
    return {"text": bb.result_text(job), "tool_calls": [], "model": "sho", "job_id": job["id"]}


TRANSPORTS = {"claude": call_claude, "antigravity": call_antigravity, "sho": call_sho}


# =============================================================================
# The one interface
# =============================================================================
def pick_mode(cfg: BrainConfig, tools, schema) -> str:
    if cfg.mode != "auto":
        return cfg.mode
    if tools:
        return "hermes" if cfg.provider in ("antigravity", "sho") else "native"
    return "json" if schema else "native"


def _raw_call(cfg: BrainConfig, messages, tools, schema, mode, post) -> dict:
    if cfg.provider == "fake":
        return cfg.fake(messages=messages, tools=tools, schema=schema, mode=mode)
    fn = TRANSPORTS.get(cfg.provider, call_openai)
    return fn(cfg, messages, tools, schema, mode, post)


def chat(messages: List[dict], *, tools: Optional[List[dict]] = None, schema: Optional[dict] = None,
         cfg: Optional[BrainConfig] = None, checker: Optional[Callable] = None,
         post: Callable = http_post_json) -> dict:
    """Ask the brain. Returns {text, tool_calls, provider, model, mode, attempts, repairs, errors}.

    tool_calls are parsed, name-resolved, coerced and validated with ``checker``
    (default nexus_actions.check_call). With ``schema`` and no tools the reply's
    JSON is in ``data``. Invalid output is retried with the errors as feedback."""
    cfg = cfg or BrainConfig()
    if checker is None and tools:
        import nexus_actions
        checker = nexus_actions.check_call
    mode = pick_mode(cfg, tools, schema)
    names = [t["function"]["name"] for t in (tools or [])]
    convo = list(messages)
    log: Dict[str, Any] = {"attempts": 0, "repairs": [], "errors": [], "mode_switches": []}
    started = time.monotonic()
    text, prose, checked, data, repairs_left, switches = "", "", [], None, cfg.max_repairs, 0
    while True:
        log["attempts"] += 1
        try:
            raw = _raw_call(cfg, convo, tools, schema, mode, post)
        except BrainError as e:
            msg = str(e).lower()
            unsupported = ("does not support tools" in msg or "tools" in msg and ("not supported" in msg or "unsupported" in msg)
                           or "response_format" in msg or "json_schema" in msg)
            if unsupported and mode in ("native", "json") and cfg.provider != "fake" and switches < 2:
                switches += 1
                log["mode_switches"].append(f"{mode}->hermes ({str(e)[:80]})")
                mode = "hermes"
                continue
            raise
        text = raw.get("text") or ""
        calls = list(raw.get("tool_calls") or [])
        prose = strip_think(text).strip()
        if tools and not calls:
            calls, prose, notes = parse_tool_calls(text, names)
            log["repairs"] += notes
        data = None
        problems: List[str] = []
        if schema and not tools:
            try:
                data, rep = loads_lenient(strip_think(text))
                log["repairs"] += rep
                import nexus_actions
                problems = nexus_actions.validate(nexus_actions.coerce(data, schema), schema)
                data = nexus_actions.coerce(data, schema)
            except ValueError:
                problems = ["reply was not valid JSON"]
        checked = []
        for c in calls:
            if c["name"] == "_invalid":
                problems.append("a <tool_call> block was not valid JSON: " + str(c["arguments"].get("_raw", ""))[:200])
                continue
            canon, args, errs = checker(c["name"], c["arguments"]) if checker else (c["name"], c["arguments"], [])
            if errs:
                problems.append(f"{c['name']}: " + "; ".join(errs[:6]))
            else:
                checked.append({**c, "name": canon, "arguments": args})
        if not problems:
            return {"text": prose, "tool_calls": checked, "data": data, "provider": cfg.provider,
                    "model": raw.get("model") or cfg.model, "mode": mode, "raw": text,
                    "duration_s": round(time.monotonic() - started, 2), **log}
        log["errors"].append(problems)
        if repairs_left <= 0:
            break
        repairs_left -= 1
        # Feed the exact problems back (Hermes 4 is trained to repair malformed objects).
        convo = convo + [{"role": "assistant", "content": text or json.dumps({"tool_calls": calls})},
                         {"role": "user", "content": "Your last reply could not be used:\n- " + "\n- ".join(problems)
                          + ("\nReturn ONLY corrected <tool_call>{\"name\": ..., \"arguments\": {...}}</tool_call> blocks "
                             "that match the tool schemas." if tools else "\nReturn ONLY the corrected JSON object.")}]
    return {"text": prose, "tool_calls": checked, "data": data, "provider": cfg.provider, "model": cfg.model,
            "mode": mode, "raw": text, "failed_validation": True, **log}


def status(cfg: BrainConfig, get: Optional[Callable] = None) -> dict:
    """Reachability probe for the UI (local servers only; cloud = key present)."""
    if cfg.provider in LOCAL:
        url = endpoint(cfg)[0] if cfg.base_url or True else ""
        model = detect_local_model(cfg, url, get)
        return {"provider": cfg.provider, "reachable": bool(model), "model": model, "url": url}
    if cfg.provider == "antigravity":
        try:
            import antigravity_bridge as ag
            return {"provider": "antigravity", **ag.status()}
        except Exception as e:                                # noqa: BLE001
            return {"provider": "antigravity", "reachable": False, "error": str(e)}
    if cfg.provider == "sho":
        return {"provider": "sho", "reachable": True, "note": "answers arrive via data/brain/outbox"}
    try:
        endpoint(cfg)
        return {"provider": cfg.provider, "reachable": True}
    except BrainError as e:
        return {"provider": cfg.provider, "reachable": False, "error": str(e)}
