"""Give every number the renewal date it should have had.

`renews_at` was written only from what the purchase request sent, and the
interface does not send it, so it was null on every number. The Renews column
could only ever be blank, and nothing - no reminder, no report, no alert - knew
when the next monthly charge was due.

A number renews monthly from the day it was bought, so the date is derivable
from `created_at` and does not need to be guessed: step forward a month at a
time until the date is in the future, which gives the next renewal rather than
one in the past.

Purchases from now on set it themselves, in `PhoneNumberService.purchase`. This
is only for the numbers bought before that.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    from dateutil.relativedelta import relativedelta
    from django.utils import timezone

    PhoneNumber = apps.get_model('phone_numbers', 'PhoneNumber')

    now = timezone.now()
    filled = 0
    for number in PhoneNumber.objects.filter(renews_at__isnull=True):
        when = number.created_at
        if when is None:
            continue
        # Roll forward to the next renewal that has not already happened.
        while when <= now:
            when += relativedelta(months=1)
        number.renews_at = when
        number.save(update_fields=['renews_at'])
        filled += 1

    if filled:
        print(f"\n    filled renews_at on {filled} number(s) from their purchase date")


def undo(apps, schema_editor):
    """Not cleared on reverse: the dates are correct, and removing them would
    only put the blank column back."""


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0005_phonenumber_concurrency_cap_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, undo),
    ]
