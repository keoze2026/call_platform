from django.db import migrations, models


class Migration(migrations.Migration):
    """CH-675: when each user last clicked Seen on the platform owner's messages."""

    dependencies = [
        ('notifications', '0008_ch578_organization'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='owner_messages_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
