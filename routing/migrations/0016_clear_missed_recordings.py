from django.db import migrations

# Calls nobody answered never produce audio, but the dialplan posted a link for
# every call, so their rows carried a link to a file that does not exist and the
# call log showed them a play button (Sir, 20 Oct). Clear them on both the call
# log and its analytics mirror (same primary key).
MISSED = ('no_answer', 'busy', 'failed')


def clear_missed_recordings(apps, schema_editor):
    CallLog = apps.get_model('routing', 'CallLog')
    CallRecord = apps.get_model('analytics', 'CallRecord')
    missed = CallLog.objects.filter(status__in=MISSED).exclude(recording_url='')
    CallRecord.objects.filter(pk__in=missed.values('pk')).exclude(recording_url='').update(recording_url='')
    missed.update(recording_url='')


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0015_not_connected_label'),
        ('analytics', '0021_autopilotsettings'),
    ]

    operations = [
        # Nothing to undo: the cleared links pointed at files that were never made.
        migrations.RunPython(clear_missed_recordings, migrations.RunPython.noop),
    ]
