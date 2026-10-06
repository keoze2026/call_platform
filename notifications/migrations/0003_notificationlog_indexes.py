"""NotificationLog had no indexes at all. It is read per organisation by date."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('notifications', '0002_notificationpreference')]
    operations = [
        migrations.AddIndex(
            model_name='notificationlog',
            index=models.Index(fields=['organization', 'created_at'], name='notiflog_org_created_idx'),
        ),
    ]
