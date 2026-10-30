"""CH-423 (Sir's Publishers design): each publisher's calls, counted the way
the Reports page counts them, for the Publishers list.

  payable   - a call the publisher is paid for: converted, with a payout
              (analytics.services.PAID, the Reports "Paid" column)
  answered  - the buyer picked up (analytics.services.ANSWERED)
  sent      - every call from the publisher (one row per caller call: hidden
              Revo retries are not in the reporting table)

Today and this month are cut at midnight Eastern, as everywhere else; the hour
is the last 60 minutes. Nothing here is estimated: a figure not counted is 0.
"""
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, ExtractHour
from zoneinfo import ZoneInfo


def publisher_usage(organization, publisher_ids, now=None) -> dict:
    """{publisher_id (str): counters} for the publishers given."""
    from analytics.models import CallRecord
    from analytics.services import ANSWERED, CALL_TIME, PAID
    from buyers.caps import DEFAULT_TZ, windows
    from phone_numbers.models import PhoneNumber

    ids = [str(i) for i in publisher_ids]
    out = {i: {'calls_hour': 0, 'payable_today': 0, 'sent_today': 0, 'answered_today': 0,
               'payable_month': 0, 'payable_global': 0, 'campaigns': [], 'tfn_count': 0} for i in ids}
    if not ids:
        return out
    hour_ago, day, month = windows(DEFAULT_TZ, now)
    qs = (CallRecord.objects.filter(organization=organization, publisher_id__in=ids)
          .annotate(t=CALL_TIME))
    for r in qs.values('publisher_id').annotate(
            calls_hour=Count('id', filter=Q(t__gte=hour_ago) & PAID),
            payable_today=Count('id', filter=Q(t__gte=day) & PAID),
            sent_today=Count('id', filter=Q(t__gte=day)),
            answered_today=Count('id', filter=Q(t__gte=day) & ANSWERED),
            payable_month=Count('id', filter=Q(t__gte=month) & PAID),
            payable_global=Count('id', filter=PAID)):
        row = out.get(str(r.pop('publisher_id')))
        if row is not None:
            row.update(r)

    # Its tracking numbers (not released) and the campaigns they send to.
    numbers = (PhoneNumber.objects.filter(publisher_id__in=ids, released_at__isnull=True)
               .exclude(status=PhoneNumber.Status.RELEASED)
               .values_list('publisher_id', 'campaign_id', 'campaign__name'))
    seen = {}
    for pid, cid, cname in numbers:
        row = out.get(str(pid))
        if row is None:
            continue
        row['tfn_count'] += 1
        if cid and (pid, cid) not in seen:
            seen[(pid, cid)] = True
            row['campaigns'].append({'id': str(cid), 'name': cname or ''})
    for row in out.values():
        row['campaigns'].sort(key=lambda c: c['name'])
    return out


def publishers_summary(organization, now=None) -> dict:
    """The page's own figures: payable calls today in total and per Eastern
    hour (the small chart), and payouts not yet paid."""
    from analytics.models import CallRecord
    from analytics.services import CALL_TIME, PAID
    from billing.models import Transaction
    from buyers.caps import DEFAULT_TZ, windows

    _, day, _ = windows(DEFAULT_TZ, now)
    hours = [0] * 24
    paid = (CallRecord.objects.filter(organization=organization).annotate(t=CALL_TIME)
            .filter(Q(t__gte=day) & PAID)
            .annotate(h=ExtractHour('t', tzinfo=ZoneInfo(DEFAULT_TZ)))
            .values('h').annotate(n=Count('id')))
    for r in paid:
        if r['h'] is not None and 0 <= r['h'] < 24:
            hours[r['h']] = r['n']
    pending = Transaction.objects.filter(organization=organization, transaction_type='payout',
                                         status__in=['pending', 'processing'])
    agg = pending.aggregate(n=Count('id'), amount=Coalesce(Sum('amount'), Decimal('0')))
    return {'payable_today': sum(hours), 'payable_by_hour': hours,
            'pending_payouts': agg['n'],
            'pending_payout_amount': str(Decimal(agg['amount'] or 0).quantize(Decimal('0.01')))}
