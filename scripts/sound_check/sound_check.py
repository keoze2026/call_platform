#!/usr/bin/env python3
"""CH-552: what a call's recording SOUNDS like - PANNs on our own Server B.

Sir, 4 Nov: "Beep and machine-sound detection: PANNs or YAMNet on the caller
channel." And 4 Nov, three calls that were IVR (only music in the recording)
came out "No Voice | Dead Caller": Silero hears speech only, so music counts as
silence. PANNs (Cnn14, trained on Google's AudioSet - 527 kinds of sound) tells
music, ringing, dial / busy tones, beeps, silence and speech apart.

This step only LISTENS and PRINTS - no label is changed. It is how we see,
on calls we know, what PANNs hears, before any rule uses it (CH-553):

    /opt/avx-voice/venv/bin/python sound_check.py try --caller 13134598968 --caller 13526054139

For each call: the caller and agent sides when Server A's split files can be
read (CH-543), otherwise the one mixed recording; the talk part only (CH-536);
the first MAX_SECONDS. Per 2-second window, the share of each kind of sound;
printed as seconds of speech / music / tone / silence / noise, the top sounds
heard, and Silero's speech seconds for the same audio.

CPU: 2 threads at the lowest priority, so the portal and the voice check never
wait. Nothing runs on Server A.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'voice_check'))
import voice_check as vc  # noqa: E402  (log, manage, find_file, fetch, Vad, decode, RECORDINGS_DIR)

PANNS_SR = 32000
WINDOW_S = 2
MAX_SECONDS = int(os.environ.get('SOUND_MAX_SECONDS', '120'))
THREADS = int(os.environ.get('SOUND_THREADS', '2'))
THRESHOLD = float(os.environ.get('SOUND_THRESHOLD', '0.3'))
CHECKPOINT = os.environ.get('PANNS_CHECKPOINT', os.path.expanduser('~/panns_data/Cnn14_mAP=0.431.pth'))

# AudioSet names (class_labels_indices.csv) grouped into what matters on a call.
GROUPS = {
    'speech': ('Speech', 'Male speech, man speaking', 'Female speech, woman speaking',
               'Child speech, kid speaking', 'Conversation', 'Narration, monologue'),
    'music': ('Music', 'Musical instrument', 'Background music', 'Theme music', 'Jingle (music)',
              'Piano', 'Synthesizer', 'Ambient music', 'Pop music', 'Electronic music'),
    'tone': ('Beep, bleep', 'Dial tone', 'Busy signal', 'Ringtone', 'Telephone bell ringing',
             'Telephone dialing, DTMF', 'Sine wave', 'Buzzer', 'Telephone'),
    'silence': ('Silence',),
    'noise': ('Static', 'White noise', 'Pink noise', 'Hum', 'Mains hum', 'Noise', 'Buzz'),
}


def decode32(path):
    """Any recording -> 32 kHz mono float32 (numpy), or None."""
    import numpy as np
    p = subprocess.run(['nice', '-n', '19', 'ffmpeg', '-nostdin', '-v', 'error', '-i', path,
                        '-ac', '1', '-ar', str(PANNS_SR), '-f', 's16le', '-'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if p.returncode != 0 or not p.stdout:
        return None
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def talk_tail(audio, talk, sr):
    """The conversation only: the last talk+1 seconds (CH-536), then the first MAX_SECONDS."""
    if talk is not None:
        keep = (int(talk) + 1) * sr
        audio = audio[-keep:] if len(audio) > keep else audio
    return audio[:MAX_SECONDS * sr]


def audioset_labels():
    """The 527 AudioSet names, in the model's order."""
    try:
        from panns_inference import labels
        return list(labels)
    except ImportError:
        import csv
        path = os.path.expanduser('~/panns_data/class_labels_indices.csv')
        with open(path, newline='') as f:
            return [row['display_name'] for row in csv.DictReader(f)]


class Tagger:
    def __init__(self):
        import torch
        torch.set_num_threads(THREADS)
        from panns_inference import AudioTagging
        self.labels = audioset_labels()
        self.model = AudioTagging(checkpoint_path=CHECKPOINT, device='cpu')
        self.index = {g: [self.labels.index(n) for n in names if n in self.labels] for g, names in GROUPS.items()}

    def windows(self, audio):
        """(n windows, 527) probabilities, one row per WINDOW_S of audio."""
        import numpy as np
        step = WINDOW_S * PANNS_SR
        n = len(audio) // step
        if n == 0:
            return np.zeros((0, len(self.labels)), dtype=np.float32)
        batch = audio[:n * step].reshape(n, step)
        out = []
        for i in range(0, n, 16):                       # 16 windows at a time keeps memory small
            clip, _ = self.model.inference(batch[i:i + 16])
            out.append(clip)
        return np.concatenate(out, axis=0)


def summarise(probs, labels, index):
    """Seconds per group (a window counts for a group when its best member is
    >= THRESHOLD) and the five sounds heard most, on average."""
    import numpy as np
    out = {'seconds': len(probs) * WINDOW_S}
    for group, idx in index.items():
        out[group] = int(sum(1 for row in probs if idx and float(np.max(row[idx])) >= THRESHOLD) * WINDOW_S)
    if len(probs):
        mean = probs.mean(axis=0)
        top = np.argsort(-mean)[:5]
        out['top'] = [(labels[i], round(float(mean[i]), 2)) for i in top]
    else:
        out['top'] = []
    return out


def sides(item):
    """{'caller': path, 'agent': path} from the split files, or {'mixed': path};
    plus the temporary files to delete afterwards."""
    temp, paths = [], {}
    split = item.get('split') or {}
    for who in ('caller', 'agent'):
        local = os.path.join(vc.RECORDINGS_DIR, 'split', f"{item['id']}-{who}.wav")
        if os.path.isfile(local):
            paths[who] = local
            continue
        got = vc.fetch(split.get(who) or '', item['id'], who)
        if got:
            temp.append(got)
            paths[who] = got
    if len(paths) == 2:
        return paths, temp
    temp_mixed = []
    path, is_temp = vc.find_file(item)
    if is_temp and path:
        temp_mixed.append(path)
    for p in temp:
        os.unlink(p)
    return ({'mixed': path} if path else {}), temp_mixed


def try_calls(args):
    cmd = ['voice_pending', '--days', str(args.days), '--limit', '50']
    for c in args.caller:
        cmd += ['--caller', c]
    for i in args.id:
        cmd += ['--id', i]
    p = vc.manage(cmd)
    if p.returncode != 0:
        vc.log(f'voice_pending failed: {p.stderr.strip()[-300:]}')
        return 1
    items = [json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')]
    if not items:
        vc.log('no calls found with a recording for those numbers / ids')
        return 1
    os.nice(19)
    tagger = Tagger()
    vad = vc.Vad(vc.MODEL)
    for item in items:
        paths, temp = sides(item)
        try:
            print(f"\ncall {item['id']}  caller {item.get('caller', '')}  talk {item.get('talk')} s  "
                  f"({'caller and agent apart' if 'caller' in paths else 'one mixed recording' if paths else 'NO FILE'})")
            for who, path in paths.items():
                audio = decode32(path)
                pcm16 = vc.decode(path)
                if audio is None or not pcm16:
                    print(f'  {who}: cannot decode')
                    continue
                audio = talk_tail(audio, item.get('talk'), PANNS_SR)
                silero = vad.side(vc.talk_part(pcm16, item.get('talk'))[:MAX_SECONDS * vc.SR * 2])
                s = summarise(tagger.windows(audio), tagger.labels, tagger.index)
                print(f"  {who:6} Silero speech {silero['speech']:.1f} s | PANNs over {s['seconds']} s: "
                      f"speech {s['speech']} s, music {s['music']} s, tone {s['tone']} s, "
                      f"silence {s['silence']} s, noise {s['noise']} s")
                print(f"         heard most: " + ', '.join(f'{n} {v}' for n, v in s['top']))
        finally:
            for t in temp:
                os.unlink(t)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('try', help='listen to named calls and print what PANNs hears (changes nothing)')
    t.add_argument('--caller', action='append', default=[])
    t.add_argument('--id', action='append', default=[])
    t.add_argument('--days', type=int, default=3)
    a = ap.parse_args(argv)
    return try_calls(a)


if __name__ == '__main__':
    sys.exit(main())
