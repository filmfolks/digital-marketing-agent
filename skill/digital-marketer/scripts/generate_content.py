#!/usr/bin/env python3
"""
Digital Marketing Agent — LM Studio Content Generator
Runs locally on 8GB RAM systems using LM Studio's OpenAI-compatible API.

Usage:
  python3 generate_content.py --topic "AI Tools for Startups" --platforms instagram facebook --tone professional

Requirements:
  - LM Studio running with a model loaded (default: localhost:1234)
  - pip install openai

Recommended local models (fits in 8GB RAM):
  - mistral-7b-instruct-v0.3.Q4_K_M.gguf
  - Meta-Llama-3-8B-Instruct.Q4_K_M.gguf
  - Phi-3-mini-4k-instruct.Q4_K_M.gguf
"""

import argparse
import json
import os
from datetime import datetime

try:
    from openai import OpenAI
except ImportError:
    print("❌ OpenAI package not found. Run: pip install openai")
    exit(1)

# ── LM Studio connection (OpenAI-compatible) ──────────────────────────────────
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
LM_STUDIO_API_KEY  = "lm-studio"  # LM Studio doesn't need a real key

client = OpenAI(base_url=LM_STUDIO_BASE_URL, api_key=LM_STUDIO_API_KEY)

# ── System Prompt (condensed for 8GB RAM — keeps context window lean) ─────────
SYSTEM_PROMPT = """You are a world-class digital marketing expert and social media content creator.
You create platform-specific, high-converting content.

Guidelines:
- Instagram: Hook first line, story, CTA, 5-8 niche hashtags
- Facebook: Conversational, community-first, open question at end
- YouTube: SEO title (60 chars), keyword-rich description, 15 tags
- LinkedIn: Professional, value-first, bullet points, link in comment
- Blog: SEO-structured, 1500+ words, H2/H3 structure, meta description
- Twitter/X: Thread format or punchy single tweet, 1-2 hashtags

Always output in clean markdown. Label each section clearly by platform."""

def generate_for_platform(topic: str, platform: str, tone: str, brand_name: str) -> str:
    """Generate content for a single platform using LM Studio."""
    prompt = f"""
Brand: {brand_name}
Topic: {topic}
Platform: {platform}
Tone: {tone}

Generate a complete, ready-to-post content piece for {platform}. 
Include caption/copy, hashtags (if applicable), and a 2-sentence image description brief.
"""
    try:
        response = client.chat.completions.create(
            model="local-model",  # LM Studio uses whatever model is loaded
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=800,   # Keep low for 8GB RAM efficiency
            temperature=0.75,
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ Error generating {platform} content: {str(e)}"


def save_content(content_dict: dict, topic: str):
    """Save generated content to a dated markdown file."""
    today = datetime.now().strftime("%Y-%m-%d")
    safe_topic = "".join(c for c in topic if c.isalnum() or c in " _-").strip()[:40]
    filename = f"content_{today}_{safe_topic.replace(' ', '_')}.md"
    output_dir = os.path.join(os.path.dirname(__file__), "..", "output")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# 📣 Marketing Content: {topic}\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("---\n\n")
        for platform, content in content_dict.items():
            f.write(f"## {platform.upper()}\n\n")
            f.write(content)
            f.write("\n\n---\n\n")

    print(f"\n✅ Content saved to: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Digital Marketing Content Generator (LM Studio)")
    parser.add_argument("--topic",     required=True,  help="Content topic or campaign theme")
    parser.add_argument("--platforms", nargs="+", default=["instagram", "facebook"],
                        choices=["instagram", "facebook", "youtube", "linkedin", "twitter", "blog"],
                        help="Platforms to generate content for")
    parser.add_argument("--tone",       default="professional",
                        choices=["professional", "casual", "humorous", "inspirational", "educational"],
                        help="Brand tone of voice")
    parser.add_argument("--brand",      default="My Brand", help="Brand or company name")
    parser.add_argument("--test",       action="store_true", help="Test LM Studio connection only")
    args = parser.parse_args()

    # Test connection
    print(f"\n🔌 Connecting to LM Studio at {LM_STUDIO_BASE_URL}...")
    try:
        models = client.models.list()
        print(f"✅ Connected! Available model: {models.data[0].id if models.data else 'Default'}")
    except Exception as e:
        print(f"❌ Cannot connect to LM Studio: {e}")
        print("   Make sure LM Studio is open with a model loaded and the local server started.")
        exit(1)

    if args.test:
        print("✅ Connection test passed.")
        return

    print(f"\n📋 Campaign brief:")
    print(f"   Topic:     {args.topic}")
    print(f"   Platforms: {', '.join(args.platforms)}")
    print(f"   Tone:      {args.tone}")
    print(f"   Brand:     {args.brand}\n")

    content_dict = {}
    for platform in args.platforms:
        print(f"⚙️  Generating {platform} content...", flush=True)
        content = generate_for_platform(args.topic, platform, args.tone, args.brand)
        content_dict[platform] = content
        print(f"   ✅ {platform} done.\n")

    filepath = save_content(content_dict, args.topic)

    # Print preview
    print("\n" + "="*60)
    print("📄 CONTENT PREVIEW (first platform):")
    print("="*60)
    first_platform = args.platforms[0]
    print(content_dict[first_platform][:500] + "...\n")
    print(f"🎉 Full content saved to: {filepath}")


if __name__ == "__main__":
    main()
