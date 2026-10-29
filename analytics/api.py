from ninja import Router, Query
from django.http import HttpRequest, HttpResponse
from typing import List
from accounts.api import JWTAuth
from accounts.permissions import Capability, require, scope_queryset
from django.db.models.functions import Coalesce
import logging
from security.enforcement import require_reports_unlock


logger = logging.getLogger(__name__)


def _f(filters, name, default=None):
    """Read a filter field, tolerating filters being None."""
    return getattr(filters, name, default) if filters is not None else default
from .schemas import (
    AnalyticsFilterSchema,
    DashboardSchema,
    TimeSeriesPointSchema,
    CampaignPerformanceSchema,
    BuyerPerformanceSchema,
    PublisherPerformanceSchema,
    CallLogListSchema,
)
from .services import AnalyticsService

router = Router(tags=["Analytics"], auth=JWTAuth())


@router.get("/dashboard", response={200: DashboardSchema})
def dashboard(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Real-time dashboard — total calls, live calls, revenue, conversion rate."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    data = AnalyticsService.get_dashboard(request.auth, filters)
    return 200, data


@router.get("/snapshot", response={200: dict})
def snapshot(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Everything the dashboard shows, from one moment in time.

    The dashboard was assembling itself from four separate requests - totals,
    campaigns, destinations and the revenue series - each landing a second or two
    apart. While calls are arriving that is four different moments, so the header
    read 192 while the chart read 190 and one panel showed $44 against another's
    $45. Every figure was correct for the instant it was taken; they simply were
    not the same instant.

    This returns all of them from a single evaluation, so the page is internally
    consistent even mid-call. The individual endpoints stay for anything that
    wants one section on its own.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    from django.utils import timezone

    taken_at = timezone.now()
    data = {
        "taken_at": taken_at.isoformat(),
        "dashboard": AnalyticsService.get_dashboard(request.auth, filters),
        "campaigns": AnalyticsService.get_campaign_performance(request.auth, filters),
        "time_series": AnalyticsService.get_time_series(request.auth, filters),
    }

    # Destinations live in another app; a failure there should cost that panel,
    # not the whole dashboard.
    try:
        from buyers.destinations_api import format_destination, gather_destination_counts
        from buyers.destination import Destination
        start = _f(filters, 'date_from') or _f(filters, 'start_date')
        end = _f(filters, 'date_to') or _f(filters, 'end_date')
        tz_name = _f(filters, 'timezone') or 'America/New_York'
        # Same slice as /api/destinations/: a buyer gets their own, a
        # publisher none. The dashboard card was built from this block, so
        # scoping the destinations API alone left the card still showing a
        # buyer every other buyer's traffic.
        from accounts.permissions import scope_queryset
        from django.db.models import Q
        dests = scope_queryset(
            request.auth,
            Destination.objects.all(),
            buyer_field='buyer_id', publisher_field=None,
        ).filter(
            buyer__organization=request.auth.organization,
        ).select_related('buyer')
        # The lines switched on, plus (CH-404, Sir 30 Oct) every line that had
        # calls in the selected days even if switched off since: a buyer whose
        # TFN was paused after its cap showed an empty card with 80 calls on
        # the chart above it, and the Buyers card could not list the buyers
        # that worked today. Only those two kinds are formatted - formatting
        # all 165 rows cost seconds on every load.
        used = _tfns_with_calls(request.auth.organization, start, end, tz_name)
        dests = dests.filter(Q(enabled=True) | Q(tfn__in=used))
        # Honour the "All destinations" dropdown when one is picked.
        chosen = _f(filters, 'destination')
        if chosen:
            dests = dests.filter(tfn=chosen)
        # One row per buyer and number: a buyer can keep old switched-off
        # rows of the same TFN as history, and each would count the same
        # calls again. The live row wins, else the one updated last.
        seen, rows = set(), []
        for d in dests.order_by('-enabled', '-updated_at'):
            k = (d.buyer_id, d.tfn)
            if k not in seen:
                seen.add(k)
                rows.append(d)
        dests = rows
        # Every counter in grouped queries, not five per row.
        counts = gather_destination_counts(
            request.auth.organization, [d.tfn for d in dests], start, end, tz_name)
        data["destinations"] = [
            format_destination(d, start_date=start, end_date=end, tz_name=tz_name, counts=counts)
            for d in dests
        ]
    except Exception:
        logger.exception('snapshot: destinations section failed')
        data["destinations"] = []

    return 200, data


@router.get("/time-series", response={200: List[TimeSeriesPointSchema]})
def time_series(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Calls/revenue/profit over time. granularity: hour | day | week | month"""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    data = AnalyticsService.get_time_series(request.auth, filters)
    return 200, data


@router.get("/campaigns", response={200: list})
def campaign_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per campaign."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    from analytics.report_cache import closed_range_response
    data = closed_range_response(
        request, filters, 'campaigns',
        lambda: AnalyticsService.get_campaign_performance(request.auth, filters))
    return 200, data


@router.get("/carriers", response={200: list})
def carrier_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per caller carrier — the CALLER PROFILE tab."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    from analytics.report_cache import closed_range_response
    data = closed_range_response(
        request, filters, 'carriers',
        lambda: AnalyticsService.get_carrier_performance(request.auth, filters))
    return 200, data


@router.get("/buyers", response={200: list})
def buyer_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per buyer — win rate, avg bid, payout."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    from analytics.report_cache import closed_range_response
    data = closed_range_response(
        request, filters, 'buyers',
        lambda: AnalyticsService.get_buyer_performance(request.auth, filters))
    return 200, data


@router.get("/publishers", response={200: list})
def publisher_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per publisher — calls, conversion, spam rate."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    from analytics.report_cache import closed_range_response
    data = closed_range_response(
        request, filters, 'publishers',
        lambda: AnalyticsService.get_publisher_performance(request.auth, filters))
    return 200, data


@router.get("/calls", response={200: CallLogListSchema})
def call_log(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Full paginated call log with all filters."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    from analytics.report_cache import closed_range_response
    data = closed_range_response(
        request, filters, 'calls',
        lambda: AnalyticsService.get_call_log(request.auth, filters))
    return 200, data


@router.get("/report-stamp", response={200: dict})
def report_stamp(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """CH-219: a fingerprint of the report's calls and the server time. The
    Reports page asks every few seconds and reloads only when it changes, so the
    report keeps itself current with auto refresh Off."""
    from accounts.workspace_permissions import require_perm
    from analytics.report_stamp import report_stamp as stamp_for
    require_perm(request.auth, 'reporting', 'view')
    require_reports_unlock(request, filters)
    return 200, stamp_for(request.auth, filters)


@router.get("/calls/export", auth=JWTAuth(), response=None)
def export_calls(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    # The export is the screen as a file, and whether this buyer's logins may
    # take the file home is the workspace admin's call. Hidden buttons are not
    # permissions - the refusal here is (buyer access spec, test #3).
    _u = request.auth
    from accounts.workspace_permissions import require_perm
    require_perm(_u, 'reporting', 'download')
    if getattr(_u, 'role', '') in ('buyer', 'publisher'):
        # `has` only - importing Capability here made it function-local and
        # the staff path's pre-existing require(..., Capability.EXPORT) hit
        # UnboundLocalError: a manager could not export while every partner
        # test passed. Module-level Capability serves both branches.
        from accounts.permissions import has
        if not has(_u, Capability.EXPORT):
            from django.http import JsonResponse
            return JsonResponse(
                {"detail": "Your account is not allowed to download reports"},
                status=403,
            )
    """Download full call log as CSV."""
    require_reports_unlock(request, filters)
    from django.http import StreamingHttpResponse

    # "Download Reports" is one of the toggles on a partner's settings page. It
    # gated nothing before: the switch sat in the browser and the export was
    # open to any login that could reach the URL. Staff hold this through their
    # role; a partner only if somebody switched it on.
    require(request.auth, Capability.EXPORT)
    
    csv_generator = AnalyticsService.export_csv(request.auth, filters)
    response = StreamingHttpResponse(csv_generator, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="call_log.csv"'
    return response

@router.get("/calls/{call_id}/recording", response={200: dict, 404: dict})
def get_recording(request: HttpRequest, call_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'listen')
    from routing.models import CallLog
    from routing.recordings import public_recording_url

    # "Audio Recording" on the partner settings page. A recording is a customer
    # on the phone, so a partner hears one only if somebody switched it on for
    # them. The row scoping below still applies on top: they can only ever ask
    # about their own calls.
    require(request.auth, Capability.RECORDINGS)

    try:
        call = scope_queryset(request.auth, CallLog.objects.all()).get(
            id=call_id,
            organization=request.auth.organization
        )
        # A recording of a call from before today is history like any other.
        require_reports_unlock(request, None, started_at=call.created_at)
        if not call.recording_url:
            return 404, {"detail": "No recording available for this call"}
        return 200, {
            'call_id': str(call.id),
            'recording_url': public_recording_url(call.recording_url),
            'recording_sid': getattr(call, 'recording_sid', '') or '',
            'duration': call.duration or 0,
            'caller_number': call.caller_number,
            'campaign_name': call.campaign.name if call.campaign else '',
            'created_at': call.created_at.isoformat(),
        }
    except CallLog.DoesNotExist:
        return 404, {"detail": "Call not found"}
    

@router.get("/calls/{call_id}/detail", response={200: dict, 404: dict})
def call_detail(request: HttpRequest, call_id: str):
    """Everything known about one call — the expandable row detail.

    Three sections: who called, what the routing decided, and what happened
    when. Assembled from the CallLog rather than the analytics mirror, since the
    mirror only carries terminal calls and drops the routing detail.
    """
    from routing.models import CallLog

    try:
        call = scope_queryset(request.auth, CallLog.objects.select_related(
            'campaign', 'buyer', 'publisher', 'routing_rule'
        )).get(id=call_id, organization=request.auth.organization)
        require_reports_unlock(request, None, started_at=call.created_at)
    except CallLog.DoesNotExist:
        return 404, {"detail": "Call not found"}

    return 200, AnalyticsService.format_call_detail(call)


@router.get("/live/summary", response={200: dict})
def live_summary(request: HttpRequest):
    """The Live Monitor's counters, as real figures rather than a running tally.

    The monitor showed four calls in flight with Started, Completed, Missed and
    Revenue all at zero. Nothing was wrong with the calls: the backend only ever
    returned the list of live calls, so the page was counting events it had seen
    since it connected. A call that began before the page opened was never
    "started" as far as that counter knew.

    These are today's totals, so the panel reads the same whenever it is opened
    and agrees with the dashboard.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'reporting', 'view')
    from decimal import Decimal

    from django.db.models import Count, Q, Sum
    from django.utils import timezone

    from routing.models import CallLog

    LIVE = ['in_progress', 'ringing', 'initiated', 'queued']
    org = request.auth.organization
    start_of_day = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)

    today = scope_queryset(
        request.auth,
        CallLog.objects.filter(organization=org, created_at__gte=start_of_day),
    )

    counts = today.aggregate(
        started=Count('id'),
        completed=Count('id', filter=Q(status='completed')),
        # Everything that reached nobody: unanswered, refused, or failed.
        missed=Count('id', filter=~Q(status__in=LIVE + ['completed'])),
        revenue=Coalesce(Sum('revenue', filter=Q(status='completed')), Decimal('0')),
    )

    live = today.filter(status__in=LIVE).order_by('created_at')
    longest = live.first()

    return 200, {
        'as_of': timezone.now().isoformat(),
        'in_flight': live.count(),
        'started': counts['started'] or 0,
        'completed': counts['completed'] or 0,
        'missed': counts['missed'] or 0,
        'revenue': counts['revenue'] or Decimal('0'),
        'longest_active': {
            'id': str(longest.id),
            'caller_number': longest.caller_number,
            'campaign_name': longest.campaign.name if longest.campaign_id else '',
            'started_at': longest.created_at.isoformat(),
            'seconds': int((timezone.now() - longest.created_at).total_seconds()),
        } if longest else None,
    }


@router.get("/live", response={200: list})
def live_calls(request: HttpRequest):
    from routing.models import CallLog
    from django.utils import timezone

    calls = scope_queryset(request.auth, CallLog.objects.filter(
        organization=request.auth.organization,
        status='in_progress'
    ).select_related('campaign', 'buyer', 'publisher')).order_by('-created_at')

    return 200, [
        {
            'id': str(c.id),
            'caller_number': c.caller_number,
            'called_number': c.called_number,
            'campaign_id': str(c.campaign_id) if c.campaign_id else None,
            'campaign_name': c.campaign.name if c.campaign else None,
            'buyer_id': str(c.buyer_id) if c.buyer_id else None,
            'buyer_name': c.buyer.name if c.buyer else None,
            'destination': getattr(c, 'destination_number', '') or '',
            'duration_seconds': (timezone.now() - c.created_at).seconds,
            'started_at': c.created_at.isoformat(),
        }
        for c in calls
    ]


@router.get('/caller-profile/{caller_number}')
def get_caller_profile(request, caller_number: str):
    from analytics.services import CallerProfileService
    organization_id = str(request.auth.organization_id)
    return CallerProfileService.get_profile(organization_id, caller_number)

def _tfns_with_calls(org, start, end, tz_name):
    """Destination numbers sent calls between `start` and `end` (dates, in
    the reader's zone; default today). A "No Destination" refusal stamped on a
    line was never sent to it and does not count (buyers/caps.py)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    from routing.models import CallLog
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('America/New_York')
    today = timezone.localtime(timezone.now(), tz).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        frm = datetime.fromisoformat(str(start)[:10]).replace(tzinfo=tz) if start else today
        to = (datetime.fromisoformat(str(end)[:10]).replace(tzinfo=tz) + timedelta(days=1)) if end else None
    except ValueError:
        frm, to = today, None
    qs = CallLog.objects.filter(organization=org, created_at__gte=frm).exclude(destination_number='') \
        .exclude(block_reason='No Destination')
    if to is not None:
        qs = qs.filter(created_at__lt=to)
    return set(qs.values_list('destination_number', flat=True).distinct())
