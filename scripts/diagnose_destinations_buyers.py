"""Why destinations show zero calls, and why buyers come back empty.

The endpoint audit reported every destination with callsToday=0 on a day with
24 calls, and every buyer with an empty phone_number and a zero payout_amount.
Neither tells you whether the code is wrong or the data is. This answers that
before anything is changed.

    docker compose exec -T web python manage.py shell < scripts/diagnose_destinations_buyers.py

Read-only.
"""
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from routing.models import CallLog

now = timezone.now()
today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

org = (
    Organization.objects.annotate(n=Count('call_logs'))
    .order_by('-n').first()
)
print(f"workspace: {org}   (now {now:%F %H:%M} UTC)\n")

# ── destinations ─────────────────────────────────────────────────────────────
print("=" * 70)
print("DESTINATIONS")
print("=" * 70)

dests = list(Destination.objects.filter(organization=org).select_related('buyer'))
print(f"{len(dests)} destination(s) in this workspace\n")
for d in dests:
    print(f"  tfn={d.tfn!r:20} enabled={d.enabled}  buyer={d.buyer.name if d.buyer else None!r}  tz={d.timezone}")

print("\nWhat destination_number actually holds on today's calls:")
today_calls = CallLog.objects.filter(organization=org, created_at__gte=today_start)
print(f"  {today_calls.count()} call(s) today (UTC day)")
seen = (
    today_calls.values('destination_number')
    .annotate(n=Count('id')).order_by('-n')
)
for row in seen:
    val = row['destination_number']
    match = any(d.tfn == val for d in dests)
    print(f"  {val!r:22} {row['n']:>4} call(s)   matches a destination: {match}")

print("\nSame, over the last 7 days (in case today is simply quiet):")
week = CallLog.objects.filter(organization=org, created_at__gte=now - timedelta(days=7))
print(f"  {week.count()} call(s) in 7 days")
for row in week.values('destination_number').annotate(n=Count('id')).order_by('-n')[:10]:
    val = row['destination_number']
    print(f"  {val!r:22} {row['n']:>4} call(s)   matches a destination: {any(d.tfn == val for d in dests)}")

print("\nAre today's calls in a different organization than the destinations?")
for row in (
    CallLog.objects.filter(created_at__gte=today_start)
    .values('organization__name').annotate(n=Count('id')).order_by('-n')
):
    print(f"  {row['organization__name']!r:30} {row['n']:>4} call(s)")

print("\nRevenue on today's calls (the other field the audit saw empty):")
nonzero = today_calls.exclude(revenue=None).exclude(revenue=0).count()
print(f"  {nonzero} of {today_calls.count()} today's calls have a non-zero revenue")

# ── buyers ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("BUYERS")
print("=" * 70)

buyers = list(Buyer.objects.filter(organization=org))
print(f"{len(buyers)} buyer(s)\n")
for b in buyers:
    dest_tfns = [x.tfn for x in b.destinations.all()] if hasattr(b, 'destinations') else []
    print(f"  {b.name!r:24} phone_number={b.phone_number!r:16} payout_amount={b.payout_amount}  destinations={dest_tfns}")

print("\nIs the payout actually kept on the campaign instead?")
from campaigns.models import Campaign
for c in Campaign.objects.filter(organization=org):
    print(f"  campaign {c.name!r:24} payout_amount={c.payout_amount}")
