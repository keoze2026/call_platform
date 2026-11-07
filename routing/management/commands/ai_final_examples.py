"""CH-594: calls the audit team checked by ear, as examples for the LLM.

Sir, 4 Nov: "Audit review: Correct/Wrong buttons on every flagged call ... Use
corrections to retrain." The LLM (scripts/ai_final) cannot be retrained by us,
but it follows worked examples: every run it is shown the team's latest
verdicts - first the calls it got WRONG (what it said, the right label, the
team's note), then some it got right - with each call's transcript and
measurements, and told to judge the same way. A correction made today shapes
the next call's label.

    docker compose exec -T web python manage.py ai_final_examples --limit 12

One JSON object per line: label (the team's), ai_said, wrong (bool), note,
transcript (cut), measurements. Read-only. Our own team's reviews only - the
AI audit is never a buyer's or publisher's (CH-548).
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from routing import ai_labels
from routing.models import CallLog

TRANSCRIPT_CHARS = 700


class Command(BaseCommand):
    help = "The audit team's latest verdicts, as examples for the LLM (JSON lines)."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=12)
        parser.add_argument('--days', type=int, default=30)

    def handle(self, *args, **o):
        limit = max(0, o['limit'])
        if not limit:
            return
        since = timezone.now() - timedelta(days=max(1, o['days']))
        # CH-616 (Sir, 6 Nov): "For now train it for NV" - verdicts on No
        # Voice calls; CH-643 (8 Nov): and on Valid / Caller OK calls, so a
        # No Voice call the AI missed is shown to it as a mistake.
        base = (CallLog.objects.filter(ai_reviewed_at__gte=since, ai_label__in=ai_labels.AUDITED)
                .exclude(ai_review_label='')
                .order_by('-ai_reviewed_at'))
        # The mistakes teach the most: up to two thirds of the examples.
        wrong = list(base.exclude(ai_review_label=F('ai_label'))[:max(1, (limit * 2) // 3)])
        right = list(base.filter(ai_review_label=F('ai_label'))[:max(0, limit - len(wrong))])
        for c in wrong + right:
            self.stdout.write(json.dumps({
                'label': c.ai_review_label,
                'ai_said': c.ai_label,
                'wrong': c.ai_review_label != c.ai_label,
                'note': (c.ai_review_note or '')[:200],
                'transcript': (c.ai_transcript or '')[:TRANSCRIPT_CHARS],
                'measurements': c.ai_comment or '',
                'talk': int(c.duration or 0),
            }))
