"""Owner > Carrier Costs as statements (CH-433, Sir 30 Oct).

Sir's design: each carrier as a statement you can send - its TFN codes with
their TFNs, TFN fees, calls, TCL, minutes, ACL and minute cost, the monthly
charges, and the total - then the carriers merged. Today, Yesterday, This
week, This month or any dates (Eastern days).

How each figure is counted (Sir: "make sure all metrics are 100% correct"):

* A carrier is the company; its TFN codes are the Carrier rows that share the
  first word of their name (XOLO: XLR, HYU, XHR; Freedom: KMQ). The routing
  rate (termination) belongs to the carrier its name starts with.
* TFN fees: a carrier bills a TFN for the whole month it was live in, even if
  it was deleted on the 14th. So a TFN counts in a month when it was live at
  any moment from the 1st up to the end of the period, at its own price (the
  carrier's price when it has none), and a period gets its share of the month:
  1 day of October is 1/31, the whole of October is 31/31.
* Minutes: the connected seconds of the calls to the code's TFNs, each call
  rounded the way the carrier bills (exact seconds, or up to 6 / 30 / 60),
  divided by 60. Minute cost = minutes x the per-minute rate.
* Calls: one per caller call, as the reports count them (hidden retries of the
  sending platform are not counted again; their seconds are still billed).
  ACL = TCL / connected calls.
* Termination: the calls forwarded to a buyer and the connected seconds of the
  outbound leg, at the routing rate.
* Monthly charges (a carrier's portal fee, the termination fee): the period's
  share of the month, as the TFN fees.
* Every money line is rounded to the cent and the totals add up those lines,
  so the statement adds up on paper.
"""
import calendar
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest
from django.utils import timezone
from ninja.errors import HttpError

from accounts.owner_api import owner_router, require_owner
from phone_numbers.models import Carrier, PhoneNumber
from phone_numbers.owner_costs import (
    CENT, CONNECTED, EASTERN, FORWARDED_CONNECTED, MAX_DAYS, SHOWN,
    _billed, _day, _last10, _routing_rate, _routing_window,
)
from routing.models import CallLog

ZERO = Decimal('0')


def _cents(d) -> Decimal:
    return Decimal(d or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _tenth_cent(d) -> str:
    return str(Decimal(d or 0).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))


def _s(d) -> str:
    return str(_cents(d))


def _months(first: date, last: date):
    """Each calendar month the days first..last touch: (month start, month end
    (exclusive), days of the period in it, days in the month)."""
    out = []
    d = date(first.year, first.month, 1)
    while d <= last:
        n = calendar.monthrange(d.year, d.month)[1]
        nxt = d + timedelta(days=n)
        a, b = max(first, d), min(last, nxt - timedelta(days=1))
        out.append((d, nxt, (b - a).days + 1, n))
        d = nxt
    return out


def _at(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=EASTERN)


def _word(name: str) -> str:
    """The first word of a carrier's name: the company ("Xolo High Risk")."""
    for part in (name or '').replace('-', ' ').replace('_', ' ').split():
        word = ''.join(c for c in part if c.isalnum())
        if word:
            return word
    return name or '?'


def _company(name: str) -> str:
    word = _word(name)
    return word.upper() if len(word) <= 4 else word[:1].upper() + word[1:]


def _period(start: Optional[str], end: Optional[str]):
    today = timezone.now().astimezone(EASTERN).date()
    first = _day(start, 'start') if start else today
    last = _day(end, 'end') if end else first
    if last < first:
        raise HttpError(400, "end must not be before start.")
    if (last - first).days >= MAX_DAYS:
        raise HttpError(400, f"Pick at most {MAX_DAYS} days.")
    return first, last, _at(first), _at(last + timedelta(days=1))


def _fee_share(n: PhoneNumber, months, hi: datetime) -> Decimal:
    """The part of this TFN's monthly price the period carries (0 when it was
    not live in the period's months)."""
    share = ZERO
    for m_start, m_end, days, in_month in months:
        if n.created_at >= min(hi, _at(m_end)):
            continue
        if n.released_at is not None and n.released_at < _at(m_start):
            continue
        share += Decimal(days) / Decimal(in_month)
    return share


@owner_router.get("/carrier-statement", response={200: dict})
def carrier_statement(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    """The carriers' statements for `start`..`end` (Eastern days, default today)."""
    require_owner(request)
    first, last, lo, hi = _period(start, end)
    months = _months(first, last)
    month_share = sum((Decimal(days) / Decimal(n) for _, _, days, n in months), ZERO)
    month_lo = _at(months[0][0])

    carriers = list(Carrier.objects.order_by('name', 'code'))
    numbers = list(
        PhoneNumber.objects
        .filter(created_at__lt=hi)
        .filter(Q(released_at__isnull=True) | Q(released_at__gte=month_lo))
        .order_by('number')
    )

    # Calls to the TFNs in the period, matched by last ten digits.
    increments = Carrier.BILLING_INCREMENTS
    stats = {}
    blank = {'calls': 0, 'connected': 0, 'tcl': 0, **{f'b{i}': 0 for i in increments if i > 1}}
    org_ids = {n.organization_id for n in numbers}
    for r in (
        CallLog.objects
        .filter(organization_id__in=org_ids, created_at__gte=lo, created_at__lt=hi)
        .values('organization_id', 'called_number')
        .annotate(
            calls=Count('id', filter=SHOWN),
            connected=Count('id', filter=CONNECTED & SHOWN),
            tcl=Coalesce(Sum('duration', filter=CONNECTED), 0),
            **{f'b{i}': _billed(i) for i in increments if i > 1},
        )
    ):
        s = stats.setdefault((r['organization_id'], _last10(r['called_number'])), dict(blank))
        for k in s:
            s[k] += r[k]

    # Two records for the same ten digits (written "+1..." and "1...") would
    # both match the calls: they are counted once, on the newest record.
    calls_on = {}
    for n in sorted(numbers, key=lambda x: x.created_at):
        calls_on[(n.organization_id, _last10(n.number))] = n.id

    def stats_of(n):
        key = (n.organization_id, _last10(n.number))
        return stats.get(key, blank) if calls_on.get(key) == n.id else blank

    by_carrier = {}
    unassigned = []
    for n in numbers:
        share = _fee_share(n, months, hi)
        if not share:
            continue
        (by_carrier.setdefault(n.carrier_id, []) if n.carrier_id else unassigned).append((n, share))

    companies = {}

    def company(name):
        key = _word(name).lower()
        if key not in companies:
            companies[key] = {'name': _company(name), 'codes': [], 'monthly': [], 'termination': None, 'numbers': []}
        return companies[key]

    for c in carriers:
        rows = by_carrier.get(c.id, [])
        if not rows and not c.portal_monthly_fee:
            continue
        inc = c.billing_increment or 1
        fees, rates = ZERO, set()
        calls = connected = tcl = billed = 0
        live = deleted = 0
        listed = []
        for n, share in rows:
            price = n.tfn_price if n.tfn_price is not None else c.number_monthly_cost
            rates.add(_s(price))
            fee = price * share
            # Summed unrounded, so "10 x $5.00 / 31" reads $1.61, not 10 x $0.16.
            fees += fee
            gone = n.status == PhoneNumber.Status.RELEASED
            deleted += gone
            live += not gone
            s = stats_of(n)
            calls += s['calls']
            connected += s['connected']
            tcl += s['tcl']
            billed += s['tcl'] if inc <= 1 else s.get(f'b{inc}', s['tcl'])
            listed.append({
                'id': str(n.id),
                'number': n.number,
                'code': c.code,
                'deleted': gone,
                'added_at': n.created_at.isoformat(),
                'deleted_at': n.released_at.isoformat() if n.released_at else None,
                'rate': _s(price),
                'billed': _s(fee),
            })
        minutes = (Decimal(billed) / Decimal(60)).quantize(CENT)
        fees = _cents(fees)
        minute_cost = _cents(minutes * c.per_minute_cost)
        g = company(c.name)
        if rows:
            g['codes'].append({
                'kind': 'tfn',
                'carrier_id': str(c.id),
                'code': c.code,
                'name': c.name,
                'risk': c.risk,
                'tfns': len(rows),
                'live': live,
                'deleted': deleted,
                # One price for all its TFNs, or empty when they differ.
                'tfn_rate': rates.pop() if len(rates) == 1 else '',
                'number_monthly_cost': _s(c.number_monthly_cost),
                'portal_monthly_fee': _s(c.portal_monthly_fee),
                'tfn_fees': _s(fees),
                'calls': calls,
                'connected': connected,
                'tcl_seconds': tcl,
                'billed_seconds': billed,
                'minutes': str(minutes),
                'acl_seconds': round(tcl / connected) if connected else 0,
                'per_minute_cost': str(c.per_minute_cost),
                'billing_increment': inc,
                'minute_cost': _s(minute_cost),
                'total': _s(fees + minute_cost),
            })
            g['numbers'].extend(listed)
        if c.portal_monthly_fee:
            g['monthly'].append({
                'kind': 'monthly',
                'carrier_id': str(c.id),
                'code': c.code,
                'risk': c.risk,
                'fee_month': _s(c.portal_monthly_fee),
                'amount': _s(c.portal_monthly_fee * month_share),
            })

    # Routing on to buyers: the termination rate, with the carrier its name starts with.
    rate = _routing_rate()
    r = _routing_window(lo, hi, rate.billing_increment)
    r_minutes = (Decimal(r['billed']) / Decimal(60)).quantize(CENT)
    g = company(rate.name or 'Termination')
    g['termination'] = {
        'kind': 'termination',
        'name': rate.name,
        'calls': r['forwarded'],
        'connected': r['connected'],
        'tcl_seconds': r['seconds'],
        'billed_seconds': r['billed'],
        'minutes': str(r_minutes),
        'acl_seconds': round(r['seconds'] / r['connected']) if r['connected'] else 0,
        'per_minute_cost': str(rate.per_minute_cost),
        'billing_increment': rate.billing_increment,
        'minute_cost': _s(r_minutes * rate.per_minute_cost),
    }
    g['termination']['total'] = g['termination']['minute_cost']
    if rate.monthly_fee:
        g['monthly'].append({
            'kind': 'monthly',
            'code': 'termination',
            'risk': '',
            'fee_month': _s(rate.monthly_fee),
            'amount': _s(rate.monthly_fee * month_share),
        })

    out = []
    for g in companies.values():
        rows = g['codes'] + ([g['termination']] if g['termination'] else [])
        if not rows and not g['monthly']:
            continue
        tfn_fees = sum((Decimal(x.get('tfn_fees', '0')) for x in rows), ZERO)
        minutes = sum((Decimal(x['minutes']) for x in rows), ZERO)
        minute_cost = sum((Decimal(x['minute_cost']) for x in rows), ZERO)
        monthly = sum((Decimal(x['amount']) for x in g['monthly']), ZERO)
        # Low risk first, then by code (Sir's statement: XLR, HYU, XHR).
        g['codes'].sort(key=lambda x: (x['risk'] != 'low', x['code']))
        order = {x['code']: i for i, x in enumerate(g['codes'])}
        g['numbers'].sort(key=lambda x: (order.get(x['code'], 99), x['number']))
        out.append({
            'name': g['name'],
            'codes': g['codes'],
            'termination': g['termination'],
            'monthly': g['monthly'],
            'numbers': g['numbers'],
            'totals': {
                'tfns': sum(x['tfns'] for x in g['codes']),
                'live': sum(x['live'] for x in g['codes']),
                'deleted': sum(x['deleted'] for x in g['codes']),
                'tfn_fees': _s(tfn_fees),
                'minutes': str(minutes),
                'minute_cost': _s(minute_cost),
                'monthly': _s(monthly),
                'total': _s(tfn_fees + minute_cost + monthly),
            },
        })
    # The largest bill first.
    out.sort(key=lambda x: -Decimal(x['totals']['total']))
    grand = sum((Decimal(x['totals']['total']) for x in out), ZERO)

    # What would make the statement read low.
    warnings = []
    for x in out:
        for c in x['codes']:
            if c['tcl_seconds'] and Decimal(c['per_minute_cost']) == 0:
                warnings.append({'kind': 'no_minute_rate', 'code': c['code'], 'calls': c['calls']})
            if c['tfns'] and Decimal(c['tfn_fees']) == 0:
                warnings.append({'kind': 'no_number_fee', 'code': c['code'], 'numbers': c['tfns']})
    un_calls = sum(stats_of(n)['calls'] for n, _ in unassigned)
    if unassigned:
        warnings.append({'kind': 'no_carrier', 'numbers': len(unassigned), 'calls': un_calls})

    return 200, {
        'start': first.isoformat(),
        'end': last.isoformat(),
        'days': (last - first).days + 1,
        # The period's share of a month, and the days of that month when the
        # period sits in one month (the "÷ 31" on the statement).
        'month_share': str(month_share.quantize(Decimal('0.0001'))),
        'month_days': months[0][3] if len(months) == 1 else None,
        'whole_month': len(months) == 1 and months[0][2] == months[0][3],
        'carriers': out,
        'total': _s(grand),
        'tfns': {
            'total': sum(x['totals']['tfns'] for x in out),
            'live': sum(x['totals']['live'] for x in out),
            'deleted': sum(x['totals']['deleted'] for x in out),
        },
        'warnings': warnings,
    }


@owner_router.get("/carrier-statement/cdrs", response={200: dict})
def carrier_statement_cdrs(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    """Every call of the period, as the carriers bill it: the inbound leg on our
    TFN (its carrier code, billed seconds and cost) and the leg forwarded to the
    buyer (termination billed seconds and cost). For checking an invoice line
    by line."""
    require_owner(request)
    first, last, lo, hi = _period(start, end)
    limit = 100000

    # Which carrier carried each TFN when the call came in.
    carriers = {c.id: c for c in Carrier.objects.all()}
    owners = {}
    for n in PhoneNumber.objects.filter(created_at__lt=hi, carrier__isnull=False).order_by('created_at'):
        owners.setdefault((n.organization_id, _last10(n.number)), []).append(n)

    def carrier_at(org_id, number, when):
        found = None
        for n in owners.get((org_id, _last10(number)), []):
            if n.created_at <= when and (n.released_at is None or n.released_at >= when):
                found = n
        return carriers.get(found.carrier_id) if found else None

    rate = _routing_rate()
    r_inc = rate.billing_increment or 1

    def rounded(seconds, inc):
        return seconds if inc <= 1 else -(-seconds // inc) * inc

    rows = []
    qs = (
        CallLog.objects.filter(created_at__gte=lo, created_at__lt=hi)
        .order_by('created_at')
        .values_list('id', 'created_at', 'organization_id', 'caller_number', 'called_number',
                     'destination_number', 'status', 'duration', 'retry_hidden')
    )
    for cid, at, org_id, caller, called, dest, status, duration, hidden in qs.iterator():
        c = carrier_at(org_id, called, at)
        if c is None and not dest:
            continue
        connected = status in ('completed', 'in_progress')
        in_billed = rounded(duration, c.billing_increment or 1) if (c and connected) else 0
        out_billed = rounded(duration, r_inc) if (dest and status == 'completed') else 0
        rows.append({
            'call_id': str(cid),
            'time': at.astimezone(EASTERN).strftime('%Y-%m-%d %H:%M:%S'),
            'caller': caller,
            'tfn': called,
            'carrier': _company(c.name) if c else '',
            'code': c.code if c else '',
            'status': status,
            'retry': hidden,
            'duration': duration,
            'billed_seconds': in_billed,
            'cost': _tenth_cent(Decimal(in_billed) / Decimal(60) * c.per_minute_cost) if c else '0.0000',
            'destination': dest,
            'termination_seconds': out_billed,
            'termination_cost': _tenth_cent(Decimal(out_billed) / Decimal(60) * rate.per_minute_cost),
        })
        if len(rows) >= limit:
            break

    return 200, {
        'start': first.isoformat(),
        'end': last.isoformat(),
        'rows': rows,
        'truncated': len(rows) >= limit,
    }
