from accounts.permissions import require, Capability
from django.conf import settings
from ninja import Router, Schema
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


# Plain dict rather than BuyerOutSchema so the `invite` block reaches the
# caller - a schema would filter it out, and whether the invitation went is what
# the person who just filled in the form wants to know.
@router.post("", response={201: dict, 400: dict})
def create_buyer(request: HttpRequest, data: CreateBuyerSchema):
    require(request.auth, Capability.CREATE)
    try:
        created = BuyerService.create(data, request.auth)
        # Carried off the in-memory object before the re-read drops it.
        invite = getattr(created, 'invite_result', None)
        buyer = BuyerService.get_buyer(str(created.id), request.auth)
        body = BuyerService.format_buyer(buyer)
        if invite is not None:
            body['invite'] = invite
        return 201, body
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
    require(request.auth, Capability.EDIT)
    try:
        BuyerService.update(buyer_id, data, request.auth)
        buyer = BuyerService.get_buyer(buyer_id, request.auth)
        return 200, BuyerService.format_buyer(buyer)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/{buyer_id}", response={200: MessageResponseSchema, 404: dict})
def delete_buyer(request: HttpRequest, buyer_id: str):
    require(request.auth, Capability.DELETE)
    try:
        BuyerService.delete(buyer_id, request.auth)
        return 200, {"message": "Buyer archived successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{buyer_id}/pause", response={200: MessageResponseSchema, 400: dict, 404: dict})
def pause_buyer(request: HttpRequest, buyer_id: str):
    require(request.auth, Capability.CREATE)
    try:
        BuyerService.pause(buyer_id, request.auth)
        return 200, {"message": "Buyer paused successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/{buyer_id}/activate", response={200: MessageResponseSchema, 400: dict, 404: dict})
def activate_buyer(request: HttpRequest, buyer_id: str):
    require(request.auth, Capability.CREATE)
    try:
        BuyerService.activate(buyer_id, request.auth)
        return 200, {"message": "Buyer activated successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.patch("/{buyer_id}/cap", response={200: dict, 400: dict, 404: dict})
def update_cap(request: HttpRequest, buyer_id: str, data: BuyerCapSchema):
    require(request.auth, Capability.EDIT)
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
    require(request.auth, Capability.CREATE)
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
    require(request.auth, Capability.DELETE)
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
    require(request.auth, Capability.DELETE)
    from buyers.models import BuyerCampaign
    try:
        # Scoped to the caller's organization: without it any authenticated user
        # could delete another organization's assignment by its ids alone.
        bc = BuyerCampaign.objects.get(
            buyer_id=buyer_id,
            campaign_id=campaign_id,
            buyer__organization=request.auth.organization,
        )
        bc.delete()
        return 200, {"message": "Campaign detached", "success": True}
    except BuyerCampaign.DoesNotExist:
        return 404, {"detail": "Assignment not found"}


@router.post("/{buyer_id}/invite", response={200: dict, 400: dict, 404: dict})
def invite_buyer(request: HttpRequest, buyer_id: str):
    """Send the buyer a link to set up their own login.

    The previous version emailed `buyer.created_by.email` - the admin who created
    the record, not the buyer - and generated a token it never stored, so the
    link it sent could never be validated. It also ignored the email address the
    form collects.
    """
    import json

    from accounts.partner_invites import InviteError, invite_partner
    from buyers.models import Buyer as BuyerModel

    require(request.auth, Capability.CREATE)

    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        body = {}

    try:
        buyer = BuyerModel.objects.get(id=buyer_id, organization=request.auth.organization)
    except BuyerModel.DoesNotExist:
        return 404, {"detail": "Buyer not found"}

    # The form's address first; the record's own as a fallback. Buyer stores it
    # as contact_email, not email.
    from accounts.auto_invite import partner_email
    email = body.get('email') or partner_email(buyer)

    try:
        return 200, invite_partner(
            organization=request.auth.organization,
            partner=buyer,
            kind='buyer',
            email=email,
            contact_name=body.get('contact_name') or body.get('name') or '',
            invited_by=request.auth,
        )
    except InviteError as e:
        return 400, {"detail": str(e)}


@router.get("/{buyer_id}/reporting-config", response={200: dict, 404: dict})
def get_reporting_config(request: HttpRequest, buyer_id: str):
    from buyers.models import Buyer
    from django.core.cache import cache
    try:
        buyer = Buyer.objects.get(id=buyer_id, organization=request.auth.organization)
        key = f'buyer_reporting_config_{buyer_id}'
        config = cache.get(key) or {'visible_columns': ['incoming', 'connected', 'qualified', 'converted', 'not_connected', 'acl', 'tcl', 'cost']}
        return 200, config
    except Buyer.DoesNotExist:
        return 404, {"detail": "Buyer not found"}


@router.put("/{buyer_id}/reporting-config", response={200: dict, 404: dict})
def update_reporting_config(request: HttpRequest, buyer_id: str):
    require(request.auth, Capability.EDIT)
    import json as _json
    from buyers.models import Buyer
    from django.core.cache import cache
    VALID_COLUMNS = {'incoming', 'connected', 'qualified', 'converted', 'not_connected', 'acl', 'tcl', 'cost'}
    try:
        buyer = Buyer.objects.get(id=buyer_id, organization=request.auth.organization)
        body = _json.loads(request.body)
        columns = body.get('visible_columns', [])
        columns = [c for c in columns if c in VALID_COLUMNS]
        config = {'visible_columns': columns}
        key = f'buyer_reporting_config_{buyer_id}'
        cache.set(key, config, timeout=None)
        return 200, config
    except Buyer.DoesNotExist:
        return 404, {"detail": "Buyer not found"}


class RemoveMemberSchema(Schema):
    email: str


@router.post("/{buyer_id}/members/remove", response={200: dict, 403: dict, 404: dict})
def remove_buyer_member(request: HttpRequest, buyer_id: str, data: RemoveMemberSchema):
    """Take one person's access to this buyer away.

    The trash icon in the Members table had no endpoint behind it, so removing
    somebody changed the browser and nothing else - they kept their password and
    their open sessions.

    `member_not_found` is a distinct code because the interface treats it as
    "already removed" and clears the row, while a missing endpoint (a plain 404)
    means the login is still live and it has to say so.
    """
    from accounts.models import User
    from accounts.partner_access import revoke_partner_access

    require(request.auth, Capability.EDIT)

    email = (data.email or '').strip().lower()
    user = User.objects.filter(
        email__iexact=email,
        buyer_id=buyer_id,
        organization=request.auth.organization,
    ).first()
    if user is None:
        return 404, {"detail": f"{email} is not a member of this buyer.",
                     "code": "member_not_found"}

    if not revoke_partner_access(user, reason='removed from buyer'):
        return 403, {"detail": "That account is a staff login and was not changed.",
                     "code": "not_a_partner"}

    return 200, {"detail": "removed"}
