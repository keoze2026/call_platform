#!/usr/bin/env bash
# CH-549: what the speech-to-text test needs on Server B, in the voice check's
# own Python (/opt/avx-voice/venv). Nothing in the app or containers changes.
# Run once, as root:   bash /opt/call_platform/scripts/ai_compare/install.sh
set -euo pipefail
VENV=/opt/avx-voice/venv
[ -x "$VENV/bin/python" ] || { echo "run scripts/voice_check/install.sh first"; exit 1; }
"$VENV/bin/pip" install --quiet 'websockets>=14,<17'
"$VENV/bin/python" -c 'import websockets; print("websockets", websockets.__version__)'
mkdir -p /opt/ai-compare && chmod 700 /opt/ai-compare
echo "ok"
