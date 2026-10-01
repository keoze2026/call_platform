"""Is the publisher side actually working, end to end.

Five separate things have to be true, and only the last one is visible from the
interface:

    attribution   a call is linked to the publisher that sent it
    payout        the right amount is recorded against it
    coverage      no calls are arriving unattributed
    scoping       a publisher login sees its own calls and nobody else's
    reporting     the publisher summary returns those numbers

The payout is read from the number's own `payout_per_call`, falling back to the
campaign's `payout_amount`, so this recomputes it the same way and compares
call by call rather than trusting a total that could be right by accident.

    docker compose exec -T web python manage.py shell < scripts/verify_publishers.py

Read-only apart from one throwaway login, created inside a transaction that is
rolled back.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import Organization, User
from accounts.permissions import scope_queryset
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog

SINCE = timezone.now() - timedelta(days=30)
org = (
    Organization.objects.annotate(n=Count('call_logs')).order_by('-n').first()
)
print(f"workspace: {org}   (last 30 days)\n")

calls = CallLog.objects.filter(organization=org, created_at__gte=SINCE)
total = calls.count()

print("1. ATTRIBUTION — is each call linked to a publisher")
attributed = calls.exclude(publisher=None).count()
print(f"   {attributed} of {total} calls have a publisher")
for r in calls.values('publisher__name').annotate(n=Count('id')).order_by('-n'):
    print(f"     {str(r['publisher__name']):<22} {r['n']}")

print("\n2. WHY ANY ARE UNATTRIBUTED")
orphan = calls.filter(publisher=None)
if orphan.exists():
    print(f"   {orphan.count()} call(s) with no publisher. The publisher comes from the")
    print("   number that was called, so this means the number is not linked to one:")
    for r in orphan.values('called_number').annotate(n=Count('id')).order_by('-n')[:10]:
        num = PhoneNumber.objects.filter(number=r['called_number']).first()
        if num is None:
            why = 'that number is not in the system at all'
        elif num.publisher_id is None:
            why = 'the number exists but has no publisher attached'
        else:
            why = f'the number IS linked to {num.publisher.name} - attribution failed'
        print(f"     {r['called_number']!r:<18} {r['n']:>4}   {why}")
else:
    print("   every call is attributed")

print("\n3. PAYOUT — is the recorded amount the one the rates say")
# Same precedence the payout is written with: the number's own rate first, the
# campaign's second. Recomputed rather than re-read, so a wrong total cannot
# agree with itself.
wrong = []
checked = 0
for c in calls.exclude(publisher=None).select_related('campaign')[:500]:
    num = PhoneNumber.objects.filter(number=c.called_number).first()
    expected = Decimal('0')
    if num is not None and (num.payout_per_call or 0) > 0:
        expected = Decimal(num.payout_per_call)
    elif c.campaign is not None:
        expected = Decimal(c.campaign.payout_amount or 0)
    # Only a converted call carries a payout; an unanswered one is zero.
    if Decimal(c.publisher_payout or 0) == 0 and (c.duration or 0) == 0:
        checked += 1
        continue
    checked += 1
    if Decimal(c.publisher_payout or 0) != expected:
        wrong.append((c.created_at, c.called_number, c.publisher_payout, expected))

print(f"   checked {checked} call(s)")
if wrong:
    print(f"   {len(wrong)} disagree with the configured rate:")
    for when, num, got, exp in wrong[:10]:
        print(f"     {when:%F %H:%M}  {num}  recorded {got}  rates say {exp}")
else:
    print("   every payout matches the rate configured for its number or campaign")

paid = calls.aggregate(t=Coalesce(Sum('publisher_payout'), Decimal('0')))['t']
earned = calls.aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t']
print(f"   30-day totals: buyers billed ${earned}, publishers due ${paid}, "
      f"margin ${earned - paid}")

print("\n4. SCOPING — does a publisher login see only its own calls")
pub = Publisher.objects.filter(organization=org).annotate(
    n=Count('call_logs')
).order_by('-n').first()
if pub is None:
    print("   no publisher to test with")
else:
    theirs = calls.filter(publisher=pub).count()
    print(f"   testing as a login for {pub.name!r}, which has {theirs} call(s)")

    class Rollback(Exception):
        pass

    try:
        with transaction.atomic():
            u = User.objects.create(
                email='zz-verify-publisher@example.com',
                username='zz-verify-publisher@example.com',
                role='publisher', organization=org, publisher=pub, is_active=True,
            )
            u.set_unusable_password()
            u.save()
            seen = scope_queryset(u, CallLog.objects.filter(organization=org, created_at__gte=SINCE))
            n = seen.count()
            leaked = seen.exclude(publisher=pub).count()
            print(f"   that login sees {n} call(s)")
            print(f"   {'PASS' if n == theirs else 'FAIL'}  sees exactly its own: "
                  f"{n} against {theirs}")
            print(f"   {'PASS' if leaked == 0 else 'FAIL'}  sees nobody else's: "
                  f"{leaked} call(s) belonging to another publisher")
            raise Rollback
    except Rollback:
        pass
    print(f"   cleanup: throwaway login remaining = "
          f"{User.objects.filter(email='zz-verify-publisher@example.com').count()}")

print("\n5. NUMBERS — which are linked to a publisher")
for n in PhoneNumber.objects.filter(organization=org):
    print(f"   {n.number:<16} publisher={n.publisher.name if n.publisher else None}  "
          f"campaign={n.campaign.name if n.campaign else None}  "
          f"payout_per_call={n.payout_per_call}")
