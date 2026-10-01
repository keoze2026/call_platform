"""Give every mirrored call the time it actually arrived.

`CallRecord.started_at` exists on the model and was never written, so it is null
on every row. `created_at` is `auto_now_add` — the moment the mirror row was
written, which is when the call reached a terminal status rather than when it
rang.

Every chart and every date filter in analytics buckets on `created_at`, so a
call that arrived at 10:50 and finished at 12:05 was reported in the 12:00 hour.
On 1 October that moved five calls out of 10:00 and 11:00 into 12:00: the hourly
chart read 9 and 97 where the call log held 13 and 98.

The daily total stayed correct, which is why nobody caught it. Only the shape of
the day was wrong — and the shape of the day is the entire point of an hourly
chart.

`CallRecord.id` is the `CallLog.id` (the mirror uses `update_or_create(id=call.id)`),
so the real arrival time is a direct lookup rather than a guess. Where the call
log row has since been deleted, `created_at` is used — no worse than today.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    CallRecord = apps.get_model('analytics', 'CallRecord')
    CallLog = apps.get_model('routing', 'CallLog')

    arrived = dict(CallLog.objects.values_list('id', 'created_at'))

    from_log = fallback = 0
    batch = []
    for record in CallRecord.objects.filter(started_at__isnull=True).only('id', 'created_at'):
        when = arrived.get(record.id)
        if when is None:
            when = record.created_at
            fallback += 1
        else:
            from_log += 1
        record.started_at = when
        batch.append(record)
        if len(batch) >= 2000:
            CallRecord.objects.bulk_update(batch, ['started_at'])
            batch = []
    if batch:
        CallRecord.objects.bulk_update(batch, ['started_at'])

    if from_log or fallback:
        print(f"\n    started_at filled on {from_log + fallback} record(s): "
              f"{from_log} from the call log, {fallback} fell back to the mirror's "
              f"own timestamp because the call log row is gone")


def undo(apps, schema_editor):
    """Not cleared on reverse: the times are correct, and removing them would
    only put the wrong buckets back."""


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0010_callrecord_platform_cost'),
        ('routing', '0012_caller_profile_and_dnc'),
    ]

    operations = [
        migrations.RunPython(backfill, undo),
    ]
