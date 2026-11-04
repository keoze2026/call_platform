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
        from routing import ai_labels
        counts = {'voice': 0, 'no_voice': 0, 'missing': 0, 'skipped': 0}
        now = timezone.now()
        new_no_voice = []
        orgs = set()
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
            extra = {}
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
                # CH-543: each side apart (two channels) and the AI label.
                ai = ai_labels.decide(r)

                def num(key):
                    try:
                        return max(0.0, float(r[key])) if r.get(key) is not None else None
                    except (TypeError, ValueError):
                        return None
                extra = {
                    'voice_channels': 2 if int(r.get('channels') or 1) >= 2 else 1,
                    'voice_caller_seconds': num('caller_speech'),
                    'voice_agent_seconds': num('agent_speech'),
                    'voice_caller_first': num('caller_first'),
                    'voice_beep_seconds': num('beep'),
                    'voice_audio_fault': ai['fault'],
                    'ai_label': ai['label'],
                    'ai_confidence': ai['confidence'],
                    'ai_comment': ai['comment'][:300],
                    'ai_checked_at': now,
                }
            try:
                before = (CallLog.objects.filter(id=call_id)
                          .values_list('ai_label', 'organization_id').first()) or ('', None)
                n = CallLog.objects.filter(id=call_id).update(
                    voice_label=label, voice_seconds=speech, voice_audio_seconds=audio, voice_checked_at=now,
                    **extra)
            except Exception:
                n = 0
            if not n:
                counts['skipped'] += 1
                continue
            # updated_at too (CH-546): the Reports page reloads when its calls'
            # fingerprint changes, and .update() leaves auto_now alone.
            CallRecord.objects.filter(id=call_id).update(voice_label=label, voice_seconds=speech,
                                                         ai_label=extra.get('ai_label', ''), updated_at=now)
            counts[label] += 1
            orgs.add(before[1])
            if extra.get('ai_label') in ai_labels.NO_VOICE and before[0] != extra['ai_label']:
                new_no_voice.append(call_id)
        summary = 'voice {voice} | no_voice {no_voice} | missing {missing} | skipped {skipped}'.format(**counts)
        # CH-536: the instant report to the team's Telegram (VOICE_TELEGRAM_ALERTS).
        if new_no_voice:
            from routing.voice_alerts import send_no_voice
            try:
                sent = send_no_voice(new_no_voice)
                if sent:
                    summary += f' | telegram {sent}'
            except Exception:
                import logging
                logging.getLogger(__name__).exception('voice alerts failed')
        # CH-543: "Pending AI check" turns into the label on open pages at once.
        from routing.call_events import publish
        for org_id in orgs:
            publish(org_id, 'ai.checked')
        self.stdout.write(summary)
