"""Campaign Groups API (CH-328). Internal: publisher and buyer logins get 403.

    GET    /api/campaign-groups                  groups with their campaigns
    POST   /api/campaign-groups                  {name, description, campaign_ids}
    PATCH  /api/campaign-groups/{id}             {name?, description?, campaign_ids?}
    DELETE /api/campaign-groups/{id}             the campaigns stay, ungrouped
    GET    /api/campaign-groups/{id}/destinations   every destination across it
    POST   /api/campaign-groups/apply            one change, every campaign of the groups
                                                 (numbers: [...] for several destinations, CH-353)
    POST   /api/campaign-groups/{id}/line        one destination: which campaigns, and each one's
                                                 priority / weight (CH-415)
    GET    /api/campaign-groups/{id}/history     the group's change history (CH-415)
"""
from typing import List, Optional

from django.db import IntegrityError
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from accounts.api import JWTAuth
from accounts.permissions import Capability, require

from . import groups as G
from .models import CampaignGroup

router = Router(tags=["Campaign Groups"], auth=JWTAuth())


class GroupIn(Schema):
    name: str
    description: str = ''
    campaign_ids: List[str] = []


class GroupPatch(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    campaign_ids: Optional[List[str]] = None


class ApplyNumber(Schema):
    number: str
    buyer_id: Optional[str] = None
    revenue: Optional[float] = None
    convert_on: Optional[str] = None
    convert_seconds: Optional[int] = None


class ApplyIn(Schema):
    group_ids: List[str]
    action: str
    number: str = ''
    # CH-353: several destinations at once (all or nothing); `number` is one.
    numbers: Optional[List[ApplyNumber]] = None
    buyer_id: Optional[str] = None
    priority: Optional[int] = None
    weight: Optional[int] = None
    revenue: Optional[float] = None
    convert_on: Optional[str] = None
    convert_seconds: Optional[int] = None


class LineCampaign(Schema):
    campaign_id: str
    included: bool
    priority: Optional[int] = None
    weight: Optional[int] = None


class LineIn(Schema):
    number: str
    buyer_id: Optional[str] = None
    campaigns: List[LineCampaign]
    revenue: Optional[float] = None
    convert_on: Optional[str] = None
    convert_seconds: Optional[int] = None


def _staff(user, area='campaigns', action='view'):
    """Only the team: groups are never shown to publishers or buyers."""
    if getattr(user, 'role', '') in ('buyer', 'publisher'):
        raise HttpError(403, 'Campaign Groups are for the team only.')
    from accounts.workspace_permissions import require_perm
    require_perm(user, area, action)


def _get(user, group_id) -> CampaignGroup:
    g = CampaignGroup.objects.filter(organization=user.organization, id=group_id).first()
    if g is None:
        raise HttpError(404, 'Group not found')
    return g


@router.get("", response={200: dict})
def list_groups(request: HttpRequest):
    _staff(request.auth)
    qs = CampaignGroup.objects.filter(organization=request.auth.organization).prefetch_related('campaigns')
    return 200, {'items': [G.format_group(g) for g in qs]}


@router.post("", response={201: dict, 400: dict})
def create_group(request: HttpRequest, data: GroupIn):
    _staff(request.auth, 'campaigns', 'edit-campaign')
    require(request.auth, Capability.CREATE)
    name = data.name.strip()
    if not name:
        return 400, {'detail': 'Give the group a name'}
    try:
        g = CampaignGroup.objects.create(organization=request.auth.organization, name=name[:100],
                                         description=data.description.strip(), created_by=request.auth)
    except IntegrityError:
        return 400, {'detail': f'There is already a group called {name}'}
    try:
        G.set_members(g, data.campaign_ids)
    except ValueError as e:
        g.delete()
        return 400, {'detail': str(e)}
    return 201, G.format_group(g)


# Before /{group_id}: Django tries the paths in order.
@router.post("/apply", response={200: dict, 400: dict})
def apply_change(request: HttpRequest, data: ApplyIn):
    _staff(request.auth, 'call-flows', 'delete' if data.action == G.REMOVE else 'edit')
    require(request.auth, Capability.EDIT)
    try:
        if data.numbers:
            return 200, G.apply_many(request.auth, data.group_ids, data.action,
                                     [n.dict() for n in data.numbers], priority=data.priority,
                                     weight=data.weight, revenue=data.revenue,
                                     convert_on=data.convert_on, convert_seconds=data.convert_seconds)
        return 200, G.apply(request.auth, data.group_ids, data.action, data.number, buyer_id=data.buyer_id,
                            priority=data.priority, weight=data.weight, revenue=data.revenue,
                            convert_on=data.convert_on, convert_seconds=data.convert_seconds)
    except ValueError as e:
        return 400, {'detail': str(e)}


@router.patch("/{group_id}", response={200: dict, 400: dict})
def update_group(request: HttpRequest, group_id: str, data: GroupPatch):
    _staff(request.auth, 'campaigns', 'edit-campaign')
    require(request.auth, Capability.EDIT)
    g = _get(request.auth, group_id)
    if data.name is not None:
        if not data.name.strip():
            return 400, {'detail': 'Give the group a name'}
        g.name = data.name.strip()[:100]
    if data.description is not None:
        g.description = data.description.strip()
    try:
        g.save()
    except IntegrityError:
        return 400, {'detail': f'There is already a group called {g.name}'}
    if data.campaign_ids is not None:
        try:
            G.set_members(g, data.campaign_ids)
        except ValueError as e:
            return 400, {'detail': str(e)}
    return 200, G.format_group(g)


@router.delete("/{group_id}", response={200: dict})
def delete_group(request: HttpRequest, group_id: str):
    _staff(request.auth, 'campaigns', 'edit-campaign')
    # Editing, not deleting: no campaign and no destination goes with it.
    require(request.auth, Capability.EDIT)
    g = _get(request.auth, group_id)
    name = g.name
    g.delete()   # the campaigns stay, with their destinations; only the grouping goes
    return 200, {'message': f'{name} deleted. Its campaigns keep their destinations.', 'success': True}


@router.get("/{group_id}/destinations", response={200: dict})
def group_destinations(request: HttpRequest, group_id: str):
    _staff(request.auth, 'call-flows', 'view')
    g = _get(request.auth, group_id)
    return 200, {'group': G.format_group(g), 'items': G.group_destinations(g)}


@router.post("/{group_id}/line", response={200: dict, 400: dict})
def set_line(request: HttpRequest, group_id: str, data: LineIn):
    """CH-415: one destination in the group - in exactly these campaigns, each
    with its own priority and weight. All or nothing."""
    removing = any(not c.included for c in data.campaigns)
    _staff(request.auth, 'call-flows', 'delete' if removing else 'edit')
    require(request.auth, Capability.EDIT)
    g = _get(request.auth, group_id)
    try:
        return 200, G.set_line(request.auth, g, data.number, [c.dict() for c in data.campaigns],
                               buyer_id=data.buyer_id, revenue=data.revenue,
                               convert_on=data.convert_on, convert_seconds=data.convert_seconds)
    except ValueError as e:
        return 400, {'detail': str(e)}


@router.get("/{group_id}/history", response={200: dict})
def group_history(request: HttpRequest, group_id: str):
    _staff(request.auth)
    return 200, {'items': G.history(_get(request.auth, group_id))}
