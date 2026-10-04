"""Add the fraud score column to the reporting mirror.

Schema only, and no backfill: CallLog.ipqs_fraud_score is 0 or null for every
call in the system because no provider has ever returned one. Copying that
across would write 1074 zeroes, and 0 is a real IPQS score meaning "clean" -
every unscored call would read as verified.

Null until IPQS is switched on. From then the enrichment task writes the real
score and the mirror picks it up on the re-mirror, like the geo fields.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0013_backfill_caller_geo'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='ipqs_fraud_score',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
