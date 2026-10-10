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
           timezone_name: str = None):
    """Every buyer with caller statistics - same field meanings as /campaigns."""
    from analytics.services import AnalyticsService
    return 200, AnalyticsService.get_buyer_performance(
        request.auth, _filters(date_from, date_to, timezone_name))


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
    d = (
        Destination.objects.select_related('buyer')
        .filter(organization=request.auth.organization, tfn__endswith=digits)
        .first()
    )
    if d is None:
        return 404, {"detail": f"No destination ends in {digits}"}
    return 200, format_destination(d, start_date=date_from, end_date=date_to,
                                   tz_name=timezone_name)


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
