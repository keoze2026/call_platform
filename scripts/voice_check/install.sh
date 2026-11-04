#!/usr/bin/env bash
# CH-531: install the voice check on the host that holds the recordings.
# Run once, as root, on Server B:   bash /opt/call_platform/scripts/voice_check/install.sh
# Installs into /opt/avx-voice only (its own Python environment): nothing in
# the app, the containers or Asterisk changes, and no call is touched.
set -euo pipefail
DIR=/opt/avx-voice
MODEL_URL=https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx
MODEL_SHA256=2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f

command -v ffmpeg >/dev/null || apt-get install -y ffmpeg
mkdir -p "$DIR"
if [ ! -x "$DIR/venv/bin/pip" ]; then
  rm -rf "$DIR/venv"
  # Ubuntu needs python3-venv for this; installed only if missing.
  python3 -m venv "$DIR/venv" 2>/dev/null || { rm -rf "$DIR/venv"; apt-get install -y python3-venv; python3 -m venv "$DIR/venv"; }
fi
"$DIR/venv/bin/pip" install --quiet --upgrade pip
"$DIR/venv/bin/pip" install --quiet 'numpy<3' 'onnxruntime>=1.17,<2'

if ! echo "$MODEL_SHA256  $DIR/silero_vad.onnx" | sha256sum -c --status 2>/dev/null; then
  curl -fsSL -o "$DIR/silero_vad.onnx.part" "$MODEL_URL"
  echo "$MODEL_SHA256  $DIR/silero_vad.onnx.part" | sha256sum -c --status \
    || { echo "model checksum does not match - not installed"; rm -f "$DIR/silero_vad.onnx.part"; exit 1; }
  mv "$DIR/silero_vad.onnx.part" "$DIR/silero_vad.onnx"
fi
"$DIR/venv/bin/python" -c 'import onnxruntime, numpy; print("onnxruntime", onnxruntime.__version__, "numpy", numpy.__version__)'
echo "model ok: $DIR/silero_vad.onnx"
echo
echo "Next (read-only test, saves nothing):"
echo "  cd /opt/call_platform && $DIR/venv/bin/python scripts/voice_check/voice_check.py --dry-run --limit 20"
