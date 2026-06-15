from ninja import Router, Schema
from typing import Optional, List, Union
from accounts.api import JWTAuth
from django.db.models import Sum, Count, Q

router = Router(tags=["Destinations"], auth=JWTAuth())


class DestinationSchema(Schema):
    buyer_id: Union[str, None] = None
    name: str
    tfn: str
    forward_type: str = 'number'
    enabled: bool = True
    concurrency_cap: int = 0
    hourly_cap: int = 0
    daily_cap: int = 0
    monthly_cap: int = 0
    global_cap: int = 0
    ring_duration_sec: int = 30
    timezone: str = 'America/New_York'
    filter_enabled: bool = False
    filter_groups: list = []
    business_hours_enabled: bool = False
    business_hour_slots: list = []


class DestinationUpdateSchema(Schema):
    name: Optional[str] = None
    tfn: Optional[str] = None
    forward_type: Optional[str] = None
    enabled: Optional[bool] = None
    concurrency_cap: Optional[int] = None
    hourly_cap: Optional[int] = None
    daily_cap: Optional[int] = None
    monthly_cap: Optional[int] = None
    global_cap: Optional[int] = None
    ring_duration_sec: Optional[int] = None
    timezone: Optional[str] = None
    filter_enabled: Optional[bool] = None
    filter_groups: Optional[list] = None
    business_hours_enabled: Optional[bool] = None
    business_hour_slots: Optional[list] = None


def format_destination(d):
    return {
        'id': str(d.id),
        'buyer_id': str(d.buyer_id) if d.buyer_id else None,
        'buyer_name': d.buyer.name if d.buyer else None,
        'name': d.name,
        'tfn': d.tfn,
        'forward_type': d.forward_type,
        'enabled': d.enabled,
        'concurrency_cap': d.concurrency_cap,
        'hourly_cap': d.hourly_cap,
        'daily_cap': d.daily_cap,
        'monthly_cap': d.monthly_cap,
        'global_cap': d.global_cap,
        'live_calls': d.live_calls,
        'hourly_calls': d.hourly_calls,
        'daily_calls': d.daily_calls,
        'monthly_calls': d.monthly_calls,
        'global_calls': d.global_calls,
        'ring_duration_sec': d.ring_duration_sec,
        'timezone': d.timezone,
        'filter_enabled': d.filter_enabled,
        'filter_groups': d.filter_groups,
        'business_hours_enabled': d.business_hours_enabled,
        'business_hour_slots': d.business_hour_slots,
        'created_at': d.created_at.isoformat(),
        'updated_at': d.updated_at.isoformat(),
    }


@router.get("/", response={200: dict})
def list_destinations(request, page: int = 1, page_size: int = 50, buyer_id: Optional[str] = None, enabled: Optional[bool] = None):
    from config.pagination import paginate_list
    from buyers.destination import Destination
    qs = Destination.objects.filter(organization=request.auth.organization).select_related('buyer')
    if buyer_id:
        qs = qs.filter(buyer_id=buyer_id)
    if enabled is not None:
        qs = qs.filter(enabled=enabled)
    data = [format_destination(d) for d in qs]
    return 200, paginate_list(data, page, page_size)


@router.get("/stats/", response={200: dict})
def get_destination_stats(request):
    from buyers.destination import Destination
    qs = Destination.objects.filter(organization=request.auth.organization)
    stats = qs.aggregate(
        total_live=Sum('live_calls'),
        total_cc=Sum('concurrency_cap'),
        active_tfns=Count('id', filter=Q(enabled=True)),
        active_live=Sum('live_calls', filter=Q(enabled=True)),
    )
    total_live = stats['total_live'] or 0
    total_cc = stats['total_cc'] or 0
    return 200, {
        'active_live': stats['active_live'] or 0,
        'total_live': total_live,
        'total_cc': total_cc,
        'active_tfns': stats['active_tfns'] or 0,
        'vacant_cc': total_cc - total_live,
    }


@router.post("/", response={201: dict, 400: dict})
def create_destination(request, payload: DestinationSchema):
    from buyers.destination import Destination
    from buyers.models import Buyer
    buyer = None
    if payload.buyer_id:
        try:
            buyer = Buyer.objects.get(id=payload.buyer_id, organization=request.auth.organization)
        except (Buyer.DoesNotExist, Exception):
            return 400, {"detail": "Buyer not found or invalid buyer_id"}
    d = Destination.objects.create(
        organization=request.auth.organization,
        buyer=buyer,
        name=payload.name,
        tfn=payload.tfn,
        forward_type=payload.forward_type,
        enabled=payload.enabled,
        concurrency_cap=payload.concurrency_cap,
        hourly_cap=payload.hourly_cap,
        daily_cap=payload.daily_cap,
        monthly_cap=payload.monthly_cap,
        global_cap=payload.global_cap,
        ring_duration_sec=payload.ring_duration_sec,
        timezone=payload.timezone,
        filter_enabled=payload.filter_enabled,
        filter_groups=payload.filter_groups,
        business_hours_enabled=payload.business_hours_enabled,
        business_hour_slots=payload.business_hour_slots,
    )
    return 201, format_destination(d)


@router.get("/{destination_id}/", response={200: dict, 404: dict})
def get_destination(request, destination_id: str):
    from buyers.destination import Destination
    try:
        d = Destination.objects.get(id=destination_id, organization=request.auth.organization)
        return 200, format_destination(d)
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}


@router.patch("/{destination_id}/", response={200: dict, 404: dict})
def update_destination(request, destination_id: str, payload: DestinationUpdateSchema):
    from buyers.destination import Destination
    try:
        d = Destination.objects.get(id=destination_id, organization=request.auth.organization)
        for k, v in payload.dict(exclude_none=True).items():
            setattr(d, k, v)
        d.save()
        return 200, format_destination(d)
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}


@router.delete("/{destination_id}/", response={200: dict, 404: dict})
def delete_destination(request, destination_id: str):
    from buyers.destination import Destination
    try:
        Destination.objects.get(id=destination_id, organization=request.auth.organization).delete()
        return 200, {"detail": "Destination deleted"}
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}
