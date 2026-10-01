from accounts.permissions import require, Capability
from ninja import Router
from django.http import HttpRequest
from typing import List
from .schemas import (
    CreatePublisherSchema, UpdatePublisherSchema,
    PublisherOutSchema, PublisherListOutSchema,
    PublisherCapSchema, PublisherStatsSchema,
    AssignCampaignSchema, MessageResponseSchema
)
from .services import PublisherService
from accounts.api import JWTAuth

router = Router(tags=["Publishers"], auth=JWTAuth())


# Returns a plain dict rather than PublisherOutSchema so the `invite` block
# reaches the caller - a schema would filter it out, and whether the invitation
# went is the thing the person who just filled in the form wants to know.
@router.post("", response={201: dict, 400: dict})
def create_publisher(request: HttpRequest, data: CreatePublisherSchema):
    require(request.auth, Capability.CREATE)
    try:
        created = PublisherService.create(data, request.auth)
        # Carried off the in-memory object before the re-read drops it.
        invite = getattr(created, 'invite_result', None)
        publisher = PublisherService.get_publisher(str(created.id), request.auth)
        body = PublisherService.format_publisher(publisher)
        if invite is not None:
            body['invite'] = invite
        return 201, body
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_publishers(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    publishers = PublisherService.list_publishers(request.auth)
    data = [
        {
            'id': str(p.id),
            'name': p.name,
            'status': p.status,
            'email': p.email,
            'payout_amount': str(p.payout_amount),
            'unique_id': p.unique_id,
            'created_at': p.created_at.isoformat(),
        }
        for p in publishers
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/{publisher_id}", response={200: PublisherOutSchema, 404: dict})
def get_publisher(request: HttpRequest, publisher_id: str):
    try:
        publisher = PublisherService.get_publisher(publisher_id, request.auth)
        return 200, PublisherService.format_publisher(publisher)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/{publisher_id}", response={200: PublisherOutSchema, 400: dict, 404: dict})
def update_publisher(request: HttpRequest, publisher_id: str, data: UpdatePublisherSchema):
    require(request.auth, Capability.EDIT)
    try:
        PublisherService.update(publisher_id, data, request.auth)
        publisher = PublisherService.get_publisher(publisher_id, request.auth)
        return 200, PublisherService.format_publisher(publisher)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/{publisher_id}", response={200: MessageResponseSchema, 404: dict})
def delete_publisher(request: HttpRequest, publisher_id: str):
    require(request.auth, Capability.DELETE)
    try:
        PublisherService.delete(publisher_id, request.auth)
        return 200, {"message": "Publisher archived successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{publisher_id}/pause", response={200: MessageResponseSchema, 400: dict, 404: dict})
def pause_publisher(request: HttpRequest, publisher_id: str):
    require(request.auth, Capability.CREATE)
    try:
        PublisherService.pause(publisher_id, request.auth)
        return 200, {"message": "Publisher paused successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/{publisher_id}/activate", response={200: MessageResponseSchema, 400: dict, 404: dict})
def activate_publisher(request: HttpRequest, publisher_id: str):
    require(request.auth, Capability.CREATE)
    try:
        PublisherService.activate(publisher_id, request.auth)
        return 200, {"message": "Publisher activated successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.patch("/{publisher_id}/cap", response={200: dict, 400: dict, 404: dict})
def update_cap(request: HttpRequest, publisher_id: str, data: PublisherCapSchema):
    require(request.auth, Capability.EDIT)
    try:
        cap = PublisherService.update_cap(publisher_id, data, request.auth)
        return 200, {
            'id': str(cap.id),
            'max_calls_daily': cap.max_calls_daily,
            'max_calls_monthly': cap.max_calls_monthly,
            'max_calls_global': cap.max_calls_global,
        }
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{publisher_id}/campaigns", response={200: dict, 400: dict, 404: dict})
def assign_campaign(request: HttpRequest, publisher_id: str, data: AssignCampaignSchema):
    require(request.auth, Capability.CREATE)
    try:
        assignment = PublisherService.assign_campaign(publisher_id, data, request.auth)
        return 200, {
            'id': str(assignment.id),
            'campaign_id': str(assignment.campaign_id),
            'campaign_name': assignment.campaign.name,
            'payout_amount': str(assignment.payout_amount),
            'is_active': assignment.is_active,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/{publisher_id}/campaigns/{campaign_id}", response={200: MessageResponseSchema, 404: dict})
def remove_campaign(request: HttpRequest, publisher_id: str, campaign_id: str):
    require(request.auth, Capability.DELETE)
    try:
        PublisherService.remove_campaign(publisher_id, campaign_id, request.auth)
        return 200, {"message": "Publisher removed from campaign successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{publisher_id}/invite", response={200: dict, 400: dict, 404: dict})
def invite_publisher(request: HttpRequest, publisher_id: str):
    """Send the publisher a link to set up their own login.

    There was no such endpoint. The interface offered "Invite a publisher" with
    nothing behind it, so the dialog sat on "Inviting..." and never finished.
    """
    import json

    from accounts.partner_invites import InviteError, invite_partner
    from publishers.models import Publisher as PublisherModel

    require(request.auth, Capability.CREATE)

    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        body = {}

    try:
        publisher = PublisherModel.objects.get(
            id=publisher_id, organization=request.auth.organization
        )
    except PublisherModel.DoesNotExist:
        return 404, {"detail": "Publisher not found"}

    from accounts.auto_invite import partner_email
    email = body.get('email') or partner_email(publisher)

    try:
        return 200, invite_partner(
            organization=request.auth.organization,
            partner=publisher,
            kind='publisher',
            email=email,
            contact_name=body.get('contact_name') or body.get('name') or '',
            invited_by=request.auth,
        )
    except InviteError as e:
        return 400, {"detail": str(e)}


@router.get("/{publisher_id}/stats", response={200: PublisherStatsSchema, 404: dict})
def get_stats(request: HttpRequest, publisher_id: str):
    try:
        stats = PublisherService.get_stats(publisher_id, request.auth)
        return 200, stats
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.get("/{publisher_id}/payouts", response={200: dict, 404: dict})
def list_publisher_payouts(request: HttpRequest, publisher_id: str, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    from billing.models import Transaction
    try:
        publisher = PublisherService.get_publisher(publisher_id, request.auth)
        transactions = Transaction.objects.filter(
            organization=request.auth.organization,
            publisher_name=publisher.name,
            transaction_type='payout'
        ).order_by('-created_at')
        data = [
            {
                'id': str(t.id),
                'amount': float(t.amount),
                'status': t.status,
                'period': t.description,
                'paid_at': t.created_at.isoformat() if t.status == 'completed' else None,
                'created_at': t.created_at.isoformat(),
            }
            for t in transactions
        ]
        return 200, paginate_list(data, page, page_size)
    except ValueError as e:
        return 404, {"detail": str(e)}
