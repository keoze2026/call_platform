"""Owner > Carrier Costs (CH-154).

What each carrier charges US, set against what our numbers actually did, so
the owner can check a carrier's invoice for anything extra. Owner only, like
every endpoint on owner_router. Numbers a workspace deleted stay here (status
"released") with the date they were deleted, but vanish from the workspace's
own Tracking Numbers list.

Month bill = number fees (every number live at any point in the month) +
the carrier's flat portal fee + connected minutes x per-minute cost. Minutes
are exact seconds / 60, so it is an estimate to compare with the invoice, not
the invoice itself.
"""
import calendar
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpRequest
from django.utils import timezone
from ninja import Schema
from ninja.errors import HttpError

from accounts.owner_api import owner_router, require_owner
from phone_numbers.models import Carrier, PhoneNumber, RoutingRate
from routing.models import CallLog

CONNECTED = Q(status__in=['completed', 'in_progress'])
CENT = Decimal('0.01')


def _last10(number: str) -> str:
    return ''.join(c for c in (number or '') if c.isdigit())[-10:]


def _month_bounds(month: Optional[str]):
    """(start, end, 'YYYY-MM') for the month asked for, default this month."""
    now = timezone.now()
    if month:
        try:
            y, m = month.split('-')
            y, m = int(y), int(m)
            if not 1 <= m <= 12 or y < 2000:
                raise ValueError
        except ValueError:
            raise HttpError(400, "month must look like 2026-10.")
    else:
        y, m = now.year, now.month
    start = datetime(y, m, 1, tzinfo=dt_timezone.utc)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = datetime(ny, nm, 1, tzinfo=dt_timezone.utc)
    return start, end, f'{y:04d}-{m:02d}'


def _money(d: Decimal) -> str:
    return str(d.quantize(CENT))


class CarrierRowSchema(Schema):
    id: str
    organization_id: str
    organization_name: str
    name: str
    code: str
    risk: str
    is_active: bool
    number_monthly_cost: str
    portal_monthly_fee: str
    per_minute_cost: str
    numbers: List[dict]
    totals: dict


class CarrierCostsSchema(Schema):
    month: str
    carriers: List[CarrierRowSchema]
    grand_total: str
    unassigned: dict


class CarrierCostsUpdateSchema(Schema):
    number_monthly_cost: Optional[str] = None
    portal_monthly_fee: Optional[str] = None
    per_minute_cost: Optional[str] = None


class AssignCarrierSchema(Schema):
    carrier_id: str
    numbers: List[str]


def _decimal(value: str, field: str, places: str) -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise HttpError(400, f"{field} must be a number.")
    if not d.is_finite() or d < 0 or d > Decimal('100000'):
        raise HttpError(400, f"{field} must be between 0 and 100000.")
    return d.quantize(Decimal(places))


@owner_router.get("/carrier-costs", response={200: CarrierCostsSchema})
def carrier_costs(request: HttpRequest, month: Optional[str] = None):
    require_owner(request)
    start, end, label = _month_bounds(month)

    carriers = list(Carrier.objects.select_related('organization').order_by('name', 'code'))
    numbers = list(
        PhoneNumber.objects
        .filter(created_at__lt=end)
        .filter(Q(released_at__isnull=True) | Q(released_at__gte=start))
        .select_related('campaign')
        .order_by('number')
    )

    # One grouped query for the whole month, then matched by last ten digits
    # (dialled numbers arrive without the +).
    org_ids = {n.organization_id for n in numbers}
    stats = {}
    rows = (
        CallLog.objects
        .filter(organization_id__in=org_ids, created_at__gte=start, created_at__lt=end)
        .values('organization_id', 'called_number')
        .annotate(
            calls=Count('id'),
            connected=Count('id', filter=CONNECTED),
            tcl=Coalesce(Sum('duration', filter=CONNECTED), 0),
        )
    )
    for r in rows:
        s = stats.setdefault((r['organization_id'], _last10(r['called_number'])),
                             {'calls': 0, 'connected': 0, 'tcl': 0})
        s['calls'] += r['calls']
        s['connected'] += r['connected']
        s['tcl'] += r['tcl']

    by_carrier = {}
    unassigned = []
    for n in numbers:
        (by_carrier.setdefault(n.carrier_id, []) if n.carrier_id else unassigned).append(n)

    def number_row(n, carrier):
        s = stats.get((n.organization_id, _last10(n.number)), {'calls': 0, 'connected': 0, 'tcl': 0})
        minutes = Decimal(s['tcl']) / Decimal(60)
        # The price the number was added at (CH-189); the carrier's current
        # price only for a number that has none of its own.
        if n.tfn_price is not None:
            fee = n.tfn_price
        else:
            fee = carrier.number_monthly_cost if carrier else Decimal('0')
        minute_cost = (minutes * carrier.per_minute_cost) if carrier else Decimal('0')
        return {
            'id': str(n.id),
            'number': n.number,
            'deleted': n.status == PhoneNumber.Status.RELEASED,
            'added_at': n.created_at.isoformat(),
            'deleted_at': n.released_at.isoformat() if n.released_at else None,
            'campaign': n.campaign.name if n.campaign_id else '',
            'calls': s['calls'],
            'connected': s['connected'],
            'tcl_seconds': s['tcl'],
            'aht_seconds': round(s['tcl'] / s['connected']) if s['connected'] else 0,
            'number_fee': _money(fee),
            'minute_cost': _money(minute_cost),
            'total': _money(fee + minute_cost),
        }

    def totals(rows_, carrier):
        calls = sum(r['calls'] for r in rows_)
        connected = sum(r['connected'] for r in rows_)
        tcl = sum(r['tcl_seconds'] for r in rows_)
        fees = sum((Decimal(r['number_fee']) for r in rows_), Decimal('0'))
        mins = sum((Decimal(r['minute_cost']) for r in rows_), Decimal('0'))
        portal = carrier.portal_monthly_fee if carrier else Decimal('0')
        return {
            'numbers': len(rows_),
            'live': sum(1 for r in rows_ if not r['deleted']),
            'deleted': sum(1 for r in rows_ if r['deleted']),
            'calls': calls,
            'connected': connected,
            'tcl_seconds': tcl,
            'aht_seconds': round(tcl / connected) if connected else 0,
            'number_fees': _money(fees),
            'portal_fee': _money(portal),
            'minute_charges': _money(mins),
            'monthly_bill': _money(fees + portal + mins),
        }

    out = []
    grand = Decimal('0')
    for c in carriers:
        rows_ = [number_row(n, c) for n in by_carrier.get(c.id, [])]
        t = totals(rows_, c)
        # A carrier with no numbers this month and no portal fee has nothing to check.
        if not rows_ and not c.portal_monthly_fee:
            continue
        grand += Decimal(t['monthly_bill'])
        out.append({
            'id': str(c.id),
            'organization_id': str(c.organization_id),
            'organization_name': c.organization.name,
            'name': c.name,
            'code': c.code,
            'risk': c.risk,
            'is_active': c.is_active,
            'number_monthly_cost': _money(c.number_monthly_cost),
            'portal_monthly_fee': _money(c.portal_monthly_fee),
            'per_minute_cost': str(c.per_minute_cost),
            'numbers': rows_,
            'totals': t,
        })

    un_rows = [number_row(n, None) for n in unassigned]
    return 200, {
        'month': label,
        'carriers': out,
        'grand_total': _money(grand),
        'unassigned': {'numbers': un_rows, 'totals': totals(un_rows, None)},
    }


@owner_router.patch("/carriers/{carrier_id}/costs", response={200: dict})
def update_carrier_costs(request: HttpRequest, carrier_id: str, data: CarrierCostsUpdateSchema):
    require_owner(request)
    try:
        c = Carrier.objects.get(id=carrier_id)
    except (Carrier.DoesNotExist, ValueError):
        raise HttpError(404, "Carrier not found.")
    fields = []
    if data.number_monthly_cost is not None:
        c.number_monthly_cost = _decimal(data.number_monthly_cost, 'number_monthly_cost', '0.01')
        fields.append('number_monthly_cost')
    if data.portal_monthly_fee is not None:
        c.portal_monthly_fee = _decimal(data.portal_monthly_fee, 'portal_monthly_fee', '0.01')
        fields.append('portal_monthly_fee')
    if data.per_minute_cost is not None:
        c.per_minute_cost = _decimal(data.per_minute_cost, 'per_minute_cost', '0.0001')
        fields.append('per_minute_cost')
    if fields:
        c.save(update_fields=fields + ['updated_at'])
    return 200, {
        'id': str(c.id),
        'number_monthly_cost': _money(c.number_monthly_cost),
        'portal_monthly_fee': _money(c.portal_monthly_fee),
        'per_minute_cost': str(c.per_minute_cost),
    }


@owner_router.post("/carrier-costs/assign", response={200: dict})
def assign_carrier(request: HttpRequest, data: AssignCarrierSchema):
    """Tag numbers with the carrier that carries them (same workspace only)."""
    require_owner(request)
    try:
        carrier = Carrier.objects.get(id=data.carrier_id)
    except (Carrier.DoesNotExist, ValueError):
        raise HttpError(404, "Carrier not found.")
    ids = [i for i in data.numbers if i][:500]
    updated = PhoneNumber.objects.filter(
        id__in=ids, organization_id=carrier.organization_id,
    ).update(
        carrier=carrier,
        # A number moved onto a carrier takes that carrier's TFN price (CH-189).
        tfn_price=carrier.number_monthly_cost or None,
    )
    return 200, {'updated': updated, 'skipped': len(ids) - updated}


# --------------------------------------------------------------------------
# Routing cost (CH-186): what the termination carrier charges to route our
# calls on to buyers. Sir, 21 Oct: "set routing cost so we can see how much
# cost from yesterday". Counted from our own call records, per Eastern day:
# every call we forwarded to a buyer, and the connected seconds of the ones
# that connected (the outbound leg is what the carrier bills). Exact seconds
# x the per-minute rate - an estimate to check the carrier's invoice against.
# --------------------------------------------------------------------------

EASTERN = ZoneInfo('America/New_York')
FORWARDED_CONNECTED = Q(status='completed')
MAX_DAYS = 93


class RoutingRateUpdateSchema(Schema):
    name: Optional[str] = None
    per_minute_cost: Optional[str] = None
    monthly_fee: Optional[str] = None


def _routing_rate() -> RoutingRate:
    rate, _ = RoutingRate.objects.get_or_create(key='termination')
    return rate


def _rate_out(rate: RoutingRate) -> dict:
    return {
        'name': rate.name,
        'per_minute_cost': str(rate.per_minute_cost),
        'monthly_fee': _money(rate.monthly_fee),
    }


def _day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise HttpError(400, f"{field} must look like 2026-10-20.")


@owner_router.get("/routing-cost", response={200: dict})
def routing_cost(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    """Routing cost per Eastern day, `start` to `end` inclusive (YYYY-MM-DD).
    Without dates: yesterday."""
    require_owner(request)
    today = timezone.now().astimezone(EASTERN).date()
    first = _day(start, 'start') if start else today - timedelta(days=1)
    last = _day(end, 'end') if end else first
    if last < first:
        raise HttpError(400, "end must not be before start.")
    if (last - first).days >= MAX_DAYS:
        raise HttpError(400, f"Pick at most {MAX_DAYS} days.")

    lo = datetime.combine(first, time.min, tzinfo=EASTERN)
    hi = datetime.combine(last + timedelta(days=1), time.min, tzinfo=EASTERN)
    rate = _routing_rate()
    per_second = rate.per_minute_cost / Decimal(60)

    grouped = (
        CallLog.objects
        .filter(created_at__gte=lo, created_at__lt=hi)
        .exclude(destination_number='')
        .annotate(day=TruncDate('created_at', tzinfo=EASTERN))
        .values('day')
        .annotate(
            forwarded=Count('id'),
            connected=Count('id', filter=FORWARDED_CONNECTED),
            seconds=Coalesce(Sum('duration', filter=FORWARDED_CONNECTED), 0),
        )
    )
    by_day = {r['day']: r for r in grouped}

    days = []
    tot = {'forwarded': 0, 'connected': 0, 'seconds': 0}
    d = first
    while d <= last:
        r = by_day.get(d, {'forwarded': 0, 'connected': 0, 'seconds': 0})
        cost = Decimal(r['seconds']) * per_second
        days.append({
            'date': d.isoformat(),
            'forwarded': r['forwarded'],
            'connected': r['connected'],
            'seconds': r['seconds'],
            'minutes': str((Decimal(r['seconds']) / Decimal(60)).quantize(CENT)),
            'cost': _money(cost),
            'cost_exact': str(cost.quantize(Decimal('0.0001'))),
        })
        for k in tot:
            tot[k] += r[k]
        d += timedelta(days=1)

    total_cost = Decimal(tot['seconds']) * per_second
    return 200, {
        **_rate_out(rate),
        'start': first.isoformat(),
        'end': last.isoformat(),
        'days': days,
        'totals': {
            **tot,
            'minutes': str((Decimal(tot['seconds']) / Decimal(60)).quantize(CENT)),
            'cost': _money(total_cost),
            'cost_exact': str(total_cost.quantize(Decimal('0.0001'))),
        },
    }


@owner_router.patch("/routing-cost/rate", response={200: dict})
def update_routing_rate(request: HttpRequest, data: RoutingRateUpdateSchema):
    require_owner(request)
    rate = _routing_rate()
    if data.name is not None and data.name.strip():
        rate.name = data.name.strip()[:100]
    if data.per_minute_cost is not None:
        rate.per_minute_cost = _decimal(data.per_minute_cost, 'per_minute_cost', '0.0001')
    if data.monthly_fee is not None:
        rate.monthly_fee = _decimal(data.monthly_fee, 'monthly_fee', '0.01')
    rate.save()
    return 200, _rate_out(rate)
