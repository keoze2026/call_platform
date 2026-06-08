from ninja import Router, Schema
from typing import Optional, List
from accounts.api import JWTAuth

router = Router(tags=["Shields"], auth=JWTAuth())


class ShieldSchema(Schema):
    name: str
    shield_type: str
    campaign_ids: list = []
    is_active: bool = True


def get_or_create_shields(organization):
    from django.core.cache import cache
    key = f'shields_{organization.id}'
    shields = cache.get(key)
    if not shields:
        shields = []
    return shields


def save_shields(organization, shields):
    from django.core.cache import cache
    key = f'shields_{organization.id}'
    cache.set(key, shields, timeout=None)


@router.get("/", response={200: dict})
def list_shields(request, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    shields = get_or_create_shields(request.auth.organization)
    return 200, paginate_list(shields, page, page_size)


@router.post("/", response={201: dict})
def create_shield(request, payload: ShieldSchema):
    import uuid
    shields = get_or_create_shields(request.auth.organization)
    shield = {
        'id': str(uuid.uuid4()),
        'name': payload.name,
        'shield_type': payload.shield_type,
        'campaign_ids': payload.campaign_ids,
        'is_active': payload.is_active,
    }
    # Apply shield to campaigns
    if payload.campaign_ids and payload.shield_type == 'voip':
        from campaigns.models import Campaign
        Campaign.objects.filter(
            id__in=payload.campaign_ids,
            organization=request.auth.organization
        ).update(block_voip=payload.is_active)
    shields.append(shield)
    save_shields(request.auth.organization, shields)
    return 201, shield


@router.delete("/{shield_id}/", response={200: dict})
def delete_shield(request, shield_id: str):
    shields = get_or_create_shields(request.auth.organization)
    shields = [s for s in shields if s['id'] != shield_id]
    save_shields(request.auth.organization, shields)
    return 200, {"detail": "Shield deleted"}
