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
        qs = qs.filter(shield_type=shield_type)
    data = [{'id': str(s.id), 'name': s.name, 'shield_type': s.shield_type, 'campaign_ids': s.campaign_ids, 'is_active': s.is_active, 'blocked_carriers': s.blocked_carriers} for s in qs]
    return 200, paginate_list(data, page, page_size)


@router.post("/", response={201: dict})
def create_shield(request, payload: ShieldSchema):
    from spam_protection.models import Shield
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
        return 200, {'id': str(shield.id), 'name': shield.name, 'shield_type': shield.shield_type, 'campaign_ids': shield.campaign_ids, 'is_active': shield.is_active, 'blocked_carriers': shield.blocked_carriers}
    except Shield.DoesNotExist:
        return 404, {"detail": "Shield not found"}


@router.patch("/{shield_id}/", response={200: dict, 404: dict})
def patch_shield(request, shield_id: str, payload: ShieldUpdateSchema):
    from spam_protection.models import Shield
    try:
        shield = Shield.objects.get(id=shield_id, organization=request.auth.organization)
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
        shield.delete()
        return 200, {"detail": "Shield deleted"}
    except Shield.DoesNotExist:
        return 404, {"detail": "Shield not found"}
