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



# BLACKLIST


@router.get('/blacklist', response={200: dict})
def list_blacklist(request, campaign_id: uuid.UUID = None, active_only: bool = False, page: int = 1, page_size: int = 500):
    """
    List blacklisted numbers for the authenticated user's organisation.
    """
    qs = Blacklist.objects.filter(organization_id=request.auth.organization_id)

    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)
    if active_only:
        qs = qs.filter(is_active=True)

    qs = qs.order_by('-created_at')
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    items = list(qs[start:end].values(
        'id', 'phone_number', 'reason', 'notes', 'is_active', 'campaign_id', 'expires_at', 'created_at'
    ))
    for item in items:
        item['id'] = str(item['id'])
        item['campaign_id'] = str(item['campaign_id']) if item['campaign_id'] else None
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
    entry = Blacklist.objects.create(
        organization_id=request.auth.organization_id,
        phone_number=payload.phone_number or payload.number,
        reason=payload.reason,
        notes=payload.notes,
        campaign_id=payload.campaign_id,
        expires_at=payload.expires_at,
        added_by=request.auth,
    )
    return 201, entry


@router.get('/blacklist/{entry_id}', response=BlacklistOutSchema)
def get_blacklist_entry(request, entry_id: uuid.UUID):
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    return entry


@router.patch('/blacklist/{entry_id}', response={200: BlacklistOutSchema, 400: dict})
def update_blacklist_entry(request, entry_id: uuid.UUID, payload: BlacklistUpdateSchema):
    if payload.phone_number is not None:
        return 400, {"detail": "phone_number is immutable after creation. Delete and recreate to change the number."}
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    for field, value in payload.dict(exclude_none=True).items():
        if field != 'phone_number':
            setattr(entry, field, value)
    entry.save()
    return 200, entry


@router.delete('/blacklist/{entry_id}', response=MessageSchema)
def delete_blacklist_entry(request, entry_id: uuid.UUID):
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    entry.delete()
    return {'message': 'Blacklist entry deleted'}


@router.post('/blacklist/{entry_id}/deactivate', response=BlacklistOutSchema)
def deactivate_blacklist_entry(request, entry_id: uuid.UUID):
    """Soft-disable a blacklist entry without deleting it."""
    entry = get_object_or_404(
        Blacklist, id=entry_id, organization_id=request.auth.organization_id
    )
    entry.is_active = False
    entry.save()
    return entry



# WHITELIST


@router.get('/whitelist', response=List[WhitelistOutSchema])
def list_whitelist(request, campaign_id: uuid.UUID = None, active_only: bool = True):
    qs = Whitelist.objects.filter(organization_id=request.auth.organization_id)

    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)
    if active_only:
        qs = qs.filter(is_active=True)

    return qs.order_by('-created_at')


@router.post('/whitelist', response={201: WhitelistOutSchema})
def create_whitelist(request, payload: WhitelistCreateSchema):
    """Add a trusted number that bypasses all spam checks."""
    entry = Whitelist.objects.create(
        organization_id=request.auth.organization_id,
        phone_number=payload.phone_number or payload.number,
        notes=payload.notes,
        campaign_id=payload.campaign_id,
        added_by=request.auth,
    )
    return 201, entry


@router.get('/whitelist/{entry_id}', response=WhitelistOutSchema)
def get_whitelist_entry(request, entry_id: uuid.UUID):
    entry = get_object_or_404(
        Whitelist, id=entry_id, organization_id=request.auth.organization_id
    )
    return entry


@router.patch('/whitelist/{entry_id}', response=WhitelistOutSchema)
def update_whitelist_entry(request, entry_id: uuid.UUID, payload: WhitelistUpdateSchema):
    entry = get_object_or_404(
        Whitelist, id=entry_id, organization_id=request.auth.organization_id
    )
    for field, value in payload.dict(exclude_none=True).items():
        setattr(entry, field, value)
    entry.save()
    return entry


@router.delete('/whitelist/{entry_id}', response=MessageSchema)
def delete_whitelist_entry(request, entry_id: uuid.UUID):
    entry = get_object_or_404(
        Whitelist, id=entry_id, organization_id=request.auth.organization_id
    )
    entry.delete()
    return {'message': 'Whitelist entry deleted'}



# ANONYMOUS CALL BLOCK


@router.get('/anonymous-block', response=List[AnonymousBlockOutSchema])
def list_anonymous_blocks(request):
    """List all campaigns with anonymous call blocking configured."""
    return AnonymousCallBlock.objects.filter(
        organization_id=request.auth.organization_id
    ).order_by('-created_at')


@router.post('/anonymous-block', response={201: AnonymousBlockOutSchema})
def create_anonymous_block(request, payload: AnonymousBlockCreateSchema):
    """Enable anonymous call blocking for a campaign."""
    block, _ = AnonymousCallBlock.objects.update_or_create(
        organization_id=request.auth.organization_id,
        campaign_id=payload.campaign_id,
        defaults={'is_active': payload.is_active}
    )
    return 201, block


@router.patch('/anonymous-block/{campaign_id}', response=AnonymousBlockOutSchema)
def update_anonymous_block(request, campaign_id: uuid.UUID, payload: AnonymousBlockUpdateSchema):
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
    number = phone_number
    if not number:
        return 200, {"number": number, "is_blocked": False, "is_whitelisted": False, "is_spam": False, "allowed": True, "confidence": None, "reason": None}
    from spam_protection.models import Blacklist, Whitelist
    is_blocked = Blacklist.objects.filter(organization=request.auth.organization, phone_number=number, is_active=True).exists()
    is_whitelisted = Whitelist.objects.filter(organization=request.auth.organization, phone_number=number, is_active=True).exists()
    return 200, {"number": number, "is_blocked": is_blocked, "is_whitelisted": is_whitelisted, "is_spam": is_blocked, "allowed": not is_blocked, "confidence": None, "reason": "blacklisted" if is_blocked else None}


@router.get("/anonymous-block", response={200: dict})
def get_anonymous_block(request):
    from django.core.cache import cache
    org_id = str(request.auth.organization.id)
    config = cache.get(f'anon_block_{org_id}')
    if config is None:
        config = {"enabled": False, "campaigns": []}
    return 200, config


@router.post("/anonymous-block", response={200: dict})
def create_anonymous_block(request):
    import json
    from django.core.cache import cache
    data = json.loads(request.body)
    cache.set(f'anon_block_{request.auth.organization.id}', data, timeout=None)
    return 200, {"message": "Anonymous block updated", "success": True}


@router.patch("/anonymous-block/{campaign_id}", response={200: dict})
def update_anonymous_block(request, campaign_id: str):
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
