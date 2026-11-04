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

from panns_sound import (MAX_SECONDS, PANNS_SR, Tagger, decode32, summarise,  # noqa: E402,F401
                         talk_tail)


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
