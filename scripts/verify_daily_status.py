"""Check every line of the daily status against the source data.

The report is only worth anything if its figures can be checked, so this
recomputes each one a different way and says whether they agree. Asked the day
after a wrong balance went out, which is the right time to ask.

    docker compose exec -T web python manage.py shell < scripts/verify_daily_status.py

Read-only.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import Organization
from billing.models import BillingAccount, Transaction
from buyers.destination import Destination
from routing.models import CallLog

DAY = (timezone.now() - timedelta(days=1)).date()
print(f"checking the report for {DAY:%A %-d %B}\n")

since = timezone.now() - timedelta(days=30)
org_ids = list(
    CallLog.objects.filter(created_at__gte=since)
    .values_list('organization_id', flat=True).distinct()
)
orgs = list(Organization.objects.filter(id__in=org_ids))
print("workspaces included: " + ", ".join(o.name for o in orgs))
print("workspaces excluded: " + ", ".join(
    o.name for o in Organization.objects.exclude(id__in=org_ids)
) + "   (no call in 30 days)")
print()

calls = CallLog.objects.filter(created_at__date=DAY, organization_id__in=org_ids)

print("CALLS")
print(f"  rows in the call log            {calls.count()}")
print(f"  distinct call ids               {calls.values('id').distinct().count()}")

print("\nCONNECTED")
connected = calls.filter(status__in=['completed', 'in_progress']).count()
print(f"  status completed or in_progress {connected}")
print(f"  with a duration over zero       {calls.filter(duration__gt=0).count()}")
print(f"  with an answered_at timestamp   {calls.exclude(answered_at=None).count()}")
print("  (these can differ: a call can connect and be cut off before it is billed)")

print("\nCONVERTED")
print(f"  calls with a conversion event   {calls.filter(conversions__isnull=False).distinct().count()}")
from webhooks.models import ConversionEvent
print(f"  conversion events that day      {ConversionEvent.objects.filter(created_at__date=DAY).count()}")
print("  (zero here means no pixel or postback fired, not that nothing converted)")

print("\nCLIENT BILLED — what the buyers owe")
rev = calls.aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t']
print(f"  sum of CallLog.revenue          ${rev}")
print(f"  calls with revenue over zero    {calls.filter(revenue__gt=0).count()}")
by_campaign = calls.values('campaign__name', 'campaign__payout_amount').annotate(n=Count('id'))
for r in by_campaign:
    print(f"  {r['campaign__name']!r}: {r['n']} calls at payout {r['campaign__payout_amount']}")

print("\nAVORTYX EARNED — what was charged for routing them")
charges = Transaction.objects.filter(
    created_at__date=DAY, transaction_type='charge', organization_id__in=org_ids,
).exclude(call_sid='')
total = charges.aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
print(f"  charges carrying a call_sid     {charges.count()}  totalling ${total}")
print(f"  smallest / largest              ${charges.order_by('amount').first().amount if charges else 0} / "
      f"${charges.order_by('-amount').first().amount if charges else 0}")
fees = Transaction.objects.filter(
    created_at__date=DAY, transaction_type='charge', call_sid='', organization_id__in=org_ids,
).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
print(f"  fees that day (not counted)     ${fees}")
refunds = Transaction.objects.filter(
    created_at__date=DAY, transaction_type='refund', organization_id__in=org_ids,
).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
print(f"  refunds that day (not counted)  ${refunds}")

print("\nBALANCE — state now, not that day")
for a in BillingAccount.objects.filter(organization_id__in=org_ids).select_related('organization'):
    print(f"  {a.organization.name:<12} ${a.balance}")

print("\nLIVE NUMBERS — state now, not that day")
for d in Destination.objects.filter(enabled=True, organization_id__in=org_ids).select_related('buyer'):
    print(f"  {d.tfn}  buyer {d.buyer.name if d.buyer else None}")

print("\nWhat the calls that day actually did:")
for r in calls.values('status').annotate(n=Count('id')).order_by('-n'):
    print(f"  {r['status']:<14} {r['n']}")
