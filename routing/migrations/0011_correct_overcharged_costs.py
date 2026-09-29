"""Correct the stored cost of calls billed at ten times the rate.

Between 18 and 25 September the per-minute rate was $0.45 instead of $0.045, so
every call in that window was charged ten times over. The difference - $1,042.07
across 395 calls - was refunded on 26 September, but the per-call cost still held
the original charge, so those dates displayed a figure the client never actually
paid.

Dividing by ten gives the amount they were left paying once the refund is taken
into account, which is what the Cost column should show.

Runs on deploy. Migrations apply once, so this cannot double-correct.
"""
from decimal import Decimal
from datetime import date

from django.db import migrations

# The day the rate was corrected. Anything charged before this was at $0.45.
CUTOFF = date(2026, 9, 26)
FACTOR = Decimal('10')


def correct(apps, schema_editor):
    for label, name in (('routing', 'CallLog'), ('analytics', 'CallRecord')):
        model = apps.get_model(label, name)
        rows = list(
            model.objects.filter(platform_cost__gt=0, created_at__date__lt=CUTOFF)
        )
        for row in rows:
            row.platform_cost = (
                Decimal(row.platform_cost) / FACTOR
            ).quantize(Decimal('0.01'))
        if rows:
            model.objects.bulk_update(rows, ['platform_cost'], batch_size=1000)


def uncorrect(apps, schema_editor):
    """Restore the original charge, so the migration is reversible."""
    for label, name in (('routing', 'CallLog'), ('analytics', 'CallRecord')):
        model = apps.get_model(label, name)
        rows = list(
            model.objects.filter(platform_cost__gt=0, created_at__date__lt=CUTOFF)
        )
        for row in rows:
            row.platform_cost = (
                Decimal(row.platform_cost) * FACTOR
            ).quantize(Decimal('0.01'))
        if rows:
            model.objects.bulk_update(rows, ['platform_cost'], batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0010_calllog_platform_cost'),
        ('analytics', '0010_callrecord_platform_cost'),
    ]

    operations = [
        migrations.RunPython(correct, uncorrect),
    ]
