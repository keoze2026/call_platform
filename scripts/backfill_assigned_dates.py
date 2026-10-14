"""Fill the Assigned date on numbers assigned before the stamp existed.

Every assignment path stamps `assigned_at` since CH-082; rows assigned
before then show an empty Assigned column. Backfilled from the honest
nearest fact: the number's first call (it was certainly assigned by then),
else the day the number entered the platform. Marked estimates, not
invented precision.

    docker compose exec web python scripts/backfill_assigned_dates.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    src = open(os.path.join(ROOT, 'manage.py')).read()
    os.environ['DJANGO_SETTINGS_MODULE'] = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)", src).group(1)

import django  # noqa: E402
django.setup()

from phone_numbers.models import PhoneNumber  # noqa: E402
from routing.models import CallLog  # noqa: E402

blank = PhoneNumber.objects.filter(campaign__isnull=False, assigned_at__isnull=True)
print(f'assigned numbers with no assigned date: {blank.count()}')
for pn in blank:
    digits = ''.join(c for c in pn.number if c.isdigit())[-10:]
    first = (CallLog.objects.filter(called_number__endswith=digits)
             .order_by('created_at').first())
    stamp = first.created_at if first else pn.created_at
    source = 'first call' if first else 'number created'
    pn.assigned_at = stamp
    pn.save(update_fields=['assigned_at'])
    print(f'  {pn.number}  -> {stamp:%Y-%m-%d} ({source})')
print('done; every assigned number now shows a date')
