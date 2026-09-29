"""Caller profile fields and do-not-call status.

The interface showed a caller profile with city, zip and timezone, and a TCPA
Shield - none of which had a field behind them. Telnyx supplies a carrier name
and a line type and nothing else.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0011_correct_overcharged_costs'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog', name='caller_city',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='calllog', name='caller_zip',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='calllog', name='caller_timezone',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='calllog', name='is_dnc',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='calllog', name='dnc_reason',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
    ]
