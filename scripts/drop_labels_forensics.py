"""Why 23 JUNE refused calls at 4/5 - and proof of the new drop labels.

Default run: read-only forensics on campaign 23 JUNE for today, answering
the owner's dispute question with numbers.

    docker compose exec web python scripts/drop_labels_forensics.py

--smoke: builds a throwaway campaign and proves the three new behaviours
over the real engine, then deletes everything it made.

    docker compose exec web python scripts/drop_labels_forensics.py --smoke
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    src = open(os.path.join(ROOT, 'manage.py')).read()
    os.environ['DJANGO_SETTINGS_MODULE'] = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)", src).group(1)

import django  # noqa: E402
django.setup()

from datetime import timedelta  # noqa: E402
from django.db.models import Count  # noqa: E402
from django.utils import timezone  # noqa: E402
from campaigns.models import Campaign  # noqa: E402
from routing.models import CallLog  # noqa: E402


def forensics():
    c = Campaign.objects.filter(name__iexact='23 JUNE').first()
    if c is None:
        print('no campaign named 23 JUNE')
        return
    try:
        cap = c.cap
        print(f'campaign: {c.name} ({c.id})')
        print(f'caps: concurrency={cap.max_concurrency} daily={cap.max_calls_daily} '
              f'monthly={cap.max_calls_monthly} global={cap.max_calls_global}')
    except Exception:
        cap = None
        print(f'campaign: {c.name} - NO cap row')

    today = timezone.now().date()
    todays = CallLog.objects.filter(campaign=c, created_at__date=today)
    print(f'\ntoday: {todays.count()} rows total')
    for r in todays.values('status').annotate(n=Count('id')).order_by('-n'):
        print(f"  status {r['status']}: {r['n']}")
    print('top refusal reasons today:')
    for r in (todays.exclude(block_reason='').values('block_reason')
              .annotate(n=Count('id')).order_by('-n')[:8]):
        print(f"  {r['n']:4d}  {r['block_reason']}")

    old_daily = todays.count()
    new_daily = (todays.filter(block_reason='')
                 .exclude(destination_number='').count())
    print(f'\ndaily cap counting - old logic counted: {old_daily} '
          f'(every row, refusals included)')
    print(f'daily cap counting - new logic counts:  {new_daily} '
          f'(dispatched to a destination only)')
    if cap:
        month_start = today.replace(day=1)
        old_month = CallLog.objects.filter(campaign=c, created_at__date__gte=month_start).count()
        new_month = (CallLog.objects.filter(campaign=c, created_at__date__gte=month_start,
                                            block_reason='')
                     .exclude(destination_number='').count())
        old_global = CallLog.objects.filter(campaign=c).count()
        new_global = (CallLog.objects.filter(campaign=c, block_reason='')
                      .exclude(destination_number='').count())

        def verdict(name, old, new, limit):
            if limit <= 0:
                return f'  {name}: unlimited'
            return (f'  {name}: limit {limit} | old counting {old} '
                    f'{"TRIPPED" if old >= limit else "ok"} | new counting {new} '
                    f'{"TRIPPED" if new >= limit else "ok"}')

        print('\nwhich limit was refusing calls:')
        print(verdict('daily  ', old_daily, new_daily, cap.max_calls_daily))
        print(verdict('monthly', old_month, new_month, cap.max_calls_monthly))
        print(verdict('global ', old_global, new_global, cap.max_calls_global))

    live = CallLog.objects.filter(
        campaign=c, status__in=['in_progress', 'ringing'],
        ended_at__isnull=True,
        created_at__gte=timezone.now() - timedelta(hours=4),
    )
    print(f'\nlive rows in the 4h window right now: {live.count()}'
          + (f' / cc limit {cap.max_concurrency}' if cap and cap.max_concurrency else ''))
    for r in live.order_by('created_at'):
        age = int((timezone.now() - r.created_at).total_seconds() / 60)
        print(f'  {r.created_at:%H:%M} ({age} min ago) {r.status} caller={r.caller_number} '
              f'-> {r.destination_number or "(none)"}')
    if live.exists():
        print('  a row older than any real call is a stuck row, counted as live')


def smoke():
    from accounts.models import User
    from buyers.models import Buyer
    from buyers.destination import Destination
    from campaigns.models import CampaignCap
    from routing.models import RoutingRule, RuleDestination
    from routing.engine import RoutingEngine

    admin = User.objects.filter(role='admin', is_active=True,
                                organization__isnull=False).first()
    org = admin.organization
    fail = 0

    def check(label, got, want):
        nonlocal fail
        ok = got == want
        fail += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r} (want {want!r})")

    import uuid as _uuid

    def mklog(**kw):
        # twilio_call_sid is unique on the table, so every row needs its own.
        kw.setdefault('twilio_call_sid', f'droplabel-{_uuid.uuid4()}')
        return CallLog.objects.create(
            organization=org, caller_number='x', called_number='y', **kw)

    camp = Campaign.objects.create(organization=org, name='DROPLABEL SMOKE')
    cap = CampaignCap.objects.create(campaign=camp)
    buyer = Buyer.objects.create(organization=org, name='DROPLABEL BUYER', status='active')
    tfn = '15550009999'
    Destination.objects.create(organization=org, buyer=buyer, name='droplabel',
                               tfn=tfn, enabled=True, concurrency_cap=1)
    rule = RoutingRule.objects.create(organization=org, campaign=camp, name='droplabel',
                                      rule_type=RoutingRule.RuleType.PRIORITY,
                                      status=RoutingRule.Status.ACTIVE, priority=1)
    RuleDestination.objects.create(rule=rule, destination=tfn,
                                   destination_type='phone', buyer=buyer, priority=1)
    caller = {'caller_number': '15551230000', 'phone_number': None}
    try:
        # 1. A refused row (ours) must NOT eat the daily quota.
        cap.max_calls_daily = 1
        cap.save()
        mklog(campaign=camp, status='failed', block_reason='No matching rule found')
        r = RoutingEngine.route_call(str(camp.id), dict(caller))
        check('refused rows ignored by daily cap', r['error'], None)

        # 2. A dispatched row DOES count, with the daily label, never overflow.
        mklog(campaign=camp, status='completed', destination_number=tfn)
        r = RoutingEngine.route_call(str(camp.id), dict(caller))
        check('daily full names itself       ', r['error'], 'Campaign daily limit reached')
        cap.max_calls_daily = 0
        cap.save()

        # 3. Buyer line at CC -> missed on that TFN, not a campaign drop.
        live = mklog(campaign=camp, buyer=buyer, status='in_progress',
                     destination_number=tfn)
        r = RoutingEngine.route_call(str(camp.id), dict(caller))
        check('destination CC full label     ', r['error'], 'Destination missed (CC full)')
        check('refused TFN carried           ', r.get('refused_destination'), tfn)
        check('refused buyer carried         ', getattr(r.get('refused_buyer'), 'id', None), buyer.id)
        live.delete()

        # 4. Campaign CC truly full -> the one and only overflow label.
        cap.max_concurrency = 1
        cap.save()
        mklog(campaign=camp, status='in_progress', destination_number=tfn)
        r = RoutingEngine.route_call(str(camp.id), dict(caller))
        check('campaign cc full -> overflow  ', r['error'], 'Campaign cap reached')

        # 5. The off-by-one the boss's team caught: "not coming above 4 cc".
        # The caller's own row exists (status ringing) before the check runs;
        # it must not count against the campaign's CC.
        CallLog.objects.filter(campaign=camp, status__in=['in_progress', 'ringing']).delete()
        cap.max_concurrency = 5
        cap.save()
        for _ in range(4):
            mklog(campaign=camp, status='in_progress')
        mklog(campaign=camp, status='ringing', twilio_call_sid='droplabel-self')
        with_self = dict(caller)
        with_self['twilio_call_sid'] = 'droplabel-self'
        r = RoutingEngine.route_call(str(camp.id), dict(with_self))
        check('4 live + itself routes (5th)  ', r['error'], None)
        mklog(campaign=camp, status='in_progress')
        r = RoutingEngine.route_call(str(camp.id), dict(with_self))
        check('5 live + itself refuses (6th) ', r['error'], 'Campaign cap reached')
    finally:
        CallLog.objects.filter(campaign=camp).delete()
        camp.delete()
        buyer.delete()
        print('smoke objects deleted')
    print('ALL GREEN' if fail == 0 else f'{fail} FAILURES')
    sys.exit(1 if fail else 0)


if '--smoke' in sys.argv:
    smoke()
else:
    forensics()
