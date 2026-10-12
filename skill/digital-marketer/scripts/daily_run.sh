#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Digital Marketing Agent — Daily Automation Cron Runner     ║
# ║  Works with LM Studio (local) or Gemini via environment     ║
# ╚══════════════════════════════════════════════════════════════╝
#
# SETUP:
#   chmod +x daily_run.sh
#   Add to cron: crontab -e
#   Add line: 0 9 * * * /path/to/digital-marketing-agent/skill/digital-marketer/scripts/daily_run.sh
#
# This runs every day at 9 AM, generates content, and saves to output folder.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/output"
LOG_DIR="$ROOT_DIR/logs"
DATE=$(date +%Y-%m-%d)
LOGFILE="$LOG_DIR/run_$DATE.log"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR"

echo "============================================" | tee -a "$LOGFILE"
echo "🚀 Digital Marketing Agent — Daily Run" | tee -a "$LOGFILE"
echo "📅 Date: $DATE" | tee -a "$LOGFILE"
echo "⏰ Time: $(date +%H:%M:%S)" | tee -a "$LOGFILE"
echo "============================================" | tee -a "$LOGFILE"

# ── Read today's campaign from config ─────────────────────────
CAMPAIGN_FILE="$ROOT_DIR/campaign.json"
if [ ! -f "$CAMPAIGN_FILE" ]; then
    echo "⚠️  No campaign.json found. Creating default template..." | tee -a "$LOGFILE"
    cat > "$CAMPAIGN_FILE" <<EOF
{
    "brand_name": "My Brand",
    "daily_topic": "Daily marketing tip for your audience",
    "tone": "professional",
    "platforms": ["instagram", "facebook"],
    "active": true
}
EOF
fi

BRAND=$(python3 -c "import json; d=json.load(open('$CAMPAIGN_FILE')); print(d.get('brand_name','My Brand'))")
TOPIC=$(python3 -c "import json; d=json.load(open('$CAMPAIGN_FILE')); print(d.get('daily_topic','Marketing tip'))")
TONE=$(python3 -c "import json; d=json.load(open('$CAMPAIGN_FILE')); print(d.get('tone','professional'))")
PLATFORMS=$(python3 -c "import json; d=json.load(open('$CAMPAIGN_FILE')); print(' '.join(d.get('platforms',['instagram','facebook'])))")
ACTIVE=$(python3 -c "import json; d=json.load(open('$CAMPAIGN_FILE')); print(d.get('active',True))")

if [ "$ACTIVE" = "False" ]; then
    echo "⏸️  Campaign is paused (active: false). Skipping." | tee -a "$LOGFILE"
    exit 0
fi

echo "📣 Brand: $BRAND" | tee -a "$LOGFILE"
echo "💡 Topic: $TOPIC" | tee -a "$LOGFILE"
echo "🎭 Tone: $TONE" | tee -a "$LOGFILE"
echo "📱 Platforms: $PLATFORMS" | tee -a "$LOGFILE"

# ── Run content generator ──────────────────────────────────────
echo "" | tee -a "$LOGFILE"
echo "⚙️  Running universal content generator..." | tee -a "$LOGFILE"

python3 "$SCRIPT_DIR/generate_content.py" \
    --provider auto \
    --topic "$TOPIC" \
    --platforms $PLATFORMS \
    --tone "$TONE" \
    --brand "$BRAND" \
    --campaign-file "$CAMPAIGN_FILE" 2>&1 | tee -a "$LOGFILE"

echo "" | tee -a "$LOGFILE"
echo "✅ Daily run complete. Check $OUTPUT_DIR for today's content." | tee -a "$LOGFILE"
echo "============================================" | tee -a "$LOGFILE"
