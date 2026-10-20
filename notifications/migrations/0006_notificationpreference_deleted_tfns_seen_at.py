from django.db import migrations, models


class Migration(migrations.Migration):
    """CH-179: when each user last acknowledged the "TFNs deleted" notice."""

    dependencies = [
        ('notifications', '0005_autopilot_alert_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='deleted_tfns_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
