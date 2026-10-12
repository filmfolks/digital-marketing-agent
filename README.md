# 🚀 Digital Marketing Agent (Nexus Command Center)

An enterprise-grade, multi-harness **Autonomous Digital Marketing & Social Media AI Agent**. Designed to run seamlessly across **Google Antigravity**, **Local LLMs (Hermes 3, Llama 3, Mistral via LM Studio or Ollama)**, and **Cloud Subscriptions (Gemini, Groq, Claude, DeepSeek, OpenAI)**.

---

## 🌟 Supported AI Harnesses

| Harness | Best For | Typical Models | Hardware / Requirements |
|---|---|---|---|
| **Google Antigravity** | Autonomous multi-step workflows, image generation, cron scheduling | Gemini 2.0 / Gemini 1.5 Pro | Antigravity IDE / CLI |
| **Hermes 3 (Local)** | 100% private, zero-cost, high-converting copywriting | `Hermes-3-Llama-3.1-8B` | 8GB+ RAM (LM Studio or Ollama) |
| **LM Studio (Local)** | Easy visual model management & local OpenAI endpoint | `Mistral-7B`, `Llama-3-8B`, `Phi-3` | Local Server on `localhost:1234` |
| **Ollama (Local)** | Command-line local model execution | `hermes3:8b`, `llama3.1:8b` | Ollama on `localhost:11434` |
| **Cloud APIs** | Ultra-fast inference & massive context windows | Gemini, Groq, Claude, DeepSeek | API Key in UI or `.env` |

---

## ⚡ Quick Start

### 1. Running the Nexus Web UI

Open directly in your browser or serve locally:

```bash
# Option A: Open directly in your browser
open ui/index.html

# Option B: Run via local HTTP server
cd ui
python3 -m http.server 8080
# Navigate to: http://localhost:8080
```

Inside the UI:
1. Select your AI Harness from the top-right header or **Settings**:
   - **Google Antigravity**: Formulates prompt directives and skill invocations.
   - **Local Models**: Connects to LM Studio (`http://localhost:1234/v1`) or Ollama (`http://localhost:11434/v1`).
   - **Cloud APIs**: Direct connection to Gemini, Groq, Claude, DeepSeek, or OpenAI.
2. Click **Test Connection** to verify your provider before generating.
3. Enter your topic, select platforms (Instagram, Facebook, YouTube, LinkedIn, Twitter, Blog), and click **Generate Content**.

---

### 2. Using with Google Antigravity (Agentic Mode)

Install the skill into your Antigravity environment:

```bash
# macOS / Linux
cp -r skill/digital-marketer ~/.gemini/config/skills/

# Windows (PowerShell)
Copy-Item -Recurse .\skill\digital-marketer $env:USERPROFILE\.gemini\config\skills\
```

In Antigravity chat, trigger your agent:
```
Tell me about your digital marketing skills.
Activate digital-marketer skill and generate today's content based on campaign.json.
```

#### Scheduled Autonomous Runs via Antigravity:
```
# Daily morning content run (9 AM)
/schedule CronExpression="0 9 * * *" Prompt="Activate digital-marketer skill. Check campaign.json and generate today's social media content."

# Weekly content calendar planning (Monday 8 AM)
/schedule CronExpression="0 8 * * 1" Prompt="Activate digital-marketer skill. Generate a 7-day multi-platform content calendar."
```

---

### 3. Using Local Models (Hermes 3, LM Studio & Ollama)

#### Option A: LM Studio (Recommended for 8GB RAM Mac / PC)
1. Download [LM Studio](https://lmstudio.ai).
2. Download a recommended 8GB-friendly model (Q4_K_M quantization):
   - **`NousResearch/Hermes-3-Llama-3.1-8B-GGUF`** (Best instruction following & copywriting)
   - **`mistral-7b-instruct-v0.3.Q4_K_M.gguf`** (Fast & reliable)
   - **`Phi-3-mini-4k-instruct.Q4_K_M.gguf`** (Ultra-lightweight ~2.2 GB)
3. In LM Studio, click **Local Server** → Load Model → **Start Server** (`http://localhost:1234/v1`).
4. Generate via Python CLI:
   ```bash
   python3 skill/digital-marketer/scripts/generate_content.py \
     --provider lmstudio \
     --topic "5 AI marketing automations for solopreneurs" \
     --platforms instagram linkedin
   ```

#### Option B: Ollama
1. Pull Hermes 3 or Llama 3:
   ```bash
   ollama run hermes3:8b
   ```
2. Generate via Python CLI:
   ```bash
   python3 skill/digital-marketer/scripts/generate_content.py \
     --provider ollama \
     --model hermes3:8b \
     --topic "Product launch teaser"
   ```

---

### 4. Automated Daily Cron Runs (macOS / Linux)

Automate daily marketing runs at 9:00 AM:

```bash
# Make scripts executable
chmod +x skill/digital-marketer/scripts/daily_run.sh
chmod +x skill/digital-marketer/scripts/lm_studio_run.sh

# Open crontab
crontab -e

# Add this line:
0 9 * * * /path/to/digital-marketing-agent/skill/digital-marketer/scripts/daily_run.sh
```

Every generated package is automatically timestamped and saved into `output/`.

---

## 📁 Project Architecture

```
digital-marketing-agent/
├── .env.example                ← Environment & API key templates
├── .gitignore                  ← Protects credentials and local logs
├── requirements.txt            ← Python dependencies (optional LiteLLM / SDKs)
├── campaign.json               ← Daily campaign goals & target audience settings
├── output/                     ← Timestamped generated markdown content
├── logs/                       ← Daily automated execution logs
├── ui/
│   ├── index.html              ← Nexus Marketing Command Center
│   ├── ai-client.js            ← Unified multi-harness browser AI client
│   ├── script.js               ← Persistent application logic & hub tools
│   ├── style.css               ← Glassmorphism interface styles
│   └── credentials.js          ← Safe local credential placeholders
└── skill/
    └── digital-marketer/
        ├── SKILL.md            ← Core Antigravity agent instructions
        ├── references/
        │   ├── copywriting_swipe_file.md  ← 50+ hooks, formulas & CTAs
        │   ├── platform_best_practices.md ← Algorithmic growth rules
        │   └── hashtag_categories.md      ← Niche hashtag banks by industry
        ├── examples/
        │   └── sample_campaign.md         ← Complete 5-day product launch
        └── scripts/
            ├── generate_content.py        ← Universal Python AI generator
            ├── daily_run.sh               ← Multi-provider automation runner
            └── lm_studio_run.sh           ← Local model cron runner
```

---

## 🔒 Security & Privacy Notice

- All API keys and social media tokens entered in the UI are stored **strictly in your local browser (`localStorage`)**.
- No credentials are ever transmitted to third-party tracking servers.
- When running locally via LM Studio or Ollama, **100% of your data remains on your machine**.

---

## 🛠️ Troubleshooting

| Issue | Resolution |
|---|---|
| **LM Studio connection fails** | Ensure LM Studio has a model loaded and the "Local Server" toggle is green on port `1234`. |
| **Ollama connection fails** | Run `ollama serve` or check `http://localhost:11434/api/tags` in your browser. |
| **8GB RAM Out of Memory** | Use `Q4_K_M` or `Q4_0` quantizations. Lower context length to 4096 tokens in LM Studio settings. |
| **Skill not detected in Antigravity** | Verify directory exists at `~/.gemini/config/skills/digital-marketer/SKILL.md`. |
| **UI styles or scripts blocked** | If using Safari with local files, open via `python3 -m http.server 8080` in the `ui` folder. |
