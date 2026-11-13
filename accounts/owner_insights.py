"""CH-675: the Organizations page with more details (Sir, 13 Nov: "Org page
front end - change it to the updated version with more details").

Everything the new Organizations list and account page read, for the owner
only (accounts/owner_api.py gates every route with require_owner):

- health / runway of each account (list and account page);
- the account's day: live now, calls in from publishers, routed to buyers,
  connected, revenue (Overview), and what needs attention (Health checks);
- buying and selling by campaign (Traffic);
- its toll-free numbers and where each came from (Numbers);
- any number on the platform: TFN or caller ID (Caller & TFN lookup);
- its money in and out (Billing).

Reads only. "Today" is the server day (UTC), the same as the list's Calls
today and Revenue today before this change and the header's counters.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

OK, SUSPENDED, BILLING_NOT_SET, NO_BALANCE, LOW_BALANCE = (
    'ok', 'suspended', 'billing_not_set', 'no_balance', 'low_balance')
# A retired TFN is still shown as needing attention for this long.
RECENT_RETIRED_DAYS = 7


def day_start(now=None):
    now = now or timezone.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def runway_minutes(billing):
    """How many minutes the balance pays for at the account's rate, or None."""
    if billing is None:
        return None
    rate = billing.per_minute_rate or Decimal('0')
    if rate <= 0:
        return None
    return max(0, int((billing.balance or Decimal('0')) / rate))


def health(org, billing, is_main=False) -> str:
    """One word for the list's Health column - the first thing that needs
    attention, or ok. Our own account is not charged per minute, so only
    suspended or a balance at or under zero shows for it."""
    if not org.is_active:
        return SUSPENDED
    if billing is None:
        return OK if is_main else BILLING_NOT_SET
    balance = billing.balance or Decimal('0')
    if balance <= 0 and (billing.credit_limit or Decimal('0')) <= 0:
        return NO_BALANCE
    if is_main:
        return OK
    minutes = runway_minutes(billing)
    if balance < (billing.low_balance_threshold or Decimal('0')) or (minutes is not None and minutes < 60):
        return LOW_BALANCE
    return OK


def live_by_org(org_ids) -> dict:
    """{organization id: calls in progress now}, one query."""
    from routing.live import live
    from routing.models import CallLog
    return {r['organization']: r['n'] for r in
            live(CallLog.objects.filter(organization__in=org_ids, retry_hidden=False))
            .values('organization').annotate(n=Count('id'))}


def _calls_today(org, start):
    from analytics.models import CallRecord
    return CallRecord.objects.filter(organization=org, created_at__gte=start)


def overview(org, billing) -> dict:
    """The account page's Overview: its day so far and what needs attention."""
    from accounts.models import User
    from billing.models import Transaction
    from phone_numbers.models import PhoneNumber
    from accounts.main_account import is_main_org
    from routing.models import CallLog
    start = day_start()
    is_main = is_main_org(org.id)
    calls = _calls_today(org, start)
    agg = calls.aggregate(
        calls_in=Count('id'),
        routed=Count('id', filter=Q(buyer_id__isnull=False)),
        connected=Count('id', filter=Q(status='completed')),
        tfns=Count('called_number', distinct=True))
    revenue = (Transaction.objects.filter(organization=org, created_at__gte=start,
                                          transaction_type='charge', status='completed')
               .aggregate(s=Sum('amount'))['s'] or Decimal('0'))
    live_now = live_by_org([org.id]).get(org.id, 0)
    users = User.objects.filter(organization=org)
    return {
        'balance': str(billing.balance) if billing else None,
        'per_minute_rate': str(billing.per_minute_rate) if billing else None,
        'markup_percent': str(billing.markup_percent) if billing else None,
        'runway_minutes': runway_minutes(billing),
        'live_now': live_now,
        'calls_today': agg['calls_in'],
        'tfns_with_calls_today': agg['tfns'],
        'revenue_today': str(revenue),
        'users': users.count(),
        'flow': {'from_publishers': agg['calls_in'], 'routed_to_buyers': agg['routed'],
                 'connected': agg['connected']},
        'last_call_at': (CallLog.objects.filter(organization=org, retry_hidden=False)
                         .order_by('-created_at').values_list('created_at', flat=True).first()),
        'health': health(org, billing, is_main),
        'checks': checks(org, billing, users, PhoneNumber, is_main),
    }


def checks(org, billing, users, PhoneNumber, is_main=False) -> list:
    """Health checks: [{level: action|info, code, title, detail}] - only what
    needs attention."""
    out = []
    state = health(org, billing, is_main)
    if state == SUSPENDED:
        out.append({'level': 'action', 'code': 'suspended', 'title': 'Account suspended',
                    'detail': 'Its logins are refused until it is re-activated.'})
    elif state == BILLING_NOT_SET:
        out.append({'level': 'action', 'code': 'billing_not_set', 'title': 'Billing not set',
                    'detail': 'No rates or balance yet.'})
    elif state == NO_BALANCE:
        out.append({'level': 'action', 'code': 'no_balance', 'title': 'No balance',
                    'detail': 'Its balance is $0 or less.'})
    elif state == LOW_BALANCE:
        mins = runway_minutes(billing)
        out.append({'level': 'action', 'code': 'low_balance', 'title': 'Low balance',
                    'detail': (f'About {mins} min left at its rate.' if mins is not None
                               else 'Below its low-balance level.')})
    retired_qs = (PhoneNumber.objects.filter(
        organization=org, status=PhoneNumber.Status.RELEASED,
        released_at__gte=timezone.now() - timedelta(days=RECENT_RETIRED_DAYS))
        .exclude(retired_reason=''))
    n_retired = retired_qs.count()
    if n_retired:
        shown = list(retired_qs.order_by('-released_at').values_list('number', flat=True)[:3])
        more = f' +{n_retired - 3} more' if n_retired > 3 else ''
        out.append({'level': 'action', 'code': 'tfn_expired',
                    'title': f'{n_retired} TFN{"s" if n_retired != 1 else ""} expired (last {RECENT_RETIRED_DAYS} days)',
                    'detail': f'{", ".join(shown)}{more} no longer receive{"s" if n_retired == 1 else ""} calls'})
    if not PhoneNumber.objects.filter(organization=org, status=PhoneNumber.Status.ACTIVE).exists():
        out.append({'level': 'info', 'code': 'no_tfns', 'title': 'No live TFNs',
                    'detail': 'No toll-free number is taking calls on this account.'})
    no_2fa = users.filter(is_active=True, mfa_enabled=False).count()
    if no_2fa:
        out.append({'level': 'info', 'code': '2fa_off', 'title': f'2FA off for {no_2fa} user(s)',
                    'detail': 'Ask them to turn it on.'})
    if not is_main and not org.tfn_risk:
        out.append({'level': 'info', 'code': 'type_not_set', 'title': 'Account type not set',
                    'detail': 'It cannot buy stock numbers until it is set to High Risk or Low Risk.'})
    return out


def traffic(org, start, end) -> list:
    """Buying and selling: by campaign, publisher (bought from) and buyer (sold
    to) - calls in, calls sent on, connected, minutes, spend."""
    rows = (_calls_today(org, start).filter(created_at__lt=end)
            .values('campaign_name', 'publisher_name', 'buyer_name')
            .annotate(calls_in=Count('id'),
                      calls_out=Count('id', filter=Q(buyer_id__isnull=False)),
                      connected=Count('id', filter=Q(status='completed')),
                      seconds=Sum('duration_seconds'),
                      spend=Sum('platform_cost'))
            .order_by('-calls_in', 'campaign_name'))
    return [{'campaign': r['campaign_name'] or '', 'publisher': r['publisher_name'] or '',
             'buyer': r['buyer_name'] or '', 'calls_in': r['calls_in'], 'calls_out': r['calls_out'],
             'connected': r['connected'], 'minutes': round((r['seconds'] or 0) / 60, 1),
             'spend': str((r['spend'] or Decimal('0')).quantize(Decimal('0.01')))} for r in rows]


def _source(n, main_id) -> str:
    """Whether a TFN is part of the Avortyx stock: 'avortyx' for our own
    account's numbers and any number sold from the stock (CH-665 keeps its
    sale_risk after the sale), 'own' for a number the client added itself."""
    if n.organization_id == main_id or n.sale_risk:
        return 'avortyx'
    return 'own'


def _risk(n) -> str:
    if n.sale_risk:
        return n.sale_risk
    try:
        return n.carrier.risk if n.carrier_id else ''
    except Exception:  # noqa: BLE001
        return ''


def _state(n) -> str:
    if n.status == 'active':
        return 'live'
    if n.status == 'released':
        return 'expired' if n.retired_reason else 'released'
    return n.status


def numbers(org, limit=1000) -> list:
    from accounts.tenancy import main_organization_id
    from phone_numbers.models import PhoneNumber
    main_id = main_organization_id()
    qs = (PhoneNumber.objects.filter(organization=org).select_related('campaign', 'carrier')
          .order_by('status', '-created_at')[:limit])
    items = list(qs)
    today = {r['called_number']: r['n'] for r in
             _calls_today(org, day_start()).values('called_number').annotate(n=Count('id'))}
    return [{'id': str(n.id), 'number': n.number, 'source': _source(n, main_id), 'risk': _risk(n),
             'campaign': n.campaign.name if n.campaign_id and n.campaign else '',
             'calls_today': today.get(n.number, 0), 'state': _state(n),
             'retired_reason': n.retired_reason or '', 'added': n.created_at.isoformat(),
             'released_at': n.released_at.isoformat() if n.released_at else None} for n in items]


def lookup(raw: str) -> dict:
    """Is this number on the platform? A TFN (which account, campaign, how
    many calls, first and last seen, source, risk, state) and / or a caller
    ID (its calls, by account, first and last seen, the latest ones)."""
    from accounts.tenancy import main_organization_id
    from analytics.models import CallRecord
    from phone_numbers.models import PhoneNumber
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if len(digits) < 10:
        return {'number': raw, 'found': False, 'error': 'Enter a 10 or 11 digit number.'}
    tail = digits[-10:]
    out = {'number': '+1' + tail, 'found': False, 'tfn': None, 'caller': None}

    tfn = (PhoneNumber.objects.filter(number__endswith=tail).select_related('organization', 'campaign', 'carrier')
           .order_by('-created_at').first())
    if tfn:
        calls = CallRecord.objects.filter(called_number__endswith=tail)
        agg = calls.aggregate(total=Count('id'), first=Min('created_at'), last=Max('created_at'))
        out['tfn'] = {
            'number': tfn.number, 'account': tfn.organization.name, 'account_id': str(tfn.organization_id),
            'campaign': tfn.campaign.name if tfn.campaign_id and tfn.campaign else '',
            'source': _source(tfn, main_organization_id()), 'risk': _risk(tfn), 'state': _state(tfn),
            'retired_reason': tfn.retired_reason or '',
            'released_at': tfn.released_at.isoformat() if tfn.released_at else None,
            'added': tfn.created_at.isoformat(), 'total_calls': agg['total'],
            'first_call': agg['first'].isoformat() if agg['first'] else None,
            'last_call': agg['last'].isoformat() if agg['last'] else None,
        }
        out['found'] = True

    calls = CallRecord.objects.filter(caller_number__endswith=tail)
    total = calls.count()
    if total:
        span = calls.aggregate(first=Min('created_at'), last=Max('created_at'))
        by_account = list(calls.values('organization__name').annotate(n=Count('id')).order_by('-n')[:10])
        recent = calls.select_related('organization').order_by('-created_at')[:10]
        out['caller'] = {
            'total_calls': total,
            'first_call': span['first'].isoformat() if span['first'] else None,
            'last_call': span['last'].isoformat() if span['last'] else None,
            'by_account': [{'account': r['organization__name'], 'calls': r['n']} for r in by_account],
            'recent': [{'at': c.created_at.isoformat(), 'account': c.organization.name,
                        'tfn': c.called_number, 'campaign': c.campaign_name, 'buyer': c.buyer_name,
                        'status': c.status, 'duration': c.duration_seconds} for c in recent],
        }
        out['found'] = True
    return out


def transactions(org, limit=50) -> list:
    from billing.models import Transaction
    rows = Transaction.objects.filter(organization=org).order_by('-created_at')[:max(1, min(limit, 200))]
    return [{'id': str(t.id), 'at': t.created_at.isoformat(), 'type': t.transaction_type,
             'status': t.status, 'amount': str(t.amount), 'balance_after': str(t.balance_after),
             'description': t.description or ''} for t in rows]
