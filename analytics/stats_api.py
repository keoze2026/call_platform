"""Read-only call statistics for integrations - the call-stats bot.

Authenticated by API key (portal: Settings -> API keys), never by browser
session. Every figure comes from the same service functions the portal's
dashboard and reports run on, so the bot and the screen can never disagree -
one definition of AHT, one definition of connected, one of Dupe (CHANGES:
CH-079/080/090 are the history of why that matters).

Read-only by construction: this router registers GET endpoints only.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router

from accounts.api import APIKeyAuth
from analytics.models import CallRecord
from routing.models import CallLog

stats_router = Router(tags=["Stats API"], auth=APIKeyAuth())

LIVE_STATUSES = ['in_progress', 'ringing', 'initiated']


class _Filters:
    """The attribute bag the analytics services expect.

    They read filters with getattr(filters, name, None), so a plain object
    with the right names is the whole contract.
    """
    _names = (
        'date_from', 'date_to', 'start_date', 'end_date', 'campaign_id',
        'buyer_id', 'publisher_id', 'status', 'destination', 'search',
        'timezone', 'is_qualified', 'is_converted', 'is_duplicate', 'is_spam',
        'created_at__gte', 'created_at__lte',
    )

    def __init__(self, **kwargs):
        for name in self._names:
            setattr(self, name, kwargs.get(name))


def _filters(date_from=None, date_to=None, tz=None):
    return _Filters(date_from=date_from, date_to=date_to, timezone=tz)


@stats_router.get("/", response={200: dict})
def index(request: HttpRequest):
    """The base URL answers with the map instead of a 404.

    The first thing every integrator does is open the base in a browser; a
    Not Found there reads as "the API is down" when it is only "there is no
    page here". Auth still required - the map costs a key, same as the data.
    """
    return 200, {
        "service": "Avortyx Stats API",
        "documentation": "docs/STATS_API.md in the backend repository",
        "endpoints": [
            "GET /api/stats/campaigns?date_from&date_to&timezone_name",
            "GET /api/stats/buyers?date_from&date_to&timezone_name&include_idle",
            "GET /api/stats/destinations?date_from&date_to&timezone_name&buyer_id&enabled&has_calls",
            "GET /api/stats/missed?date_from&date_to&timezone_name",
            "GET /api/stats/tfn/{number}",
            "GET /api/stats/concurrency",
        ],
    }


@stats_router.get("/campaigns", response={200: list})
def campaigns(request: HttpRequest, date_from: str = None, date_to: str = None,
              timezone_name: str = None):
    """Every campaign with its call statistics for the window.

    Fields per row include: total_calls (incoming), connected_calls,
    not_connected_calls, live_calls, qualified_calls, converted_calls,
    conversion_rate, duplicate_calls (every duplicate that arrived),
    avg_duration (AHT, seconds of talk from buyer pickup),
    total_duration_sec (TCL over connected calls), revenue/payout/profit
    (role permitting), billable_minutes, total_cost.
    """
    from analytics.services import AnalyticsService
    return 200, AnalyticsService.get_campaign_performance(
        request.auth, _filters(date_from, date_to, timezone_name))


@stats_router.get("/buyers", response={200: list})
def buyers(request: HttpRequest, date_from: str = None, date_to: str = None,
           timezone_name: str = None, include_idle: bool = False):
    """Every buyer with caller statistics - same field meanings as /campaigns.

    Each row also carries `status` (active / paused / archived),
    `concurrency_cap` (summed over the buyer's enabled destinations) and
    `live_calls` (summed the same way, the figure routing caps against).
    `include_idle=true` adds buyers with no calls in the window as zero rows.
    """
    from django.db.models import Sum
    from analytics.services import AnalyticsService
    from accounts.permissions import scope_queryset
    from buyers.models import Buyer
    from buyers.destination import Destination

    rows = AnalyticsService.get_buyer_performance(
        request.auth, _filters(date_from, date_to, timezone_name))

    # The same slice the key's creator sees: a buyer key gets its own buyer.
    visible = scope_queryset(
        request.auth,
        Buyer.objects.filter(organization=request.auth.organization),
        buyer_field='id', publisher_field=None,
    )
    status_by_id = {b.id: b.status for b in visible}
    caps = {
        r['buyer_id']: r for r in
        Destination.objects.filter(organization=request.auth.organization,
                                   enabled=True, buyer_id__in=status_by_id)
        .values('buyer_id')
        .annotate(cc=Sum('concurrency_cap'), live=Sum('live_calls'))
    }

    seen = set()
    for row in rows:
        bid = row.get('buyer_id')
        seen.add(bid)
        cap_row = caps.get(bid, {})
        row['status'] = status_by_id.get(bid)
        row['concurrency_cap'] = cap_row.get('cc', 0) or 0
        row['live_calls'] = cap_row.get('live', 0) or 0

    if include_idle:
        for b in visible:
            if b.id in seen:
                continue
            cap_row = caps.get(b.id, {})
            rows.append({
                'buyer_id': b.id, 'buyer_name': b.name,
                'status': b.status,
                'concurrency_cap': cap_row.get('cc', 0) or 0,
                'live_calls': cap_row.get('live', 0) or 0,
                'total_calls': 0, 'connected_calls': 0,
                'not_connected_calls': 0, 'converted': 0,
            })
    return 200, rows


@stats_router.get("/destinations", response={200: list})
def destinations(request: HttpRequest, date_from: str = None, date_to: str = None,
                 timezone_name: str = None, buyer_id: str = None,
                 enabled: bool = None, has_calls: bool = False):
    """One row per destination visible to the key - the same fields as
    /tfn/{number} - plus `incoming_calls`, the calls that reached the TFN in
    the date window (defaulting to today in `timezone_name`).

    Filters: `buyer_id`, `enabled=true|false`, and `has_calls=true` to keep
    only destinations that received calls in the window. A buyer-created key
    sees only that buyer's destinations, same as the Destinations page.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from buyers.destination import Destination
    from buyers.destinations_api import format_destination, gather_destination_counts
    from accounts.permissions import scope_queryset
    from analytics.services import mask_partner_money

    qs = scope_queryset(
        request.auth,
        Destination.objects.select_related('buyer')
        .filter(organization=request.auth.organization),
        buyer_field='buyer_id', publisher_field=None,
    )
    if buyer_id:
        qs = qs.filter(buyer_id=buyer_id)
    if enabled is not None:
        qs = qs.filter(enabled=enabled)
    dests = list(qs.order_by('name'))

    # Cap counters (live/hourly/daily/monthly/all-time) in grouped queries,
    # on their natural windows so each number stays true to its cap.
    tfns = [d.tfn for d in dests if d.tfn]
    counts = gather_destination_counts(request.auth.organization, tfns)

    # The requested window, one grouped query - same day-cut convention as
    # format_destination: the reader's timezone, defaulting to today.
    try:
        tz = ZoneInfo(timezone_name or 'America/New_York')
    except Exception:
        tz = ZoneInfo('America/New_York')
    local_now = timezone.localtime(timezone.now(), tz)
    if date_from:
        window_start = datetime.fromisoformat(date_from + 'T00:00:00').replace(tzinfo=tz)
    else:
        window_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window = Q(created_at__gte=window_start)
    if date_to:
        window &= Q(created_at__lte=datetime.fromisoformat(date_to + 'T23:59:59').replace(tzinfo=tz))
    incoming = dict(
        CallLog.objects.filter(organization=request.auth.organization,
                               destination_number__in=tfns)
        .filter(window)
        .values_list('destination_number')
        .annotate(c=Count('id'))
    )

    rows = []
    for d in dests:
        n = incoming.get(d.tfn, 0)
        if has_calls and n == 0:
            continue
        row = format_destination(d, counts=counts)
        row['incoming_calls'] = n
        rows.append(mask_partner_money(request.auth, row))
    return 200, rows


@stats_router.get("/missed", response={200: dict})
def missed(request: HttpRequest, date_from: str = None, date_to: str = None,
           timezone_name: str = None):
    """Missed calls grouped by campaign and by buyer.

    Missed = the call arrived and nobody connected: status no_answer (which
    includes duplicates dropped under Different) plus busy. Refused calls
    (failed) are listed separately so a blocked caller is not read as a
    missed opportunity.
    """
    from analytics.services import AnalyticsService
    qs = AnalyticsService._base_qs(request.auth, _filters(date_from, date_to, timezone_name))

    def grouped(field_id, field_name):
        rows = (
            qs.values(field_id, field_name)
            .annotate(
                missed=Count('id', filter=Q(status__in=['no_answer', 'busy'])),
                refused=Count('id', filter=Q(status='failed')),
                incoming=Count('id'),
            )
            .order_by('-missed')
        )
        return [
            {
                'id': str(r[field_id]) if r[field_id] else None,
                'name': r[field_name] or '',
                'missed': r['missed'],
                'refused': r['refused'],
                'incoming': r['incoming'],
            }
            for r in rows
        ]

    return 200, {
        'by_campaign': grouped('campaign_id', 'campaign_name'),
        'by_buyer': grouped('buyer_id', 'buyer_name'),
    }


@stats_router.get("/tfn/{number}", response={200: dict, 404: dict})
def tfn(request: HttpRequest, number: str, date_from: str = None,
        date_to: str = None, timezone_name: str = None):
    """One TFN: its destination row with cc, cap and call counters.

    `number` matches on the last ten digits, so 18779641530 and
    +18779641530 are the same TFN.
    """
    from buyers.destination import Destination
    from buyers.destinations_api import format_destination

    digits = ''.join(c for c in number if c.isdigit())[-10:]
    if not digits:
        return 404, {"detail": "Not a phone number"}
    from accounts.permissions import scope_queryset
    from analytics.services import mask_partner_money
    # Same slice as the Destinations page: a buyer-created key sees the
    # buyer's own TFNs, a publisher's none - a key is its creator, no wider.
    qs = scope_queryset(
        request.auth,
        Destination.objects.select_related('buyer'),
        buyer_field='buyer_id', publisher_field=None,
    )
    d = (
        qs.filter(organization=request.auth.organization, tfn__endswith=digits)
        .first()
    )
    if d is None:
        return 404, {"detail": f"No destination ends in {digits}"}
    return 200, mask_partner_money(
        request.auth,
        format_destination(d, start_date=date_from, end_date=date_to,
                           tz_name=timezone_name),
    )


@stats_router.get("/concurrency", response={200: dict})
def concurrency(request: HttpRequest):
    """Calls in flight right now, total and grouped by campaign and buyer.

    Counted from the call log's live statuses - the same figures the Live
    Monitor shows, taken at this moment.
    """
    live = CallLog.objects.filter(
        organization=request.auth.organization,
        status__in=LIVE_STATUSES,
        created_at__gte=timezone.now() - timedelta(hours=4),
    )

    def grouped(field_id, name_attr):
        rows = live.values(field_id).annotate(cc=Count('id')).order_by('-cc')
        out = []
        for r in rows:
            out.append({'id': str(r[field_id]) if r[field_id] else None, 'cc': r['cc']})
        return out

    by_campaign = []
    for r in live.values('campaign_id', 'campaign__name').annotate(cc=Count('id')).order_by('-cc'):
        by_campaign.append({'id': str(r['campaign_id']) if r['campaign_id'] else None,
                            'name': r['campaign__name'] or '', 'cc': r['cc']})
    by_buyer = []
    for r in live.values('buyer_id', 'buyer__name').annotate(cc=Count('id')).order_by('-cc'):
        by_buyer.append({'id': str(r['buyer_id']) if r['buyer_id'] else None,
                         'name': r['buyer__name'] or '', 'cc': r['cc']})

    return 200, {
        'total_cc': live.count(),
        'by_campaign': by_campaign,
        'by_buyer': by_buyer,
        'taken_at': timezone.now().isoformat(),
    }
