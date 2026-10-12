#!/bin/bash
# ==============================================================================
# Digital Marketing Agent — Local LLM Automation Runner
# Designed for LM Studio, Ollama, and Hermes local models (e.g. 8GB RAM setups)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/output"
LOG_DIR="$ROOT_DIR/logs"
DATE=$(date +%Y-%m-%d)
LOGFILE="$LOG_DIR/local_run_$DATE.log"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR"

echo "============================================" | tee -a "$LOGFILE"
echo "🤖 Digital Marketing Agent — Local Model Run" | tee -a "$LOGFILE"
echo "📅 Date: $DATE | Time: $(date +%H:%M:%S)" | tee -a "$LOGFILE"
echo "============================================" | tee -a "$LOGFILE"

# Determine endpoint: LM Studio (1234) or Ollama (11434)
PROVIDER="lmstudio"
URL="http://localhost:1234/v1"

if curl -s --max-time 1 "http://localhost:1234/v1/models" >/dev/null 2>&1; then
    PROVIDER="lmstudio"
    URL="http://localhost:1234/v1"
    echo "✅ Detected active LM Studio instance at $URL" | tee -a "$LOGFILE"
elif curl -s --max-time 1 "http://localhost:11434/api/tags" >/dev/null 2>&1; then
    PROVIDER="ollama"
    URL="http://localhost:11434/v1"
    echo "✅ Detected active Ollama instance at $URL" | tee -a "$LOGFILE"
else
    echo "⚠️ Warning: Neither LM Studio (1234) nor Ollama (11434) responded to ping." | tee -a "$LOGFILE"
    echo "Will attempt default connection to $URL..." | tee -a "$LOGFILE"
fi

CAMPAIGN_FILE="$ROOT_DIR/campaign.json"
if [ ! -f "$CAMPAIGN_FILE" ]; then
    echo "Creating default campaign.json..."
    cat > "$CAMPAIGN_FILE" <<EOF
{
    "brand_name": "My Brand",
    "daily_topic": "5 ways AI transforms daily marketing workflows",
    "tone": "professional",
    "platforms": ["instagram", "facebook", "linkedin"],
    "active": true
}
EOF
fi

python3 "$SCRIPT_DIR/generate_content.py" \
    --provider "$PROVIDER" \
    --url "$URL" \
    --campaign-file "$CAMPAIGN_FILE" 2>&1 | tee -a "$LOGFILE"

echo "============================================" | tee -a "$LOGFILE"
echo "🏁 Run finished. Check $OUTPUT_DIR" | tee -a "$LOGFILE"
