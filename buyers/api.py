from ninja import Router
from django.http import HttpRequest
from typing import List
from .schemas import (
    CreateBuyerSchema, UpdateBuyerSchema,
    BuyerOutSchema, BuyerListOutSchema,
    BuyerCapSchema, BuyerStatsSchema,
    AssignCampaignSchema, MessageResponseSchema
)
from .services import BuyerService
from .models import Buyer
from accounts.api import JWTAuth

router = Router(tags=["Buyers"], auth=JWTAuth())


@router.post("", response={201: BuyerOutSchema, 400: dict})
def create_buyer(request: HttpRequest, data: CreateBuyerSchema):
    try:
        buyer = BuyerService.create(data, request.auth)
        buyer = BuyerService.get_buyer(str(buyer.id), request.auth)
        return 201, BuyerService.format_buyer(buyer)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_buyers(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    buyers = BuyerService.list_buyers(request.auth)
    data = [
        {
            'id': str(b.id),
            'name': b.name,
            'status': b.status,
            'routing_type': b.routing_type,
            'phone_number': b.phone_number,
            'payout_amount': str(b.payout_amount),
            'created_at': b.created_at.isoformat(),
        }
        for b in buyers
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/{buyer_id}", response={200: BuyerOutSchema, 404: dict})
def get_buyer(request: HttpRequest, buyer_id: str):
    try:
        buyer = BuyerService.get_buyer(buyer_id, request.auth)
        return 200, BuyerService.format_buyer(buyer)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/{buyer_id}", response={200: BuyerOutSchema, 400: dict, 404: dict})
def update_buyer(request: HttpRequest, buyer_id: str, data: UpdateBuyerSchema):
    try:
        BuyerService.update(buyer_id, data, request.auth)
        buyer = BuyerService.get_buyer(buyer_id, request.auth)
        return 200, BuyerService.format_buyer(buyer)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/{buyer_id}", response={200: MessageResponseSchema, 404: dict})
def delete_buyer(request: HttpRequest, buyer_id: str):
    try:
        BuyerService.delete(buyer_id, request.auth)
        return 200, {"message": "Buyer archived successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{buyer_id}/pause", response={200: MessageResponseSchema, 400: dict, 404: dict})
def pause_buyer(request: HttpRequest, buyer_id: str):
    try:
        BuyerService.pause(buyer_id, request.auth)
        return 200, {"message": "Buyer paused successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/{buyer_id}/activate", response={200: MessageResponseSchema, 400: dict, 404: dict})
def activate_buyer(request: HttpRequest, buyer_id: str):
    try:
        BuyerService.activate(buyer_id, request.auth)
        return 200, {"message": "Buyer activated successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.patch("/{buyer_id}/cap", response={200: dict, 400: dict, 404: dict})
def update_cap(request: HttpRequest, buyer_id: str, data: BuyerCapSchema):
    try:
        cap = BuyerService.update_cap(buyer_id, data, request.auth)
        return 200, {
            'id': str(cap.id),
            'max_calls_daily': cap.max_calls_daily,
            'max_calls_monthly': cap.max_calls_monthly,
            'max_calls_global': cap.max_calls_global,
            'max_concurrency': cap.max_concurrency,
        }
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{buyer_id}/campaigns", response={200: dict, 400: dict, 404: dict})
def assign_campaign(request: HttpRequest, buyer_id: str, data: AssignCampaignSchema):
    try:
        assignment = BuyerService.assign_campaign(buyer_id, data, request.auth)
        return 200, {
            'id': str(assignment.id),
            'campaign_id': str(assignment.campaign_id),
            'campaign_name': assignment.campaign.name,
            'priority': assignment.priority,
            'weight': assignment.weight,
            'is_active': assignment.is_active,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/{buyer_id}/campaigns/{campaign_id}", response={200: MessageResponseSchema, 404: dict})
def remove_campaign(request: HttpRequest, buyer_id: str, campaign_id: str):
    try:
        BuyerService.remove_campaign(buyer_id, campaign_id, request.auth)
        return 200, {"message": "Buyer removed from campaign successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.get("/{buyer_id}/stats", response={200: BuyerStatsSchema, 404: dict})
def get_stats(request: HttpRequest, buyer_id: str):
    try:
        stats = BuyerService.get_stats(buyer_id, request.auth)
        return 200, stats
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.delete("/{buyer_id}/campaigns/{campaign_id}", response={200: dict, 404: dict})
def detach_campaign(request: HttpRequest, buyer_id: str, campaign_id: str):
    from buyers.models import BuyerCampaign
    try:
        bc = BuyerCampaign.objects.get(buyer_id=buyer_id, campaign_id=campaign_id)
        bc.delete()
        return 200, {"message": "Campaign detached", "success": True}
    except BuyerCampaign.DoesNotExist:
        return 404, {"detail": "Assignment not found"}


@router.post("/{buyer_id}/invite", response={200: dict, 400: dict, 404: dict})
def invite_buyer(request: HttpRequest, buyer_id: str):
    import secrets
    from django.core.mail import send_mail
    from buyers.models import Buyer as BuyerModel
    try:
        buyer = BuyerModel.objects.get(id=buyer_id, organization=request.auth.organization)
        if not buyer.created_by or not buyer.created_by.email:
            return 400, {"detail": "No email found for this buyer"}
        token = secrets.token_urlsafe(32)
        invite_link = f"https://avortyx.com/buyer-setup?token={token}&buyer_id={buyer_id}"
        send_mail(
            subject="You have been invited to Avortyx as a Buyer",
            message=f"Hi,\n\nYou have been invited to join Avortyx as a buyer.\n\nClick the link below:\n\n{invite_link}\n\nAvortyx Team",
            from_email="support@keozx.com",
            recipient_list=[buyer.created_by.email],
            fail_silently=True,
        )
        return 200, {"message": "Invite sent", "invite_link": invite_link, "success": True}
    except BuyerModel.DoesNotExist:
        return 404, {"detail": "Buyer not found"}
