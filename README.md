# Digital Marketing Agent — Installation & Setup Guide

## Quick Install (Copy to Any Device)

### Step 1: Copy the Skill
Copy the entire `digital-marketer` folder to the Antigravity skills directory:

```bash
# macOS / Linux
cp -r /path/to/digital-marketing-agent/skill/digital-marketer ~/.gemini/config/skills/

# Windows (PowerShell)
Copy-Item -Recurse .\skill\digital-marketer $env:USERPROFILE\.gemini\config\skills\
```

### Step 2: Verify Installation
Open Antigravity and check that the skill is recognized. Type:
```
Tell me about your digital marketing skills
```
The agent should respond with its full marketing toolkit.

---

## Running the UI

### Option A: Open Directly (No server needed)
```bash
open /path/to/digital-marketing-agent/ui/index.html
# or double-click index.html in Finder
```

### Option B: Serve Locally (Better for PWA features)
```bash
# If you have Python:
cd /path/to/digital-marketing-agent/ui
python3 -m http.server 8080
# Then open: http://localhost:8080

# If you have Node.js:
npx serve .
```

---

## LM Studio Setup (8GB RAM Local Mode)

### Step 1: Install LM Studio
Download from: https://lmstudio.ai

### Step 2: Download a Model
Recommended for 8GB RAM (Q4_K_M quantization):
- **mistral-7b-instruct-v0.3.Q4_K_M** (~4.1 GB) — Best all-around
- **Meta-Llama-3-8B-Instruct.Q4_K_M** (~4.7 GB) — Strong reasoning
- **Phi-3-mini-4k-instruct.Q4_K_M** (~2.2 GB) — Fastest, fits easily

### Step 3: Start the Local Server
In LM Studio: Click "Local Server" → Load your model → Click "Start Server"
Default URL: `http://localhost:1234/v1`

### Step 4: Install Python Dependencies
```bash
pip install openai
```

### Step 5: Run the Content Generator
```bash
cd /path/to/digital-marketing-agent/skill/digital-marketer/scripts

python3 generate_content.py \
  --topic "5 tips to grow your Instagram" \
  --platforms instagram facebook \
  --tone professional \
  --brand "My Brand"
```

### Step 6: Set Up Daily Automation (macOS/Linux)
```bash
# Make the script executable
chmod +x /path/to/digital-marketing-agent/skill/digital-marketer/scripts/daily_run.sh

# Open crontab
crontab -e

# Add this line (runs every day at 9 AM):
0 9 * * * /path/to/digital-marketing-agent/skill/digital-marketer/scripts/daily_run.sh
```

---

## Antigravity Automation Setup

### Daily Post Generation
Open Antigravity chat and type:
```
/schedule CronExpression="0 9 * * *" Prompt="Activate the digital-marketer skill. Check campaign.json in my Documents/Agents/digital-marketing-agent folder and generate today's social media content."
```

### Weekly Content Calendar
```
/schedule CronExpression="0 8 * * 1" Prompt="Activate the digital-marketer skill. Generate a 7-day content calendar for the current campaign."
```

### Monthly Analytics Report
```
/schedule CronExpression="0 8 1 * *" Prompt="Activate the digital-marketer skill. Generate a monthly performance report with recommendations."
```

---

## Project Structure

```
digital-marketing-agent/
├── campaign.json               ← Edit your daily campaign settings here
├── ui/
│   ├── index.html              ← Open this in your browser
│   ├── style.css
│   └── script.js
├── skill/
│   └── digital-marketer/
│       ├── SKILL.md            ← Core agent instructions (copy to ~/.gemini/config/skills/)
│       ├── references/
│       │   ├── copywriting_swipe_file.md
│       │   ├── platform_best_practices.md
│       │   └── hashtag_categories.md
│       ├── examples/
│       │   └── sample_campaign.md
│       └── scripts/
│           ├── generate_content.py    ← LM Studio content generator
│           └── daily_run.sh           ← Cron automation script
└── output/                     ← Generated content saved here
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| LM Studio not connecting | Make sure "Local Server" is started in LM Studio |
| Low RAM issues | Use Q2_K or Q4_0 quantization instead of Q4_K_M |
| Skill not recognized | Check path: `~/.gemini/config/skills/digital-marketer/SKILL.md` |
| UI not loading | Open in Chrome/Firefox; Safari may block local file JS |
| Cron not running | Check logs: `~/Documents/Agents/digital-marketing-agent/logs/` |
