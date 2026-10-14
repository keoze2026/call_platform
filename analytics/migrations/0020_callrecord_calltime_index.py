from django.db import migrations, models
from django.db.models.functions import Coalesce


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0019_callrecord_ring_seconds'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='callrecord',
            index=models.Index(
                models.F('organization'),
                Coalesce('started_at', 'created_at'),
                name='callrec_org_calltime_idx',
            ),
        ),
    ]
