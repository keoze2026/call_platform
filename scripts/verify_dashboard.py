"""Check every number on the dashboard against the call log.

Three tables have already been caught showing figures the backend never sent:
the country breakdown (five countries, two in the data), the carrier "Unknown"
row (45 against a real 4), and the status column calling an unanswered call a
failure. Each was found separately, by somebody noticing.

This asks every reporting endpoint the same question at once — does your total
match the call log — so the next one announces itself instead of waiting to be
spotted by the person the platform is being sold to.

    docker compose exec -T web python manage.py shell < scripts/verify_dashboard.py

Read-only. It calls the services the API calls, with the filters the browser
sends, and counts the same day straight from CallLog for comparison.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import User
from analytics.services import AnalyticsService
from routing.models import CallLog

TZNAME = 'America/New_York'
TZ = ZoneInfo(TZNAME)
DAY = timezone.now().astimezone(TZ).date()
DAYSTR = DAY.isoformat()


class Filters:
    date_from = DAYSTR
    date_to = DAYSTR
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
if user is None:
    print('No admin user to check as.')
    raise SystemExit

org = user.organization
print(f'checking {DAY:%A %-d %B} in {TZNAME}, workspace {org}\n')

start = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
end = start + timedelta(days=1)
raw = CallLog.objects.filter(organization=org, created_at__gte=start, created_at__lt=end)

TRUTH = {
    'calls': raw.count(),
    'connected': raw.filter(status__in=['completed', 'in_progress']).count(),
    'revenue': raw.aggregate(v=Coalesce(Sum('revenue'), Decimal('0')))['v'],
    'payout': raw.aggregate(v=Coalesce(Sum('publisher_payout'), Decimal('0')))['v'],
}

print('STRAIGHT FROM THE CALL LOG')
for k, v in TRUTH.items():
    print(f'  {k:<12} {v}')
print()

results = []


def compare(panel, label, reported, expected, tolerance=0):
    ok = abs(float(reported or 0) - float(expected or 0)) <= tolerance
    results.append((ok, panel, label, reported, expected))
    mark = 'PASS' if ok else 'FAIL'
    print(f'  {mark}  {panel:<22} {label:<16} report {str(reported):>10}   call log {str(expected):>10}')


def pick(d, *names):
    for n in names:
        if isinstance(d, dict) and n in d and d[n] is not None:
            return d[n]
    return None


print('EVERY PANEL, AGAINST THAT')

# ── dashboard ────────────────────────────────────────────────────────────────
try:
    dash = AnalyticsService.get_dashboard(user, Filters())
    compare('dashboard', 'calls', pick(dash, 'total_calls', 'calls'), TRUTH['calls'])
    rev = pick(dash, 'total_revenue', 'revenue')
    if rev is not None:
        compare('dashboard', 'revenue', rev, TRUTH['revenue'], tolerance=0.01)
except Exception as e:
    print(f'  FAIL  dashboard             raised {type(e).__name__}: {e}')
    results.append((False, 'dashboard', 'raised', str(e)[:40], ''))

# ── time series ──────────────────────────────────────────────────────────────
try:
    series = AnalyticsService.get_time_series(user, Filters())
    compare('time series', 'calls', sum(r['calls'] for r in series), TRUTH['calls'])
    compare('time series', 'connected', sum(r['connected'] for r in series), TRUTH['connected'])
    compare('time series', 'revenue',
            sum(Decimal(str(r['revenue'])) for r in series), TRUTH['revenue'], tolerance=0.01)
except Exception as e:
    print(f'  FAIL  time series           raised {type(e).__name__}: {e}')
    results.append((False, 'time series', 'raised', str(e)[:40], ''))

# ── the per-dimension breakdowns ─────────────────────────────────────────────
# Each one slices the same day a different way, so each one must still add up to
# the same total. A breakdown that does not is inventing or dropping rows.
BREAKDOWNS = [
    ('campaigns', AnalyticsService.get_campaign_performance),
    ('buyers', AnalyticsService.get_buyer_performance),
    ('publishers', AnalyticsService.get_publisher_performance),
    ('carriers', AnalyticsService.get_carrier_performance),
]
for name, fn in BREAKDOWNS:
    try:
        rows = fn(user, Filters())
        total = 0
        for r in rows:
            v = pick(r, 'total_calls', 'calls', 'incoming')
            total += int(v or 0)
        compare(name, 'calls', total, TRUTH['calls'])
    except Exception as e:
        print(f'  FAIL  {name:<22} raised {type(e).__name__}: {e}')
        results.append((False, name, 'raised', str(e)[:40], ''))

# ── the call log itself ──────────────────────────────────────────────────────
try:
    log = AnalyticsService.get_call_log(user, Filters())
    total = log.get('total') if isinstance(log, dict) else None
    if total is None and isinstance(log, dict):
        for key in ('count', 'total_count'):
            if key in log:
                total = log[key]
                break
    if total is not None:
        compare('call log', 'rows', total, TRUTH['calls'])
    else:
        print('  ----  call log               no total in the response, cannot compare')
except Exception as e:
    print(f'  FAIL  call log               raised {type(e).__name__}: {e}')
    results.append((False, 'call log', 'raised', str(e)[:40], ''))

# ── the two things that have actually been wrong ─────────────────────────────
print()
print('THE DIMENSIONS THAT HAVE BEEN FABRICATED BEFORE')

countries = list(raw.values('caller_country').annotate(n=Count('id')).order_by('-n'))
print(f'  countries in the call log: {len(countries)}')
for r in countries:
    print(f"    {repr(r['caller_country']):<8} {r['n']}")
print('  (if the interface shows more countries than this, it is generating them)')

print()
statuses = list(raw.values('status').annotate(n=Count('id')).order_by('-n'))
print('  statuses in the call log:')
for r in statuses:
    print(f"    {r['status']:<14} {r['n']}")
print('  (no_answer means the buyer did not pick up; failed means we refused to route)')

print()
line_types = list(raw.exclude(ipqs_line_type='').values('ipqs_line_type').annotate(n=Count('id')).order_by('-n'))
print('  line types as stored:')
for r in line_types:
    print(f"    {r['ipqs_line_type']:<22} {r['n']}")
seen = {}
for r in line_types:
    seen.setdefault(r['ipqs_line_type'].strip().lower(), []).append(r['ipqs_line_type'])
split = {k: v for k, v in seen.items() if len(v) > 1}
if split:
    print('  SPLIT BY CASE - these are the same type counted separately:')
    for k, v in split.items():
        print(f'    {k}: {v}')

# ── verdict ──────────────────────────────────────────────────────────────────
print()
print('=' * 72)
failed = [r for r in results if not r[0]]
if not failed:
    print('EVERY PANEL MATCHES THE CALL LOG')
else:
    print(f'{len(failed)} PANEL(S) DISAGREE WITH THE CALL LOG')
    for _ok, panel, label, reported, expected in failed:
        print(f'   {panel} {label}: shows {reported}, call log has {expected}')
print('=' * 72)
