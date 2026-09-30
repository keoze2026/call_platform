"""Which of the phone-number fields the audit saw empty are actually broken.

The audit reported 17 fields empty on every row. Most of them are settings that
are off on purpose - a cap that is disabled reads zero, and that is correct. The
ones worth fixing are the fields nothing can ever fill.

    docker compose exec -T web python manage.py shell < scripts/diagnose_phone_numbers.py

Read-only.
"""
from django.db.models import Count

from phone_numbers.models import PhoneNumber

TOLL_FREE_PREFIXES = ('800', '833', '844', '855', '866', '877', '888')

nums = list(PhoneNumber.objects.all().select_related('campaign', 'publisher'))
print(f"{len(nums)} phone number(s)\n")

print("by type and status:")
for row in PhoneNumber.objects.values('number_type', 'status', 'vendor').annotate(n=Count('id')):
    print(f"  {row['number_type']:<10} {row['status']:<10} {row['vendor']:<10} {row['n']}")

def is_toll_free(n):
    digits = ''.join(c for c in n if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits[:3] in TOLL_FREE_PREFIXES

tf = [n for n in nums if is_toll_free(n.number)]
print(f"\ntoll-free by area code: {len(tf)} of {len(nums)}")
print("  (a toll-free number has no state, so an empty `state` is correct for these)")

local = [n for n in nums if not is_toll_free(n.number)]
print(f"\nlocal numbers, which SHOULD have a state: {len(local)}")
for n in local:
    print(f"  {n.number:<16} state={n.state!r:<8} type={n.number_type}")

print("\nfields that are empty on every single number:")
CHECK = [
    'friendly_name', 'state', 'label', 'cap_enabled', 'daily_cap', 'monthly_cap',
    'concurrency_enabled', 'concurrency_cap', 'vendor_enabled', 'payout_per_call',
    'traffic_source_enabled', 'traffic_source_id', 'renews_at', 'sms_enabled',
    'allocated_capacity', 'twilio_sid',
]
for f in CHECK:
    filled = [n for n in nums if getattr(n, f, None) not in (None, '', 0, False, '0.00')]
    flag = '' if filled else '   <- never filled on any number'
    print(f"  {f:<24} filled on {len(filled):>3} of {len(nums)}{flag}")

print("\nassignment, which is what decides whether a number earns anything:")
print(f"  attached to a campaign : {sum(1 for n in nums if n.campaign_id)} of {len(nums)}")
print(f"  attached to a publisher: {sum(1 for n in nums if n.publisher_id)} of {len(nums)}")

print("\nnumbers taking calls but attached to no campaign (they route nowhere):")
from routing.models import CallLog
for n in nums:
    if n.campaign_id:
        continue
    c = CallLog.objects.filter(called_number=n.number).count()
    if c:
        print(f"  {n.number}  {c} call(s)  status={n.status}")
