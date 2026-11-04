"""CH-543: the AI audit label of a call, from each side of its recording.

Sir, 4 Nov: "Record every call in dual channel (caller left, agent right). The
AI analyses each side separately. Classify every call into:
  - No Voice | Dead Caller: both sides silent. Flag "Possible audio fault" if
    there's no audio data.
  - No Voice | No Beep or Customer: agent speaks, caller silent.
  - No Voice | Only Agent + Beeping: agent speaks, caller side has tones or
    machine sound but no human.
  - Valid Call."

The listening is done on our own server (scripts/voice_check): Silero VAD, a
neural voice detector, measures the seconds of human speech on the caller's
side and on the agent's side separately; a tone detector measures beeping on
the caller's side. This module turns those numbers into the label - the same
numbers always give the same label, so a wrong one can be traced to its
numbers and the thresholds tuned from the audit team's corrections.

  caller speech >= VOICE_MIN_SECONDS                     -> valid
  else agent speech >= VOICE_MIN_SECONDS:
       caller beeping >= AI_BEEP_SECONDS                 -> agent_beep
       otherwise                                          -> agent_only
  else                                                    -> dead_caller
       (+ "Possible audio fault" when neither file holds any audio data)

A call recorded before Server A split the sides has one mixed file: nobody
speaking is still "dead_caller"; speech there is "mixed", shown as "Voice Found"
(CH-548, Sir's one word) - which side spoke cannot be told.

Confidence (%) says how clear the numbers were: far from the thresholds is
high, close to them is lower.
"""
from django.conf import settings

VALID = 'valid'
DEAD = 'dead_caller'
AGENT_ONLY = 'agent_only'
AGENT_BEEP = 'agent_beep'
MIXED = 'mixed'
LABELS = (VALID, DEAD, AGENT_ONLY, AGENT_BEEP)          # what a reviewer can pick
NO_VOICE = (DEAD, AGENT_ONLY, AGENT_BEEP)
NAMES = {
    VALID: 'Valid Call',
    DEAD: 'No Voice | Dead Caller',
    AGENT_ONLY: 'No Voice | No Beep or Customer',
    AGENT_BEEP: 'No Voice | Only Agent + Beeping',
    MIXED: 'Voice Found',          # Sir, 4 Nov: one word for it
}
FAULT_NOTE = 'Possible audio fault'


def _min_speech() -> float:
    return float(getattr(settings, 'VOICE_MIN_SECONDS', 1.0) or 1.0)


def _min_beep() -> float:
    return float(getattr(settings, 'AI_BEEP_SECONDS', 0.3) or 0.3)


def decide(r: dict) -> dict:
    """{label, confidence, fault, comment} from one voice-check result."""
    thr, beep_thr = _min_speech(), _min_beep()
    fault = bool(r.get('no_data'))
    if int(r.get('channels') or 1) < 2 or r.get('caller_speech') is None:
        speech = float(r.get('speech') or 0)
        if speech >= thr:
            return {'label': MIXED, 'confidence': None, 'fault': False,
                    'comment': f'Voice found: {speech:.1f} s of speech (caller and agent not recorded apart)'}
        return {'label': DEAD, 'confidence': 70 if not fault else 90, 'fault': fault,
                'comment': (FAULT_NOTE + ': no audio data. ' if fault else '')
                + f'No speech on the call ({speech:.1f} s, one channel)'}
    caller = float(r.get('caller_speech') or 0)
    agent = float(r.get('agent_speech') or 0)
    beep = float(r.get('beep') or 0)
    first = r.get('caller_first')
    sides = f'caller {caller:.1f} s, agent {agent:.1f} s, beeping {beep:.1f} s'
    if caller >= thr:
        when = f', caller first spoke {float(first):.0f} s after answer' if first is not None else ''
        return {'label': VALID, 'confidence': 95 if caller >= 3 * thr else 80, 'fault': False,
                'comment': f'Caller speaks ({sides}{when})'}
    if agent >= thr:
        if beep >= beep_thr:
            return {'label': AGENT_BEEP, 'confidence': 90 if beep >= 1.0 else 75, 'fault': False,
                    'comment': f'Agent speaks, caller side only beeping / machine sound ({sides})'}
        return {'label': AGENT_ONLY, 'confidence': 90 if caller == 0 else 75, 'fault': False,
                'comment': f'Agent speaks, caller silent, no beeping ({sides})'}
    return {'label': DEAD, 'confidence': 95 if caller + agent == 0 else 80, 'fault': fault,
            'comment': (FAULT_NOTE + ': no audio data on either side. ' if fault else '')
            + f'Both sides silent ({sides})'}
