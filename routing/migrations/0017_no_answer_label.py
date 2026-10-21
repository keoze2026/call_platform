from django.db import migrations

NEW = 'No Answer'


def rename_dialed(apps, schema_editor):
    """Calls that were sent to a destination and did not connect read "No Answer".

    Before CH-193 a line that refused the call at once was written as "No
    Destination", the same words as a call that was never sent anywhere, and a
    line that rang out as "Destination no answer". A call that was dialed has
    the destination it went to in its routing record ("selected"); one refused
    before dialing has none, and keeps "No Destination".
    """
    CallLog = apps.get_model('routing', 'CallLog')
    CallRecord = apps.get_model('analytics', 'CallRecord')
    ids = [
        pk for pk, trace in CallLog.objects.filter(block_reason='No Destination')
        .values_list('pk', 'routing_trace').iterator(chunk_size=2000)
        if isinstance(trace, dict) and trace.get('selected')
    ]
    ids += list(CallLog.objects.filter(block_reason='Destination no answer').values_list('pk', flat=True))
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        CallLog.objects.filter(pk__in=chunk).update(block_reason=NEW)
        # The reports copy has the same primary key.
        CallRecord.objects.filter(pk__in=chunk).update(block_reason=NEW)


class Migration(migrations.Migration):
    """Sir, 21 Oct: 14503668132 reached TBLA's line, which refused it in a
    second, and the log said "No Destination" - "its wrong fail reason"."""

    dependencies = [
        ('routing', '0016_clear_missed_recordings'),
        ('analytics', '0021_autopilotsettings'),
    ]

    operations = [
        migrations.RunPython(rename_dialed, migrations.RunPython.noop),
    ]
