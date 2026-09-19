import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
        ('accounts', '0008_telegram_link'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notificationrule',
            name='event',
            field=models.CharField(choices=[
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
            ], max_length=50),
        ),
        migrations.CreateModel(
            name='NotificationPreference',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('popups_enabled', models.BooleanField(default=True, help_text='Master switch for pop-up alerts')),
                ('popup_events', models.JSONField(blank=True, default=list, help_text='Event types that surface as a pop-up. Empty means none.')),
                ('sound_enabled', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notification_preference',
                    to='accounts.user',
                )),
            ],
            options={'db_table': 'notification_preferences'},
        ),
    ]
