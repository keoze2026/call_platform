"""CH-672: correct the ring time (TTC) of calls that moved to the next buyer.

Before CH-672 a call that moved on (CH-514) showed the LAST buyer with the
caller's whole wait as its ring time, and kept each missed try with 0 s:
5619328838, 12 Nov 10:31 AM EST - HOZ shown 21 s, it had the call 6 s; KOZ,
rung first for 15 s, kept 0 s.

    python manage.py fix_failover_ttc              # dry run: what would change
    python manage.py fix_failover_ttc --apply      # write it
    python manage.py fix_failover_ttc --days 3 --caller 5619328838

Only unanswered calls with tries are touched (an answered call's ring time
came from the switch and was already the last buyer's own). A missed try is
only corrected when it was kept with 0 s. Nothing is deleted.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Correct ring times of calls that moved to the next buyer (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--caller', default='')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **o):
        from routing.models import CallLog
        from routing.signals import mirror_call_log
        since = timezone.now() - timedelta(days=max(1, o['days']))
        roots = (CallLog.objects.filter(retry_hidden=False, created_at__gte=since, ended_at__isnull=False)
                 .exclude(status=CallLog.Status.COMPLETED).select_related('buyer'))
        if o['caller']:
            roots = roots.filter(caller_number__endswith=o['caller'][-10:])
        root_ids = set(CallLog.objects.filter(retry_root_id__in=roots.values('id'), twilio_call_sid__contains='#try')
                       .values_list('retry_root_id', flat=True))
        changed = 0
        for call in roots.filter(id__in=root_ids).order_by('created_at'):
            tries = list(CallLog.objects.filter(retry_root_id=call.id, twilio_call_sid__contains='#try')
                         .exclude(pk=call.pk).select_related('buyer').order_by('ended_at'))
            start = call.created_at
            lines = []
            for t in tries:
                if not t.ended_at:
                    continue
                if not t.ring_seconds:
                    ring = max(0, int((t.ended_at - start).total_seconds()))
                    lines.append(f'  try  {t.buyer.name if t.buyer else "-":8} {t.destination_number}: '
                                 f'ring {t.ring_seconds} -> {ring} s')
                    if o['apply']:
                        CallLog.objects.filter(pk=t.pk).update(ring_seconds=ring, created_at=start)
                start = t.ended_at
            ring = max(0, int((call.ended_at - start).total_seconds()))
            if call.ring_seconds != ring:
                lines.append(f'  call {call.buyer.name if call.buyer else "-":8} {call.destination_number}: '
                             f'ring {call.ring_seconds} -> {ring} s')
                if o['apply']:
                    CallLog.objects.filter(pk=call.pk).update(ring_seconds=ring)
            if lines:
                changed += 1
                self.stdout.write(f'{timezone.localtime(call.created_at):%b %d %H:%M:%S} {call.caller_number}')
                self.stdout.write('\n'.join(lines))
                if o['apply']:
                    try:
                        mirror_call_log(call.id)
                    except Exception as e:  # noqa: BLE001 - the call row is fixed either way
                        self.stderr.write(f'  report copy not refreshed: {e}')
        self.stdout.write(f"\n{changed} call(s) {'corrected' if o['apply'] else 'would be corrected (dry run)'}")
