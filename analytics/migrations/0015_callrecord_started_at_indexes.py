"""Index started_at on the reporting mirror.

Every report buckets and filters on it - analytics uses
CALL_TIME = Coalesce('started_at', 'created_at') - and the column had no index,
so each query scanned the table.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('analytics', '0014_callrecord_fraud_score')]
    operations = [
        migrations.AddIndex(
            model_name='callrecord',
            index=models.Index(fields=['organization', 'started_at'], name='callrec_org_started_idx'),
        ),
        migrations.AddIndex(
            model_name='callrecord',
            index=models.Index(fields=['started_at'], name='callrec_started_idx'),
        ),
    ]
