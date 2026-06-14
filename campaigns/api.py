from ninja import Router
from django.http import HttpRequest
from typing import List
from .schemas import (
    CreateCampaignSchema, UpdateCampaignSchema,
    CampaignOutSchema, CampaignListOutSchema,
    CampaignCapSchema, CampaignScheduleSchema,
    CampaignStatsSchema, MessageResponseSchema
)
from .services import CampaignService
from accounts.api import JWTAuth

router = Router(tags=["Campaigns"], auth=JWTAuth())


@router.post("", response={201: CampaignOutSchema, 400: dict})
def create_campaign(request: HttpRequest, data: CreateCampaignSchema):
    try:
        campaign = CampaignService.create(data, request.auth)
        return 201, CampaignService.format_campaign(campaign)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_campaigns(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    campaigns = CampaignService.list_campaigns(request.auth)
    data = [
        {
            'id': str(c.id),
            'name': c.name,
            'status': c.status,
            'routing_type': c.routing_type,
            'payout_amount': str(c.payout_amount),
            'revenue_amount': str(c.revenue_amount),
            'created_at': c.created_at.isoformat(),
        }
        for c in campaigns
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/{campaign_id}", response={200: CampaignOutSchema, 404: dict})
def get_campaign(request: HttpRequest, campaign_id: str):
    try:
        campaign = CampaignService.get_campaign(campaign_id, request.auth)
        return 200, CampaignService.format_campaign(campaign)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/{campaign_id}", response={200: CampaignOutSchema, 400: dict, 404: dict})
def update_campaign(request: HttpRequest, campaign_id: str, data: UpdateCampaignSchema):
    try:
        campaign = CampaignService.update(campaign_id, data, request.auth)
        campaign = CampaignService.get_campaign(campaign_id, request.auth)
        return 200, CampaignService.format_campaign(campaign)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/{campaign_id}", response={200: MessageResponseSchema, 404: dict})
def delete_campaign(request: HttpRequest, campaign_id: str):
    try:
        CampaignService.delete(campaign_id, request.auth)
        return 200, {"message": "Campaign archived successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{campaign_id}/pause", response={200: MessageResponseSchema, 400: dict, 404: dict})
def pause_campaign(request: HttpRequest, campaign_id: str):
    try:
        CampaignService.pause(campaign_id, request.auth)
        return 200, {"message": "Campaign paused successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/{campaign_id}/activate", response={200: MessageResponseSchema, 400: dict, 404: dict})
def activate_campaign(request: HttpRequest, campaign_id: str):
    try:
        CampaignService.activate(campaign_id, request.auth)
        return 200, {"message": "Campaign activated successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.patch("/{campaign_id}/cap", response={200: dict, 400: dict, 404: dict})
def update_cap(request: HttpRequest, campaign_id: str, data: CampaignCapSchema):
    try:
        cap = CampaignService.update_cap(campaign_id, data, request.auth)
        return 200, {
            'id': str(cap.id),
            'max_calls_daily': cap.max_calls_daily,
            'max_calls_monthly': cap.max_calls_monthly,
            'max_calls_global': cap.max_calls_global,
            'max_concurrency': cap.max_concurrency,
        }
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.put("/{campaign_id}/schedules", response={200: dict, 400: dict, 404: dict})
def update_schedules(request: HttpRequest, campaign_id: str, data: List[CampaignScheduleSchema]):
    try:
        schedules = CampaignService.update_schedules(campaign_id, data, request.auth)
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return 200, {
            'schedules': [
                {
                    'id': str(s.id),
                    'day_of_week': s.day_of_week,
                    'day_name': days[s.day_of_week],
                    'open_time': s.open_time.strftime('%H:%M'),
                    'close_time': s.close_time.strftime('%H:%M'),
                    'is_closed': s.is_closed,
                }
                for s in schedules
            ]
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/{campaign_id}/stats", response={200: CampaignStatsSchema, 404: dict})
def get_stats(request: HttpRequest, campaign_id: str):
    try:
        stats = CampaignService.get_stats(campaign_id, request.auth)
        return 200, stats
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.put("/{campaign_id}/schedules", response={200: dict, 404: dict})
def replace_schedules(request: HttpRequest, campaign_id: str):
    import json
    from campaigns.models import CampaignSchedule
    try:
        campaign = CampaignService.get_campaign(campaign_id, request.auth)
        data = json.loads(request.body)
        schedules = data if isinstance(data, list) else data.get('schedules', [])
        CampaignSchedule.objects.filter(campaign=campaign).delete()
        for s in schedules:
            CampaignSchedule.objects.create(
                campaign=campaign,
                day_of_week=s.get('day_of_week', 0),
                open_time=s.get('open_time', '09:00'),
                close_time=s.get('close_time', '17:00'),
                is_closed=s.get('is_closed', False),
            )
        return 200, {"message": "Schedules updated", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}
