"""CH-258: a buyer's daily report, sent to its Telegram."""
import datetime

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0020_all_report_columns_by_default'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuyerTelegramReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False)),
                ('chat_id', models.CharField(blank=True, default='', max_length=64)),
                ('send_time', models.TimeField(default=datetime.time(21, 0))),
                ('no_recording_link', models.BooleanField(default=False)),
                ('connected_only', models.BooleanField(default=False)),
                ('hide_fail_reason', models.BooleanField(default=False)),
                ('column_names', models.JSONField(blank=True, default=dict)),
                ('comment_rewrites', models.JSONField(blank=True, default=dict)),
                ('last_sent_on', models.DateField(blank=True, null=True)),
                ('last_sent_at', models.DateTimeField(blank=True, null=True)),
                ('last_result', models.CharField(blank=True, default='', max_length=255)),
                ('attempts_on', models.DateField(blank=True, null=True)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('buyer', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='telegram_report', to='buyers.buyer')),
            ],
            options={
                'db_table': 'buyer_telegram_reports',
            },
        ),
    ]
