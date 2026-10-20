from accounts.permissions import require, Capability
from .numbers import canonical, variants
from accounts.api import JWTAuth
import uuid
from typing import List
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router

from .models import Blacklist, Whitelist, AnonymousCallBlock, SpamReport
from .schemas import (
    BlacklistCreateSchema, BlacklistUpdateSchema, BlacklistOutSchema,
    WhitelistCreateSchema, WhitelistUpdateSchema, WhitelistOutSchema,
    AnonymousBlockCreateSchema, AnonymousBlockUpdateSchema, AnonymousBlockOutSchema,
    SpamReportOutSchema,
    MessageSchema,
)

router = Router(tags=['Spam & Fraud Protection'], auth=JWTAuth())


def _buyer_member_gate(user):
    """What a buyer member may do here, per the workspace admin's switch.

    Returns (is_buyer_member, buyer) and raises nothing - the callers decide
    the refusal wording. Admin and staff pass untouched; a buyer member needs
    the permission on, and acts only on their own buyer's entries.
    """
    if getattr(user, 'role', '') != 'buyer':
        return False, None
    return True, getattr(user, 'buyer', None)


def _publisher_member_gate(user):
    """The same gate for a publisher login: (is_publisher_member, publisher).

    A publisher with the Block Numbers switch on blocks callers on its own
    numbers only. Before this, only a buyer login could use the switch.
    """
    if getattr(user, 'role', '') != 'publisher':
        return False, None
    return True, getattr(user, 'publisher', None)


def _member_may_block(user) -> bool:
    from accounts.permissions import has, Capability
    return has(user, Capability.BLOCK_NUMBERS)



# BLACKLIST


@router.get('/blacklist', response={200: dict})
def list_blacklist(request, campaign_id: uuid.UUID = None, publisher_id: uuid.UUID = None, active_only: bool = False, page: int = 1, page_size: int = 500):
    """
    List blacklisted numbers for the authenticated user's organisation.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    qs = Blacklist.objects.filter(organization_id=request.auth.organization_id)

    is_member, member_buyer = _buyer_member_gate(request.auth)
    if is_member:
        # Their own buyer's blocks, nothing organization-wide - the org list
        # is the workspace's business, not a client login's.
        qs = qs.filter(buyer=member_buyer) if member_buyer else qs.none()

    is_pub_member, member_publisher = _publisher_member_gate(request.auth)
    if is_pub_member:
        # A publisher login sees its own publisher's blocks and nothing else.
        qs = qs.filter(publisher=member_publisher) if member_publisher else qs.none()
    elif publisher_id:
        qs = qs.filter(publisher_id=publisher_id)
    else:
        # The workspace list stays what it was: publisher-scoped rows live on
        # that publisher's own settings page.
        qs = qs.filter(publisher__isnull=True)

    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)
    if active_only:
        qs = qs.filter(is_active=True)

    qs = qs.order_by('-created_at')
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    items = list(qs[start:end].values(
        'id', 'phone_number', 'reason', 'notes', 'is_active', 'campaign_id', 'publisher_id', 'expires_at', 'created_at'
    ))
    for item in items:
        item['id'] = str(item['id'])
        item['campaign_id'] = str(item['campaign_id']) if item['campaign_id'] else None
        item['publisher_id'] = str(item['publisher_id']) if item['publisher_id'] else None
        item['expires_at'] = item['expires_at'].isoformat() if item['expires_at'] else None
        item['created_at'] = item['created_at'].isoformat() if item['created_at'] else None

    import math
    return 200, {
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': math.ceil(total / page_size) if page_size else 1
    }


@router.post('/blacklist', response={201: BlacklistOutSchema})
def create_blacklist(request, payload: BlacklistCreateSchema):
    """Add a number to the blacklist."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    is_member, member_buyer = _buyer_member_gate(request.auth)
    scoped_buyer = None
    if is_member:
        # The admin's switch IS the member's capability here - the generic
        # CREATE check ran first and refused every member regardless of the
        # switch, which the acceptance run caught: 403 with the switch on.
        if not (member_buyer and _member_may_block(request.auth)):
            from ninja.errors import HttpError
            raise HttpError(403, "Your account is not allowed to block numbers")
        # A member blocks for their own buyer alone, whatever the payload
        # says - routing then skips this buyer, never the whole call.
        scoped_buyer = member_buyer
    is_pub_member, member_publisher = _publisher_member_gate(request.auth)
    scoped_publisher = None
    if is_pub_member:
        if not (member_publisher and _member_may_block(request.auth)):
            from ninja.errors import HttpError
            raise HttpError(403, "Your account is not allowed to block numbers")
        # A publisher blocks on its own numbers alone, whatever the payload says.
        scoped_publisher = member_publisher
    elif not is_member:
        require(request.auth, Capability.CREATE)
        if payload.publisher_id:
            from publishers.models import Publisher
            scoped_publisher = get_object_or_404(
                Publisher, id=payload.publisher_id,
                organization_id=request.auth.organization_id,
            )
    entry = Blacklist.objects.create(
        organization_id=request.auth.organization_id,
        phone_number=canonical(payload.phone_number or payload.number),
        reason=payload.reason,
        notes=payload.notes,
        campaign_id=None if (is_member or scoped_publisher) else payload.campaign_id,
        buyer=scoped_buyer,
        publisher=scoped_publisher,
        expires_at=payload.expires_at,
        added_by=request.auth,
    )
    return 201, entry


@router.get('/blacklist/{entry_id}', response=BlacklistOutSchema)
def get_blacklist_entry(request, entry_id: uuid.UUID):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    return entry


@router.patch('/blacklist/{entry_id}', response={200: BlacklistOutSchema, 400: dict})
def update_blacklist_entry(request, entry_id: uuid.UUID, payload: BlacklistUpdateSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    if getattr(request.auth, 'role', '') not in ('buyer', 'publisher'):
        require(request.auth, Capability.EDIT)
    if payload.phone_number is not None:
        return 400, {"detail": "phone_number is immutable after creation. Delete and recreate to change the number."}
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    is_member, member_buyer = _buyer_member_gate(request.auth)
    if is_member:
        from ninja.errors import HttpError
        if not (member_buyer and _member_may_block(request.auth)):
            raise HttpError(403, "Your account is not allowed to block numbers")
        if entry.buyer_id != member_buyer.id:
            raise HttpError(403, "Not your buyer's entry")
    is_pub_member, member_publisher = _publisher_member_gate(request.auth)
    if is_pub_member:
        from ninja.errors import HttpError
        if not (member_publisher and _member_may_block(request.auth)):
            raise HttpError(403, "Your account is not allowed to block numbers")
        if entry.publisher_id != member_publisher.id:
            raise HttpError(403, "Not your publisher's entry")
    for field, value in payload.dict(exclude_none=True).items():
        if field != 'phone_number':
            setattr(entry, field, value)
    entry.save()
    return 200, entry


@router.delete('/blacklist/{entry_id}', response=MessageSchema)
def delete_blacklist_entry(request, entry_id: uuid.UUID):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'delete')
    if getattr(request.auth, 'role', '') not in ('buyer', 'publisher'):
        require(request.auth, Capability.DELETE)
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    is_member, member_buyer = _buyer_member_gate(request.auth)
    if is_member:
        from ninja.errors import HttpError
        if not (member_buyer and _member_may_block(request.auth)):
            raise HttpError(403, "Your account is not allowed to block numbers")
        if entry.buyer_id != member_buyer.id:
            raise HttpError(403, "Not your buyer's entry")
    is_pub_member, member_publisher = _publisher_member_gate(request.auth)
    if is_pub_member:
        from ninja.errors import HttpError
        if not (member_publisher and _member_may_block(request.auth)):
            raise HttpError(403, "Your account is not allowed to block numbers")
        if entry.publisher_id != member_publisher.id:
            raise HttpError(403, "Not your publisher's entry")
    entry.delete()
    return {'message': 'Blacklist entry deleted'}


@router.post('/blacklist/{entry_id}/deactivate', response=BlacklistOutSchema)
def deactivate_blacklist_entry(request, entry_id: uuid.UUID):
    """Soft-disable a blacklist entry without deleting it."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    if getattr(request.auth, 'role', '') not in ('buyer', 'publisher'):
        require(request.auth, Capability.CREATE)
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    is_member, member_buyer = _buyer_member_gate(request.auth)
    if is_member:
        from ninja.errors import HttpError
        if not (member_buyer and _member_may_block(request.auth)):
            raise HttpError(403, "Your account is not allowed to block numbers")
        if entry.buyer_id != member_buyer.id:
            raise HttpError(403, "Not your buyer's entry")
    is_pub_member, member_publisher = _publisher_member_gate(request.auth)
    if is_pub_member:
        from ninja.errors import HttpError
        if not (member_publisher and _member_may_block(request.auth)):
            raise HttpError(403, "Your account is not allowed to block numbers")
        if entry.publisher_id != member_publisher.id:
            raise HttpError(403, "Not your publisher's entry")
    entry.is_active = False
    entry.save()
    return entry



# WHITELIST


@router.get('/whitelist', response=List[WhitelistOutSchema])
def list_whitelist(request, campaign_id: uuid.UUID = None, active_only: bool = True):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    qs = Whitelist.objects.filter(organization_id=request.auth.organization_id)

    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)
    if active_only:
        qs = qs.filter(is_active=True)

    return qs.order_by('-created_at')


@router.post('/whitelist', response={201: WhitelistOutSchema})
def create_whitelist(request, payload: WhitelistCreateSchema):
    """Add a trusted number that bypasses all spam checks."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    require(request.auth, Capability.CREATE)
    entry = Whitelist.objects.create(
        organization_id=request.auth.organization_id,
        phone_number=canonical(payload.phone_number or payload.number),
        notes=payload.notes,
        campaign_id=payload.campaign_id,
        added_by=request.auth,
    )
    return 201, entry


@router.get('/whitelist/{entry_id}', response=WhitelistOutSchema)
def get_whitelist_entry(request, entry_id: uuid.UUID):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    entry = get_object_or_404(
        Whitelist, id=entry_id, organization_id=request.auth.organization_id
    )
    return entry


@router.patch('/whitelist/{entry_id}', response=WhitelistOutSchema)
def update_whitelist_entry(request, entry_id: uuid.UUID, payload: WhitelistUpdateSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    require(request.auth, Capability.EDIT)
    entry = get_object_or_404(
        Whitelist, id=entry_id, organization_id=request.auth.organization_id
    )
    for field, value in payload.dict(exclude_none=True).items():
        setattr(entry, field, value)
    entry.save()
    return entry


@router.delete('/whitelist/{entry_id}', response=MessageSchema)
def delete_whitelist_entry(request, entry_id: uuid.UUID):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'delete')
    require(request.auth, Capability.DELETE)
    entry = get_object_or_404(
        Whitelist, id=entry_id, organization_id=request.auth.organization_id
    )
    entry.delete()
    return {'message': 'Whitelist entry deleted'}



# ANONYMOUS CALL BLOCK


@router.get('/anonymous-block', response=List[AnonymousBlockOutSchema])
def list_anonymous_blocks(request):
    """List all campaigns with anonymous call blocking configured."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    return AnonymousCallBlock.objects.filter(
        organization_id=request.auth.organization_id
    ).order_by('-created_at')


@router.post('/anonymous-block', response={201: AnonymousBlockOutSchema})
def create_anonymous_block(request, payload: AnonymousBlockCreateSchema):
    """Enable anonymous call blocking for a campaign."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    require(request.auth, Capability.CREATE)
    block, _ = AnonymousCallBlock.objects.update_or_create(
        organization_id=request.auth.organization_id,
        campaign_id=payload.campaign_id,
        defaults={'is_active': payload.is_active}
    )
    return 201, block


@router.patch('/anonymous-block/{campaign_id}', response=AnonymousBlockOutSchema)
def update_anonymous_block(request, campaign_id: uuid.UUID, payload: AnonymousBlockUpdateSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    require(request.auth, Capability.EDIT)
    block = get_object_or_404(
        AnonymousCallBlock,
        campaign_id=campaign_id,
        organization_id=request.auth.organization_id
    )
    block.is_active = payload.is_active
    block.save()
    return block



# SPAM REPORTS  (read-only — created automatically by the routing engine
@router.get('/reports', response=List[SpamReportOutSchema])
def list_spam_reports(
    request,
    campaign_id: uuid.UUID = None,
    phone_number: str = None,
    block_reason: str = None,
    limit: int = 100,
):
    """
    Read-only audit log of all calls blocked by spam protection.
    Supports filtering by campaign, phone number, and block reason.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    qs = SpamReport.objects.filter(
        organization_id=request.auth.organization_id
    )

    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)
    if phone_number:
        qs = qs.filter(phone_number=phone_number)
    if block_reason:
        qs = qs.filter(block_reason=block_reason)

    return qs.order_by('-created_at')[:limit]



# QUICK CHECK (utility endpoint — useful for frontend "check a number" feature)


@router.get('/check', response=dict)
def check_number(request, phone_number: str, campaign_id: uuid.UUID = None):
    """
    Quick lookup — is this number blacklisted or whitelisted?
    Does not log a spam report.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    from .services import SpamProtectionService

    result = SpamProtectionService.check(
        phone_number=phone_number,
        organization_id=str(request.auth.organization_id),
        campaign_id=str(campaign_id) if campaign_id else None,
        log=False,
    )
    if isinstance(result, tuple):
        status, data = result
        if isinstance(data, dict) and 'is_spam' not in data:
            data['is_spam'] = not data.get('allowed', True)
            data['confidence'] = data.get('confidence', None)
        return status, data
    return result


@router.get("/check", response={200: dict})
def check_number(request, phone_number: str = ''):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    number = phone_number
    if not number:
        return 200, {"number": number, "is_blocked": False, "is_whitelisted": False, "is_spam": False, "allowed": True, "confidence": None, "reason": None}
    from spam_protection.models import Blacklist, Whitelist
    # `organization_id`, not `organization`. These two models store a plain
    # UUIDField rather than a ForeignKey, so there is no `organization`
    # relation to filter on and Django raised FieldError - this endpoint
    # returned 500 for every number it was ever asked about.
    org_id = request.auth.organization_id
    is_blocked = Blacklist.objects.filter(organization_id=org_id, phone_number__in=variants(number), is_active=True).exists()
    is_whitelisted = Whitelist.objects.filter(organization_id=org_id, phone_number__in=variants(number), is_active=True).exists()
    return 200, {"number": number, "is_blocked": is_blocked, "is_whitelisted": is_whitelisted, "is_spam": is_blocked, "allowed": not is_blocked, "confidence": None, "reason": "blacklisted" if is_blocked else None}


@router.get("/anonymous-block", response={200: dict})
def get_anonymous_block(request):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'view')
    from django.core.cache import cache
    org_id = str(request.auth.organization.id)
    config = cache.get(f'anon_block_{org_id}')
    if config is None:
        config = {"enabled": False, "campaigns": []}
    return 200, config


@router.post("/anonymous-block", response={200: dict})
def create_anonymous_block(request):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    require(request.auth, Capability.CREATE)
    import json
    from django.core.cache import cache
    data = json.loads(request.body)
    cache.set(f'anon_block_{request.auth.organization.id}', data, timeout=None)
    return 200, {"message": "Anonymous block updated", "success": True}


@router.patch("/anonymous-block/{campaign_id}", response={200: dict})
def update_anonymous_block(request, campaign_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'blocked-numbers', 'edit')
    require(request.auth, Capability.EDIT)
    import json
    from django.core.cache import cache
    data = json.loads(request.body)
    config = cache.get(f'anon_block_{request.auth.organization.id}', {'enabled': False, 'campaigns': []})
    campaigns = config.get('campaigns', [])
    if campaign_id not in campaigns and data.get('enabled'):
        campaigns.append(campaign_id)
    elif campaign_id in campaigns and not data.get('enabled'):
        campaigns.remove(campaign_id)
    config['campaigns'] = campaigns
    cache.set(f'anon_block_{request.auth.organization.id}', config, timeout=None)
    return 200, {"message": "Updated", "success": True}
