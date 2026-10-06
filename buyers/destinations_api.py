from accounts.permissions import require, Capability
from django.core.exceptions import ValidationError
from ninja import Router, Schema
from typing import Optional, List, Union
from accounts.api import JWTAuth
from django.db.models import Sum, Count, Q
from django.db.models.functions import Coalesce
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

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


def format_destination(d, start_date=None, end_date=None, tz_name=None):
    """One destination row.

    `tz_name` is the IANA zone the caller reads these numbers in. Without it
    the day and month were cut on UTC midnight, so an Eastern user's Daily
    count reset at 8pm the previous evening - calls taken after 8pm were
    counted against tomorrow. Falls back to the destination's own timezone,
    which is the zone its schedule is written in.
    """
    from django.utils import timezone
    from routing.models import CallLog
    from analytics.models import CallRecord
    from django.db.models import Q, Count, Sum
    from django.db.models.functions import Coalesce
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    org = d.organization
    try:
        tz = ZoneInfo(tz_name or d.timezone or 'America/New_York')
    except Exception:
        tz = ZoneInfo('America/New_York')

    now = timezone.now()
    # Midnight where the reader is, converted back to an absolute instant.
    local_now = timezone.localtime(now, tz)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

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

    # 2. Which calls belong to this destination, with no date filter on it.
    # Kept separate from the date range below: the month and all-time counts
    # were built by adding a month filter on top of a query that already had
    # the day filter in it, so "calls this month" and "calls all time" could
    # never be larger than "calls today". Every cap reading in the interface
    # was really today's number under four different labels.
    from django.utils.dateparse import parse_datetime

    dest_q = Q(organization=org)
    if d.tfn:
        dest_q &= Q(destination_number=d.tfn)
    else:
        # No TFN means routing can never select it, so it has no calls.
        dest_q &= Q(id__isnull=True)

    # 3. The requested range, defaulting to today.
    range_q = Q()
    if start_date:
        dt = parse_datetime(start_date + 'T00:00:00') or timezone.datetime.fromisoformat(start_date)
        if timezone.is_naive(dt): dt = timezone.make_aware(dt)
        range_q &= Q(created_at__gte=dt)
    else:
        range_q &= Q(created_at__gte=today_start)

    if end_date:
        dt = parse_datetime(end_date + 'T23:59:59') or timezone.datetime.fromisoformat(end_date)
        if timezone.is_naive(dt): dt = timezone.make_aware(dt)
        range_q &= Q(created_at__lte=dt)

    today_stats = CallLog.objects.filter(dest_q & range_q).aggregate(
        total_calls=Count('id'),
        revenue=Coalesce(Sum('revenue'), Decimal('0.00'))
    )
    daily_count = today_stats['total_calls'] or 0
    revenue_today = float(today_stats['revenue'] or 0)

    # 4. Hourly, monthly and all-time, each measured from the destination alone.
    hour_ago = now - timedelta(hours=1)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    hourly_count = CallLog.objects.filter(dest_q, created_at__gte=hour_ago).count()
    monthly_count = CallLog.objects.filter(dest_q, created_at__gte=month_start).count()
    global_count = CallLog.objects.filter(dest_q).count()

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
    end_date: Optional[str] = None,
    # IANA name, e.g. "America/New_York". Decides where the Daily and Monthly
    # counters are cut. Without it they were cut on UTC midnight, so an Eastern
    # user's Daily count reset at 8pm the evening before. The Reports endpoints
    # already take this parameter; these did not.
    timezone: Optional[str] = None,
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
    
    data = [format_destination(d, val_from, val_to, tz_name=timezone) for d in qs]
    return 200, paginate_list(data, page, page_size)


@router.get("/stats/", response={200: dict})
def get_destination_stats(request, timezone: Optional[str] = None):
    """Headline numbers for the Destinations page.

    active_live and total_live were the same query, so Active Live always
    equalled Total Live and the figure said nothing: a call on a paused
    destination counted as active capacity in use.

    total_cc summed the concurrency cap of every destination including the
    disabled ones, so Unfilled CC claimed free capacity on destinations that
    cannot take a call.
    """
    from buyers.destination import Destination
    from routing.models import CallLog
    org = request.auth.organization
    qs = Destination.objects.filter(organization=org)

    from datetime import timedelta
    # Aliased: the request parameter is called `timezone`, and a plain
    # `from django.utils import timezone` here would rebind that name and
    # throw the caller's zone away.
    from django.utils import timezone as djtz
    live_q = CallLog.objects.filter(
        organization=org,
        status__in=['in_progress', 'ringing'],
        ended_at__isnull=True,
        created_at__gte=djtz.now() - timedelta(hours=4)
    )
    # Everything in flight, active or paused. Unchanged.
    total_live = live_q.count()

    # Only what is on an enabled destination, matched on the number dialled -
    # the same way format_destination decides which calls belong to a row.
    enabled_tfns = list(
        qs.filter(enabled=True).values_list('tfn', flat=True)
    )
    active_live = (
        live_q.filter(destination_number__in=enabled_tfns).count()
        if enabled_tfns else 0
    )

    stats = qs.aggregate(
        # Capacity only counts where a call could actually land.
        total_cc=Sum('concurrency_cap', filter=Q(enabled=True)),
        active_tfns=Count('id', filter=Q(enabled=True)),
    )
    total_cc = stats['total_cc'] or 0
    return 200, {
        'active_live': active_live,
        'total_live': total_live,
        'total_cc': total_cc,
        'active_tfns': stats['active_tfns'] or 0,
        # Free capacity on active destinations, never negative.
        'vacant_cc': max(0, total_cc - active_live),
    }



def _enabled_clash(organization, tfn, enabled, exclude_id=None):
    """Is another live destination already on this number?

    Returns the message to show, or None. Calls are attributed by an exact match
    on the number, so two live destinations sharing one means both buyers are
    credited with the same call and both caps count it.
    """
    from buyers.destination import Destination

    if not enabled or not tfn:
        return None

    qs = Destination.objects.filter(
        organization=organization, tfn=tfn, enabled=True,
    ).select_related('buyer')
    if exclude_id:
        qs = qs.exclude(id=exclude_id)

    other = qs.first()
    if other is None:
        return None

    held_by = other.buyer.name if other.buyer else 'another destination'
    return (
        f"{tfn} is already live on {held_by}. Two live destinations cannot share "
        f"a number - every call to it would be counted for both. Switch that one "
        f"off first, or use a different number."
    )


def _buyer_already_live(organization, buyer, enabled, exclude_id=None):
    """Does this buyer already have a live destination?

    Routing resolves a buyer's destination with a single `.first()`, so a second
    enabled one never receives a call - it sits in the interface looking active
    and silently does nothing. Returns the message to show, or None.
    """
    from buyers.destination import Destination

    if not enabled or buyer is None:
        return None

    qs = Destination.objects.filter(
        organization=organization, buyer=buyer, enabled=True,
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)

    other = qs.first()
    if other is None:
        return None

    return (
        f"{buyer.name} already routes to {other.tfn}. A buyer can only have one "
        f"live destination - a second one would never receive a call. Switch "
        f"{other.tfn} off first if you want calls to go somewhere else."
    )


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

    # The database refuses two live destinations on one number. Catching it here
    # turns an IntegrityError 500 into something the person can act on, and
    # names the buyer already holding it so they can go and look.
    clash = _enabled_clash(
        request.auth.organization, payload.get_tfn(), payload.enabled,
    )
    if clash:
        return 400, {"detail": clash}

    clash = _buyer_already_live(request.auth.organization, buyer, payload.enabled)
    if clash:
        return 400, {"detail": clash}

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

        # Both an edit to the number and switching one on can collide with a
        # destination that is already live on it.
        clash = _enabled_clash(
            request.auth.organization, d.tfn, d.enabled, exclude_id=d.id,
        )
        if clash:
            return 400, {"detail": clash}

        clash = _buyer_already_live(
            request.auth.organization, d.buyer, d.enabled, exclude_id=d.id,
        )
        if clash:
            return 400, {"detail": clash}

        d.save()
        try:
            from routing.models import RuleDestination
            if d.buyer_id:
                RuleDestination.objects.filter(buyer_id=d.buyer_id).update(destination=d.tfn)
        except Exception:
            # The destination was saved but the routing rules still point at the
            # old number, so calls keep going to the previous one.
            logger.exception('failed to sync routing rules to new destination: dest=%s', d.id)
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
