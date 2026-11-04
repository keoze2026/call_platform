#!/usr/bin/env python3
"""CH-531: listen to every answered call's recording and say if anyone spoke.

Sir, 4 Nov: "We have audit members who spend lots of time auditing no voice
calls ... add AI to audit calls - just for us, not for consumer."

Runs on the HOST that holds the recordings (like recordings_to_mp3.sh), every
5 minutes from cron, at low priority. It changes no recording and does not
touch Asterisk, the containers or the calls.

  1. asks the app which answered calls have not been checked yet
     (manage.py voice_pending)
  2. reads each recording: the file on this disk (RECORDINGS_DIR/<id>.mp3 or
     .wav), or else its link (B2 / nginx) into a temporary file
  3. decodes it with ffmpeg to 16 kHz mono and runs Silero VAD - an open
     neural voice-activity model, on this server, no outside service, no cost
     - over every 32 ms of the call: seconds of speech heard
  4. sends the results back (manage.py voice_results), which labels each
     call "voice" or "no_voice" (under VOICE_MIN_SECONDS of speech: nobody
     spoke, or nothing the model could hear) or "missing" (no readable file)

Install: scripts/voice_check/install.sh (once, as root). Then:
    /opt/avx-voice/venv/bin/python /opt/call_platform/scripts/voice_check/voice_check.py --dry-run
    cron: */5 * * * * /opt/avx-voice/venv/bin/python /opt/call_platform/scripts/voice_check/voice_check.py >> /var/log/voice_check.log 2>&1

Settings (environment): RECORDINGS_DIR (/var/spool/asterisk/recordings),
APP_DIR (/opt/call_platform), VOICE_MODEL (/opt/avx-voice/silero_vad.onnx),
BATCH (200 calls a run), MIN_AGE_MIN (10), DAYS (2), SAVE_EVERY (10).

Prints one line per call ("[12/200] <call id>: audio 42.0s, speech 18.3s")
and saves the results every SAVE_EVERY calls (CH-534).
"""
import argparse
import fcntl
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request

RECORDINGS_DIR = os.environ.get('RECORDINGS_DIR', '/var/spool/asterisk/recordings')
APP_DIR = os.environ.get('APP_DIR', '/opt/call_platform')
MODEL = os.environ.get('VOICE_MODEL', '/opt/avx-voice/silero_vad.onnx')
MANAGE = os.environ.get('MANAGE', 'docker compose exec -T web python manage.py')
BATCH = int(os.environ.get('BATCH', '200'))
MIN_AGE_MIN = int(os.environ.get('MIN_AGE_MIN', '10'))
DAYS = int(os.environ.get('DAYS', '2'))
SAVE_EVERY = int(os.environ.get('SAVE_EVERY', '10'))

SR = 16000
CHUNK = 512          # Silero's window at 16 kHz: 32 ms
CONTEXT = 64         # samples of the previous window the model is given
THRESHOLD = 0.5      # speech probability per window
MIN_RUN = 8          # a sound must last 8 windows (~0.25 s) in a row to count as speech


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


class Vad:
    """Silero VAD v5 (ONNX), the same way the model's own OnnxWrapper runs it."""

    def __init__(self, path):
        import numpy as np
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.np = np
        self.session = ort.InferenceSession(path, sess_options=opts, providers=['CPUExecutionProvider'])

    def speech_seconds(self, pcm16):
        """Seconds of speech in 16 kHz mono int16 audio."""
        np = self.np
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, CONTEXT), dtype=np.float32)
        sr = np.array(SR, dtype=np.int64)
        windows, run = 0, 0
        for start in range(0, len(audio) - CHUNK + 1, CHUNK):
            x = np.concatenate([context, audio[start:start + CHUNK].reshape(1, CHUNK)], axis=1)
            out, state = self.session.run(None, {'input': x, 'state': state, 'sr': sr})
            context = x[:, -CONTEXT:]
            if float(out[0][0]) >= THRESHOLD:
                run += 1
            else:
                windows += run if run >= MIN_RUN else 0
                run = 0
        windows += run if run >= MIN_RUN else 0
        return round(windows * CHUNK / SR, 2), round(len(audio) / SR, 2)


def decode(path):
    """Any recording -> 16 kHz mono int16, or None."""
    p = subprocess.run(['nice', '-n', '19', 'ffmpeg', '-nostdin', '-v', 'error', '-i', path,
                        '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    return p.stdout if p.returncode == 0 and p.stdout else None


def manage(args, stdin=None):
    cmd = shlex.split(MANAGE) + args
    return subprocess.run(cmd, cwd=APP_DIR, input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=600)


def find_file(item):
    call_id = item['id']
    names = [item.get('file') or '', f'{call_id}.mp3', f'{call_id}.wav']
    for name in names:
        if name and '/' not in name:
            path = os.path.join(RECORDINGS_DIR, name)
            if os.path.isfile(path):
                return path, False
    url = item.get('url') or ''
    if url.startswith(('http://', 'https://')):
        suffix = os.path.splitext(url.split('?')[0])[1] or '.mp3'
        fd, tmp = tempfile.mkstemp(prefix='voice-', suffix=suffix)
        os.close(fd)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'avortyx-voice-check'})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
                while True:
                    block = r.read(1 << 16)
                    if not block:
                        break
                    f.write(block)
            return tmp, True
        except Exception as e:
            log(f'{call_id}: download failed: {e}')
            os.unlink(tmp)
    return None, False


def check_files(paths):
    """Local test: voice_check.py --file a.mp3 b.wav"""
    vad = Vad(MODEL)
    for path in paths:
        pcm = decode(path)
        if pcm is None:
            print(f'{path}: could not decode')
            continue
        speech, audio = vad.speech_seconds(pcm)
        print(f'{path}: audio {audio:.1f}s, speech {speech:.1f}s')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='check, print, save nothing')
    ap.add_argument('--limit', type=int, default=BATCH)
    ap.add_argument('--retry-missing', action='store_true')
    ap.add_argument('--file', nargs='+', help='just measure these files and exit')
    o = ap.parse_args()
    if o.file:
        check_files(o.file)
        return 0

    lock = open('/tmp/voice_check.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('previous run still going, skipped')
        return 0
    try:
        os.nice(19)
    except OSError:
        pass
    if not os.path.isfile(MODEL):
        log(f'model not found: {MODEL} - run scripts/voice_check/install.sh')
        return 1

    args = ['voice_pending', '--limit', str(o.limit), '--days', str(DAYS), '--min-age', str(MIN_AGE_MIN)]
    if o.retry_missing:
        args.append('--retry-missing')
    p = manage(args)
    if p.returncode != 0:
        log(f'voice_pending failed: {p.stderr.strip()[-300:]}')
        return 1
    items = []
    for line in p.stdout.splitlines():
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    if not items:
        return 0

    vad = Vad(MODEL)
    log(f'{len(items)} calls to check' + (' (dry run, nothing saved)' if o.dry_run else ''))
    pending, done, started, failed = [], 0, time.time(), False

    def save(batch):
        # CH-534: saved every SAVE_EVERY calls, so results appear while a
        # long first run is still going and a stopped run keeps its work.
        p = manage(['voice_results'], stdin='\n'.join(json.dumps(r) for r in batch) + '\n')
        if p.returncode != 0:
            log(f'voice_results failed: {p.stderr.strip()[-300:]}')
            return False
        log(f'saved {len(batch)}: {p.stdout.strip()}')
        return True

    for item in items:
        path, temporary = find_file(item)
        try:
            pcm = decode(path) if path else None
            if pcm is None:
                result = {'id': item['id'], 'missing': True}
                line = 'no readable recording'
            else:
                speech, audio = vad.speech_seconds(pcm)
                result = {'id': item['id'], 'speech': speech, 'audio': audio}
                line = f'audio {audio:.1f}s, speech {speech:.1f}s'
        finally:
            if temporary and path:
                os.unlink(path)
        done += 1
        # One line per call: you can see it working, call by call.
        log(f"[{done}/{len(items)}] {item['id']}: {line}")
        if o.dry_run:
            continue
        pending.append(result)
        if len(pending) >= SAVE_EVERY:
            failed = not save(pending) or failed
            pending = []
    if not o.dry_run and pending:
        failed = not save(pending) or failed
    log(f'{done} calls in {time.time() - started:.0f}s' + (' - dry run, nothing saved' if o.dry_run else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
