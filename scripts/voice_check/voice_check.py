#!/usr/bin/env python3
"""CH-531: listen to every answered call's recording and say if anyone spoke.

CH-543 (Sir, 4 Nov - AI audit upgrade): where Server A records the caller and
the agent apart (deploy/split_channels.py: recordings/split/<id>-caller.wav and
<id>-agent.wav), each side is listened to separately - speech seconds on each
side, when the caller first spoke, beeping on the caller's side, and whether
the files hold any audio data at all. voice_results turns that into Sir's
labels (routing/ai_labels.py). Calls without the split files are checked as
one mixed recording, as before.

CH-547 ("Pending AI check" should clear at once): cron starts it every minute
with --loop 55 - it keeps looking for 55 s, takes each call MIN_AGE_SECONDS (5)
after it ends and saves its result straight away:
    * * * * * cd /opt/call_platform && BATCH=20 /opt/avx-voice/venv/bin/python scripts/voice_check/voice_check.py --loop 55 >> /var/log/voice_check.log 2>&1

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
    cron: * * * * * cd /opt/call_platform && /opt/avx-voice/venv/bin/python scripts/voice_check/voice_check.py >> /var/log/voice_check.log 2>&1

Settings (environment): RECORDINGS_DIR (/var/spool/asterisk/recordings),
APP_DIR (/opt/call_platform), VOICE_MODEL (/opt/avx-voice/silero_vad.onnx),
BATCH (200 calls a run), MIN_AGE_MIN (1), DAYS (2), SAVE_EVERY (10),
RETRY_MINUTES (15).

Prints one line per call ("[12/200] <call id>: audio 42.0s, speech 18.3s")
and saves the results every SAVE_EVERY calls (CH-534).

CH-536: runs every minute and checks a call 1 minute after it ends, so the
portal shows Voice / No voice about 2 minutes after the call, not 10-15.
For an answered call it listens only to the last <talk time + 1 s> of the
recording - the conversation - so the ringing recorded before the answer
(CH-535) can never count as someone speaking.
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
MIN_AGE_MIN = int(os.environ.get('MIN_AGE_MIN', '1'))
# CH-547 (Sir: "Pending AI check" should clear at once): in --loop mode a call
# is picked up this many seconds after it ends (the recording is complete at
# hang-up), and the next look comes LOOP_PAUSE seconds after an empty one.
MIN_AGE_SECONDS = int(os.environ.get('MIN_AGE_SECONDS', '5'))
LOOP_PAUSE = float(os.environ.get('LOOP_PAUSE', '3'))
DAYS = int(os.environ.get('DAYS', '2'))
SAVE_EVERY = int(os.environ.get('SAVE_EVERY', '10'))
# CH-536: a call checked a minute after it ends whose file cannot be read yet is
# left unchecked and tried again each run; only after this many minutes is it
# saved as "missing" (No file).
RETRY_MINUTES = int(os.environ.get('RETRY_MINUTES', '15'))

SR = 16000
CHUNK = 512          # Silero's window at 16 kHz: 32 ms
CONTEXT = 64         # samples of the previous window the model is given
THRESHOLD = 0.5      # speech probability per window
MIN_RUN = 8          # a sound must last 8 windows (~0.25 s) in a row to count as speech
# CH-540 beeps (Sir: "No Voice | Only Agent and pop-up beeping sound"): a window
# with no speech whose sound is one clear tone - most of its power in one
# narrow band between 300 and 3400 Hz, and loud enough to hear. A beep is such
# a tone lasting 0.1 to 2 s; a longer steady tone (a hum) is not counted.
BEEP_MIN_RMS = 0.01          # about -40 dBFS
BEEP_TONAL = 0.6             # share of the window's power in the strongest band
BEEP_MIN_RUN, BEEP_MAX_RUN = 3, 62      # windows: 0.1 s .. 2 s
# CH-594 echo (Sir, 5 Nov: "No voice AI is not working correctly ... I need
# 100% accuracy"): the caller's line often carries the AGENT's own voice back
# (line or speakerphone echo), a little later and quieter. Silero hears
# speech there, so an agent-only call read "caller speaks" - Valid. A caller
# window is echo when the agent spoke in the ECHO_LAG s before it (or just
# after, for jitter) and the caller side is under ECHO_RATIO of the agent's
# loudness then. A real caller talks louder than an echo, and mostly when the
# agent is quiet, so his speech still counts.
ECHO_LAG = float(os.environ.get('ECHO_LAG', '0.5'))          # seconds
ECHO_RATIO = float(os.environ.get('ECHO_RATIO', '0.35'))     # about -9 dB


# CH-553: PANNs (scripts/voice_check/panns_sound.py) is asked only about a
# side where Silero heard less speech than this - is it music (IVR / hold
# music) or machine tones? The label itself is decided on the server.
SPEECH_GATE = float(os.environ.get('VOICE_MIN_SECONDS', '1.0'))


def sound_of(pcm16):
    """{'music', 'tone', 'speech', 'seconds'} from PANNs, or None when it is
    not installed (the AI audit then works as before)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import panns_sound
    except Exception:
        return None
    try:
        return panns_sound.sound(pcm16, SR)
    except Exception as e:
        log(f'PANNs failed: {e}')
        return None


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
        """(seconds of speech, seconds of audio) in 16 kHz mono int16 audio."""
        speech, audio, _ = self.listen(pcm16)
        return speech, audio

    def listen(self, pcm16):
        """(speech s, audio s, beep s) in 16 kHz mono int16 audio (CH-540)."""
        r = self.side(pcm16)
        return r['speech'], r['audio'], r['beep']

    def side(self, pcm16):
        """One side of a call (or the mixed file), CH-543: seconds of speech,
        of audio and of beeping, when speech first started (s from the start
        of pcm16, None if never), and whether the file holds no audio data at
        all (digital silence - a recording fault, not a quiet line).
        CH-594: also each window's speech probability and loudness ('probs',
        'rms'), for the echo check of a split call (never sent on)."""
        np = self.np
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, CONTEXT), dtype=np.float32)
        sr = np.array(SR, dtype=np.int64)
        windows, run, first = 0, 0, None
        probs = []
        for i, start in enumerate(range(0, len(audio) - CHUNK + 1, CHUNK)):
            x = np.concatenate([context, audio[start:start + CHUNK].reshape(1, CHUNK)], axis=1)
            out, state = self.session.run(None, {'input': x, 'state': state, 'sr': sr})
            context = x[:, -CONTEXT:]
            probs.append(float(out[0][0]))
            if float(out[0][0]) >= THRESHOLD:
                run += 1
                if run == MIN_RUN and first is None:
                    first = round((i - MIN_RUN + 1) * CHUNK / SR, 2)
            else:
                windows += run if run >= MIN_RUN else 0
                run = 0
        windows += run if run >= MIN_RUN else 0
        no_data = bool(len(audio) == 0 or float(np.max(np.abs(audio))) < 2.0 / 32768)
        n = len(probs)
        rms = (np.sqrt(np.mean(audio[:n * CHUNK].reshape(n, CHUNK) ** 2, axis=1)) if n
               else np.zeros(0, dtype=np.float32))
        return {'speech': round(windows * CHUNK / SR, 2), 'audio': round(len(audio) / SR, 2),
                'beep': beep_seconds(np, audio, probs), 'first': first, 'no_data': no_data,
                'probs': probs, 'rms': rms}


def echo_free(np, caller_probs, caller_rms, agent_probs, agent_rms):
    """CH-594: the caller's speech with the agent's echo taken out.

    Both sides are the talk part of the same call, cut from the END of each
    file (talk_part), so they are lined up from the end. Returns (speech s,
    first speech s from the start of the caller's part or None, echo s)."""
    nc, na = len(caller_probs), len(agent_probs)
    n = min(nc, na)
    cp = np.asarray(caller_probs, dtype=np.float32)
    cr = np.asarray(caller_rms, dtype=np.float32)
    caller_speech = cp >= THRESHOLD
    echo = np.zeros(nc, dtype=bool)
    if n:
        ap = np.asarray(agent_probs, dtype=np.float32)[na - n:]
        ar = np.asarray(agent_rms, dtype=np.float32)[na - n:]
        talking = np.where(ap >= THRESHOLD, ar, 0.0)          # the agent's loudness while he speaks
        lag = max(1, int(round(ECHO_LAG * SR / CHUNK)))
        lead = 2                                              # ~60 ms of network jitter
        padded = np.concatenate([np.zeros(lag, dtype=np.float32), talking, np.zeros(lead, dtype=np.float32)])
        # loudest agent speech from `lag` windows before to `lead` after each window
        near = np.max(np.stack([padded[k:k + n] for k in range(lag + lead + 1)]), axis=0)
        off = nc - n
        echo[off:] = caller_speech[off:] & (near > 0) & (cr[off:] < ECHO_RATIO * near)
    clean = caller_speech & ~echo
    windows, run, first = 0, 0, None
    for i, flag in enumerate(list(clean) + [False]):
        if flag:
            run += 1
            if run == MIN_RUN and first is None:
                first = round((i - MIN_RUN + 1) * CHUNK / SR, 2)
            continue
        windows += run if run >= MIN_RUN else 0
        run = 0
    return (round(windows * CHUNK / SR, 2), first,
            round(int(echo.sum()) * CHUNK / SR, 2))


def beep_seconds(np, audio, probs):
    """Seconds of beeping in float audio (16 kHz), given the speech
    probability of each CHUNK window: tonal, loud enough, not speech, in runs
    of 0.1 to 2 s."""
    if not probs:
        return 0.0
    n = len(probs)
    frames = audio[:n * CHUNK].reshape(n, CHUNK)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    spec = np.abs(np.fft.rfft(frames * np.hanning(CHUNK), axis=1)) ** 2
    freqs = np.fft.rfftfreq(CHUNK, 1.0 / SR)
    band = (freqs >= 300) & (freqs <= 3400)
    total = spec.sum(axis=1) + 1e-12
    inband = spec[:, band]
    peak = inband.argmax(axis=1)
    # the strongest bin and its two neighbours (a tone spreads over ~3 bins)
    padded = np.pad(inband, ((0, 0), (1, 1)))
    rows = np.arange(n)
    near = padded[rows, peak] + padded[rows, peak + 1] + padded[rows, peak + 2]
    tonal = (near / total >= BEEP_TONAL) & (rms >= BEEP_MIN_RMS) & (np.asarray(probs) < THRESHOLD)
    beeps, run = 0, 0
    for flag in list(tonal) + [False]:
        if flag:
            run += 1
            continue
        if BEEP_MIN_RUN <= run <= BEEP_MAX_RUN:
            beeps += run
        run = 0
    return round(beeps * CHUNK / SR, 2)


def talk_part(pcm16, talk):
    """CH-536: the conversation only - the last talk+1 seconds of an answered
    call's recording. Anything before it is the ringing (CH-535)."""
    if talk is None:
        return pcm16
    keep = (int(talk) + 1) * SR * 2          # int16 = 2 bytes a sample
    return pcm16[-keep:] if len(pcm16) > keep else pcm16


def missing_for_good(item):
    """CH-536: no readable file. Saved as "missing" only once the call ended
    RETRY_MINUTES ago (or its age is unknown); before that it is tried again."""
    age = item.get('age')
    return age is None or int(age) >= RETRY_MINUTES


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


def fetch(url, call_id, what):
    """A recording by link into a temporary file, or None."""
    if not url.startswith(('http://', 'https://')):
        return None
    suffix = os.path.splitext(url.split('?')[0])[1] or '.wav'
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
        return tmp
    except Exception as e:
        # CH-546: always say why - a split file that cannot be fetched is why
        # a call falls back to one channel.
        log(f'{call_id}: {what} download failed: {e}')
        os.unlink(tmp)
        return None


def split_sides(vad, item):
    """CH-543: caller and agent apart, from the two files Server A writes
    (deploy/split_channels.py), or None when they are not there."""
    split = item.get('split') or {}
    paths = {}
    try:
        for who in ('caller', 'agent'):
            local = os.path.join(RECORDINGS_DIR, 'split', f"{item['id']}-{who}.wav")
            paths[who] = (local, False) if os.path.isfile(local) else (fetch(split.get(who) or '', item['id'], who), True)
            if not paths[who][0]:
                return None
        pcm = {who: decode(paths[who][0]) for who in paths}
        if not pcm['caller'] or not pcm['agent']:
            log(f"{item['id']}: split files could not be decoded (caller "
                f"{'ok' if pcm['caller'] else 'unreadable'}, agent {'ok' if pcm['agent'] else 'unreadable'})")
            return None
        talk = item.get('talk')
        caller_pcm, agent_pcm = talk_part(pcm['caller'], talk), talk_part(pcm['agent'], talk)
        caller = vad.side(caller_pcm)
        agent = vad.side(agent_pcm)
        # CH-594: the agent's echo on the caller's line is not the caller.
        raw_speech, echo = caller['speech'], 0.0
        if all(k in side for side in (caller, agent) for k in ('probs', 'rms')):
            import numpy
            caller['speech'], caller['first'], echo = echo_free(
                numpy, caller['probs'], caller['rms'], agent['probs'], agent['rms'])
        # CH-553: no speech on a side - music or machine tones there?
        caller_sound = sound_of(caller_pcm) if caller['speech'] < SPEECH_GATE else None
        agent_sound = sound_of(agent_pcm) if (caller['speech'] < SPEECH_GATE
                                               and agent['speech'] < SPEECH_GATE) else None
    finally:
        for path, temporary in paths.values():
            if temporary and path:
                os.unlink(path)
    # talk_part keeps one second before the answer: first speech from the answer.
    first = caller['first']
    if first is not None and talk is not None:
        first = round(max(0.0, first - 1.0), 2)
    return {
        'id': item['id'], 'channels': 2,
        'speech': round(caller['speech'] + agent['speech'], 2),
        'audio': round(max(len(pcm['caller']), len(pcm['agent'])) / 2 / SR, 2),
        'caller_speech': caller['speech'], 'agent_speech': agent['speech'],
        # CH-594: what Silero heard on the caller's line, and how much of it
        # was the agent's echo (taken out of caller_speech).
        'caller_speech_raw': raw_speech, 'caller_echo': echo,
        'caller_first': first, 'beep': caller['beep'],
        'no_data': caller['no_data'] and agent['no_data'],
        **({'caller_music': caller_sound['music'], 'caller_tone': caller_sound['tone'],
            'caller_sound_s': caller_sound['seconds']} if caller_sound else {}),
        **({'agent_music': agent_sound['music'], 'agent_sound_s': agent_sound['seconds']} if agent_sound else {}),
    }


def check_files(paths):
    """Local test: voice_check.py --file a.mp3 b.wav"""
    vad = Vad(MODEL)
    for path in paths:
        pcm = decode(path)
        if pcm is None:
            print(f'{path}: could not decode')
            continue
        speech, audio, beep = vad.listen(pcm)
        print(f'{path}: audio {audio:.1f}s, speech {speech:.1f}s, beep {beep:.1f}s')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='check, print, save nothing')
    ap.add_argument('--limit', type=int, default=BATCH)
    ap.add_argument('--retry-missing', action='store_true')
    ap.add_argument('--file', nargs='+', help='just measure these files and exit')
    # CH-553: check calls that already have this AI label again (e.g.
    # dead_caller, once PANNs is installed: IVR calls become Voicemail / IVR).
    ap.add_argument('--recheck', metavar='AI_LABEL', default='')
    ap.add_argument('--days', type=int, default=DAYS)
    ap.add_argument('--loop', type=int, default=0,
                    help='keep checking for this many seconds (CH-547: cron every minute with --loop 55)')
    o = ap.parse_args()
    if o.file:
        check_files(o.file)
        return 0

    # A re-check has its own lock: it never waits for, or blocks, the
    # every-minute check of new calls.
    lock = open('/tmp/voice_recheck.lock' if o.recheck else '/tmp/voice_check.lock', 'w')
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

    vad = None
    if not o.loop or o.recheck:
        vad = Vad(MODEL)
        return one_pass(o, vad, f'--min-age {MIN_AGE_MIN}')[1]
    # CH-547: keep looking for o.loop seconds - a call is checked a few
    # seconds after it ends instead of up to two minutes later.
    deadline, failed = time.time() + o.loop, 0
    vad = Vad(MODEL)
    while time.time() < deadline:
        done, rc = one_pass(o, vad, f'--min-age-seconds {MIN_AGE_SECONDS}')
        failed = failed or rc
        if not done:
            time.sleep(LOOP_PAUSE)
    return failed


def one_pass(o, vad, age_arg):
    """Check the waiting calls once. Returns (calls handled, exit code)."""
    args = ['voice_pending', '--limit', str(o.limit), '--days', str(o.days)] + age_arg.split()
    if o.retry_missing:
        args.append('--retry-missing')
    if o.recheck:
        args = ['voice_pending', '--limit', str(o.limit), '--days', str(o.days), '--ai-label', o.recheck]
    p = manage(args)
    if p.returncode != 0:
        log(f'voice_pending failed: {p.stderr.strip()[-300:]}')
        return 0, 1
    items = []
    for line in p.stdout.splitlines():
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    if not items:
        return 0, 0

    log(f'{len(items)} calls to check' + (' (dry run, nothing saved)' if o.dry_run else ''))
    pending, done, started, failed = [], 0, time.time(), False
    # CH-547: a few new calls are saved one by one, so each label shows at
    # once; a backlog is saved in tens (CH-534).
    save_every = 1 if (o.loop and len(items) <= 5) else SAVE_EVERY

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
        # CH-543: caller and agent apart when Server A recorded them so.
        result = split_sides(vad, item)
        if result is not None:
            done += 1
            log(f"[{done}/{len(items)}] {item['id']}: caller speech {result['caller_speech']:.1f}s"
                + (f" (+{result['caller_echo']:.1f}s agent echo removed)" if result.get('caller_echo') else '')
                + f", agent speech {result['agent_speech']:.1f}s, caller beep {result['beep']:.1f}s"
                + (f", caller music {result['caller_music']}s" if 'caller_music' in result else '')
                + (f", agent music {result['agent_music']}s" if 'agent_music' in result else '')
                + (', NO AUDIO DATA' if result['no_data'] else ''))
            if not o.dry_run:
                pending.append(result)
                if len(pending) >= save_every:
                    failed = not save(pending) or failed
                    pending = []
            continue
        path, temporary = find_file(item)
        try:
            pcm = decode(path) if path else None
            if pcm is None and not missing_for_good(item):
                result = None
                line = 'file not readable yet - tried again next run'
            elif pcm is None:
                result = {'id': item['id'], 'missing': True}
                line = 'no readable recording'
            else:
                audio = round(len(pcm) / 2 / SR, 2)
                part = talk_part(pcm, item.get('talk'))
                one = vad.side(part)
                result = {'id': item['id'], 'speech': one['speech'], 'audio': audio, 'beep': one['beep'],
                          'channels': 1, 'no_data': one['no_data']}
                line = f"audio {audio:.1f}s, speech {one['speech']:.1f}s, beep {one['beep']:.1f}s (one channel)"
                # CH-553: nobody spoke - music (IVR) or machine tones?
                snd = sound_of(part) if one['speech'] < SPEECH_GATE and not one['no_data'] else None
                if snd:
                    result.update({'music': snd['music'], 'tone': snd['tone'], 'sound_s': snd['seconds']})
                    line += f", music {snd['music']}s, tone {snd['tone']}s"
        finally:
            if temporary and path:
                os.unlink(path)
        done += 1
        # One line per call: you can see it working, call by call.
        log(f"[{done}/{len(items)}] {item['id']}: {line}")
        if o.dry_run or result is None:
            continue
        pending.append(result)
        if len(pending) >= save_every:
            failed = not save(pending) or failed
            pending = []
    if not o.dry_run and pending:
        failed = not save(pending) or failed
    log(f'{done} calls in {time.time() - started:.0f}s' + (' - dry run, nothing saved' if o.dry_run else ''))
    return done, 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
