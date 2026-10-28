"""Owner > Carrier Costs (CH-154).

What each carrier charges US, set against what our numbers actually did, so
the owner can check a carrier's invoice for anything extra. Owner only, like
every endpoint on owner_router. Numbers a workspace deleted stay here (status
"released") with the date they were deleted, but vanish from the workspace's
own Tracking Numbers list.

Month bill = number fees (every number live at any point in the month) +
the carrier's flat portal fee + connected minutes x per-minute cost. Each
call's seconds are rounded the way the carrier bills them (CH-254: exact
seconds, or up to 6 / 30 / 60 seconds). An estimate to compare with the
invoice, not the invoice itself.

CH-254: the month is the Eastern calendar month (as the routing cost and the
reports count days), and the page total is the whole bill: every TFN carrier
plus routing on to buyers (XOLO termination minutes and its monthly fee).
"""
import calendar
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import List, Optional

from django.db.models import Count, ExpressionWrapper, F, IntegerField, Q, Sum
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
EASTERN = ZoneInfo('America/New_York')


def _billed(increment: int, connected=CONNECTED):
    """Connected seconds with each call rounded up to `increment` seconds."""
    if increment <= 1:
        return Coalesce(Sum('duration', filter=connected), 0)
    rounded = ExpressionWrapper(
        (F('duration') + (increment - 1)) / increment * increment, output_field=IntegerField())
    return Coalesce(Sum(rounded, filter=connected), 0)


def _month_share(first: date, last: date) -> Decimal:
    """How much of a month's fixed fees falls in the days first..last (CH-334).

    A fee of $350 a month is $350 for the whole month, $350 x 1/31 for one
    day of October, $350 x 7/31 for a week of it. Days are counted per
    calendar month, so a whole month is exactly 1."""
    counts = {}
    d = first
    while d <= last:
        counts[(d.year, d.month)] = counts.get((d.year, d.month), 0) + 1
        d += timedelta(days=1)
    return sum((Decimal(n) / Decimal(calendar.monthrange(y, m)[1]) for (y, m), n in counts.items()),
               Decimal('0'))


def _last10(number: str) -> str:
    return ''.join(c for c in (number or '') if c.isdigit())[-10:]


def _month_bounds(month: Optional[str]):
    """(start, end, 'YYYY-MM') for the month asked for, default this month.

    Eastern calendar month (CH-254). It was UTC, so October began at 8 PM
    Eastern on 30 Sep, and disagreed with the routing cost beside it."""
    now = timezone.now().astimezone(EASTERN)
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
    start = datetime(y, m, 1, tzinfo=EASTERN)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = datetime(ny, nm, 1, tzinfo=EASTERN)
    return start, end, f'{y:04d}-{m:02d}'


def _money(d) -> str:
    # Half a cent rounds up, as money is rounded ($0.025 -> $0.03).
    return str(Decimal(d or 0).quantize(CENT, rounding=ROUND_HALF_UP))


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
    billing_increment: int
    numbers: List[dict]
    totals: dict


class CarrierCostsSchema(Schema):
    month: str
    carriers: List[CarrierRowSchema]
    grand_total: str
    unassigned: dict
    routing: dict
    summary: dict
    warnings: List[dict]
    profit: dict


class CarrierCostsUpdateSchema(Schema):
    number_monthly_cost: Optional[str] = None
    portal_monthly_fee: Optional[str] = None
    per_minute_cost: Optional[str] = None
    billing_increment: Optional[int] = None


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
    bill = _bill(start, end)
    grand = bill.pop('grand')
    return 200, {'month': label, **bill, 'profit': _company_profit(start, end, grand)}


def _bill(start, end, share: Decimal = Decimal('1')) -> dict:
    """The carriers' bill for start..end. `share` is the part of the monthly
    fees (TFN fees, monthly charges, the routing fee) that falls in it: 1 for
    a month, less for a day or a week (CH-334)."""
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
    increments = Carrier.BILLING_INCREMENTS
    rows = (
        CallLog.objects
        .filter(organization_id__in=org_ids, created_at__gte=start, created_at__lt=end)
        .values('organization_id', 'called_number')
        .annotate(
            calls=Count('id'),
            connected=Count('id', filter=CONNECTED),
            tcl=Coalesce(Sum('duration', filter=CONNECTED), 0),
            **{f'billed_{i}': _billed(i) for i in increments if i > 1},
        )
    )
    blank = {'calls': 0, 'connected': 0, 'tcl': 0, **{f'billed_{i}': 0 for i in increments if i > 1}}
    for r in rows:
        s = stats.setdefault((r['organization_id'], _last10(r['called_number'])), dict(blank))
        for k in s:
            s[k] += r[k]

    by_carrier = {}
    unassigned = []
    for n in numbers:
        (by_carrier.setdefault(n.carrier_id, []) if n.carrier_id else unassigned).append(n)

    def number_row(n, carrier):
        s = stats.get((n.organization_id, _last10(n.number)), blank)
        increment = carrier.billing_increment if carrier else 1
        billed = s['tcl'] if increment <= 1 else s.get(f'billed_{increment}', s['tcl'])
        minutes = Decimal(billed) / Decimal(60)
        # The price the number was added at (CH-189); the carrier's current
        # price only for a number that has none of its own.
        if n.tfn_price is not None:
            fee = n.tfn_price
        else:
            fee = carrier.number_monthly_cost if carrier else Decimal('0')
        fee = fee * share
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
            # The seconds the carrier bills: each call rounded to its increment.
            'billed_seconds': billed,
            'aht_seconds': round(s['tcl'] / s['connected']) if s['connected'] else 0,
            'number_fee': _money(fee),
            'minute_cost': _money(minute_cost),
            'total': _money(fee + minute_cost),
        }

    def totals(rows_, carrier):
        calls = sum(r['calls'] for r in rows_)
        connected = sum(r['connected'] for r in rows_)
        tcl = sum(r['tcl_seconds'] for r in rows_)
        billed = sum(r['billed_seconds'] for r in rows_)
        fees = sum((Decimal(r['number_fee']) for r in rows_), Decimal('0'))
        mins = sum((Decimal(r['minute_cost']) for r in rows_), Decimal('0'))
        portal = (carrier.portal_monthly_fee if carrier else Decimal('0')) * share
        return {
            'numbers': len(rows_),
            'live': sum(1 for r in rows_ if not r['deleted']),
            'deleted': sum(1 for r in rows_ if r['deleted']),
            'calls': calls,
            'connected': connected,
            'tcl_seconds': tcl,
            'billed_seconds': billed,
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
            'billing_increment': c.billing_increment,
            'numbers': rows_,
            'totals': t,
        })

    un_rows = [number_row(n, None) for n in unassigned]
    un_totals = totals(un_rows, None)

    # Routing on to buyers (XOLO termination) for the same month (CH-254). It
    # had its own card and was left out of the total.
    rate = _routing_rate()
    r = _routing_window(start, end, rate.billing_increment)
    routing_minutes = Decimal(r['billed']) * Decimal(rate.per_minute_cost or 0) / Decimal(60)
    routing_fee = Decimal(rate.monthly_fee or 0) * share
    routing = {
        **_rate_out(rate),
        'forwarded': r['forwarded'],
        'connected': r['connected'],
        'seconds': r['seconds'],
        'billed_seconds': r['billed'],
        'minutes': str((Decimal(r['billed']) / Decimal(60)).quantize(CENT)),
        'minute_cost': _money(routing_minutes),
        'fee': _money(routing_fee),
        'total': _money(routing_minutes + routing_fee),
    }
    grand += routing_minutes + routing_fee

    def add(key):
        return _money(sum((Decimal(c['totals'][key]) for c in out), Decimal('0')))

    summary = {
        'number_fees': add('number_fees'),
        'portal_fees': add('portal_fee'),
        'minute_charges': add('minute_charges'),
        'carriers_total': add('monthly_bill'),
        'routing_minutes': routing['minute_cost'],
        'routing_monthly_fee': routing['fee'],
        'routing_total': routing['total'],
        'grand_total': _money(grand),
    }

    # What would make the bill read low: a carrier with talk time and no
    # minute rate, or TFNs and no TFN price; numbers with calls and no carrier.
    warnings = []
    for c in out:
        t = c['totals']
        if t['tcl_seconds'] > 0 and Decimal(c['per_minute_cost']) == 0:
            warnings.append({'kind': 'no_minute_rate', 'carrier': c['name'], 'code': c['code'],
                             'calls': t['calls']})
        if t['numbers'] > 0 and Decimal(t['number_fees']) == 0:
            warnings.append({'kind': 'no_number_fee', 'carrier': c['name'], 'code': c['code'],
                             'numbers': t['numbers']})
    if un_totals['numbers']:
        warnings.append({'kind': 'no_carrier', 'numbers': un_totals['numbers'], 'calls': un_totals['calls']})

    return {
        'carriers': out,
        'grand_total': _money(grand),
        'unassigned': {'numbers': un_rows, 'totals': un_totals},
        'routing': routing,
        'summary': summary,
        'warnings': warnings,
        'grand': grand,
    }


# Charges taken from customers, by the description each kind is written with.
INCOME_LINES = (
    ('calls', 'Call charge'),            # minutes, with recording and VoIP Shield
    ('rejected', 'Rejected call fee'),   # with VoIP Shield when it checked the caller
    ('voip', 'VoIP Shield fee'),         # a missed or refused call the Shield checked
    ('portal', 'Portal access fee'),
    ('tfn', 'Tracking number '),         # a number bought in the portal
)


def _company_profit(start, end, carrier_costs: Decimal) -> dict:
    """Company profit before tax for the month (CH-257, Sir 24 Oct): what
    customers were charged - minutes, recording, VoIP Shield, rejected calls,
    TFN and portal fees - minus what the carriers charge us (this page's
    total). Customers are the user workspaces; our own portal's charges to
    itself are our own money and are left out. Refunds to customers come off."""
    from billing.models import Transaction
    from accounts.models import Organization

    ours = [o.id for o in Organization.objects.filter(users__is_superuser=True, users__is_active=True).distinct()]
    base = Transaction.objects.filter(
        created_at__gte=start, created_at__lt=end,
        status=Transaction.Status.COMPLETED,
    ).exclude(organization_id__in=ours)
    charges = base.filter(transaction_type=Transaction.Type.CHARGE)

    lines, taken = {}, Q()
    for key, prefix in INCOME_LINES:
        q = Q(description__startswith=prefix)
        lines[key] = charges.filter(q).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
        taken |= q
    lines['other'] = charges.exclude(taken).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
    refunds = base.filter(transaction_type=Transaction.Type.REFUND).aggregate(
        t=Coalesce(Sum('amount'), Decimal('0')))['t']

    # Profit from the rounded figures shown, so the three always add up.
    income = Decimal(_money(sum(lines.values(), Decimal('0')) - refunds))
    carrier_costs = Decimal(_money(carrier_costs))
    customers = charges.values('organization_id').distinct().count()
    return {
        **{k: _money(v) for k, v in lines.items()},
        'refunds': _money(refunds),
        'income': _money(income),
        'customers': customers,
        'carrier_costs': _money(carrier_costs),
        'profit': _money(income - carrier_costs),
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
    if data.billing_increment is not None:
        if data.billing_increment not in Carrier.BILLING_INCREMENTS:
            raise HttpError(400, "billing_increment must be 1, 6, 30 or 60 seconds.")
        c.billing_increment = data.billing_increment
        fields.append('billing_increment')
    if fields:
        c.save(update_fields=fields + ['updated_at'])
    return 200, {
        'id': str(c.id),
        'number_monthly_cost': _money(c.number_monthly_cost),
        'portal_monthly_fee': _money(c.portal_monthly_fee),
        'per_minute_cost': str(c.per_minute_cost),
        'billing_increment': c.billing_increment,
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

FORWARDED_CONNECTED = Q(status='completed')
# One row per caller call, as every report counts them (CH-215): the earlier
# tries of a call the sending platform retried are hidden there, so they are
# not counted as calls here either. Their talk seconds, if any, are still
# billed by the carrier and still counted.
SHOWN = Q(retry_hidden=False)
MAX_DAYS = 93


class RoutingRateUpdateSchema(Schema):
    name: Optional[str] = None
    per_minute_cost: Optional[str] = None
    monthly_fee: Optional[str] = None
    billing_increment: Optional[int] = None


def _routing_rate() -> RoutingRate:
    rate, _ = RoutingRate.objects.get_or_create(key='termination')
    return rate


def _rate_out(rate: RoutingRate) -> dict:
    return {
        'name': rate.name,
        'per_minute_cost': str(rate.per_minute_cost),
        'monthly_fee': _money(rate.monthly_fee),
        'billing_increment': rate.billing_increment,
    }


def _forwarded(lo, hi):
    """Calls forwarded to a buyer between lo and hi."""
    return CallLog.objects.filter(created_at__gte=lo, created_at__lt=hi).exclude(destination_number='')


def _routing_counts(increment: int) -> dict:
    return {
        'forwarded': Count('id', filter=SHOWN),
        'connected': Count('id', filter=FORWARDED_CONNECTED & SHOWN),
        'seconds': Coalesce(Sum('duration', filter=FORWARDED_CONNECTED), 0),
        'billed': _billed(increment, FORWARDED_CONNECTED),
    }


def _routing_window(lo, hi, increment: int = 1) -> dict:
    """Calls forwarded to a buyer between lo and hi, the connected seconds and
    the seconds the carrier bills (each call rounded to `increment`)."""
    return _forwarded(lo, hi).aggregate(**_routing_counts(increment))


def _day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise HttpError(400, f"{field} must look like 2026-10-20.")


def _days(start, end, default: date):
    """(first, last, lo, hi): the Eastern days start..end inclusive."""
    first = _day(start, 'start') if start else default
    last = _day(end, 'end') if end else first
    if last < first:
        raise HttpError(400, "end must not be before start.")
    if (last - first).days >= MAX_DAYS:
        raise HttpError(400, f"Pick at most {MAX_DAYS} days.")
    lo = datetime.combine(first, time.min, tzinfo=EASTERN)
    hi = datetime.combine(last + timedelta(days=1), time.min, tzinfo=EASTERN)
    return first, last, lo, hi


def _minutes(seconds) -> str:
    return str((Decimal(seconds) / Decimal(60)).quantize(CENT))


@owner_router.get("/routing-cost", response={200: dict})
def routing_cost(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    """Routing cost per Eastern day, `start` to `end` inclusive (YYYY-MM-DD).
    Without dates: yesterday."""
    require_owner(request)
    today = timezone.now().astimezone(EASTERN).date()
    first, last, lo, hi = _days(start, end, today - timedelta(days=1))
    rate = _routing_rate()
    per_second = rate.per_minute_cost / Decimal(60)

    grouped = (
        _forwarded(lo, hi)
        .annotate(day=TruncDate('created_at', tzinfo=EASTERN))
        .values('day')
        .annotate(**_routing_counts(rate.billing_increment))
    )
    by_day = {r['day']: r for r in grouped}

    def figures(r):
        cost = Decimal(r['billed']) * per_second
        return {
            'forwarded': r['forwarded'],
            'connected': r['connected'],
            'seconds': r['seconds'],
            'billed_seconds': r['billed'],
            # Average talk time of a connected call, and what one costs: so a
            # low total can be read at a glance (Sir, 28 Oct: "2770 calls just
            # $93 cost?" - 23,386 minutes x $0.004).
            'aht_seconds': round(r['seconds'] / r['connected']) if r['connected'] else 0,
            'minutes': _minutes(r['billed']),
            'cost': _money(cost),
            'cost_exact': str(cost.quantize(Decimal('0.0001'))),
            'cost_per_call': str((cost / r['connected']).quantize(Decimal('0.0001'))) if r['connected'] else '0.0000',
        }

    days = []
    tot = {'forwarded': 0, 'connected': 0, 'seconds': 0, 'billed': 0}
    blank = dict(tot)
    d = first
    while d <= last:
        r = by_day.get(d, blank)
        days.append({'date': d.isoformat(), **figures(r)})
        for k in tot:
            tot[k] += r[k]
        d += timedelta(days=1)

    return 200, {
        **_rate_out(rate),
        'start': first.isoformat(),
        'end': last.isoformat(),
        'days': days,
        'totals': figures(tot),
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
    if data.billing_increment is not None:
        if data.billing_increment not in Carrier.BILLING_INCREMENTS:
            raise HttpError(400, "billing_increment must be 1, 6, 30 or 60 seconds.")
        rate.billing_increment = data.billing_increment
    rate.save()
    return 200, _rate_out(rate)


# --------------------------------------------------------------------------
# Company profit for any days (CH-334). Sir, 28 Oct, on the month's profit
# card: "make same in today's sheet too" - Today, Yesterday, This week, This
# month or any dates, like the routing cost beside it. What customers were
# charged in those days, minus the carriers' bill for them: minutes as they
# happened, monthly fees by the day (a $350 month is $11.29 on a day of
# October).
# --------------------------------------------------------------------------

@owner_router.get("/company-profit", response={200: dict})
def company_profit(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    """Company profit before tax, `start` to `end` inclusive (Eastern days).
    Without dates: today."""
    require_owner(request)
    today = timezone.now().astimezone(EASTERN).date()
    first, last, lo, hi = _days(start, end, today)
    share = _month_share(first, last)
    bill = _bill(lo, hi, share)
    return 200, {
        'start': first.isoformat(),
        'end': last.isoformat(),
        'fee_share': str(share.quantize(Decimal('0.0001'))),
        'summary': bill['summary'],
        'profit': _company_profit(lo, hi, bill['grand']),
    }


# --------------------------------------------------------------------------
# The day's expenses, line by line (CH-336). Sir, 28 Oct, on the company
# profit card: "for daily create like this - TFN cost, Xolo HR min, Freedom
# HR min and their cost, then expense of that day". Same days as the profit
# card (Today, Yesterday, This week, This month or any dates) and the same
# bill (_bill): the profit's "Carrier costs" is this card's total.
# --------------------------------------------------------------------------

def _min(seconds) -> str:
    return str((Decimal(seconds or 0) / Decimal(60)).quantize(CENT))


@owner_router.get("/expenses", response={200: dict})
def expenses(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    """What our carriers charge us for `start`..`end` (Eastern days, default
    today), one line per cost: TFN fees, each TFN carrier's minutes, monthly
    charges, routing minutes and the routing fee. Monthly fees by the day."""
    require_owner(request)
    today = timezone.now().astimezone(EASTERN).date()
    first, last, lo, hi = _days(start, end, today)
    share = _month_share(first, last)
    bill = _bill(lo, hi, share)
    summary, routing = bill['summary'], bill['routing']

    carriers = [{
        'name': c['name'],
        'code': c['code'],
        'calls': c['totals']['calls'],
        'connected': c['totals']['connected'],
        'minutes': _min(c['totals']['billed_seconds']),
        'per_minute_cost': c['per_minute_cost'],
        'billing_increment': c['billing_increment'],
        'cost': c['totals']['minute_charges'],
        'numbers': c['totals']['numbers'],
        'tfn_cost': c['totals']['number_fees'],
        'monthly_charge': c['totals']['portal_fee'],
    } for c in bill['carriers']]
    un = bill['unassigned']['totals']

    return 200, {
        'start': first.isoformat(),
        'end': last.isoformat(),
        'fee_share': str(share.quantize(Decimal('0.0001'))),
        'days': (last - first).days + 1,
        'tfn': {
            'numbers': sum(c['numbers'] for c in carriers),
            'cost': summary['number_fees'],
        },
        'carriers': carriers,
        'monthly_charges': summary['portal_fees'],
        'routing': {
            'name': routing['name'],
            'connected': routing['connected'],
            'minutes': routing['minutes'],
            'per_minute_cost': routing['per_minute_cost'],
            'billing_increment': routing['billing_increment'],
            'cost': routing['minute_cost'],
            'fee': routing['fee'],
        },
        # TFNs with calls but no carrier: their minutes have no rate, so they
        # are not in the total - shown so a low total can be explained.
        'no_carrier': {'numbers': un['numbers'], 'calls': un['calls'], 'minutes': _min(un['tcl_seconds'])},
        'total': summary['grand_total'],
    }
