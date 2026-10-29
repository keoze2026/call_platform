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
