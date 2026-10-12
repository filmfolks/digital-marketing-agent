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
}

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
    for p in ["gemini", "groq", "deepseek", "openai", "claude"]:
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

def generate_for_platform(topic, platform, tone, brand, provider_key, model_name=None, custom_url=None):
    """Generate content for a single platform using chosen AI harness."""
    cfg = PROVIDERS.get(provider_key, PROVIDERS["lmstudio"])
    url = custom_url or cfg["url"]
    
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
"""

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
    else:
        err_msg = resp.get("error", "Unknown HTTP error")
        return f"❌ Error generating {platform} content (HTTP {status}): {err_msg}"

def generate_claude(topic, platform, tone, brand, api_key, model):
    if not api_key:
        return "❌ Anthropic API key missing. Set ANTHROPIC_API_KEY in environment or .env."
    
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
            {"role": "user", "content": f"Brand: {brand}\nTopic: {topic}\nPlatform: {platform}\nTone: {tone}\n\nGenerate ready-to-post {platform} content."}
        ]
    }
    status, resp = http_post_json(url, payload, headers, timeout=60)
    if status == 200:
        content = resp.get("content", [])
        if content and "text" in content[0]:
            return content[0]["text"].strip()
    return f"❌ Claude API Error (HTTP {status}): {resp.get('error', 'Unknown')}"

def save_content(content_dict, topic, brand):
    """Save generated content to a dated markdown file in output directory."""
    today = datetime.now().strftime("%Y-%m-%d")
    safe_topic = "".join(c for c in topic if c.isalnum() or c in " _-").strip()[:40]
    filename = f"content_{today}_{safe_topic.replace(' ', '_')}.md"
    
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
                        choices=["instagram", "facebook", "youtube", "linkedin", "twitter", "blog"],
                        help="Target social platforms")
    parser.add_argument("--tone", default="professional",
                        choices=["professional", "casual", "humorous", "inspirational", "educational", "urgent"],
                        help="Brand voice tone")
    parser.add_argument("--brand", default="", help="Brand name")
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "lmstudio", "ollama", "gemini", "groq", "claude", "deepseek", "openai"],
                        help="AI provider harness")
    parser.add_argument("--model", default=None, help="Specific model name (e.g. hermes-3-llama-3.1-8b, gemini-2.0-flash)")
    parser.add_argument("--url", default=None, help="Custom base URL for local or proxy API")
    parser.add_argument("--campaign-file", default=None, help="Path to campaign.json configuration")
    parser.add_argument("--test", action="store_true", help="Test connection to selected or detected AI provider")
    args = parser.parse_args()

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

    print("\n" + "="*60)
    print("🚀 DIGITAL MARKETING AGENT — CONTENT GENERATOR")
    print("="*60)
    print(f"   Provider:  {cfg['name']}")
    print(f"   Model:     {args.model or cfg.get('default_model')}")
    print(f"   Brand:     {brand}")
    print(f"   Topic:     {topic}")
    print(f"   Tone:      {tone}")
    print(f"   Platforms: {', '.join(platforms)}")
    print("="*60 + "\n")

    content_dict = {}
    for p in platforms:
        print(f"⚙️  Generating {p.upper()} content...", flush=True)
        content = generate_for_platform(
            topic=topic,
            platform=p,
            tone=tone,
            brand=brand,
            provider_key=provider,
            model_name=args.model,
            custom_url=args.url
        )
        content_dict[p] = content
        print(f"   ✅ {p} finished.\n")

    filepath = save_content(content_dict, topic, brand)

    print("="*60)
    print("🎉 CONTENT GENERATION COMPLETE!")
    print(f"📁 Saved file: {filepath}")
    print("="*60)

    # Preview first platform
    if platforms and platforms[0] in content_dict:
        print(f"\n--- PREVIEW: {platforms[0].upper()} ---\n")
        print(content_dict[platforms[0]][:400] + "...\n")

if __name__ == "__main__":
    main()
