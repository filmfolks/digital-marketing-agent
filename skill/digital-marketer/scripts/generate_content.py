#!/usr/bin/env python3
"""
Digital Marketing Agent — Universal Multi-Harness Content Generator
Supports:
  - Local Models: LM Studio (port 1234), Ollama (port 11434), Hermes 3, Llama 3, Mistral
  - Cloud APIs: Google Gemini, Groq, Anthropic Claude, DeepSeek, OpenAI
  - Auto-detection mode (finds active local server or env keys)
  - Zero required pip dependencies (uses built-in urllib / requests if available)
  - Optional LiteLLM / OpenAI SDK integration if installed

Usage:
  python3 generate_content.py --topic "5 Tips for AI Marketing" --platforms instagram linkedin
  python3 generate_content.py --provider ollama --model hermes-3-llama-3.1-8b
  python3 generate_content.py --provider gemini --topic "SaaS Launch"
  python3 generate_content.py --test
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

# Attempt to load .env file if available
def load_env_file():
    env_paths = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    for p in env_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception:
                pass

load_env_file()


class GenerationError(RuntimeError):
    """Raised when a provider fails — lets main() exit non-zero instead of
    silently saving an error string into the output file as if it were copy."""

# Standard library HTTP requester
def http_post_json(url, payload, headers=None, timeout=60):
    import urllib.request
    import urllib.error

    headers = headers or {}
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "DigitalMarketingAgent/2.0")

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return e.code, {"error": err_body}
    except Exception as e:
        return 0, {"error": str(e)}

def http_get_json(url, timeout=5):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "DigitalMarketingAgent/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return 0, {"error": str(e)}

# ── Provider Configurations ──────────────────────────────────────────────────
PROVIDERS = {
    # External agent brain: no HTTP call. Requests become job files in
    # data/brain/inbox and the answer comes back via outbox / POST /brain/result.
    "sho": {
        "name": "Sho (external agent brain)",
        "kind": "brain",
        "url": "",
        "default_model": "sho",
    },
    "lmstudio": {
        "name": "LM Studio (Local)",
        "url": os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1/chat/completions"),
        "check_url": "http://localhost:1234/v1/models",
        "api_key": "lm-studio",
        "default_model": os.getenv("LOCAL_MODEL_NAME", "local-model"),
    },
    "ollama": {
        "name": "Ollama (Local)",
        "url": os.getenv("OLLAMA_URL", "http://localhost:11434/v1/chat/completions"),
        "check_url": "http://localhost:11434/api/tags",
        "api_key": "ollama",
        "default_model": os.getenv("LOCAL_MODEL_NAME", "hermes-3-llama-3.1-8b"),
    },
    "gemini": {
        "name": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "api_key_env": "GEMINI_API_KEY",
        "default_model": "gemini-2.0-flash",
    },
    "groq": {
        "name": "Groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "claude": {
        "name": "Anthropic Claude",
        "url": "https://api.anthropic.com/v1/messages",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-sonnet-20241022",
    },
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "name": "OpenAI",
        "url": "https://api.openai.com/v1/chat/completions",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "xai": {
        "name": "xAI Grok",
        "url": "https://api.x.ai/v1/chat/completions",
        "api_key_env": "XAI_API_KEY",
        "default_model": "grok-4",
    },
    "openrouter": {
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "nousresearch/hermes-4-405b",
    },
    "qwen": {
        "name": "Qwen (DashScope)",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "api_key_env": "QWEN_API_KEY",
        "default_model": "qwen-plus",
    },
}


def normalize_base_url(url: str, provider_key: str) -> str:
    """Accept either a base URL or a full endpoint path.

    BUG FIX: lm_studio_run.sh passes `--url http://localhost:1234/v1`, but the
    provider table stores the full `/v1/chat/completions` path, so the script
    POSTed to `http://localhost:1234/v1` and got a 404 from LM Studio. Both forms
    now work, for any OpenAI-compatible server.
    """
    if not url:
        return url
    u = url.rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if provider_key == "claude" and not u.endswith("/messages"):
        return u + "/messages"
    return u + "/chat/completions"

def detect_best_provider():
    """Auto-detect available local LLM server or active cloud API key."""
    # 1. Check LM Studio
    status, _ = http_get_json("http://localhost:1234/v1/models", timeout=1.5)
    if status == 200:
        return "lmstudio"

    # 2. Check Ollama
    status, _ = http_get_json("http://localhost:11434/api/tags", timeout=1.5)
    if status == 200:
        return "ollama"

    # 3. Check Cloud API keys in environment
    for p in ["gemini", "groq", "deepseek", "openai", "claude", "xai", "openrouter", "qwen"]:
        env_key = PROVIDERS[p].get("api_key_env")
        if env_key and os.getenv(env_key):
            return p

    # Default fallback to LM Studio
    return "lmstudio"

# ── System Prompts ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a world-class Senior Digital Marketing Director and Social Media Copywriter with 10+ years of high-growth brand experience.
You generate platform-specific, high-converting content with thumb-stopping hooks, clear value delivery, and persuasive CTAs.

Platform Formulas:
- Instagram: Hook first line, storytelling context, 3 actionable bullets, engagement CTA, 5-8 relevant hashtags, visual direction brief.
- Facebook: Conversational, community-first tone, open discussion question at end.
- YouTube: CTR-optimized A/B titles (<60 chars), SEO description outline with timestamps, 12-15 tags, thumbnail concept.
- LinkedIn: Value-first, whitespace, bulleted insights, professional CTA (no external links in post body).
- Twitter/X: Concise thread format or punchy single tweet with hook.
- Blog: SEO-optimized H1, H2/H3 outline, meta title (55 chars), meta desc (155 chars), primary keyword focus.

Always output clean, copy-paste-ready Markdown."""

# Set from the active Brand Profile (agent/brand_research.py) in main().
BRAND_CONTEXT = ""
BRAND_GUARDRAILS = {}


def load_brand_profile(brand_id=None):
    """Return the Brand Profile for brand_id (or the active brand), or None."""
    try:
        import importlib.util
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        spec = importlib.util.spec_from_file_location("nexus_brand_research_gc",
                                                      os.path.join(root, "agent", "brand_research.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        store = mod.BrandStore()
        bid = brand_id or store.active()
        if not bid:                              # client build: fall back to the hard-wired client
            try:
                cfg_path = os.environ.get("NEXUS_CLIENT_CONFIG") or os.path.join(root, "config", "client.json")
                with open(cfg_path, encoding="utf-8") as fh:
                    bid = json.load(fh).get("client_id") or None
            except (OSError, ValueError):
                bid = None
        prof = store.read(bid, "profile") if bid else None
        return (prof, mod.brand_context(prof), mod.brand_guardrails(prof)) if prof else None
    except Exception:
        return None


def brand_block():
    if not BRAND_CONTEXT:
        return ""
    return ("\n\n" + BRAND_CONTEXT + "\nStay strictly on-brand: use only the services, USPs and voice above; "
            "never invent prices, awards or statistics.")


def apply_brand_guardrails(text, g=None):
    """Mirror of ui/ai-client.js applyGuardrails for the brand's avoid-words / emoji policy."""
    g = BRAND_GUARDRAILS if g is None else g
    if not g or not text:
        return text
    import re as _re
    for w in g.get("bannedWords") or []:
        w = str(w).strip()
        if w:
            text = _re.sub(r"\b" + _re.escape(w) + r"\b", "[removed]", text, flags=_re.I)
    if g.get("allowEmoji") is False:
        text = _re.sub("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)
    return text


def _brain_module():
    import importlib.util
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if os.path.join(root, "agent") not in sys.path:
        sys.path.insert(0, os.path.join(root, "agent"))
    spec = importlib.util.spec_from_file_location("brain_bridge", os.path.join(root, "agent", "brain_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def generate_via_sho(topic, platform, tone, brand, timeout_s=None, **wait_kw):
    """Hand the request to the Sho brain and wait for its answer (or time out)."""
    bb = _brain_module()
    task = {"topic": topic, "platform": platform, "platforms": [platform], "tone": tone, "brand": brand,
            "brand_context": BRAND_CONTEXT, "guardrails": BRAND_GUARDRAILS}
    try:
        job = bb.ask("generate", task, timeout_s=timeout_s, **wait_kw)
    except TimeoutError as e:
        return f"⚠️ Error: {e}"
    r = job.get("result")
    if isinstance(r, dict) and isinstance(r.get("content"), dict):
        return str(r["content"].get(platform) or next(iter(r["content"].values()), "")).strip()
    return bb.result_text(job).strip()


def generate_for_platform(topic, platform, tone, brand, provider_key, model_name=None, custom_url=None):
    """Generate content for a single platform using chosen AI harness."""
    if provider_key == "sho":
        return generate_via_sho(topic, platform, tone, brand)
    cfg = PROVIDERS.get(provider_key, PROVIDERS["lmstudio"])
    url = normalize_base_url(custom_url, provider_key) if custom_url else cfg["url"]
    
    # Model selection
    is_hermes = "hermes" in (model_name or "").lower()
    model = model_name or cfg.get("default_model", "local-model")

    # API key resolution
    api_key = cfg.get("api_key")
    if not api_key and cfg.get("api_key_env"):
        api_key = os.getenv(cfg["api_key_env"], "")

    # Claude requires distinct Messages API payload format
    if provider_key == "claude":
        return generate_claude(topic, platform, tone, brand, api_key, model)

    user_prompt = f"""Brand: {brand}
Topic: {topic}
Platform: {platform}
Tone of Voice: {tone}

Generate an exceptional, ready-to-post content package for {platform}.
Include:
1. Complete Post Copy / Caption / Script
2. Strategic Hashtags (if applicable)
3. Direct Call-To-Action (Engagement + Conversion)
4. Visual Direction Brief (composition, text overlay, mood)
""" + brand_block()

    headers = {
        "Content-Type": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # ChatML formatting nuance for Hermes models if local
    hermes_intro = "You are Hermes, an expert marketing strategist and growth hacker." if is_hermes else ""
    sys_content = f"{hermes_intro}\n{SYSTEM_PROMPT}".strip()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.75,
        "max_tokens": 1000,
        "stream": False
    }

    status, resp = http_post_json(url, payload, headers, timeout=60)

    if status == 200:
        choices = resp.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "").strip()
        return "⚠️ Error: AI model returned empty choices array."
    err_msg = resp.get("error", "Unknown HTTP error")
    raise GenerationError(f"HTTP {status} from {cfg['name']}: {err_msg}")

def generate_claude(topic, platform, tone, brand, api_key, model):
    if not api_key:
        raise GenerationError("Anthropic API key missing. Set ANTHROPIC_API_KEY in the environment or .env.")
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": model,
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": f"Brand: {brand}\nTopic: {topic}\nPlatform: {platform}\nTone: {tone}\n\nGenerate ready-to-post {platform} content." + brand_block()}
        ]
    }
    status, resp = http_post_json(url, payload, headers, timeout=60)
    if status == 200:
        content = resp.get("content", [])
        if content and "text" in content[0]:
            return content[0]["text"].strip()
    raise GenerationError(f"Claude API error (HTTP {status}): {resp.get('error', 'Unknown')}")

def save_content(content_dict, topic, brand):
    """Save generated content to a dated markdown file in output directory."""
    # Include the time so two runs on the same topic the same day no longer
    # silently overwrite each other's output.
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_topic = "".join(c for c in topic if c.isalnum() or c in " _-").strip()[:40]
    filename = f"content_{stamp}_{safe_topic.replace(' ', '_')}.md"
    
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "output"))
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# 📣 Marketing Content Package: {topic}\n\n")
        f.write(f"**Brand:** {brand}  \n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n\n")
        f.write("---\n\n")
        for platform, content in content_dict.items():
            f.write(f"## {platform.upper()}\n\n")
            f.write(content)
            f.write("\n\n---\n\n")

    return filepath

def main():
    parser = argparse.ArgumentParser(description="Universal Digital Marketing Content Generator")
    parser.add_argument("--topic", help="Content topic or campaign theme")
    parser.add_argument("--platforms", nargs="+", default=["instagram", "facebook"],
                        choices=["instagram", "facebook", "youtube", "linkedin", "twitter",
                                 "tiktok", "blog", "email"],
                        help="Target social platforms")
    parser.add_argument("--tone", default="professional",
                        choices=["professional", "casual", "humorous", "inspirational", "educational", "urgent"],
                        help="Brand voice tone")
    parser.add_argument("--brand", default="", help="Brand name")
    parser.add_argument("--provider", default=os.getenv("AI_PROVIDER", "auto"),
                        choices=["auto", "sho", "lmstudio", "ollama", "gemini", "groq", "claude",
                                 "deepseek", "openai", "xai", "openrouter", "qwen"],
                        help="AI provider harness")
    parser.add_argument("--model", default=None, help="Specific model name (e.g. hermes-3-llama-3.1-8b, gemini-2.0-flash)")
    parser.add_argument("--url", default=None, help="Custom base URL for local or proxy API")
    parser.add_argument("--campaign-file", default=None, help="Path to campaign.json configuration")
    parser.add_argument("--test", action="store_true", help="Test connection to selected or detected AI provider")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable output on stdout (used by the MCP server / relay)")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not write a markdown file into output/")
    parser.add_argument("--brand-id", default=None, help="Brand Setup id (default: the active brand)")
    parser.add_argument("--no-brand-profile", action="store_true", help="Ignore the saved Brand Profile")
    args = parser.parse_args()

    global BRAND_CONTEXT, BRAND_GUARDRAILS
    if not args.no_brand_profile:
        loaded = load_brand_profile(args.brand_id)
        if loaded:
            prof, BRAND_CONTEXT, BRAND_GUARDRAILS = loaded
            if not args.brand:
                args.brand = prof.get("business_name", "")

    # Determine provider
    provider = args.provider
    if provider == "auto":
        provider = detect_best_provider()
        print(f"🔍 Auto-detected AI provider: {PROVIDERS[provider]['name']}")

    cfg = PROVIDERS.get(provider, PROVIDERS["lmstudio"])

    # Test mode
    if args.test:
        print(f"\n🔌 Testing connection to {cfg['name']}...")
        if provider in ["lmstudio", "ollama"]:
            check_url = cfg.get("check_url", "http://localhost:1234/v1/models")
            status, resp = http_get_json(check_url, timeout=3)
            if status == 200:
                print(f"✅ Connection successful! Server is active at {check_url}")
            else:
                print(f"❌ Cannot connect to {check_url}. Ensure local server is started.")
                sys.exit(1)
        else:
            env_key = cfg.get("api_key_env")
            has_key = bool(os.getenv(env_key)) if env_key else False
            if has_key:
                print(f"✅ API Key found for {cfg['name']} ({env_key}). Ready.")
            else:
                print(f"⚠️ Warning: No API key found for {cfg['name']} in environment ({env_key}).")
        return

    # Load defaults from campaign.json if topic not specified
    campaign_path = args.campaign_file or os.path.join(os.path.dirname(__file__), "..", "..", "..", "campaign.json")
    brand = args.brand
    topic = args.topic
    tone = args.tone
    platforms = args.platforms

    if os.path.isfile(campaign_path) and (not topic or not brand):
        try:
            with open(campaign_path, "r", encoding="utf-8") as f:
                camp = json.load(f)
                brand = brand or camp.get("brand_name", "My Brand")
                topic = topic or camp.get("daily_topic", "5 ways AI transforms marketing")
                tone = args.tone if args.tone != "professional" else camp.get("tone", "professional")
                if "platforms" in camp and args.platforms == ["instagram", "facebook"]:
                    platforms = camp["platforms"]
        except Exception:
            pass

    brand = brand or "My Brand"
    topic = topic or "5 strategies for social media growth"

    # In --json mode stdout belongs to the machine, so all narration goes to
    # stderr. That is what lets nexus_mcp.py / the relay parse the result safely.
    def say(*parts):
        print(*parts, file=sys.stderr if args.json else sys.stdout)

    say("\n" + "="*60)
    say("🚀 DIGITAL MARKETING AGENT — CONTENT GENERATOR")
    say("="*60)
    say(f"   Provider:  {cfg['name']}")
    say(f"   Model:     {args.model or cfg.get('default_model')}")
    say(f"   Brand:     {brand}")
    say(f"   Topic:     {topic}")
    say(f"   Tone:      {tone}")
    say(f"   Platforms: {', '.join(platforms)}")
    say("="*60 + "\n")

    content_dict = {}
    failures = []
    for p in platforms:
        say(f"⚙️  Generating {p.upper()} content...")
        try:
            content = generate_for_platform(
                topic=topic,
                platform=p,
                tone=tone,
                brand=brand,
                provider_key=provider,
                model_name=args.model,
                custom_url=args.url
            )
            content_dict[p] = apply_brand_guardrails(content)
            say(f"   ✅ {p} finished.\n")
        except GenerationError as e:
            failures.append({"platform": p, "error": str(e)})
            content_dict[p] = f"❌ {p} generation failed: {e}"
            say(f"   ❌ {p} failed: {e}\n")

    filepath = None
    if not args.no_save:
        filepath = save_content(content_dict, topic, brand)

    payload = {
        "ok": bool(content_dict) and len(failures) < len(platforms),
        "provider": provider,
        "provider_name": cfg["name"],
        "model": args.model or cfg.get("default_model"),
        "brand": brand,
        "topic": topic,
        "tone": tone,
        "platforms": platforms,
        "content": content_dict,
        "failures": failures,
        "saved_file": filepath,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        say("="*60)
        say("🎉 CONTENT GENERATION COMPLETE!" if not failures else "⚠️  COMPLETED WITH FAILURES")
        if filepath:
            say(f"📁 Saved file: {filepath}")
        for f in failures:
            say(f"   ❌ {f['platform']}: {f['error']}")
        say("="*60)

        if platforms and platforms[0] in content_dict:
            say(f"\n--- PREVIEW: {platforms[0].upper()} ---\n")
            say(content_dict[platforms[0]][:400] + "...\n")

    # Non-zero exit when nothing was produced, so cron jobs and the relay can
    # tell "generated" from "silently wrote an error message into a file".
    return 1 if failures and len(failures) == len(platforms) else 0

if __name__ == "__main__":
    sys.exit(main())
