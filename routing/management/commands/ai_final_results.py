"""CH-555: save the final AI label from scripts/ai_final/ai_final.py.

Sir, 4 Nov: "After each call, an LLM reads the transcript plus the Silero and
PANNs results and sets the final label ... It also writes a one-line summary."

JSON lines on stdin:
    {"id", "label", "confidence", "summary", "transcript", "by"}   the final label
    {"id", "error"}                                                 this try failed

The final label replaces ai_label (what every page shows); the rule label
(Silero / PANNs) stays in ai_rule_label. A call the LLM newly calls one of the
three No Voice types goes to the team's Telegram like any other. A failed try
keeps the rule label and is tried again only with --retry-failed.
"""
import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.models import CallRecord
from routing import ai_labels
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Save final AI labels (transcript + LLM) read from stdin (JSON lines).'

    def handle(self, *args, **o):
        now = timezone.now()
        counts = {'saved': 0, 'failed': 0, 'skipped': 0}
        orgs, new_no_voice = set(), []
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                call_id = str(r['id'])
                c = CallLog.objects.filter(id=call_id).values('ai_label', 'ai_rule_label', 'organization_id',
                                                               'ai_final_at', 'ai_final_by').first()
            except (ValueError, KeyError, TypeError, Exception):
                counts['skipped'] += 1
                continue
            if c is None:
                counts['skipped'] += 1
                continue
            # CH-594: with AI_FINAL_ENABLED the rule step held the No Voice
            # alert back for this label; it is sent here, once.
            waits = getattr(settings, 'AI_FINAL_ENABLED', False)
            had_final = bool(c['ai_final_at']) and not (c['ai_final_by'] or '').startswith('error')
            if r.get('error'):
                kept = {'ai_transcript': str(r['transcript'])[:20000]} if r.get('transcript') else {}
                CallLog.objects.filter(id=call_id).update(
                    ai_final_at=now, ai_final_by=f"error: {r['error']}"[:120], **kept)
                counts['failed'] += 1
                # The LLM could not decide: the alert held back for it goes out
                # on the rule label (first failure only - a retry sends nothing).
                if waits and not c['ai_final_at'] and c['ai_label'] in ai_labels.NO_VOICE:
                    new_no_voice.append(call_id)
                continue
            label = str(r.get('label') or '').strip().lower()
            if label not in ai_labels.LABELS:
                CallLog.objects.filter(id=call_id).update(
                    ai_final_at=now, ai_final_by=f'error: the LLM gave "{label[:30]}"'[:120])
                counts['failed'] += 1
                continue
            try:
                confidence = max(0, min(100, int(r.get('confidence'))))
            except (TypeError, ValueError):
                confidence = None
            summary = ' '.join(str(r.get('summary') or '').split())[:300]
            CallLog.objects.filter(id=call_id).update(
                ai_rule_label=c['ai_rule_label'] or c['ai_label'], ai_label=label, ai_confidence=confidence,
                ai_summary=summary, ai_transcript=str(r.get('transcript') or '')[:20000],
                ai_final_at=now, ai_final_by=str(r.get('by') or '')[:120])
            CallRecord.objects.filter(id=call_id).update(ai_label=label, ai_confidence=confidence,
                                                         ai_summary=summary, updated_at=now)
            counts['saved'] += 1
            orgs.add(c['organization_id'])
            # Sent before? Without AI_FINAL_ENABLED the rule step alerted on a
            # No Voice rule label; with it, only an earlier final label did.
            alerted = c['ai_label'] in ai_labels.NO_VOICE and (had_final or not waits)
            if label in ai_labels.NO_VOICE and not alerted:
                new_no_voice.append(call_id)
        out = 'saved {saved} | failed {failed} | skipped {skipped}'.format(**counts)
        if new_no_voice:
            from routing.voice_alerts import send_no_voice
            try:
                sent = send_no_voice(new_no_voice)
                if sent:
                    out += f' | telegram {sent}'
            except Exception:
                import logging
                logging.getLogger(__name__).exception('voice alerts failed')
        from routing.call_events import publish
        for org_id in orgs:
            publish(org_id, 'ai.checked')
        self.stdout.write(out)
