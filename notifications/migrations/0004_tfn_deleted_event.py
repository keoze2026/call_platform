from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_notificationlog_indexes'),
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
                ],
                max_length=50,
            ),
        ),
    ]
