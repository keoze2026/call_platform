"""Owner > Company Profit page (CH-434, Sir's design, 30 Oct).

What customers paid us, minus what carriers charge us, before tax - for
Today, Yesterday, This week, This month or any dates (Eastern days).

* Charged to customers: the money actually taken from customer workspaces
  (completed charge transactions, refunds off), as the existing profit card
  counts it (_company_profit). Our own workspace is not a customer.
  A call's charge is one transaction (minutes + recording + VoIP Shield); it
  is split into those lines from the call itself: VoIP Shield when the caller
  was checked, recording when the call has a recording, and the rest is
  minutes - so the lines always add up to what was taken.
* Carrier costs: the Carrier Costs statements (carrier_statement.build_statement),
  so both pages always show the same figures.
* "If this traffic were billed": every answered forwarded call of the period
  at the price list, each call rounded up to the whole minute as customers
  are billed, against the period's carrier minutes and fixed fees.
* By customer: each workspace's charges, and what its own traffic cost us
  (minutes on its TFNs, its TFN fees, the termination of its calls). The
  carriers' monthly charges are not split between workspaces.
* Price vs cost per minute: each TFN code's rate plus termination against the
  customer price list.
"""
import math
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from django.http import HttpRequest

from accounts.owner_api import owner_router, require_owner
from phone_numbers.carrier_statement import ZERO, _cents, _period, _s, build_statement
from phone_numbers.owner_costs import _routing_rate
from routing.models import CallLog


def _price_list():
    """The prices a new customer workspace starts on (BillingAccount defaults)."""
    from billing.models import BillingAccount
    f = {x.name: x for x in BillingAccount._meta.fields}
    return {k: Decimal(f[k].default) for k in (
        'per_minute_rate', 'markup_percent', 'recording_fee_per_minute', 'voip_shield_fee_per_call',
        'rejected_call_fee', 'tfn_purchase_fee', 'monthly_portal_fee')}


def _one(values, fallback):
    """The single value every account shares, or None when they differ."""
    vals = {Decimal(v) for v in values}
    if not vals:
        return fallback
    return vals.pop() if len(vals) == 1 else None


@owner_router.get("/company-profit-statement", response={200: dict})
def company_profit_statement(request: HttpRequest, start: Optional[str] = None, end: Optional[str] = None):
    require_owner(request)
    from accounts.models import Organization
    from billing.models import BillingAccount, Transaction

    first, last, lo, hi = _period(start, end)
    stmt = build_statement(first, last, lo, hi)
    per_org_cost = stmt.pop('per_org')

    ours = set(Organization.objects.filter(users__is_superuser=True, users__is_active=True)
               .values_list('id', flat=True).distinct())
    tx = (Transaction.objects
          .filter(created_at__gte=lo, created_at__lt=hi, status=Transaction.Status.COMPLETED)
          .exclude(organization_id__in=ours))
    charges = list(tx.filter(transaction_type=Transaction.Type.CHARGE)
                   .values('organization_id', 'amount', 'description', 'call_sid'))
    refunds = list(tx.filter(transaction_type=Transaction.Type.REFUND).values('organization_id', 'amount'))

    accounts = {a.organization_id: a for a in BillingAccount.objects.filter(
        organization_id__in={c['organization_id'] for c in charges} | {r['organization_id'] for r in refunds})}
    sids = [c['call_sid'] for c in charges if c['call_sid']]
    calls = {(c['organization_id'], c['twilio_call_sid']): c for c in CallLog.objects.filter(
        twilio_call_sid__in=sids).values('organization_id', 'twilio_call_sid', 'duration', 'status',
                                         'recording_url', 'ipqs_checked')}

    keys = ('minutes', 'recording', 'voip', 'rejected', 'tfn', 'portal', 'other')
    amount = {k: ZERO for k in keys}
    qty = {k: 0 for k in keys}
    orgs = defaultdict(lambda: {'amount': {k: ZERO for k in keys}, 'qty': {k: 0 for k in keys}, 'refunds': ZERO})

    def add(org, key, money, count=0):
        amount[key] += money
        qty[key] += count
        orgs[org]['amount'][key] += money
        orgs[org]['qty'][key] += count

    for c in charges:
        org, total, d = c['organization_id'], Decimal(c['amount']), c['description'] or ''
        acc = accounts.get(org)
        voip_fee = Decimal(acc.voip_shield_fee_per_call) if acc else ZERO
        if d.startswith('Call charge'):
            call = calls.get((org, c['call_sid'])) or {}
            answered = call.get('status') in ('completed', 'in_progress')
            mins = math.ceil((call.get('duration') or 0) / 60) if answered else 0
            voip = min(voip_fee, total) if call.get('ipqs_checked') else ZERO
            rec = ZERO
            if call.get('recording_url') and acc and mins:
                rec = min(Decimal(mins) * Decimal(acc.recording_fee_per_minute), total - voip)
            if voip:
                add(org, 'voip', voip, 1)
            if rec:
                add(org, 'recording', rec, mins)
            add(org, 'minutes', total - voip - rec, mins)
        elif d.startswith('Rejected call fee'):
            voip = min(voip_fee, total) if 'VoIP Shield' in d else ZERO
            if voip:
                add(org, 'voip', voip, 1)
            add(org, 'rejected', total - voip, 1)
        elif d.startswith('VoIP Shield fee'):
            add(org, 'voip', total, 1)
        elif d.startswith('Tracking number '):
            add(org, 'tfn', total, 1)
        elif d.startswith('Portal access fee'):
            add(org, 'portal', total, 1)
        else:
            add(org, 'other', total, 1)
    refund_total = ZERO
    for r in refunds:
        refund_total += Decimal(r['amount'])
        orgs[r['organization_id']]['refunds'] += Decimal(r['amount'])
    qty['portal'] = len({c['organization_id'] for c in charges if (c['description'] or '').startswith('Portal access fee')})

    # Prices: what the customer accounts charge (the price list when none
    # or when they differ, flagged).
    price_list = _price_list()
    cust_accounts = list(BillingAccount.objects.exclude(organization_id__in=ours))

    def price(field):
        v = _one([getattr(a, field) for a in cust_accounts], price_list[field])
        return None if v is None else str(v)

    prices = {
        'minutes': price('per_minute_rate'),
        'markup': price('markup_percent'),
        'recording': price('recording_fee_per_minute'),
        'voip': price('voip_shield_fee_per_call'),
        'rejected': price('rejected_call_fee'),
        'tfn': price('tfn_purchase_fee'),
        'portal': price('monthly_portal_fee'),
    }

    # What was taken, to the cent; the lines rounded so they add up to it
    # exactly (the rounding difference sits on the minutes line).
    # Largest remainder: each line rounded down to the cent, then the cents
    # left over go to the lines that lost the most.
    from decimal import ROUND_FLOOR
    gross = _cents(sum(amount.values(), ZERO))
    shown = {k: amount[k].quantize(Decimal('0.01'), rounding=ROUND_FLOOR) for k in keys}
    left = int((gross - sum(shown.values(), ZERO)) / Decimal('0.01'))
    for k in sorted(keys, key=lambda k: -(amount[k] - shown[k]))[:max(0, left)]:
        shown[k] += Decimal('0.01')
    lines = [{'key': k, 'quantity': qty[k], 'amount': _s(shown[k]), 'price': prices.get(k)} for k in keys]
    charged = gross - _cents(refund_total)
    carrier = Decimal(stmt['total'])
    profit = charged - carrier

    # If the period's traffic were billed at the price list.
    rate = _routing_rate()
    answered = CallLog.objects.filter(created_at__gte=lo, created_at__lt=hi, status='completed').exclude(destination_number='')
    exact_seconds = 0
    billed_minutes = 0
    for d in answered.values_list('duration', flat=True).iterator():
        d = d or 0
        exact_seconds += d
        billed_minutes += math.ceil(d / 60)
    per_min = price_list['per_minute_rate'] * (1 + price_list['markup_percent'] / 100)
    with_rec = per_min + price_list['recording_fee_per_minute']
    carrier_minutes = sum((Decimal(c['totals']['minute_cost']) for c in stmt['carriers']), ZERO)
    tfn_fees = sum((Decimal(c['totals']['tfn_fees']) for c in stmt['carriers']), ZERO)
    monthly = sum((Decimal(c['totals']['monthly']) for c in stmt['carriers']), ZERO)
    fixed = tfn_fees + monthly
    minute_parts = []
    for c in stmt['carriers']:
        for x in c['codes']:
            minute_parts.append({'label': x['code'], 'amount': x['minute_cost']})
        if c['termination']:
            minute_parts.append({'label': 'termination', 'amount': c['termination']['minute_cost']})
    what_if = {
        'connected_minutes': str((Decimal(exact_seconds) / Decimal(60)).quantize(Decimal('0.01'))),
        'billed_minutes': billed_minutes,
        'per_minute': str(per_min.quantize(Decimal('0.0001'))),
        'per_minute_recording': str(with_rec.quantize(Decimal('0.0001'))),
        'customer_minutes': _s(billed_minutes * per_min),
        'customer_minutes_recording': _s(billed_minutes * with_rec),
        'carrier_minutes': _s(carrier_minutes),
        'carrier_minute_parts': minute_parts,
        'fixed': _s(fixed),
        'tfn_fees': _s(tfn_fees),
        'monthly': _s(monthly),
        'profit': _s(_cents(billed_minutes * per_min) - _cents(carrier_minutes) - _cents(fixed)),
        'profit_recording': _s(_cents(billed_minutes * with_rec) - _cents(carrier_minutes) - _cents(fixed)),
        'portal_fee': _s(price_list['monthly_portal_fee']),
        'tfn_fee': _s(price_list['tfn_purchase_fee']),
        'voip_fee': str(price_list['voip_shield_fee_per_call'].quantize(Decimal('0.0001'))),
    }

    # By customer: every customer workspace with charges or traffic.
    names = dict(Organization.objects.filter(
        id__in=set(orgs) | {o for o in per_org_cost if o not in ours}).values_list('id', 'name'))
    by_customer = []
    for org_id, name in names.items():
        if org_id in ours:
            continue
        o = orgs.get(org_id) or {'amount': {k: ZERO for k in keys}, 'qty': {k: 0 for k in keys}, 'refunds': ZERO}
        cost = per_org_cost.get(org_id, {'minutes_cost': ZERO, 'tfn_fees': ZERO, 'termination': ZERO})
        got = _cents(sum(o['amount'].values(), ZERO) - o['refunds'])
        spent = _cents(cost['minutes_cost'] + cost['tfn_fees'] + cost['termination'])
        by_customer.append({
            'id': str(org_id),
            'name': name,
            'minutes': o['qty']['minutes'],
            'voip': o['qty']['voip'],
            'rejected': o['qty']['rejected'],
            'tfns': o['qty']['tfn'],
            'portal_fee': _s(o['amount']['portal']),
            'charged': _s(got),
            'our_cost': _s(spent),
            'profit': _s(got - spent),
        })
    by_customer.sort(key=lambda x: -Decimal(x['charged']))

    # Price vs cost per minute, codes with the same rate together.
    from phone_numbers.carrier_statement import _company
    from phone_numbers.models import Carrier
    groups = {}
    for car in Carrier.objects.filter(is_active=True).order_by('code'):
        k = (_company(car.name), car.risk, str(car.per_minute_cost))
        g = groups.setdefault(k, {'carrier': k[0], 'risk': car.risk, 'codes': [], 'carrier_rate': str(car.per_minute_cost)})
        if car.code not in g['codes']:
            g['codes'].append(car.code)
    term = Decimal(rate.per_minute_cost)
    price_rows = []
    for g in groups.values():
        cost = Decimal(g['carrier_rate']) + term
        margin = with_rec - cost
        price_rows.append({
            **g,
            'termination_rate': str(term),
            'our_cost': str(cost.quantize(Decimal('0.0001'))),
            'customer_price': str(with_rec.quantize(Decimal('0.0001'))),
            'margin': str(margin.quantize(Decimal('0.0001'))),
            'margin_pct': str((margin / with_rec * 100).quantize(Decimal('0.1'))) if with_rec else '0.0',
            'margin_no_recording': str((per_min - cost).quantize(Decimal('0.0001'))),
            'margin_no_recording_pct': str(((per_min - cost) / per_min * 100).quantize(Decimal('0.1'))) if per_min else '0.0',
        })
    price_rows.sort(key=lambda x: (x['carrier'] != (stmt['carriers'][0]['name'] if stmt['carriers'] else ''), x['risk'] != 'low', x['codes']))

    return 200, {
        'start': stmt['start'],
        'end': stmt['end'],
        'days': stmt['days'],
        'charged': _s(charged),
        'refunds': _s(refund_total),
        'customers': len({c['organization_id'] for c in charges}),
        'lines': lines,
        'carriers': [{'name': c['name'], 'total': c['totals']['total'],
                      'codes': [x['code'] for x in c['codes']] + (['termination'] if c['termination'] else [])}
                     for c in stmt['carriers']],
        'carrier_costs': stmt['total'],
        'profit': _s(profit),
        'margin_pct': str((profit / charged * 100).quantize(Decimal('0.1'))) if charged > 0 else None,
        'what_if': what_if,
        'by_customer': by_customer,
        'price_vs_cost': price_rows,
        'price_list': {k: str(v) for k, v in price_list.items()},
    }
