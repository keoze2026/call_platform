"""Add the caller geo columns the mirror was never able to carry.

Schema only. The backfill is 0013, deliberately a separate migration: a data
migration and a schema change in one transaction is what produced
'cannot ALTER TABLE because it has pending trigger events' on buyers, and the
habit is cheaper than the debugging.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0011_backfill_started_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='caller_country',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
        migrations.AddField(
            model_name='callrecord',
            name='caller_city',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='callrecord',
            name='caller_zip',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='callrecord',
            name='caller_timezone',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
    ]
