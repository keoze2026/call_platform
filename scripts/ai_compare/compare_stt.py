#!/usr/bin/env python3
"""CH-549: the speech-to-text test Sir asked for, on Server B.

Sir, 4 Nov: "Live transcription: run Deepgram (Nova-3) and AssemblyAI
(Universal-Streaming) on the same 200 of our recordings, both channels.
Compare accuracy on caller speech, latency and cost per call, and send me the
results. We go with the winner."

Each side of each call (caller, agent) is streamed to both providers the way a
live call would be: 16 kHz mono PCM in 100 ms pieces, at real-time pace, over
an encrypted connection (wss://). Silero (the model the AI audit already uses,
scripts/voice_check) measures the seconds of speech on each side as the
yardstick for "did the caller speak".

    run      stream every call to both providers (resumable: what is done is
             skipped; Ctrl+C and run again continues)
    sheet    50 calls for the audit team: caller audio (mp3) + reference.csv,
             where they type what the caller actually said
    report   the results: caller speech found / missed / made up, word error
             rate against the audit team's reference, latency, cost per call

Files: /opt/ai-compare/split/<id>-caller.wav and <id>-agent.wav (from
collect_on_a.sh + fetch_on_b.sh), results in /opt/ai-compare/results.
Keys: DEEPGRAM_API_KEY and ASSEMBLYAI_API_KEY in /opt/call_platform/.env -
read here, never printed or written anywhere.

Privacy: Deepgram gets mip_opt_out=true on every request (not used for their
model improvement); AssemblyAI keeps nothing from streaming once the account
is opted out of model training (an account setting - request it first).

Run with the voice check's Python (it already has Silero):
    /opt/avx-voice/venv/bin/python compare_stt.py run
"""
import argparse
import asyncio
import csv
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
import zipfile

BASE = os.environ.get('AI_COMPARE_DIR', '/opt/ai-compare')
ENV_FILE = os.environ.get('ENV_FILE', '/opt/call_platform/.env')
HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000
CHUNK_MS = 100
CHUNK_BYTES = SR * 2 * CHUNK_MS // 1000          # 16-bit mono
SPEECH_MIN = 1.0                                 # s of speech = "spoke" (VOICE_MIN_SECONDS)
# List prices (USD) - check them against each console's Usage page before
# quoting a cost: AssemblyAI bills streaming by the time the connection is
# open; Deepgram by the audio streamed.
AAI_USD_PER_HOUR = float(os.environ.get('AAI_USD_PER_HOUR', '0.15'))
DG_USD_PER_MIN = float(os.environ.get('DG_USD_PER_MIN', '0.0077'))
DG_URL = os.environ.get('DG_URL', 'wss://api.deepgram.com/v1/listen')
AAI_URL = os.environ.get('AAI_URL', 'wss://streaming.assemblyai.com/v3/ws')
PROVIDERS = ('deepgram', 'assemblyai')


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def read_keys(path=ENV_FILE):
    keys = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if k.strip() in ('DEEPGRAM_API_KEY', 'ASSEMBLYAI_API_KEY'):
                    keys[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in ('DEEPGRAM_API_KEY', 'ASSEMBLYAI_API_KEY'):
        keys.setdefault(k, os.environ.get(k, ''))
    return keys


def calls(split_dir):
    """Call ids that have both sides, sorted."""
    names = os.listdir(split_dir) if os.path.isdir(split_dir) else []
    ids = {n[:-len('-caller.wav')] for n in names if n.endswith('-caller.wav')}
    return sorted(i for i in ids if f'{i}-agent.wav' in names)


def decode(path):
    """Any recording -> 16 kHz mono int16 PCM, or None."""
    p = subprocess.run(['nice', '-n', '10', 'ffmpeg', '-nostdin', '-v', 'error', '-i', path,
                        '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    return p.stdout if p.returncode == 0 and p.stdout else None


async def connect(url, headers):
    import websockets
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=None, open_timeout=20)
    except TypeError:                                   # websockets < 14
        return await websockets.connect(url, extra_headers=headers, max_size=None, open_timeout=20)


async def send_realtime(ws, pcm, t0, speed):
    """PCM in CHUNK_MS pieces, each sent when its audio would have been
    spoken (t0 = when the first piece went)."""
    for i in range(0, len(pcm), CHUNK_BYTES):
        due = t0 + (i / (SR * 2)) / speed
        wait = due - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        await ws.send(pcm[i:i + CHUNK_BYTES])


async def stream_deepgram(key, pcm, speed=1.0):
    """Finals with their arrival latency, Nova-3, opted out of model improvement."""
    from urllib.parse import urlencode
    q = urlencode({'model': 'nova-3', 'encoding': 'linear16', 'sample_rate': SR, 'channels': 1,
                   'smart_format': 'true', 'interim_results': 'false', 'mip_opt_out': 'true'})
    assert DG_URL.startswith('wss://') or os.environ.get('AI_COMPARE_TEST'), 'encrypted connections only'
    ws = await connect(f'{DG_URL}?{q}', {'Authorization': f'Token {key}'})
    segs, opened = [], time.monotonic()
    t0 = time.monotonic()

    async def receive():
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            m = json.loads(raw)
            if m.get('type') != 'Results' or not m.get('is_final'):
                continue
            alt = (m.get('channel', {}).get('alternatives') or [{}])[0]
            text = (alt.get('transcript') or '').strip()
            if not text:
                continue
            words = alt.get('words') or []
            end = float(words[-1]['end']) if words else float(m.get('start', 0)) + float(m.get('duration', 0))
            segs.append({'text': text, 'end': end,
                         'latency': round(time.monotonic() - (t0 + end / speed), 3)})

    rx = asyncio.create_task(receive())
    await send_realtime(ws, pcm, t0, speed)
    await ws.send(json.dumps({'type': 'CloseStream'}))
    try:
        await asyncio.wait_for(rx, timeout=30)
    finally:
        await ws.close()
    audio = len(pcm) / (SR * 2)
    return {'segments': segs, 'audio_s': round(audio, 2), 'session_s': round(time.monotonic() - opened, 2),
            'usd': round(audio / 60 * DG_USD_PER_MIN, 6)}


async def stream_assemblyai(key, pcm, speed=1.0):
    """Turns with their arrival latency, Universal-Streaming."""
    from urllib.parse import urlencode
    q = urlencode({'sample_rate': SR, 'encoding': 'pcm_s16le', 'format_turns': 'true'})
    assert AAI_URL.startswith('wss://') or os.environ.get('AI_COMPARE_TEST'), 'encrypted connections only'
    ws = await connect(f'{AAI_URL}?{q}', {'Authorization': key})
    segs, opened, billed = [], time.monotonic(), None
    t0 = time.monotonic()
    done_turns = {}

    async def receive():
        nonlocal billed
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            m = json.loads(raw)
            kind = m.get('type')
            if kind == 'Termination':
                billed = m.get('session_duration_seconds')
                return
            if kind != 'Turn' or not m.get('end_of_turn'):
                continue
            order = m.get('turn_order')
            words = m.get('words') or []
            end = float(words[-1]['end']) / 1000 if words else 0.0
            if order not in done_turns:
                # the first end-of-turn message is when the words are known
                done_turns[order] = len(segs)
                segs.append({'text': (m.get('transcript') or '').strip(), 'end': end,
                             'latency': round(time.monotonic() - (t0 + end / speed), 3)})
            if m.get('turn_is_formatted'):
                segs[done_turns[order]]['text'] = (m.get('transcript') or '').strip()

    rx = asyncio.create_task(receive())
    await send_realtime(ws, pcm, t0, speed)
    await ws.send(json.dumps({'type': 'Terminate'}))
    try:
        await asyncio.wait_for(rx, timeout=30)
    finally:
        await ws.close()
    session = float(billed) if billed is not None else time.monotonic() - opened
    segs = [s for s in segs if s['text']]
    return {'segments': segs, 'audio_s': round(len(pcm) / (SR * 2), 2), 'session_s': round(session, 2),
            'usd': round(session / 3600 * AAI_USD_PER_HOUR, 6)}


STREAMERS = {'deepgram': stream_deepgram, 'assemblyai': stream_assemblyai}


def load_vad():
    sys.path.insert(0, os.path.join(HERE, '..', 'voice_check'))
    from voice_check import MODEL, Vad
    return Vad(MODEL)


def done_keys(path):
    out = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get('ok'):
                    out.add((r['id'], r['side'], r['provider']))
    return out


async def run(args, keys, vad=None):
    split, out = os.path.join(BASE, 'split'), os.path.join(BASE, 'results')
    os.makedirs(out, exist_ok=True)
    streams_path, silero_path = os.path.join(out, 'streams.jsonl'), os.path.join(out, 'silero.jsonl')
    ids = calls(split)[:args.limit]
    if not ids:
        log(f'no calls in {split} - run collect_on_a.sh / fetch_on_b.sh first')
        return 1
    providers = [p for p in args.providers.split(',') if p]
    for p in providers:
        need = 'DEEPGRAM_API_KEY' if p == 'deepgram' else 'ASSEMBLYAI_API_KEY'
        if not keys.get(need):
            log(f'{need} is not in {ENV_FILE}')
            return 1
    done = done_keys(streams_path)
    seen_silero = {(r['id'], r['side']) for r in _jsonl(silero_path)}
    vad = vad or load_vad()
    sem = {p: asyncio.Semaphore(args.concurrency) for p in providers}
    lock = asyncio.Lock()
    counts = {'ok': 0, 'failed': 0}

    async def one(cid, side, provider, pcm):
        async with sem[provider]:
            key = keys['DEEPGRAM_API_KEY' if provider == 'deepgram' else 'ASSEMBLYAI_API_KEY']
            rec = {'id': cid, 'side': side, 'provider': provider}
            try:
                rec.update(await STREAMERS[provider](key, pcm, args.speed))
                rec['ok'] = True
                counts['ok'] += 1
            except Exception as e:                       # one stream failing never stops the test
                rec.update({'ok': False, 'error': f'{type(e).__name__}: {e}'[:300]})
                counts['failed'] += 1
            async with lock:
                with open(streams_path, 'a') as f:
                    f.write(json.dumps(rec) + '\n')

    tasks = []
    for n, cid in enumerate(ids, 1):
        for side in ('caller', 'agent'):
            pending = [p for p in providers if (cid, side, p) not in done]
            if not pending and (cid, side) in seen_silero:
                continue
            pcm = await asyncio.to_thread(decode, os.path.join(split, f'{cid}-{side}.wav'))
            if not pcm:
                log(f'{cid} {side}: cannot decode, skipped')
                continue
            if (cid, side) not in seen_silero:
                r = await asyncio.to_thread(vad.side, pcm)
                with open(silero_path, 'a') as f:
                    f.write(json.dumps({'id': cid, 'side': side, 'speech': r['speech'], 'audio': r['audio'],
                                        'beep': r['beep']}) + '\n')
            for p in pending:
                tasks.append(asyncio.create_task(one(cid, side, p, pcm)))
        if n % 20 == 0:
            log(f'{n}/{len(ids)} calls queued')
        # keep memory flat: never more than a few hundred streams queued
        while sum(1 for t in tasks if not t.done()) > 4 * args.concurrency * len(providers):
            await asyncio.sleep(0.5)
    if tasks:
        await asyncio.gather(*tasks)
    log(f"finished: {counts['ok']} streams ok, {counts['failed']} failed "
        f"(failed ones are tried again next run)")
    return 0


def _jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def sheet(args):
    """50 calls for the audit team to write down what the caller said."""
    out = os.path.join(BASE, 'results')
    silero = {(r['id'], r['side']): r for r in _jsonl(os.path.join(out, 'silero.jsonl'))}
    ids = [i for i in calls(os.path.join(BASE, 'split')) if (i, 'caller') in silero]
    spoke = [i for i in ids if silero[(i, 'caller')]['speech'] >= SPEECH_MIN]
    quiet = [i for i in ids if silero[(i, 'caller')]['speech'] < SPEECH_MIN]
    rnd = random.Random(549)
    # mostly calls where the caller talks (accuracy), some silent ones (made-up words)
    pick = rnd.sample(spoke, min(len(spoke), args.count - min(10, len(quiet))))
    pick += rnd.sample(quiet, min(len(quiet), args.count - len(pick)))
    pick.sort()
    ref_dir = os.path.join(BASE, 'reference')
    os.makedirs(ref_dir, exist_ok=True)
    csv_path = os.path.join(ref_dir, 'reference.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['n', 'audio_file', 'call_id', 'caller_said'])
        for n, cid in enumerate(pick, 1):
            w.writerow([n, f'{n:02d}.mp3', cid, ''])
    zip_path = os.path.join(ref_dir, 'caller-audio-50.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as z:
        for n, cid in enumerate(pick, 1):
            mp3 = os.path.join(ref_dir, f'{n:02d}.mp3')
            subprocess.run(['nice', '-n', '10', 'ffmpeg', '-nostdin', '-v', 'error', '-y', '-i',
                            os.path.join(BASE, 'split', f'{cid}-caller.wav'), '-ac', '1', '-b:a', '48k', mp3],
                           check=True)
            z.write(mp3, f'{n:02d}.mp3')
            os.remove(mp3)
        z.write(csv_path, 'reference.csv')
    log(f'{len(pick)} calls: {zip_path}')
    log('The audit team listens to each mp3 and types what the CALLER said into caller_said '
        '(exact words, numbers as digits; leave it empty if the caller said nothing).')
    return 0


def words(text):
    return re.sub(r"[^a-z0-9' ]+", ' ', (text or '').lower().replace('-', ' ')).split()


def wer(ref, hyp):
    """(edits, reference words) - word error rate = edits / words."""
    r, h = words(ref), words(hyp)
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
        prev = cur
    return prev[len(h)], len(r)


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def report(args):
    out = os.path.join(BASE, 'results')
    streams = [r for r in _jsonl(os.path.join(out, 'streams.jsonl'))]
    latest = {}
    for r in streams:                                    # the last try of each stream counts
        latest[(r['id'], r['side'], r['provider'])] = r
    silero = {(r['id'], r['side']): r for r in _jsonl(os.path.join(out, 'silero.jsonl'))}
    ref = {}
    if args.reference and os.path.exists(args.reference):
        with open(args.reference, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row.get('call_id'):
                    ref[row['call_id'].strip()] = (row.get('caller_said') or '').strip()
    ids = sorted({k[0] for k in latest})
    lines, per_call = [], []
    lines.append(f'CH-549 speech-to-text test: {len(ids)} calls, both sides '
                 f'({time.strftime("%Y-%m-%d %H:%M")} server time)')
    lines.append(f'Prices used: AssemblyAI ${AAI_USD_PER_HOUR}/hour of connection, '
                 f'Deepgram ${DG_USD_PER_MIN}/min of audio - confirm on each Usage page.')
    lines.append('')
    for p in PROVIDERS:
        rows = [latest[(i, s, p)] for i in ids for s in ('caller', 'agent') if (i, s, p) in latest]
        if not rows:
            continue
        ok = [r for r in rows if r.get('ok')]
        lat = [seg['latency'] for r in ok for seg in r['segments']]
        cost = {}
        for r in ok:
            cost[r['id']] = cost.get(r['id'], 0) + r['usd']
        found = missed = invented = quiet = spoke = 0
        e = n = nref = ref_quiet = ref_invented = 0
        for i in ids:
            r, s = latest.get((i, 'caller', p)), silero.get((i, 'caller'))
            if not r or not r.get('ok') or not s:
                continue
            text = ' '.join(seg['text'] for seg in r['segments'])
            heard = len(words(text)) > 0
            if s['speech'] >= SPEECH_MIN:
                spoke += 1
                found += heard
                missed += not heard
            else:
                quiet += 1
                invented += heard
            if ref.get(i):                               # the audit team wrote the caller's words
                de, dn = wer(ref[i], text)
                e, n, nref = e + de, n + dn, nref + 1
            elif i in ref:                               # the audit team heard nothing
                ref_quiet += 1
                ref_invented += heard
            per_call.append({'provider': p, 'call_id': i, 'caller_speech_s': s['speech'],
                             'caller_text': text, 'reference': ref.get(i, ''),
                             'call_usd': round(cost.get(i, 0), 5)})
        lines.append(f'== {p} ==')
        first_error = next((r.get('error') for r in rows if not r.get('ok')), '')
        lines.append(f'  streams ok {len(ok)} / {len(rows)}'
                     + (f'  (failed {len(rows) - len(ok)}, e.g. {first_error})' if first_error else ''))
        if spoke:
            lines.append(f'  caller spoke (Silero) on {spoke} calls: words found on {found} '
                         f'({found / spoke:.0%}), missed {missed}')
        if quiet:
            lines.append(f'  caller silent (Silero) on {quiet} calls: words made up on {invented}')
        if n:
            lines.append(f'  word error rate on caller vs audit team ({nref} calls, '
                         f'{n} words): {e / n:.1%}   (lower is better)')
            if ref_quiet:
                lines.append(f'  calls the audit team heard no caller words: {ref_quiet}, '
                             f'words made up on {ref_invented}')
        else:
            lines.append('  word error rate: no reference yet (audit team sheet)')
        if lat:
            lines.append(f'  latency of final words: median {statistics.median(lat):.2f} s, '
                         f'95% within {pct(lat, 0.95):.2f} s')
        if cost:
            avg = sum(cost.values()) / len(cost)
            lines.append(f'  cost per call (both sides): average ${avg:.4f}; this test ${sum(cost.values()):.2f}')
        lines.append('')
    text = '\n'.join(lines)
    with open(os.path.join(out, 'summary.txt'), 'w') as f:
        f.write(text + '\n')
    with open(os.path.join(out, 'per_call.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['provider', 'call_id', 'caller_speech_s', 'caller_text',
                                          'reference', 'call_usd'])
        w.writeheader()
        w.writerows(per_call)
    print(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--limit', type=int, default=200)
    r.add_argument('--concurrency', type=int, default=5, help='streams at once per provider')
    r.add_argument('--providers', default='deepgram,assemblyai')
    r.add_argument('--speed', type=float, default=1.0, help='1 = real time, as on a live call')
    s = sub.add_parser('sheet')
    s.add_argument('--count', type=int, default=50)
    p = sub.add_parser('report')
    p.add_argument('--reference', default=os.path.join(BASE, 'reference', 'reference.csv'))
    a = ap.parse_args(argv)
    if a.cmd == 'run':
        return asyncio.run(run(a, read_keys()))
    if a.cmd == 'sheet':
        return sheet(a)
    return report(a)


if __name__ == '__main__':
    sys.exit(main())
