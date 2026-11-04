"""CH-553: PANNs (Cnn14, AudioSet - 527 kinds of sound) for the AI audit.

Shared by scripts/sound_check (the "try" tool, CH-552) and voice_check.py,
which asks it - only for a side where Silero heard no speech - how many
seconds were music (IVR / hold music) and machine tones (beeps, dial / busy
tone, ringing, DTMF). Optional: when PyTorch / PANNs are not installed
(scripts/sound_check/install.sh), sound() returns None and the AI audit works
as before.
"""
import os
import subprocess

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
        if n == 0 and len(audio) >= PANNS_SR // 2:
            # a call shorter than one window (0.5 to 2 s): one padded window
            audio = np.pad(audio, (0, step - len(audio)))
            n = 1
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



_TAGGER = None
_FAILED = False


def tagger():
    """The model, loaded once per process; None when PANNs is not installed."""
    global _TAGGER, _FAILED
    if _TAGGER is None and not _FAILED:
        try:
            _TAGGER = Tagger()
        except Exception:
            _FAILED = True
    return _TAGGER


def sound(pcm16, sr=16000):
    """Seconds of music and machine tone in mono int16 audio (bytes), at most
    MAX_SECONDS of it: {'music', 'tone', 'speech', 'seconds'} - or None when
    PANNs is not available."""
    t = tagger()
    if t is None or not pcm16:
        return None
    import numpy as np
    x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    x = x[:MAX_SECONDS * sr]
    if sr != PANNS_SR:                                   # the model listens at 32 kHz
        n = int(len(x) * PANNS_SR / sr)
        x = np.interp(np.arange(n) * (sr / PANNS_SR), np.arange(len(x)), x).astype(np.float32)
    s = summarise(t.windows(x), t.labels, t.index)
    return {'music': s['music'], 'tone': s['tone'], 'speech': s['speech'], 'seconds': s['seconds']}
