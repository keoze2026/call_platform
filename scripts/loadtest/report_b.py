"""CH-465 load test, Server B: what the test calls look like on B.

    cd /opt/call_platform && bash scripts/loadtest/b_loadtest.sh report

CH-469: runs inside the TEST routing, on the test copy avortyx_loadtest only.

Counts every LOADTEST call: by status, by buyer, how many Server A routed
alone (B switched off), how many were billed, and the most calls each buyer
had at once - which must never pass its limit (LT-A 400, LT-B 250).
"""
from collections import Counter

from django.conf import settings
from django.db.models import Sum

from routing.models import CallLog

if settings.DATABASES['default']['NAME'] != 'avortyx_loadtest':
    raise SystemExit('Refused: this is not the test database (avortyx_loadtest).')

calls = CallLog.objects.filter(campaign__name='LOADTEST', called_number='+19995550100')
print(f'LOADTEST calls on B: {calls.count()}')
print('  by status:', dict(Counter(calls.values_list('status', flat=True))))
print('  by buyer: ', dict(Counter(calls.values_list('buyer__name', flat=True))))
by_a = [c for c in calls.only('routing_trace') if 'Server A' in str((c.routing_trace or {}).get('routed_by', ''))]
print(f'  routed by Server A while B was off: {len(by_a)}')
done = calls.filter(status='completed')
print(f'  completed: {done.count()}, talk time {sum(done.values_list("duration", flat=True)) // 60} min, '
      f'charged {done.aggregate(s=Sum("platform_cost"))["s"] or 0}')
still_open = calls.filter(status__in=['ringing', 'in_progress']).count()
print(f'  still open (should be 0 a few minutes after the test): {still_open}')
for name, limit in (('LT-A', 400), ('LT-B', 250)):
    events = []
    for start, end in calls.filter(buyer__name=name).exclude(ended_at=None).values_list('created_at', 'ended_at'):
        events += [(start, 1), (end, -1)]
    now = peak = 0
    for _, step in sorted(events, key=lambda e: (e[0], e[1])):
        now += step
        peak = max(peak, now)
    print(f'  {name}: most calls at once {peak} (limit {limit}) -> {"OK" if peak <= limit else "OVER THE LIMIT"}')
