"""Two indexes on CallLog, added to the model by another change without a migration.

(called_number, status) is the exact lookup route_incoming_call performs on every
incoming call. (status) backs the live-calls and terminal-status filters.

Real schema operations, unlike the analytics migration alongside this one. Index
names match what makemigrations generated so Django's state stays consistent.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0005_calllog_carrier_name'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(
                fields=['called_number', 'status'],
                name='call_logs_called__4abafe_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(
                fields=['status'],
                name='call_logs_status_123c70_idx',
            ),
        ),
    ]
