"""Copy caller geo from the call log onto every CallRecord already written.

New calls fill in on their own - the enrichment task saves CallLog after the
call, which fires routing.signals.sync_call_record a second time and re-mirrors
the row. Only rows written before 0012 existed need this.

Matched on id, because mirror_call_log writes CallRecord with the CallLog's own
id (`update_or_create(id=call.id, ...)`).
"""
from django.db import migrations

BATCH = 500
FIELDS = ['caller_country', 'caller_city', 'caller_zip', 'caller_timezone']


def forwards(apps, schema_editor):
    CallRecord = apps.get_model('analytics', 'CallRecord')
    CallLog = apps.get_model('routing', 'CallLog')

    # Only the call logs that actually hold something, so an empty source row
    # never counts as work done.
    source = {
        str(r['id']): r
        for r in CallLog.objects.values('id', *FIELDS)
    }
    if not source:
        return

    batch, written = [], 0
    qs = CallRecord.objects.all().only('id', *FIELDS).iterator(chunk_size=BATCH)
    for rec in qs:
        src = source.get(str(rec.id))
        if not src:
            continue
        changed = False
        for f in FIELDS:
            val = src.get(f) or ''
            # Never overwrite a value already on the mirror with a blank.
            if val and not getattr(rec, f, ''):
                setattr(rec, f, val)
                changed = True
        if changed:
            batch.append(rec)
        if len(batch) >= BATCH:
            CallRecord.objects.bulk_update(batch, FIELDS)
            written += len(batch)
            batch = []
    if batch:
        CallRecord.objects.bulk_update(batch, FIELDS)
        written += len(batch)
    print(f'  backfilled caller geo onto {written} call records')


def backwards(apps, schema_editor):
    """No-op: 0012 drops the columns, so there is nothing to undo."""


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0012_callrecord_caller_geo'),
        ('routing', '0012_caller_profile_and_dnc'),  # where caller_city/zip/timezone are added
    ]

    operations = [migrations.RunPython(forwards, backwards)]
