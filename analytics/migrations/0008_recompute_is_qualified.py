"""Recompute is_qualified on existing call records.

Qualified now means an answered call from a new caller - answered, and not a
repeat inside the campaign's duplicate window. Rows written before this still
hold the old meaning, so the Qualified drill-down would list different calls
from the ones the column counts until they are brought in line.

Runs as part of migrate, so there is nothing to run by hand. Only a boolean is
rewritten; no call record is created, deleted or otherwise changed.
"""
from django.db import migrations
from django.db.models import Q


ANSWERED = ['completed', 'in_progress']


def recompute(apps, schema_editor):
    CallRecord = apps.get_model('analytics', 'CallRecord')
    qualified = Q(status__in=ANSWERED) & ~Q(is_duplicate=True)

    CallRecord.objects.filter(qualified).exclude(is_qualified=True).update(is_qualified=True)
    CallRecord.objects.exclude(qualified).exclude(is_qualified=False).update(is_qualified=False)


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0007_callrecord_carrier'),
    ]

    operations = [
        # Reversing leaves the flags as they are: the previous values are not
        # recoverable, and the flag is recomputed on every save regardless.
        migrations.RunPython(recompute, migrations.RunPython.noop),
    ]
