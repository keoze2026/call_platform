"""Answer, with numbers, what the Calls-by-hour chart is actually being sent.

Three questions from the frontend, taken literally:

  1. per hour: period, calls, connected, no_answer, revenue
  2. does calls equal connected + no_answer, and if not, which statuses are
     missing from the chart
  3. do those hours match a direct database count per hour in Eastern Time

    docker compose exec -T web python manage.py shell < scripts/diagnose_hourly_chart.py

Read-only. It calls the same service the endpoint calls, with the same filters
the browser sends, so what it prints is what the chart receives.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncHour

from accounts.models import User
from analytics.models import CallRecord
from analytics.services import AnalyticsService
from routing.models import CallLog

DAY = '2026-10-01'
TZNAME = 'America/New_York'
TZ = ZoneInfo(TZNAME)


class Filters:
    """Exactly what the browser sends."""
    date_from = DAY
    date_to = DAY
    timezone = TZNAME
    granularity = 'hour'
    start_date = None
    end_date = None
    campaign_id = None
    buyer_id = None
    publisher_id = None
    destination = None
    status = None


user = (
    User.objects.filter(role='admin', organization__isnull=False)
    .annotate(n=Count('organization__call_logs')).order_by('-n').first()
)
print(f"as {user.email} ({user.organization})   day {DAY} in {TZNAME}\n")

# ── 1. what the endpoint returns ─────────────────────────────────────────────
series = AnalyticsService.get_time_series(user, Filters())

print("WHAT THE CHART IS SENT")
print(f"  {'period':<28} {'calls':>6} {'conn':>6} {'noans':>6} {'c+n':>6} {'revenue':>10}")
t_calls = t_conn = t_noans = 0
t_rev = Decimal('0')
for r in series:
    s = r['connected'] + r['no_answer']
    flag = '' if s == r['calls'] else '   <- does not add up'
    print(f"  {r['period']:<28} {r['calls']:>6} {r['connected']:>6} "
          f"{r['no_answer']:>6} {s:>6} {r['revenue']:>10}{flag}")
    t_calls += r['calls']; t_conn += r['connected']; t_noans += r['no_answer']
    t_rev += Decimal(str(r['revenue']))
print(f"  {'TOTAL':<28} {t_calls:>6} {t_conn:>6} {t_noans:>6} "
      f"{t_conn + t_noans:>6} {t_rev:>10}")

# ── 2. the same day straight from the mirror the chart reads ─────────────────
start = datetime(*(int(p) for p in DAY.split('-')), tzinfo=TZ)
end = start + timedelta(days=1)

print("\nSTRAIGHT FROM CallRecord, THE TABLE THE CHART READS")
mirror = (
    CallRecord.objects.filter(
        organization=user.organization, created_at__gte=start, created_at__lt=end,
    )
    .annotate(period=TruncHour('created_at', tzinfo=TZ))
    .values('period')
    .annotate(
        calls=Count('id'),
        connected=Count('id', filter=Q(status__in=['completed', 'in_progress'])),
        revenue=Coalesce(Sum('dynamic_revenue'), Decimal('0')),
    ).order_by('period')
)
m_total = 0
for r in mirror:
    m_total += r['calls']
    print(f"  {r['period'].astimezone(TZ):%F %H:%M}  calls {r['calls']:>4}  "
          f"connected {r['connected']:>4}")
print(f"  total {m_total}")

# ── 3. and from the source of truth ──────────────────────────────────────────
print("\nSTRAIGHT FROM CallLog, THE SOURCE OF TRUTH")
source = (
    CallLog.objects.filter(
        organization=user.organization, created_at__gte=start, created_at__lt=end,
    )
    .annotate(period=TruncHour('created_at', tzinfo=TZ))
    .values('period').annotate(calls=Count('id')).order_by('period')
)
s_total = 0
for r in source:
    s_total += r['calls']
    print(f"  {r['period'].astimezone(TZ):%F %H:%M}  calls {r['calls']:>4}")
print(f"  total {s_total}")

print(f"\n  chart total {t_calls}   mirror total {m_total}   source total {s_total}")
if t_calls != s_total:
    print(f"  the chart is showing {t_calls - s_total:+d} against the call log")

# ── 4. every status that day, so nothing is invisible ────────────────────────
print("\nEVERY STATUS THAT DAY")
print("  CallLog:")
for r in (
    CallLog.objects.filter(organization=user.organization,
                           created_at__gte=start, created_at__lt=end)
    .values('status').annotate(n=Count('id')).order_by('-n')
):
    print(f"    {r['status']:<14} {r['n']:>4}")
print("  CallRecord:")
for r in (
    CallRecord.objects.filter(organization=user.organization,
                              created_at__gte=start, created_at__lt=end)
    .values('status').annotate(n=Count('id')).order_by('-n')
):
    print(f"    {r['status']:<14} {r['n']:>4}")

# ── 5. the live calls, which are added on top ────────────────────────────────
print("\nLIVE CALLS, WHICH get_time_series ADDS ON TOP OF THE ABOVE")
live = CallLog.objects.filter(
    organization=user.organization,
    status__in=['in_progress', 'ringing', 'initiated'],
    created_at__gte=start, created_at__lt=end,
)
print(f"  {live.count()} live call(s) in the day window")
# The mirror is keyed on twilio_call_sid, and the signal only mirrors terminal
# statuses - so in principle a live call is NOT in CallRecord and adding it is
# correct rather than double counting. Checked rather than assumed, because the
# webhook path writes the mirror too and may not follow the same rule.
double = 0
for c in live[:40]:
    in_mirror = CallRecord.objects.filter(
        organization=user.organization, twilio_call_sid=c.twilio_call_sid,
    ).exclude(twilio_call_sid='').first()
    if in_mirror:
        double += 1
    print(f"    {c.created_at.astimezone(TZ):%H:%M}  {c.status:<12} "
          f"also in CallRecord: {bool(in_mirror)}"
          f"{'  as ' + in_mirror.status if in_mirror else ''}")
print(f"  {double} live call(s) are in BOTH, and each of those is counted twice -")
print("  once in the mirror row and again as a live call.")

print("\nTHE DAY WINDOW EACH QUERY USES")
print("  _base_qs makes its dates aware in the REQUESTED timezone;")
print("  _live_qs calls make_aware with no timezone, so it uses the server's.")
from django.utils import timezone as djtz
from django.utils.dateparse import parse_datetime
base_from = djtz.make_aware(parse_datetime(DAY + 'T00:00:00'), TZ)
live_from = djtz.make_aware(parse_datetime(DAY + 'T00:00:00'))
print(f"    base window starts {base_from.isoformat()}")
print(f"    live window starts {live_from.isoformat()}")
if base_from != live_from:
    gap = abs((base_from - live_from).total_seconds()) / 3600
    print(f"    they differ by {gap:.0f} hours, so the two halves of the chart")
    print("    are counting different days")
