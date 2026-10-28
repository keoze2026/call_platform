"""CH-352: every call's Cost in Reports is what its charge took.

The hangup charge wrote the amount on the call log only (an .update(), which
fires no signal), so the Reports copy of most calls kept 0 and every report
fell back to an estimate - without the recording and VoIP Shield fees, and
mixed with the calls that did carry their charge. New calls are right from
CH-352; this puts the charged amount on calls already made.

    docker compose exec -T web python manage.py sync_call_costs            # list only
    docker compose exec -T web python manage.py sync_call_costs --apply
    docker compose exec -T web python manage.py sync_call_costs --days 120 --apply

After --apply the saved copies of past days are cleared, so Reports show the
fixed Cost on the next open (press Refresh on a day already open).

The truth is the balance charge itself (one CHARGE per call, keyed on the
call's sid). Nothing is charged or refunded - only the Cost shown changes.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from analytics.models import CallRecord
from billing.models import Transaction
from routing.models import CallLog


class Command(BaseCommand):
    help = "Put each call's charged amount on the call log and in Reports (CH-352)."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=60, help='how far back (default 60)')
        parser.add_argument('--apply', action='store_true', help='fix them (default: list only)')

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts['days'])
        calls = CallLog.objects.filter(created_at__gte=since).exclude(twilio_call_sid='')
        sid_of = dict(calls.values_list('id', 'twilio_call_sid'))
        org_of = dict(calls.values_list('id', 'organization_id'))

        charged = defaultdict(lambda: Decimal('0'))
        tx = Transaction.objects.filter(
            transaction_type=Transaction.Type.CHARGE,
            status=Transaction.Status.COMPLETED,
            created_at__gte=since - timedelta(days=1),
        ).exclude(call_sid='').values_list('organization_id', 'call_sid', 'amount')
        for org_id, sid, amount in tx.iterator(chunk_size=5000):
            charged[(org_id, sid)] += Decimal(amount or 0)

        log_cost = dict(calls.values_list('id', 'platform_cost'))
        rec_cost = dict(CallRecord.objects.filter(id__in=list(sid_of)).values_list('id', 'platform_cost'))

        fix_log, fix_rec = [], []
        per_day = defaultdict(lambda: [0, Decimal('0'), Decimal('0')])   # calls, shown, charged
        day_of = dict(calls.values_list('id', 'created_at'))
        for cid, sid in sid_of.items():
            should = charged.get((org_of[cid], sid))
            if should is None:
                continue          # never charged: Reports keep the estimate
            should = should.quantize(Decimal('0.0001'))
            if Decimal(log_cost.get(cid) or 0) != should:
                fix_log.append((cid, should))
            if cid in rec_cost and Decimal(rec_cost[cid] or 0) != should:
                fix_rec.append((cid, should))
                d = timezone.localtime(day_of[cid]).date()
                per_day[d][0] += 1
                per_day[d][1] += Decimal(rec_cost[cid] or 0)
                per_day[d][2] += should

        for d in sorted(per_day):
            n, shown, real = per_day[d]
            self.stdout.write(f'{d}  {n} calls  Reports had ${shown:.2f}  charged ${real:.2f}')
        self.stdout.write(
            f'\n{len(fix_rec)} calls show a Cost other than their charge in Reports; '
            f'{len(fix_log)} on the call log')
        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to fix them.')
            return
        with transaction.atomic():
            for cid, should in fix_log:
                CallLog.objects.filter(pk=cid).update(platform_cost=should)
            for cid, should in fix_rec:
                CallRecord.objects.filter(pk=cid).update(platform_cost=should)
        self.stdout.write(self.style.SUCCESS(
            f'Fixed {len(fix_rec)} calls in Reports and {len(fix_log)} on the call log.'))
        # Past days are served from a saved copy for up to 6 hours: drop them,
        # so Reports show the fixed Cost on the next open or Refresh.
        from analytics.report_cache import clear_all
        self.stdout.write(f'Cleared {clear_all()} saved past-day reports.')
