#!/usr/bin/env python3
"""CH-555: the final AI label of every answered call - transcript + LLM.

Sir, 4 Nov: "After each call, an LLM (Claude or GPT) reads the transcript
plus the Silero and PANNs results and sets the final label: Valid, the three
No Voice types, Voicemail/IVR, Robocall, or 'Caller spoke, no issue'. It also
writes a one-line summary." "Call audio sent to outside APIs must be
encrypted, and providers must not keep or train on our audio." "Live
analysis must not affect call quality on Server A."

Runs on Server B (cron, every minute, --loop 55), after the voice check has
labelled a call (Silero speech per side, PANNs music / tones):

  1. the call's audio: caller and agent apart (stereo: caller left, agent
     right) when Server A's split files can be read, otherwise the mixed
     recording; the talk part only, at most AI_FINAL_MAX_SECONDS (300);
  2. transcript - AI_STT_PROVIDER: "assemblyai" (pre-recorded; the
     transcript is DELETED from AssemblyAI as soon as it is read) or
     "deepgram" (Nova-3, mip_opt_out=true: not used for their training);
  3. AI_LLM_PROVIDER "anthropic" (Claude) or "openai" (GPT), AI_LLM_MODEL:
     reads the transcript and the measurements, answers label, confidence,
     one-line summary (JSON);
  4. manage.py ai_final_results saves it (the rule label is kept apart).

Every call out is https:// (encrypted); nothing runs on Server A. Keys in
/opt/call_platform/.env (ASSEMBLYAI_API_KEY / DEEPGRAM_API_KEY,
ANTHROPIC_API_KEY / OPENAI_API_KEY), read here, never printed.

    /opt/avx-voice/venv/bin/python ai_final.py --dry-run --limit 3   # prints, saves nothing
    /opt/avx-voice/venv/bin/python ai_final.py --loop 55             # cron
    /opt/avx-voice/venv/bin/python ai_final.py --redo --days 1      # CH-594: today's calls again

CH-594 (Sir, 5 Nov: "I need 100% accuracy"): Sir's own definitions in the
instructions (CH-600: no `temperature` - Claude 5.5 refuses it), the audit
team's Correct / Wrong verdicts shown as worked examples every run
(manage.py ai_final_examples), the first 120 s only, and real conversations
(Valid, 15 s+ of the caller's own speech) not sent at all.
"""
import argparse
import fcntl
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'voice_check'))
import voice_check as vc  # noqa: E402  (log, manage, find_file, fetch, decode, talk_part, SR, RECORDINGS_DIR)

ENV_FILE = os.environ.get('ENV_FILE', '/opt/call_platform/.env')
LABELS = ('valid', 'caller_ok', 'dead_caller', 'agent_only', 'agent_beep', 'ivr', 'robocall')
AAI_BASE = os.environ.get('AAI_BASE', 'https://api.assemblyai.com')
DG_URL = os.environ.get('DG_PRERECORDED_URL', 'https://api.deepgram.com/v1/listen')
ANTHROPIC_URL = os.environ.get('ANTHROPIC_URL', 'https://api.anthropic.com/v1/messages')
OPENAI_URL = os.environ.get('OPENAI_URL', 'https://api.openai.com/v1/chat/completions')
DEFAULT_MODELS = {'anthropic': 'claude-haiku-5-5'}
LOOP_PAUSE = 3


def read_env(path=ENV_FILE):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class Config:
    def __init__(self, env):
        get = lambda k, d='': os.environ.get(k) or env.get(k) or d
        self.stt = get('AI_STT_PROVIDER', 'assemblyai').lower()
        self.llm = get('AI_LLM_PROVIDER', 'anthropic').lower()
        self.model = get('AI_LLM_MODEL', DEFAULT_MODELS.get(self.llm, ''))
        # CH-594: 120 s (was 300). Whether anyone answered the agent shows in
        # the first two minutes; a longer call past that is a conversation.
        self.max_seconds = int(float(get('AI_FINAL_MAX_SECONDS', '120')))
        self.keys = {k: get(k) for k in ('ASSEMBLYAI_API_KEY', 'DEEPGRAM_API_KEY', 'ANTHROPIC_API_KEY',
                                         'OPENAI_API_KEY')}
        # CH-594: the audit team's verdicts shown to the LLM (0 = none).
        self.example_count = int(float(get('AI_FINAL_EXAMPLES', '12')))
        self.examples = ''

    def problems(self):
        p = []
        if self.stt not in ('assemblyai', 'deepgram'):
            p.append(f'AI_STT_PROVIDER must be assemblyai or deepgram, not "{self.stt}"')
        elif not self.keys['ASSEMBLYAI_API_KEY' if self.stt == 'assemblyai' else 'DEEPGRAM_API_KEY']:
            p.append(f'no {"ASSEMBLYAI" if self.stt == "assemblyai" else "DEEPGRAM"}_API_KEY in {ENV_FILE}')
        if self.llm not in ('anthropic', 'openai'):
            p.append(f'AI_LLM_PROVIDER must be anthropic or openai, not "{self.llm}"')
        elif not self.keys['ANTHROPIC_API_KEY' if self.llm == 'anthropic' else 'OPENAI_API_KEY']:
            p.append(f'no {"ANTHROPIC" if self.llm == "anthropic" else "OPENAI"}_API_KEY in {ENV_FILE}')
        if not self.model:
            p.append('AI_LLM_MODEL is not set')
        return p


def encrypted(url):
    if not url.startswith('https://') and not os.environ.get('AI_FINAL_TEST'):
        raise RuntimeError(f'refusing an unencrypted connection: {url.split("?")[0]}')
    return url


class AccountError(RuntimeError):
    """CH-556: the provider refuses OUR ACCOUNT (no credit, bad key, limit) -
    not this call. The run stops; no call is marked failed and nothing more is
    sent (4 Nov: Claude had no credit, and every call was still transcribed)."""


def _account_problem(code, detail):
    d = detail.lower()
    return code in (401, 402, 403, 429) or 'credit balance' in d or 'insufficient' in d or 'quota' in d


def http(method, url, headers, body=None, timeout=120):
    req = urllib.request.Request(encrypted(url), data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode('utf-8', 'replace')
        msg = f'{e.code} from {url.split("//")[1].split("/")[0]}: {detail}'
        raise (AccountError if _account_problem(e.code, detail) else RuntimeError)(msg)
    return json.loads(raw) if raw else {}


# ── audio ────────────────────────────────────────────────────────────────────

def wav_bytes(channels_pcm, sr):
    """mono / stereo 16-bit WAV from one or two int16 PCM byte strings."""
    import numpy as np
    arrays = [np.frombuffer(p, dtype=np.int16) for p in channels_pcm]
    n = max(len(a) for a in arrays)
    arrays = [np.pad(a, (0, n - len(a))) for a in arrays]
    data = np.stack(arrays, axis=1).astype(np.int16).tobytes() if len(arrays) > 1 else arrays[0].tobytes()
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(len(arrays))
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)
    return buf.getvalue()


def call_audio(item, max_seconds):
    """(wav bytes, stereo?) of the talk part, or (None, False)."""
    cap = max_seconds * vc.SR * 2
    temp, paths = [], {}
    try:
        split = item.get('split') or {}
        for who in ('caller', 'agent'):
            local = os.path.join(vc.RECORDINGS_DIR, 'split', f"{item['id']}-{who}.wav")
            if os.path.isfile(local):
                paths[who] = local
            elif split.get(who):
                got = vc.fetch(split[who], item['id'], who)
                if got:
                    temp.append(got)
                    paths[who] = got
        if len(paths) == 2:
            pcm = [vc.decode(paths['caller']), vc.decode(paths['agent'])]
            if all(pcm):
                pcm = [vc.talk_part(p, item.get('talk'))[:cap] for p in pcm]
                return wav_bytes(pcm, vc.SR), True
        path, is_temp = vc.find_file(item)
        if is_temp and path:
            temp.append(path)
        pcm = vc.decode(path) if path else None
        if not pcm:
            return None, False
        return wav_bytes([vc.talk_part(pcm, item.get('talk'))[:cap]], vc.SR), False
    finally:
        for t in temp:
            try:
                os.unlink(t)
            except OSError:
                pass


# ── transcript ───────────────────────────────────────────────────────────────

def lines_from(utterances, stereo, channel_key, text_key, start_key, start_div, first):
    """Transcript lines '[12s] Caller: ...' - channel `first` is the caller
    (left), the other the agent (AssemblyAI counts channels from 1, Deepgram
    from 0)."""
    out = []
    for u in sorted(utterances, key=lambda u: float(u.get(start_key) or 0)):
        text = (u.get(text_key) or '').strip()
        if not text:
            continue
        try:
            ch = int(u.get(channel_key, first))
        except (TypeError, ValueError):
            ch = first
        who = ('Caller' if ch == first else 'Agent') if stereo else 'Call'
        out.append(f'[{float(u.get(start_key) or 0) / start_div:.0f}s] {who}: {text}')
    return out


def transcribe_assemblyai(key, audio, stereo):
    h = {'authorization': key}
    up = http('POST', f'{AAI_BASE}/v2/upload', {**h, 'content-type': 'application/octet-stream'}, audio)
    job = http('POST', f'{AAI_BASE}/v2/transcript', {**h, 'content-type': 'application/json'},
               json.dumps({'audio_url': up['upload_url'], 'multichannel': stereo,
                           'speaker_labels': not stereo}).encode())
    tid, deadline = job['id'], time.time() + 300
    try:
        while True:
            t = http('GET', f'{AAI_BASE}/v2/transcript/{tid}', h)
            if t.get('status') == 'completed':
                break
            if t.get('status') == 'error':
                raise RuntimeError(f"AssemblyAI: {t.get('error', 'error')}"[:150])
            if time.time() > deadline:
                raise RuntimeError('AssemblyAI: no transcript after 5 minutes')
            time.sleep(1.5)
    finally:
        # Nothing is left with them: the transcript and its audio are deleted.
        try:
            http('DELETE', f'{AAI_BASE}/v2/transcript/{tid}', h)
        except Exception as e:
            vc.log(f'AssemblyAI delete of {tid} failed: {e}')
    utts = t.get('utterances') or []
    lines = lines_from(utts, stereo, 'channel', 'text', 'start', 1000, 1) if utts else []
    if not lines and (t.get('text') or '').strip():
        lines = [f"Call: {t['text'].strip()}"]
    return '\n'.join(lines)


def transcribe_deepgram(key, audio, stereo):
    q = 'model=nova-3&smart_format=true&utterances=true&mip_opt_out=true' + ('&multichannel=true' if stereo else '')
    r = http('POST', f'{DG_URL}?{q}', {'Authorization': f'Token {key}', 'Content-Type': 'audio/wav'}, audio)
    res = r.get('results') or {}
    utts = res.get('utterances') or []
    lines = lines_from(utts, stereo, 'channel', 'transcript', 'start', 1, 0)
    if not lines:
        for i, ch in enumerate(res.get('channels') or []):
            text = ((ch.get('alternatives') or [{}])[0].get('transcript') or '').strip()
            if text:
                lines.append(f"{('Caller' if i == 0 else 'Agent') if stereo else 'Call'}: {text}")
    return '\n'.join(lines)


# ── the LLM ──────────────────────────────────────────────────────────────────

SYSTEM = """You audit pay-per-call phone calls for our own audit team. A caller (the consumer) dialled an advertised number and was connected to an agent at a call centre (the buyer). The agent normally answers with an opening such as "Thank you for calling, how can I help you?". Our question is whether a real person was on the caller's side and talked about why they called.

Give the call ONE final label:
- valid: a live caller talked with the agent about their need or problem (asked something, explained an issue, gave details).
- caller_ok: a live person spoke on the caller's side, but never talked about any need - only "hello?", "who is this?", "wrong number", a word or two before hanging up.
- dead_caller: nobody spoke on either side - silence, or no audio at all.
- agent_only: the agent gave the opening (maybe repeated "hello? are you there?") and the caller's side stayed completely silent - no voice, no beeping.
- agent_beep: the agent spoke, and the caller's side had only beeps, tones, clicks or computer/machine sound - no human talking.
- ivr: no live person - a voicemail greeting, an IVR menu ("press 1"), hold music or an answering machine.
- robocall: the caller's side is a recorded or automated message or a bot talking, not a live person.

How to judge:
- The transcript is the main evidence. With two channels, "Caller:" lines are the caller's line and "Agent:" lines the agent's line.
- ECHO: the agent's own words often come back on the caller's line a moment later. "Caller:" words that repeat or closely mirror what the Agent just said are echo - not the caller speaking. Ignore them.
- Words on the caller's line that only greet ("hello", "hi", "yes?") do not make a call valid; valid needs the caller to say what they want.
- The measurements support the transcript: Silero = seconds of human speech per side (agent echo already removed from the caller's side); beeping and PANNs = seconds of tones / machine sound / music. If the transcript is empty, decide from the measurements.
- When the evidence is unclear, choose the label that fits best and give a LOW confidence (below 60) - never guess with high confidence.

Answer with JSON only: {"label": "<one of: valid, caller_ok, dead_caller, agent_only, agent_beep, ivr, robocall>", "confidence": <0-100>, "summary": "<one line, at most 25 words: what happened on the call>"}"""


def examples_text(rows):
    """CH-594: the audit team's verdicts (manage.py ai_final_examples), as
    worked examples after the instructions. '' when there are none."""
    parts = []
    for i, x in enumerate(rows, 1):
        try:
            label = str(x['label'])
        except (KeyError, TypeError):
            continue
        if label not in LABELS:
            continue
        head = (f"Example {i} - the AI said {x.get('ai_said')}, our audit team listened and corrected it to: {label}"
                if x.get('wrong') else f'Example {i} - our audit team listened and confirmed: {label}')
        body = [head, f"Talk time: {x.get('talk')} s", f"Measurements: {x.get('measurements') or '-'}"]
        if x.get('note'):
            body.append(f"Team's note: {x['note']}")
        body.append('Transcript:\n' + (str(x.get('transcript') or '').strip() or '(no words heard)'))
        parts.append('\n'.join(body))
    if not parts:
        return ''
    return ('\n\nCalls our audit team checked by ear. Their verdict is the truth - judge new calls the same '
            'way, and above all do not repeat the mistakes they corrected:\n\n' + '\n\n'.join(parts))


def load_examples(cfg):
    """The team's latest verdicts, once per pass. A failure only means no
    examples this time - the label is still given."""
    if cfg.example_count <= 0:
        cfg.examples = ''
        return
    try:
        p = vc.manage(['ai_final_examples', '--limit', str(cfg.example_count)])
        rows = [json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')] \
            if p.returncode == 0 else []
    except Exception as e:
        vc.log(f'examples not loaded: {e}')
        rows = []
    cfg.examples = examples_text(rows)


def prompt(item, transcript, stereo):
    rule = item.get('rule') or {}
    m = [f"Talk time: {item.get('talk')} s",
         'Audio: caller and agent recorded apart' if stereo else 'Audio: one mixed recording (sides not apart)',
         f"Rule label (Silero / PANNs): {rule.get('label') or '-'}",
         f"Measurements: {rule.get('comment') or '-'}"]
    return ('\n'.join(m) + '\n\nTranscript' + (' (Caller = consumer, Agent = buyer)' if stereo else '')
            + ':\n' + (transcript[:6000] or '(no words heard)'))


def llm_request(cfg, system, text, max_tokens, timeout=60):
    """CH-600: THE request to the LLM - the real question and the pre-check
    (llm_ready) send the same shape, so a setting the model refuses stops the
    run before anything is transcribed. No `temperature`: Claude 5.5 models
    refuse it ("temperature is deprecated for this model", 5 Nov - CH-594
    sent 0 and every call failed after AssemblyAI had been paid); newer GPT
    models refuse anything but the default. The instructions make the answer
    steady enough."""
    if cfg.llm == 'anthropic':
        r = http('POST', ANTHROPIC_URL,
                 {'x-api-key': cfg.keys['ANTHROPIC_API_KEY'], 'anthropic-version': '2023-06-01',
                  'content-type': 'application/json'},
                 json.dumps({'model': cfg.model, 'max_tokens': max_tokens, 'system': system,
                             'messages': [{'role': 'user', 'content': text}]}).encode(), timeout=timeout)
        return ''.join(b.get('text', '') for b in r.get('content') or [] if b.get('type') == 'text')
    r = http('POST', OPENAI_URL,
             {'Authorization': f"Bearer {cfg.keys['OPENAI_API_KEY']}", 'Content-Type': 'application/json'},
             json.dumps({'model': cfg.model, 'max_completion_tokens': max_tokens,
                         'response_format': {'type': 'json_object'},
                         'messages': [{'role': 'system', 'content': system},
                                      {'role': 'user', 'content': text}]}).encode(), timeout=timeout)
    return ((r.get('choices') or [{}])[0].get('message') or {}).get('content') or ''


def ask_llm(cfg, text):
    # CH-594: the team's corrections ride along with the instructions.
    system = SYSTEM + (getattr(cfg, 'examples', '') or '')
    out = llm_request(cfg, system, text, 300)
    found = re.search(r'\{.*\}', out, re.S)
    if not found:
        raise RuntimeError(f'the LLM did not answer in JSON: {out[:80]!r}')
    a = json.loads(found.group(0))
    label = str(a.get('label') or '').strip().lower()
    if label not in LABELS:
        raise RuntimeError(f'the LLM gave an unknown label: {label[:30]!r}')
    return {'label': label, 'confidence': a.get('confidence'), 'summary': str(a.get('summary') or '')[:300]}


# ── one call, the run ────────────────────────────────────────────────────────

def llm_ready(cfg):
    """CH-556: a one-word question to the LLM before any call is transcribed:
    a refused account (no credit, bad key) stops the run before anything is
    spent on transcripts. CH-600: the same request as the real question (the
    instructions, the same settings), so a setting the model refuses stops it
    too."""
    llm_request(cfg, SYSTEM, 'Reply with {} only.', 5, timeout=30)


def final_for(cfg, item):
    # CH-600: a retry of a call whose transcript was already paid for (the
    # LLM failed after it) asks the LLM again with that transcript.
    kept = (item.get('transcript') or '').strip()
    if kept:
        transcript = kept
        stereo = bool(re.search(r'(^|\] )(Caller|Agent): ', kept, re.M))
    else:
        audio, stereo = call_audio(item, cfg.max_seconds)
        if audio is None:
            return {'id': item['id'], 'error': 'no readable recording'}
        key = cfg.keys['ASSEMBLYAI_API_KEY' if cfg.stt == 'assemblyai' else 'DEEPGRAM_API_KEY']
        transcript = (transcribe_assemblyai if cfg.stt == 'assemblyai' else transcribe_deepgram)(key, audio, stereo)
    by = f"{cfg.stt} + {cfg.model}{'' if stereo else ' (one channel)'}"
    try:
        answer = ask_llm(cfg, prompt(item, transcript, stereo))
    except AccountError:
        raise
    except Exception as e:
        # The transcript is saved with the error, so --retry-failed does not
        # pay for it again.
        return {'id': item['id'], 'error': str(e)[:150], 'transcript': transcript, 'by': by}
    return {'id': item['id'], **answer, 'transcript': transcript, 'by': by}


def one_pass(o, cfg):
    args = ['ai_final_pending', '--limit', str(o.limit), '--days', str(o.days)]
    if o.retry_failed:
        args.append('--retry-failed')
    # CH-594: --redo gives the calls of the last --days their final label
    # again, each once (finalised before this run started).
    if getattr(o, 'redo_before', None):
        args += ['--redo-before', str(o.redo_before)]
    for i in o.id or []:
        args += ['--id', i]
    p = vc.manage(args)
    if p.returncode != 0:
        vc.log(f'ai_final_pending failed: {p.stderr.strip()[-300:]}')
        return 0, 1
    items = [json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')]
    failed = 0
    if items:
        try:
            llm_ready(cfg)
        except Exception as e:
            vc.log(f'STOPPED before any call - the {cfg.llm} account is not ready: {str(e)[:200]}')
            return 0, 2
        load_examples(cfg)
    for n, item in enumerate(items, 1):
        try:
            r = final_for(cfg, item)
        except AccountError as e:
            vc.log(f'STOPPED at call {n}/{len(items)} - a provider refused our account: {str(e)[:200]} '
                   '(this call is left as it was, tried again next run)')
            return n - 1, 2
        except Exception as e:
            r = {'id': item['id'], 'error': str(e)[:150]}
        if 'error' in r:
            vc.log(f"[{n}/{len(items)}] {item['id']}: FAILED - {r['error']}")
        else:
            vc.log(f"[{n}/{len(items)}] {item['id']}: {r['label']} ({r.get('confidence')}%) - {r['summary']}")
        if o.dry_run:
            if o.show_transcript and r.get('transcript'):
                print(r['transcript'])
            continue
        s = vc.manage(['ai_final_results'], stdin=json.dumps(r) + '\n')
        if s.returncode != 0:
            failed = 1
            vc.log(f'ai_final_results failed: {s.stderr.strip()[-300:]}')
    return len(items), failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='transcribe and ask, print, save nothing')
    ap.add_argument('--show-transcript', action='store_true', help='with --dry-run: print the transcripts')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--days', type=int, default=2)
    ap.add_argument('--retry-failed', action='store_true')
    ap.add_argument('--id', action='append', help='these calls (again)')
    ap.add_argument('--loop', type=int, default=0, help='keep going for this many seconds (cron: --loop 55)')
    ap.add_argument('--redo', action='store_true',
                    help='CH-594: give calls of the last --days their final label again (after an update)')
    o = ap.parse_args(argv)
    # CH-600: --retry-failed too, so a call failing again is not retried
    # over and over in the same run.
    o.redo_before = time.time() if (o.redo or o.retry_failed) else None
    cfg = Config(read_env())
    problems = cfg.problems()
    if problems:
        for p in problems:
            vc.log(f'not ready: {p}')
        return 1
    # CH-594: a --redo has its own lock: it never holds up the every-minute
    # run of new calls (it only takes calls that already have a label).
    # CH-600: --retry-failed too (it takes only failed calls, never new ones).
    lock = open('/tmp/ai_final_redo.lock' if (o.redo or o.retry_failed) else '/tmp/ai_final.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        vc.log('previous run still going, skipped')
        return 0
    try:
        os.nice(10)
    except OSError:
        pass
    if (o.redo or o.retry_failed) and not o.loop:
        # CH-594: through every call of the last --days, --limit at a time.
        # CH-600: --retry-failed the same way (a retried call that fails
        # again keeps its error and is not taken twice in one run).
        total, failed = 0, 0
        while True:
            done, rc = one_pass(o, cfg)
            total += done
            if rc == 2:
                return 2
            failed = failed or rc
            if not done or o.dry_run:
                break
        vc.log(f'redo finished: {total} calls')
        return failed
    if not o.loop:
        return one_pass(o, cfg)[1]
    deadline, failed = time.time() + o.loop, 0
    while time.time() < deadline:
        done, rc = one_pass(o, cfg)
        if rc == 2:                        # an account problem: wait for the next minute's run
            return 2
        failed = failed or rc
        if not done:
            time.sleep(LOOP_PAUSE)
    return failed


if __name__ == '__main__':
    sys.exit(main())
