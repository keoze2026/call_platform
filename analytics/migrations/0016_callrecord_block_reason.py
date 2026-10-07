"""Carry the refusal reason onto the reporting mirror.

The reports read CallRecord, so Fail Reason was empty on every row while the
real reason sat on CallLog.
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    """Copy the reason onto records already written."""
    CallRecord = apps.get_model('analytics', 'CallRecord')
    CallLog = apps.get_model('routing', 'CallLog')
    reasons = {
        str(r['id']): r['block_reason']
        for r in CallLog.objects.exclude(block_reason='').values('id', 'block_reason')
    }
    if not reasons:
        return
    updated, batch = 0, []
    for rec in CallRecord.objects.all().only('id', 'block_reason').iterator(chunk_size=500):
        reason = reasons.get(str(rec.id))
        if reason and not rec.block_reason:
            rec.block_reason = reason
            batch.append(rec)
        if len(batch) >= 500:
            CallRecord.objects.bulk_update(batch, ['block_reason'])
            updated += len(batch); batch = []
    if batch:
        CallRecord.objects.bulk_update(batch, ['block_reason'])
        updated += len(batch)
    print(f'  backfilled block_reason onto {updated} records')


class Migration(migrations.Migration):
    dependencies = [
        ('analytics', '0015_callrecord_started_at_indexes'),
        ('routing', '0013_calllog_destination_buyer_indexes'),
    ]
    operations = [
        migrations.AddField(
            model_name='callrecord', name='block_reason',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
