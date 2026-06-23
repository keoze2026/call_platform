from ninja import Router, Query
from django.http import HttpRequest, HttpResponse
from typing import List
from accounts.api import JWTAuth
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
def dashboard(request: HttpRequest):
    """Real-time dashboard — total calls, live calls, revenue, conversion rate."""
    data = AnalyticsService.get_dashboard(request.auth)
    return 200, data


@router.get("/time-series", response={200: List[TimeSeriesPointSchema]})
def time_series(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Calls/revenue/profit over time. granularity: hour | day | week | month"""
    data = AnalyticsService.get_time_series(request.auth, filters)
    return 200, data


@router.get("/campaigns", response={200: list})
def campaign_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per campaign."""
    data = AnalyticsService.get_campaign_performance(request.auth, filters)
    return 200, data


@router.get("/buyers", response={200: list})
def buyer_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per buyer — win rate, avg bid, payout."""
    data = AnalyticsService.get_buyer_performance(request.auth, filters)
    return 200, data


@router.get("/publishers", response={200: list})
def publisher_performance(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Performance breakdown per publisher — calls, conversion, spam rate."""
    data = AnalyticsService.get_publisher_performance(request.auth, filters)
    return 200, data


@router.get("/calls", response={200: CallLogListSchema})
def call_log(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Full paginated call log with all filters."""
    data = AnalyticsService.get_call_log(request.auth, filters)
    return 200, data


@router.get("/calls/export", auth=JWTAuth(), response=None)
def export_calls(request: HttpRequest, filters: AnalyticsFilterSchema = Query(...)):
    """Download full call log as CSV."""
    csv_data = AnalyticsService.export_csv(request.auth, filters)
    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="call_log.csv"'
    return response

@router.get("/calls/{call_id}/recording", response={200: dict, 404: dict})
def get_recording(request: HttpRequest, call_id: str):
    from routing.models import CallLog
    try:
        call = CallLog.objects.get(
            id=call_id,
            organization=request.auth.organization
        )
        if not call.recording_url:
            return 404, {"detail": "No recording available for this call"}
        return 200, {
            'call_id': str(call.id),
            'recording_url': call.recording_url,
            'recording_sid': getattr(call, 'recording_sid', '') or '',
            'duration': call.duration or 0,
            'caller_number': call.caller_number,
            'campaign_name': call.campaign.name if call.campaign else '',
            'created_at': call.created_at.isoformat(),
        }
    except CallLog.DoesNotExist:
        return 404, {"detail": "Call not found"}
    

@router.get("/live", response={200: list})
def live_calls(request: HttpRequest):
    from routing.models import CallLog
    from django.utils import timezone

    calls = CallLog.objects.filter(
        organization=request.auth.organization,
        status='in_progress'
    ).select_related('campaign', 'buyer', 'publisher').order_by('-created_at')

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