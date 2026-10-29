"""Destination and buyer call caps, counted the one way the routing and the
Destinations page both use (CH-396).

Sir, 29 Oct: "Daily cap system is not properly functioning, you can see calls
went extra than the daily cap limit set" - R48 13/12, Q07 20/10, Q17 6/3.
The Hourly, Daily, Monthly and Global caps of a destination were saved and
shown, and routing never read them: only its CC cap was checked. They are now
checked before a call is sent to the line, the same way the CC cap is.

What counts against a cap: calls SENT to the line. Two kinds of row carry the
line's number and were never a call to it:
  - "No Destination" refusals: a call the line could not take (CC full, cap
    full, paused) is recorded on that line so it shows as missed there, but
    it was never dialled. Counted, a full line would keep "filling" with the
    calls it refused and the Daily figure would run past its cap.
  - hidden Revo retries (retry_hidden): Revo resends an unanswered call every
    few seconds and each retry arrives as a new row; the Reports show one row
    per caller call, and so does this count.

The day and the month are cut at midnight in the destination's own time zone
(Eastern by default), as on the Destinations page; the hour is the last 60
minutes.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

NO_DESTINATION = 'No Destination'
DEFAULT_TZ = 'America/New_York'


def counted_q():
    """Rows that are a call sent to a line: not a refusal stamped on it, not a
    hidden retry of a call already counted."""
    return Q(retry_hidden=False) & ~Q(block_reason=NO_DESTINATION)


def own_calls_q(d):
    """Calls on destination `d`: its number, and its buyer (or none recorded).
    One TFN can be on several buyers (CH-392); each counts its own."""
    q = Q(destination_number=d.tfn)
    if d.buyer_id:
        q &= Q(buyer_id=d.buyer_id) | Q(buyer_id__isnull=True)
    return q


def _zone(name):
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def windows(tz_name, now=None):
    """(hour_ago, day_start, month_start) as absolute instants."""
    now = now or timezone.now()
    local = timezone.localtime(now, _zone(tz_name))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(hours=1), day, day.replace(day=1)


def destination_cap_hit(d, now=None):
    """'daily cap 12/12' when one of the line's caps is full, else None.
    A cap of 0 is no cap."""
    from routing.models import CallLog

    caps = (('hourly', d.hourly_cap), ('daily', d.daily_cap),
            ('monthly', d.monthly_cap), ('global', d.global_cap))
    if not any((c or 0) > 0 for _, c in caps) or not d.tfn:
        return None
    hour_ago, day_start, month_start = windows(d.timezone, now)
    since = {'hourly': hour_ago, 'daily': day_start, 'monthly': month_start, 'global': None}
    base = CallLog.objects.filter(organization_id=d.organization_id).filter(own_calls_q(d)).filter(counted_q())
    for period, cap in caps:
        if not cap or cap <= 0:
            continue
        qs = base if since[period] is None else base.filter(created_at__gte=since[period])
        n = qs.count()
        if n >= cap:
            return f'{period} cap {n}/{cap}'
    return None


def buyer_cap_hit(buyer, cap, now=None):
    """'daily cap 50/50' when one of the buyer's own caps is full, else None.
    Counted on the buyer's day (its own zone, else Eastern) - it was UTC, so
    a buyer's daily cap started again at 8 PM Eastern."""
    from routing.models import CallLog

    # Daily and monthly, the two this check has always applied. The buyer's
    # global cap was never applied; switching it on here could stop a buyer
    # whose old total passed a forgotten limit, so it stays as it was.
    limits = (('daily', cap.max_calls_daily), ('monthly', cap.max_calls_monthly))
    if not any((c or 0) > 0 for _, c in limits):
        return None
    _, day_start, month_start = windows(getattr(buyer, 'timezone', '') or DEFAULT_TZ, now)
    since = {'daily': day_start, 'monthly': month_start}
    base = CallLog.objects.filter(buyer=buyer).exclude(destination_number='').filter(counted_q())
    for period, limit in limits:
        if not limit or limit <= 0:
            continue
        qs = base if since[period] is None else base.filter(created_at__gte=since[period])
        n = qs.count()
        if n >= limit:
            return f'{period} cap {n}/{limit}'
    return None


def buyer_usage(organization, buyer_ids, now=None) -> dict:
    """{buyer_id: counters} for the Buyers page (CH-405), counted the way the
    routing counts caps: calls sent to the buyer (not "No Destination"
    refusals, not hidden Revo retries), the day and month from midnight
    Eastern - the list cut them at midnight UTC, so "today" restarted at
    8 PM. Spend is what the buyer pays (revenue); it was the publisher's
    payout, which is why every spend equalled the call count."""
    from decimal import Decimal
    from django.db.models import Count, Q, Sum
    from django.db.models.functions import Coalesce
    from buyers.destination import Destination
    from buyers.models import Buyer, BuyerCap
    from routing.models import CallLog

    buyer_ids = list(buyer_ids)
    if not buyer_ids:
        return {}
    hour_ago, day_start, month_start = windows(DEFAULT_TZ, now)
    zero = Decimal('0.00')
    money = lambda q=None: Coalesce(Sum('revenue', filter=q), zero)  # noqa: E731
    out = {b: {'calls_hourly': 0, 'calls_today': 0, 'calls_month': 0, 'calls_global': 0,
               'spend_hourly': zero, 'spend_today': zero, 'spend_month': zero, 'spend_global': zero,
               'daily_cap': 0, 'monthly_cap': 0, 'cap_used_today': 0, 'cap_used_month': 0,
               'tfn_count': 0, 'tfn_cap_total': 0, 'tfn_uncapped': 0} for b in buyer_ids}

    sent = CallLog.objects.filter(organization=organization, buyer_id__in=buyer_ids) \
        .exclude(destination_number='').filter(counted_q())
    for r in sent.values('buyer_id').annotate(
            calls_hourly=Count('id', filter=Q(created_at__gte=hour_ago)),
            calls_today=Count('id', filter=Q(created_at__gte=day_start)),
            calls_month=Count('id', filter=Q(created_at__gte=month_start)),
            calls_global=Count('id'),
            spend_hourly=money(Q(created_at__gte=hour_ago)),
            spend_today=money(Q(created_at__gte=day_start)),
            spend_month=money(Q(created_at__gte=month_start)),
            spend_global=money()):
        row = out.get(r.pop('buyer_id'))
        if row is not None:
            row.update(r)

    # The buyer's own caps, and how far it is into them - on its own day, the
    # one routing checks (buyer_cap_hit).
    caps = {c.buyer_id: c for c in BuyerCap.objects.filter(buyer_id__in=buyer_ids)}
    for b in Buyer.objects.filter(id__in=caps).only('id', 'timezone'):
        cap, row = caps[b.id], out[b.id]
        row['daily_cap'] = cap.max_calls_daily or 0
        row['monthly_cap'] = cap.max_calls_monthly or 0
        if (b.timezone or DEFAULT_TZ) == DEFAULT_TZ:
            row['cap_used_today'], row['cap_used_month'] = row['calls_today'], row['calls_month']
        else:
            _, d0, m0 = windows(b.timezone, now)
            mine = sent.filter(buyer_id=b.id)
            row['cap_used_today'] = mine.filter(created_at__gte=d0).count()
            row['cap_used_month'] = mine.filter(created_at__gte=m0).count()
    for b, row in out.items():
        if b not in caps:
            row['cap_used_today'], row['cap_used_month'] = row['calls_today'], row['calls_month']

    # Its live TFNs and their daily caps, for the cap breakdown.
    for r in Destination.objects.filter(organization=organization, buyer_id__in=buyer_ids, enabled=True) \
            .values('buyer_id').annotate(n=Count('id'), total=Coalesce(Sum('daily_cap'), 0),
                                         uncapped=Count('id', filter=Q(daily_cap__lte=0))):
        row = out.get(r['buyer_id'])
        if row is not None:
            row.update(tfn_count=r['n'], tfn_cap_total=r['total'], tfn_uncapped=r['uncapped'])
    return out
