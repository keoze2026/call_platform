from accounts.permissions import require, Capability, scope_queryset
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


def gather_destination_counts(org, tfns, start_date=None, end_date=None, tz_name=None):
    """Every count the page needs, in five grouped queries instead of 5 per row.

    format_destination ran five counts for each destination. A page of 165 cost
    1,155 queries and 1.66 seconds measured on production, and it is the
    slowest thing the Destinations page waits on.

    Grouped by buyer and destination_number (CH-392): one TFN can be live
    under several buyers, and each must count only the calls routed to it.
    Read a row with `counts_for(counts, d)`.
    """
    from datetime import timedelta
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    from django.db.models import Count, Q, Sum
    from django.db.models.functions import Coalesce
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    from routing.models import CallLog

    tfns = [t for t in tfns if t]
    if not tfns:
        return {}

    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.get_current_timezone()
    except Exception:
        tz = timezone.get_current_timezone()

    now = timezone.now()
    local_now = timezone.localtime(now, tz)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hour_ago = now - timedelta(hours=1)

    base = CallLog.objects.filter(organization=org, destination_number__in=tfns)
    out = {}

    def row_for(r):
        k = (str(r['buyer_id']) if r['buyer_id'] else None, r['destination_number'])
        if k not in out:
            out[k] = {'live': 0, 'daily': 0, 'revenue': Decimal('0.00'),
                      'hourly': 0, 'monthly': 0, 'global': 0}
        return out[k]

    def collect(qs, key, field='n'):
        for r in qs:
            row_for(r)[key] = r[field] or 0

    group = ('buyer_id', 'destination_number')
    collect(base.filter(
        status__in=['in_progress', 'ringing', 'queued'], ended_at__isnull=True,
        created_at__gte=now - timedelta(hours=4),
    ).values(*group).annotate(n=Count('id')), 'live')

    range_q = Q()
    if start_date:
        dt = parse_datetime(start_date + 'T00:00:00')
        if dt:
            range_q &= Q(created_at__gte=timezone.make_aware(dt) if timezone.is_naive(dt) else dt)
    else:
        range_q &= Q(created_at__gte=today_start)
    if end_date:
        dt = parse_datetime(end_date + 'T23:59:59')
        if dt:
            range_q &= Q(created_at__lte=timezone.make_aware(dt) if timezone.is_naive(dt) else dt)

    # CH-396: Daily / Hourly / Monthly / Global count the calls SENT to the
    # line - what its caps limit and what routing now checks (buyers/caps.py):
    # not a "No Destination" refusal stamped on it, not a hidden Revo retry.
    from buyers.caps import counted_q
    sent = base.filter(counted_q())
    for r in sent.filter(range_q).values(*group).annotate(
            n=Count('id'), rev=Coalesce(Sum('revenue'), Decimal('0.00'))):
        row = row_for(r)
        row['daily'] = r['n'] or 0
        row['revenue'] = r['rev'] or Decimal('0.00')

    collect(sent.filter(created_at__gte=hour_ago).values(*group).annotate(n=Count('id')), 'hourly')
    collect(sent.filter(created_at__gte=month_start).values(*group).annotate(n=Count('id')), 'monthly')
    collect(sent.values(*group).annotate(n=Count('id')), 'global')

    return out


def counts_for(counts, d):
    """One destination's counters from gather_destination_counts: the calls
    routed to its buyer on its number, plus any on its number with no buyer
    recorded (never routed to anyone, so they were always counted here)."""
    from decimal import Decimal

    own = counts.get((str(d.buyer_id) if d.buyer_id else None, d.tfn), {})
    loose = counts.get((None, d.tfn), {}) if d.buyer_id else {}
    if not loose:
        return own
    keys = ('live', 'daily', 'hourly', 'monthly', 'global')
    out = {k: (own.get(k, 0) or 0) + (loose.get(k, 0) or 0) for k in keys}
    out['revenue'] = (own.get('revenue') or Decimal('0.00')) + (loose.get('revenue') or Decimal('0.00'))
    return out


def _own_calls_q(d):
    """The calls that belong to destination `d`: its number, and its buyer
    (or no buyer recorded). Matches counts_for."""
    from django.db.models import Q

    q = Q(destination_number=d.tfn)
    if d.buyer_id:
        q &= Q(buyer_id=d.buyer_id) | Q(buyer_id__isnull=True)
    return q


def format_destination(d, start_date=None, end_date=None, tz_name=None, counts=None, switches=None):
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

    # CH-521: the id, not the object. Reading d.organization cost one query
    # per line - 189 of the 197 queries on a 189-line Destinations page, and the
    # same on the bot API's destination lists.
    org_id = d.organization_id
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
        live_q &= _own_calls_q(d)
    else:
        # Fallback if somehow there's no TFN (though unlikely for valid destinations)
        live_q &= Q(id__isnull=True) # returns empty

    if counts is not None:
        # Counts were gathered for the whole page in five grouped queries.
        # Done per row this function ran five counts each, so a page of 165
        # destinations cost 1,155 queries and 1.7 seconds.
        row = counts_for(counts, d)
        live_count = row.get('live', 0)
    else:
        live_count = CallLog.objects.filter(organization_id=org_id).filter(live_q).count()

    # 2. Which calls belong to this destination, with no date filter on it.
    # Kept separate from the date range below: the month and all-time counts
    # were built by adding a month filter on top of a query that already had
    # the day filter in it, so "calls this month" and "calls all time" could
    # never be larger than "calls today". Every cap reading in the interface
    # was really today's number under four different labels.
    from django.utils.dateparse import parse_datetime

    dest_q = Q(organization_id=org_id)
    if d.tfn:
        # Calls sent to the line (CH-396), as its caps count them.
        from buyers.caps import counted_q
        dest_q &= _own_calls_q(d) & counted_q()
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

    if counts is not None:
        row = counts_for(counts, d)
        daily_count = row.get('daily', 0)
        revenue_today = float(row.get('revenue', 0) or 0)
    else:
        today_stats = CallLog.objects.filter(dest_q & range_q).aggregate(
            total_calls=Count('id'),
            revenue=Coalesce(Sum('revenue'), Decimal('0.00'))
        )
        daily_count = today_stats['total_calls'] or 0
        revenue_today = float(today_stats['revenue'] or 0)

    # 4. Hourly, monthly and all-time, each measured from the destination alone.
    hour_ago = now - timedelta(hours=1)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if counts is not None:
        row = counts_for(counts, d)
        hourly_count = row.get('hourly', 0)
        monthly_count = row.get('monthly', 0)
        global_count = row.get('global', 0)
    else:
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
        # Who last switched this line on or off, and when (CH-200).
        'last_switched': _last_switched(d, switches),
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


def _last_switched(d, switches=None):
    """The latest on/off switch of this line, from the activity log. A page of
    lines passes `switches` read in one query; a single line reads its own."""
    try:
        if switches is None:
            from buyers.destination_switches import last_switches
            switches = last_switches([d.id])
        return switches.get(str(d.id))
    except Exception:
        # Showing who switched a line is never worth failing the page over.
        logger.exception('could not read the last switch of destination %s', d.id)
        return None


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
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'view')
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'view')
    from config.pagination import paginate_list
    from buyers.destination import Destination
    qs = Destination.objects.filter(organization=request.auth.organization).select_related('buyer')
    # Organization-wide was the whole filter, so a buyer login saw every
    # buyer's destinations with their calls and revenue on the dashboard card.
    # A buyer gets their own; a publisher has no destinations at all.
    qs = scope_queryset(request.auth, qs, buyer_field='buyer_id', publisher_field=None)
    if buyer_id:
        qs = qs.filter(buyer_id=buyer_id)
    if enabled is not None:
        qs = qs.filter(enabled=enabled)
        
    val_from = start_date or created_at__gte
    val_to = end_date or created_at__lte
    
    # Five grouped queries for the whole page instead of five per row: this
    # endpoint was 1,155 queries and 1.66 seconds on 165 destinations.
    rows = list(qs)
    counts = gather_destination_counts(
        request.auth.organization, [d.tfn for d in rows],
        start_date=val_from, end_date=val_to, tz_name=timezone,
    )
    from buyers.destination_switches import last_switches
    switches = last_switches([d.id for d in rows])
    data = [
        format_destination(d, val_from, val_to, tz_name=timezone, counts=counts, switches=switches)
        for d in rows
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/stats/", response={200: dict})
def get_destination_stats(request, timezone: Optional[str] = None):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'view')
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
    # Same slice as the list - the headline numbers must not say more than
    # the rows underneath them do.
    qs = scope_queryset(request.auth, qs, buyer_field='buyer_id', publisher_field=None)

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

    # Only what is on an enabled destination, matched on the number dialled
    # AND the buyer - the same way format_destination decides which calls
    # belong to a row (CH-392: one number can be live under several buyers).
    enabled_pairs = {
        (str(b) if b else None, t)
        for b, t in qs.filter(enabled=True).values_list('buyer_id', 'tfn')
    }
    enabled_tfns = {t for _, t in enabled_pairs}
    active_live = 0
    if enabled_tfns:
        for b, t in live_q.filter(destination_number__in=enabled_tfns).values_list(
                'buyer_id', 'destination_number'):
            if b is None or (str(b), t) in enabled_pairs:
                active_live += 1

    stats = qs.aggregate(
        # Capacity only counts where a call could actually land.
        total_cc=Sum('concurrency_cap', filter=Q(enabled=True)),
        active_tfns=Count('id', filter=Q(enabled=True)),
    )
    total_cc = stats['total_cc'] or 0
    vacant, open_lines, capped_lines = _unfilled_cc(
        qs.filter(enabled=True).select_related('buyer'), live_q)
    return 200, {
        'active_live': active_live,
        'total_live': total_live,
        'total_cc': total_cc,
        'active_tfns': stats['active_tfns'] or 0,
        # CH-626: free CC only where a call can still land (see _unfilled_cc).
        'vacant_cc': vacant,
        'vacant_lines': open_lines,
        'capped_lines': capped_lines,
    }


def _unfilled_cc(destinations, live_q):
    """CH-626 (Sir, 6 Nov): "Unfilled CC means free CC ... If someone has a
    10 cap with 3 CC and the 10 cap is over, the TFN is active but more calls
    cannot go due to the cap, so it should not be counted as unfilled.
    Unfilled means we can still fill them and we have cap for that
    destination."

    It was total CC of the active destinations minus the calls live on them,
    so a line whose cap was full still showed its CC as free. Now, line by
    line, a destination's free CC (its CC minus its own live calls) counts
    only when routing would still send it a call - the same checks, in the
    same order, as RoutingEngine._destination_refusal: its buyer is active,
    the buyer's own daily / monthly cap is not full, the line is not resting
    after missed calls (CH-514), and none of the line's Hourly / Daily /
    Monthly / Global caps is full. A line with no CC set adds nothing, as
    before. Returns (unfilled CC, lines it is on, lines with free CC that
    cannot take a call now)."""
    from collections import Counter

    from buyers.caps import buyer_cap_hit, destination_cap_hit
    from routing.engine import RoutingEngine

    live = Counter((str(b) if b else None, t)
                   for b, t in live_q.values_list('buyer_id', 'destination_number'))
    buyer_ok = {}
    vacant = open_lines = capped = 0
    for d in destinations:
        cc = d.concurrency_cap or 0
        if cc <= 0 or not d.tfn:
            continue
        # The line's own calls: its number and its buyer, or none recorded
        # (buyers.caps.own_calls_q).
        n = live[(str(d.buyer_id) if d.buyer_id else None, d.tfn)]
        if d.buyer_id:
            n += live[(None, d.tfn)]
        free = max(0, cc - n)
        if not free:
            continue
        buyer = d.buyer
        if buyer is not None:
            if buyer.id not in buyer_ok:
                ok = buyer.status == 'active'
                if ok:
                    try:
                        cap = buyer.cap
                    except Exception:  # noqa: BLE001 - no cap row: no buyer cap
                        cap = None
                    try:
                        ok = cap is None or buyer_cap_hit(buyer, cap) is None
                    except Exception:  # noqa: BLE001 - as routing: a failed count stops nothing
                        ok = True
                buyer_ok[buyer.id] = ok
            if not buyer_ok[buyer.id] or RoutingEngine.line_resting(buyer, d.tfn):
                capped += 1
                continue
        try:
            hit = destination_cap_hit(d)
        except Exception:  # noqa: BLE001
            hit = None
        if hit:
            capped += 1
            continue
        vacant += free
        open_lines += 1
    return vacant, open_lines, capped



def _enabled_clash(organization, tfn, enabled, buyer_id=None, exclude_id=None):
    """Is this buyer already live on this number?

    Returns the message to show, or None. The same TFN may be live under
    different buyers (CH-392, the campaign team, 29 Oct): every count matches
    a call's buyer as well as its number, so each buyer is credited with its
    own calls only. The same buyer twice on one number would count every call
    twice, so that is still refused.
    """
    from buyers.destination import Destination

    if not enabled or not tfn:
        return None

    qs = Destination.objects.filter(
        organization=organization, tfn=tfn, enabled=True, buyer_id=buyer_id,
    ).select_related('buyer')
    if exclude_id:
        qs = qs.exclude(id=exclude_id)

    other = qs.first()
    if other is None:
        return None

    held_by = other.buyer.name if other.buyer else 'another destination'
    return (
        f"{tfn} is already live on {held_by}. The same buyer cannot have one "
        f"number live twice - every call to it would be counted twice. Switch "
        f"that one off first, or use a different number."
    )


def _buyer_already_live(organization, buyer, enabled, exclude_id=None):
    """RETIRED - the boss's decision: a buyer can have multiple live TFNs.

    This guard existed because the hangup-side resolution took the buyer's
    single live destination with `.first()`, so a second enabled one silently
    never received a call. That resolution is now `pick_live_destination` in
    the engine - rotation across every live TFN, skipping ones at their
    concurrency cap - so the reason for the limit is gone and the limit went
    with it. Only `_enabled_clash` remains: one number still cannot be live
    for two buyers at once, or both would be credited with every call.
    """
    return None


@router.post("/", response={201: dict, 400: dict})
def create_destination(request, payload: DestinationSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'edit')
    from buyers.destination import Destination
    from buyers.models import Buyer
    if _is_buyer_login(request.auth):
        # CH-476: a buyer login adds lines to its own buyer only, with the
        # Create/Edit Destinations toggle (or Create); a cap or concurrency set
        # at once needs those toggles too.
        from accounts import partner_permissions as P
        from accounts.permissions import has
        from ninja.errors import HttpError
        payload.buyer_id = str(request.auth.buyer_id or '')
        if not (has(request.auth, Capability.CREATE) or P.partner_allows(request.auth, P.EDIT_DESTINATIONS)):
            raise HttpError(403, "Your access does not include: Create/Edit Destinations. "
                                 "Ask an admin of this workspace.")
        defaults = DestinationSchema(name='x')
        set_now = {f: getattr(payload, f) for f in ('concurrency_cap', 'hourly_cap', 'daily_cap',
                                                   'monthly_cap', 'global_cap')
                   if getattr(payload, f) != getattr(defaults, f)}
        if set_now:
            _buyer_may_change(request.auth, set_now)
    else:
        require(request.auth, Capability.CREATE)

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

    # The database refuses one buyer live twice on one number. Catching it here
    # turns an IntegrityError 500 into something the person can act on, and
    # names the buyer already holding it so they can go and look.
    clash = _enabled_clash(
        request.auth.organization, payload.get_tfn(), payload.enabled,
        buyer_id=buyer.id,
    )
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
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'view')
    from buyers.destination import Destination
    try:
        d = Destination.objects.get(id=destination_id, organization=request.auth.organization)
        if _is_buyer_login(request.auth) and not _own_line(request.auth, d):
            return 404, {"detail": "Destination not found"}     # CH-476: own lines only
        return 200, format_destination(d)
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}


def _is_buyer_login(user):
    return getattr(user, 'role', '') == 'buyer'


def _own_line(user, d):
    """CH-475: a buyer login only ever touches its own buyer's lines."""
    return bool(getattr(user, 'buyer_id', None)) and d.buyer_id == user.buyer_id


# CH-476 (Sir, 3 Nov): which toggle on the buyer's access page each field of
# a line needs, for the buyer's own login. Edit (the Manage Traffic toggle)
# still allows every field. Any field not listed needs Create/Edit Destinations.
def _field_permissions():
    from accounts import partner_permissions as P
    return {
        'enabled': P.PAUSE_DESTINATIONS,
        'tfn': P.EDIT_NUMBER, 'phone_number': P.EDIT_NUMBER, 'forward_type': P.EDIT_NUMBER,
        'concurrency_cap': P.SET_CONCURRENCY,
        'hourly_cap': P.SET_CAPS, 'daily_cap': P.SET_CAPS, 'monthly_cap': P.SET_CAPS, 'global_cap': P.SET_CAPS,
    }


def _buyer_may_change(user, fields):
    """For a buyer login: refuses (403, naming what is missing) unless its
    toggles cover every field it is changing. Its own buyer can never be
    swapped for another."""
    from accounts import partner_permissions as P
    from accounts.permissions import has
    from ninja.errors import HttpError
    if 'buyer_id' in fields and str(fields['buyer_id']) != str(getattr(user, 'buyer_id', '')):
        raise HttpError(403, "A buyer login cannot move a destination to another buyer.")
    if has(user, Capability.EDIT):
        return
    needed = {_field_permissions().get(f, P.EDIT_DESTINATIONS) for f in fields if f != 'buyer_id'}
    labels = {p['key']: p['label'] for p in P.PERMISSIONS}
    missing = sorted(labels[k] for k in needed if not P.partner_allows(user, k))
    if missing:
        raise HttpError(403, f"Your access does not include: {', '.join(missing)}. "
                             f"Ask an admin of this workspace.")


@router.patch("/{destination_id}/", response={200: dict, 400: dict, 404: dict})
def update_destination(request, destination_id: str, payload: DestinationUpdateSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'edit')
    fields = payload.dict(exclude_none=True)
    # CH-475/476 (Sir, 3 Nov): a buyer login changes its own lines as far as
    # the toggles on its access page allow; staff still need Edit.
    buyer_login = _is_buyer_login(request.auth)
    if buyer_login:
        _buyer_may_change(request.auth, fields)
    else:
        require(request.auth, Capability.EDIT)
    from buyers.destination import Destination
    from buyers.models import Buyer
    try:
        d = Destination.objects.get(id=destination_id, organization=request.auth.organization)
        if buyer_login and not _own_line(request.auth, d):
            return 404, {"detail": "Destination not found"}
        old_tfn = d.tfn

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
            request.auth.organization, d.tfn, d.enabled,
            buyer_id=d.buyer_id, exclude_id=d.id,
        )
        if clash:
            return 400, {"detail": clash}

        d.save()
        # Only when the line's NUMBER changed, and only the routes that pointed
        # at its old number (CH-201). This ran on every edit, a plain on/off
        # switch included, and rewrote the number of EVERY routed destination of
        # the buyer in EVERY campaign to this line's number - silently, with no
        # activity entry. With two lines or a mis-assigned buyer, switching one
        # line moved other campaigns' routing onto it (Sir, 22 Oct: "PDSO TFN
        # active in Crug", lines "switching by themselves").
        if old_tfn and d.tfn and d.tfn != old_tfn:
            try:
                from routing.models import RuleDestination
                moved = RuleDestination.objects.filter(
                    rule__organization=request.auth.organization, destination=old_tfn,
                )
                if d.buyer_id:
                    moved = moved.filter(buyer_id=d.buyer_id)
                n = moved.update(destination=d.tfn)
                logger.info('destination %s number %s -> %s: %s routes moved', d.id, old_tfn, d.tfn, n)
            except Exception:
                # The destination was saved but the routing rules still point at
                # the old number, so calls keep going to the previous one.
                logger.exception('failed to sync routing rules to new destination: dest=%s', d.id)
        return 200, format_destination(d)
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}


@router.delete("/{destination_id}/", response={200: dict, 404: dict})
def delete_destination(request, destination_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'delete')
    require(request.auth, Capability.DELETE)
    from buyers.destination import Destination
    try:
        d = Destination.objects.get(id=destination_id, organization=request.auth.organization)
        if _is_buyer_login(request.auth) and not _own_line(request.auth, d):
            return 404, {"detail": "Destination not found"}
        d.delete()
        return 200, {"detail": "Destination deleted"}
    except Destination.DoesNotExist:
        return 404, {"detail": "Destination not found"}


class BulkEnableSchema(Schema):
    ids: List[str]
    enabled: bool


@router.post("/bulk-enable", response={200: dict})
def bulk_enable(request, payload: BulkEnableSchema):
    """Play or Pause many TFNs in one request.

    The Destinations page selects a hundred and clicks Play; a hundred
    separate PATCHes meant a hundred round trips and a flood of half-failures
    nobody could read. One call, and the answer says per id what happened and
    why - the same number-clash rule a single PATCH applies, nothing softer.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'destinations', 'edit')
    # CH-475/476: play and pause is all this does: a buyer login needs the
    # Pause Destinations toggle (or Edit), on its own lines only.
    buyer_login = _is_buyer_login(request.auth)
    if buyer_login:
        _buyer_may_change(request.auth, {'enabled': payload.enabled})
    else:
        require(request.auth, Capability.EDIT)
    from buyers.destination import Destination

    updated, failed = [], []
    for raw_id in payload.ids[:500]:
        try:
            d = Destination.objects.select_related('buyer').get(
                id=raw_id, organization=request.auth.organization,
            )
        except (Destination.DoesNotExist, ValidationError, ValueError):
            failed.append({"id": raw_id, "reason": "not found"})
            continue
        if buyer_login and not _own_line(request.auth, d):
            failed.append({"id": raw_id, "reason": "not found"})
            continue

        if payload.enabled:
            clash = _enabled_clash(
                request.auth.organization, d.tfn, True,
                buyer_id=d.buyer_id, exclude_id=d.id,
            )
            if clash:
                failed.append({"id": raw_id, "tfn": d.tfn, "reason": clash})
                continue

        if d.enabled != payload.enabled:
            d.enabled = payload.enabled
            d.save(update_fields=['enabled'])
        updated.append(raw_id)

    return 200, {
        "enabled": payload.enabled,
        "updated": updated,
        "failed": failed,
        "updated_count": len(updated),
        "failed_count": len(failed),
    }
