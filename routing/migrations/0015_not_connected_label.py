from django.db import migrations

OLD = 'Caller Abandoned'
NEW = 'Not Connected'


def rename(old, new):
    def run(apps, schema_editor):
        for app, model in (('routing', 'CallLog'), ('analytics', 'CallRecord')):
            apps.get_model(app, model).objects.filter(block_reason=old).update(block_reason=new)
    return run


class Migration(migrations.Migration):
    """Sir, 20 Oct: "Instead of Caller Abandoned change comment to Not Connected".

    New calls are written with the new wording; this renames the rows already
    written (the call log and the reports copy of it) so every screen and
    export reads the same."""

    dependencies = [
        ('routing', '0014_calllog_ring_seconds'),
        ('analytics', '0021_autopilotsettings'),
    ]

    operations = [
        migrations.RunPython(rename(OLD, NEW), rename(NEW, OLD)),
    ]
