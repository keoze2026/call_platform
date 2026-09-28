"""Add destination_number to the analytics mirror, and backfill it.

The dashboard has an "All destinations" dropdown, but the mirror never carried
the destination, so the filter had nothing to work against. Backfilled from
CallLog by id - the two share a primary key.
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    CallRecord = apps.get_model('analytics', 'CallRecord')
    CallLog = apps.get_model('routing', 'CallLog')

    # Chunked: this table holds every terminal call ever routed.
    ids = list(CallRecord.objects.filter(destination_number='').values_list('id', flat=True))
    for i in range(0, len(ids), 2000):
        chunk = ids[i:i + 2000]
        sources = dict(
            CallLog.objects.filter(id__in=chunk)
            .exclude(destination_number='')
            .values_list('id', 'destination_number')
        )
        if not sources:
            continue
        rows = CallRecord.objects.filter(id__in=list(sources.keys()))
        for row in rows:
            row.destination_number = sources[row.id]
        CallRecord.objects.bulk_update(rows, ['destination_number'])


def unbackfill(apps, schema_editor):
    # Nothing to undo - the column goes with the reverse of AddField.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0008_recompute_is_qualified'),
        ('routing', '0009_calllog_routing_trace'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='destination_number',
            field=models.CharField(blank=True, db_index=True, default='', max_length=20),
        ),
        migrations.RunPython(backfill, unbackfill),
    ]
