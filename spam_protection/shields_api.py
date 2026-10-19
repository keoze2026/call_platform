from ninja import Router, Schema
from typing import Optional
from accounts.api import JWTAuth

router = Router(tags=["Shields"], auth=JWTAuth())


class ShieldSchema(Schema):
    name: str
    shield_type: str
    campaign_ids: list = []
    is_active: bool = True


class ShieldUpdateSchema(Schema):
    name: Optional[str] = None
    campaign_ids: Optional[list] = None
    is_active: Optional[bool] = None
    blocked_carriers: Optional[list] = None


def _shield_perm(user, shield_type, action):
    """VoIP and TCPA shields are separate pages with separate ticks.

    TCPA Shield has only a View tick on the Roles screen, so changing a TCPA
    shield needs the VoIP Shield edit or delete tick (the shield family's
    change rights) as well as seeing it.
    """
    from accounts.workspace_permissions import require_perm
    if shield_type == 'tcpa':
        require_perm(user, 'tcpa-shield', 'view')
        if action != 'view':
            require_perm(user, 'voip-shield', action)
    else:
        require_perm(user, 'voip-shield', action)


def _allowed_types(user):
    from accounts.workspace_permissions import has_perm
    types = []
    if has_perm(user, 'voip-shield', 'view'):
        types.append('voip')
    if has_perm(user, 'tcpa-shield', 'view'):
        types.append('tcpa')
    return types


def get_or_create_shields(organization):
    from spam_protection.models import Shield
    shields = Shield.objects.filter(organization=organization)
    return list(shields.values('id', 'name', 'shield_type', 'campaign_ids', 'is_active'))


@router.get("/", response={200: dict})
def list_shields(request, shield_type: Optional[str] = None, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    from spam_protection.models import Shield
    qs = Shield.objects.filter(organization=request.auth.organization)
    if shield_type:
        _shield_perm(request.auth, shield_type, 'view')
        qs = qs.filter(shield_type=shield_type)
    else:
        # No filter asked for: show only the kinds this role may see.
        qs = qs.exclude(shield_type__in=[t for t in ('voip', 'tcpa') if t not in _allowed_types(request.auth)])
    data = [{'id': str(s.id), 'name': s.name, 'shield_type': s.shield_type, 'campaign_ids': s.campaign_ids, 'is_active': s.is_active, 'blocked_carriers': s.blocked_carriers} for s in qs]
    return 200, paginate_list(data, page, page_size)


@router.post("/", response={201: dict})
def create_shield(request, payload: ShieldSchema):
    from spam_protection.models import Shield
    _shield_perm(request.auth, payload.shield_type, 'edit')
    shield = Shield.objects.create(
        organization=request.auth.organization,
        name=payload.name,
        shield_type=payload.shield_type,
        campaign_ids=payload.campaign_ids,
        is_active=payload.is_active,
    )
    if payload.campaign_ids and payload.shield_type == 'voip':
        from campaigns.models import Campaign
        Campaign.objects.filter(
            id__in=payload.campaign_ids,
            organization=request.auth.organization
        ).update(block_voip=payload.is_active)
    return 201, {'id': str(shield.id), 'name': shield.name, 'shield_type': shield.shield_type, 'campaign_ids': shield.campaign_ids, 'is_active': shield.is_active, 'blocked_carriers': shield.blocked_carriers}


@router.get("/{shield_id}/", response={200: dict, 404: dict})
def get_shield(request, shield_id: str):
    from spam_protection.models import Shield
    try:
        shield = Shield.objects.get(id=shield_id, organization=request.auth.organization)
        _shield_perm(request.auth, shield.shield_type, 'view')
        return 200, {'id': str(shield.id), 'name': shield.name, 'shield_type': shield.shield_type, 'campaign_ids': shield.campaign_ids, 'is_active': shield.is_active, 'blocked_carriers': shield.blocked_carriers}
    except Shield.DoesNotExist:
        return 404, {"detail": "Shield not found"}


@router.patch("/{shield_id}/", response={200: dict, 404: dict})
def patch_shield(request, shield_id: str, payload: ShieldUpdateSchema):
    from spam_protection.models import Shield
    try:
        shield = Shield.objects.get(id=shield_id, organization=request.auth.organization)
        _shield_perm(request.auth, shield.shield_type, 'edit')
        if payload.name is not None:
            shield.name = payload.name
        if payload.campaign_ids is not None:
            shield.campaign_ids = payload.campaign_ids
        if payload.is_active is not None:
            shield.is_active = payload.is_active
        if payload.blocked_carriers is not None:
            shield.blocked_carriers = payload.blocked_carriers
        shield.save()
        return 200, {'id': str(shield.id), 'name': shield.name, 'shield_type': shield.shield_type, 'campaign_ids': shield.campaign_ids, 'is_active': shield.is_active, 'blocked_carriers': shield.blocked_carriers}
    except Shield.DoesNotExist:
        return 404, {"detail": "Shield not found"}


@router.put("/{shield_id}/", response={200: dict, 404: dict})
def update_shield(request, shield_id: str, payload: ShieldUpdateSchema):
    from spam_protection.models import Shield
    try:
        shield = Shield.objects.get(id=shield_id, organization=request.auth.organization)
        _shield_perm(request.auth, shield.shield_type, 'edit')
        if payload.name is not None:
            shield.name = payload.name
        if payload.campaign_ids is not None:
            shield.campaign_ids = payload.campaign_ids
        if payload.is_active is not None:
            shield.is_active = payload.is_active
        if payload.blocked_carriers is not None:
            shield.blocked_carriers = payload.blocked_carriers
        shield.save()
        return 200, {'id': str(shield.id), 'name': shield.name, 'shield_type': shield.shield_type, 'campaign_ids': shield.campaign_ids, 'is_active': shield.is_active, 'blocked_carriers': shield.blocked_carriers}
    except Shield.DoesNotExist:
        return 404, {"detail": "Shield not found"}


@router.delete("/{shield_id}/", response={200: dict, 404: dict})
def delete_shield(request, shield_id: str):
    from spam_protection.models import Shield
    try:
        shield = Shield.objects.get(id=shield_id, organization=request.auth.organization)
        _shield_perm(request.auth, shield.shield_type, 'delete')
        shield.delete()
        return 200, {"detail": "Shield deleted"}
    except Shield.DoesNotExist:
        return 404, {"detail": "Shield not found"}
