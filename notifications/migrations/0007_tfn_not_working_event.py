"""CH-213: the TFN Not Working alert."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0006_notificationpreference_deleted_tfns_seen_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notificationrule',
            name='event',
            field=models.CharField(
                choices=[
                    ('call.missed', 'Call Missed'),
                    ('call.completed', 'Call Completed'),
                    ('campaign.cap_reached', 'Campaign Cap Reached'),
                    ('buyer.cap_reached', 'Buyer Cap Reached'),
                    ('publisher.cap_reached', 'Publisher Cap Reached'),
                    ('low.balance', 'Low Balance'),
                    ('campaign.paused', 'Campaign Paused'),
                    ('daily.summary', 'Daily Summary'),
                    ('call.started', 'New Call'),
                    ('destination.cap_reached', 'Destination Cap Reached'),
                    ('buyer.missed', 'Buyer Missed Call'),
                    ('aht.low', 'Average Handle Time Dropped'),
                    ('number.deleted', 'TFN Deleted'),
                    ('autopilot.alert', 'Autopilot Alert'),
                    ('number.not_working', 'TFN Not Working'),
                ],
                max_length=50,
            ),
        ),
    ]
