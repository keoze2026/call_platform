"""Mirror the charged amount onto the analytics table, and backfill it.

Copied from CallLog by id - the two share a primary key - so reporting reads the
amount that was actually charged rather than recomputing it.
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    CallRecord = apps.get_model('analytics', 'CallRecord')
    CallLog = apps.get_model('routing', 'CallLog')

    ids = list(CallRecord.objects.values_list('id', flat=True))
    for i in range(0, len(ids), 2000):
        chunk = ids[i:i + 2000]
        sources = dict(
            CallLog.objects.filter(id__in=chunk)
            .exclude(platform_cost=0)
            .values_list('id', 'platform_cost')
        )
        if not sources:
            continue
        rows = list(CallRecord.objects.filter(id__in=list(sources)))
        for row in rows:
            row.platform_cost = sources[row.id]
        CallRecord.objects.bulk_update(rows, ['platform_cost'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0009_callrecord_destination_number'),
        ('routing', '0010_calllog_platform_cost'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='platform_cost',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.RunPython(backfill, noop),
    ]
