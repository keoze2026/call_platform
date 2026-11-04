"""CH-531: save what scripts/voice_check heard on each recording.

Reads JSON lines on stdin: {"id", "speech": seconds of speech, "audio":
seconds of audio} or {"id", "missing": true}. The label is decided here, so
the threshold (VOICE_MIN_SECONDS, default 1.0) changes without touching the
host script:

    speech <  VOICE_MIN_SECONDS -> "no_voice"   (nobody spoke - or nothing
                                                  the model could hear)
    speech >= VOICE_MIN_SECONDS -> "voice"
    missing                      -> "missing"    (file not found / unreadable)

Updates the call and its report copy; nothing else. Prints a one-line summary.
"""
import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.models import CallRecord
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Save voice-check results read from stdin (JSON lines).'

    def handle(self, *args, **o):
        threshold = float(getattr(settings, 'VOICE_MIN_SECONDS', 1.0) or 1.0)
        counts = {'voice': 0, 'no_voice': 0, 'missing': 0, 'skipped': 0}
        now = timezone.now()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                call_id = str(r['id'])
            except (ValueError, KeyError, TypeError):
                counts['skipped'] += 1
                continue
            if r.get('missing'):
                label, speech, audio = 'missing', None, None
            else:
                try:
                    speech = max(0.0, float(r.get('speech') or 0))
                    audio = max(0.0, float(r.get('audio') or 0))
                except (TypeError, ValueError):
                    counts['skipped'] += 1
                    continue
                label = 'voice' if speech >= threshold else 'no_voice'
            try:
                n = CallLog.objects.filter(id=call_id).update(
                    voice_label=label, voice_seconds=speech, voice_audio_seconds=audio, voice_checked_at=now)
            except Exception:
                n = 0
            if not n:
                counts['skipped'] += 1
                continue
            CallRecord.objects.filter(id=call_id).update(voice_label=label, voice_seconds=speech)
            counts[label] += 1
        self.stdout.write('voice {voice} | no_voice {no_voice} | missing {missing} | skipped {skipped}'.format(**counts))
