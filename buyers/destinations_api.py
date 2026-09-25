from accounts.permissions import require, Capability
from django.core.exceptions import ValidationError
from ninja import Router, Schema
from typing import Optional, List, Union
from accounts.api import JWTAuth
from django.db.models import Sum, Count, Q
from django.db.models.functions import Coalesce
from decimal import Decimal

router = Router(tags=["Destinations"], auth=JWTAuth())


class DestinationSchema(Schema):
    buyer_id: Union[str, None] = None
    name: str
    tfn: str = ''
    phone_number: Union[str, None] = None

    def get_tfn(self):
        return self.tfn or self.phone_number or ''
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
    live: int = 0
    live_calls: int = 0
    liveCalls: int = 0


class DestinationUpdateSchema(Schema):
    # Was absent entirely, so a destination saved without a buyer could never be
    # corrected through the API - only by editing the database directly.
    buyer_id: Optional[str] = None
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


def format_destination(d, start_date=None, end_date=None):
    from django.utils import timezone
    from routing.models import CallLog
    from analytics.models import CallRecord
    from django.db.models import Q, Count, Sum
    from django.db.models.functions import Coalesce
    from decimal import Decimal

    org = d.organization
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    from datetime import timedelta
    # 1. Real-time live calls for this destination
    base_live_q = (
        Q(status__in=['in_progress', 'ringing', 'queued'], ended_at__isnull=True)
    )
    live_q = base_live_q & Q(created_at__gte=now - timedelta(hours=4))

    if d.tfn:
        live_q &= Q(destination_number=d.tfn)
    else:
        # Fallback if somehow there's no TFN (though unlikely for valid destinations)
        live_q &= Q(id__isnull=True) # returns empty

    live_count = CallLog.objects.filter(organization=org).filter(live_q).count()

    # 2. Calls and revenue today for this destination
    from django.utils.dateparse import parse_datetime
    rec_q = Q(organization=org)
    
    if start_date:
        dt = parse_datetime(start_date + 'T00:00:00') or timezone.datetime.fromisoformat(start_date)
        if timezone.is_naive(dt): dt = timezone.make_aware(dt)
        rec_q &= Q(created_at__gte=dt)
    else:
        rec_q &= Q(created_at__gte=today_start)

    if end_date:
        dt = parse_datetime(end_date + 'T23:59:59') or timezone.datetime.fromisoformat(end_date)
        if timezone.is_naive(dt): dt = timezone.make_aware(dt)
        rec_q &= Q(created_at__lte=dt)
    if d.tfn:
        rec_q &= Q(destination_number=d.tfn)
    else:
        rec_q &= Q(id__isnull=True)

    today_stats = CallLog.objects.filter(rec_q).aggregate(
        total_calls=Count('id'),
        revenue=Coalesce(Sum('revenue'), Decimal('0.00'))
    )
    daily_count = today_stats['total_calls'] or 0
    revenue_today = float(today_stats['revenue'] or 0)

    # 3. Hourly, Monthly, Global counts
    hour_ago = now - timedelta(hours=1)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    hourly_count = CallLog.objects.filter(rec_q, created_at__gte=hour_ago).count()
    monthly_count = CallLog.objects.filter(rec_q, created_at__gte=month_start).count()
    global_count = CallLog.objects.filter(rec_q).count()

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
        'live_calls': live_count,
        'live': live_count,
        'hourly_calls': hourly_count,
        'daily_calls': daily_count,
        'calls_today': daily_count,
        'monthly_calls': monthly_count,
        'global_calls': global_count,
        'revenue_today': revenue_today,
        'revenue': revenue_today,
        'liveCalls': live_count,
        'callsToday': daily_count,
        'todayCalls': daily_count,
        'dailyCalls': daily_count,
        'revenueToday': revenue_today,
        'todayRevenue': revenue_today,
        'stats': {'live': live_count, 'live_calls': live_count, 'calls_today': daily_count, 'today_calls': daily_count, 'revenue_today': revenue_today, 'daily_calls': daily_count, 'cap_today': d.daily_cap, 'daily_cap': d.daily_cap},
        'cap_today': d.daily_cap,
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
def list_destinations(
    request, 
    page: int = 1, 
    page_size: int = 50, 
    buyer_id: Optional[str] = None, 
    enabled: Optional[bool] = None,
    created_at__gte: Optional[str] = None,
    created_at__lte: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    from config.pagination import paginate_list
    from buyers.destination import Destination
    qs = Destination.objects.filter(organization=request.auth.organization).select_related('buyer')
    if buyer_id:
        qs = qs.filter(buyer_id=buyer_id)
    if enabled is not None:
        qs = qs.filter(enabled=enabled)
        
    val_from = start_date or created_at__gte
    val_to = end_date or created_at__lte
    
    data = [format_destination(d, val_from, val_to) for d in qs]
    return 200, paginate_list(data, page, page_size)


@router.get("/stats/", response={200: dict})
def get_destination_stats(request):
    from buyers.destination import Destination
    from routing.models import CallLog
    org = request.auth.organization
    qs = Destination.objects.filter(organization=org)

    from datetime import timedelta
    from django.utils import timezone
    total_live = CallLog.objects.filter(
        organization=org,
        status__in=['in_progress', 'ringing'],
        ended_at__isnull=True,
        created_at__gte=timezone.now() - timedelta(hours=4)
    ).count()

    stats = qs.aggregate(
        total_cc=Sum('concurrency_cap'),
        active_tfns=Count('id', filter=Q(enabled=True)),
    )
    total_cc = stats['total_cc'] or 0
    return 200, {
        'active_live': total_live,
        'total_live': total_live,
        'total_cc': total_cc,
        'active_tfns': stats['active_tfns'] or 0,
        'vacant_cc': max(0, total_cc - total_live),
    }



@router.post("/", response={201: dict, 400: dict})
def create_destination(request, payload: DestinationSchema):
    require(request.auth, Capability.CREATE)
    from buyers.destination import Destination
    from buyers.models import Buyer

    # A destination without a buyer is unreachable: routing resolves the live
    # destination with Destination.objects.filter(buyer=..., enabled=True), so
    # one with no buyer can never be selected. It was optional, and five were
    # saved that way - active, and silently never used.
    if not payload.buyer_id:
        return 400, {"detail": "buyer_id is required - a destination with no buyer can never receive calls"}

    try:
        buyer = Buyer.objects.get(id=payload.buyer_id, organization=request.auth.organization)
    except (Buyer.DoesNotExist, ValidationError, ValueError):
        return 400, {"detail": "Buyer not found or invalid buyer_id"}
    d = Destination.objects.create(
        organization=request.auth.organization,
        buyer=buyer,
        name=payload.name,
        tfn=payload.get_tfn(),
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


@router.patch("/{destination_id}/", response={200: dict, 400: dict, 404: dict})
def update_destination(request, destination_id: str, payload: DestinationUpdateSchema):
    require(request.auth, Capability.EDIT)
    from buyers.destination import Destination
    from buyers.models import Buyer
    try:
        d = Destination.objects.get(id=destination_id, organization=request.auth.organization)

        fields = payload.dict(exclude_none=True)

        # Resolve the buyer rather than assigning the raw id: setattr would
        # happily store an id belonging to another organization.
        if 'buyer_id' in fields:
            try:
                d.buyer = Buyer.objects.get(
                    id=fields.pop('buyer_id'), organization=request.auth.organization
                )
            except (Buyer.DoesNotExist, ValidationError, ValueError):
                return 400, {"detail": "Buyer not found or invalid buyer_id"}

        for k, v in fields.items():
            setattr(d, k, v)
        d.save()
        try:
            from routing.models import RuleDestination
            if d.buyer_id:
                RuleDestination.objects.filter(buyer_id=d.buyer_id).update(destination=d.tfn)
        except Exception:
            pass
        return 200, format_destination(d)
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}


@router.delete("/{destination_id}/", response={200: dict, 404: dict})
def delete_destination(request, destination_id: str):
    require(request.auth, Capability.DELETE)
    from buyers.destination import Destination
    try:
        Destination.objects.get(id=destination_id, organization=request.auth.organization).delete()
        return 200, {"detail": "Destination deleted"}
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}
