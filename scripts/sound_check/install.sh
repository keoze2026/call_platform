#!/usr/bin/env bash
# CH-552: PANNs (sound kinds - music, ringing, tones, beeps) for the AI audit,
# in the voice check's own Python (/opt/avx-voice/venv) on Server B.
# Run once, as root:   bash /opt/call_platform/scripts/sound_check/install.sh
# CPU-only PyTorch (no graphics card needed), the PANNs package and its model
# (Cnn14, ~330 MB, from the authors' Zenodo record). Nothing in the app, the
# containers or Asterisk changes; nothing on Server A.
set -euo pipefail
VENV=/opt/avx-voice/venv
[ -x "$VENV/bin/python" ] || { echo "run scripts/voice_check/install.sh first"; exit 1; }
command -v wget >/dev/null || apt-get install -y wget        # panns_inference downloads with wget
"$VENV/bin/pip" install --quiet --index-url https://download.pytorch.org/whl/cpu torch
"$VENV/bin/pip" install --quiet panns-inference
# First load downloads the label list and the model into /root/panns_data.
nice -n 19 "$VENV/bin/python" - <<'PY'
import os, torch
torch.set_num_threads(2)
from panns_inference import AudioTagging, labels
ckpt = os.path.expanduser('~/panns_data/Cnn14_mAP=0.431.pth')
AudioTagging(checkpoint_path=ckpt, device='cpu')
size = os.path.getsize(ckpt)
assert size > 3e8, f'model file too small ({size} bytes) - download failed'
print('torch', torch.__version__, '| PANNs labels', len(labels), '| model', round(size / 1e6), 'MB')
PY
echo "ok"
