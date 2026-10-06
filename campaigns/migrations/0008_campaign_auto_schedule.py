"""Give the campaign auto-schedule somewhere to live.

The play/pause times have been on the campaign page since the beginning,
held in a browser store. They never reached the server, so nothing could act
on them: a campaign set to pause at 5pm kept taking calls all night, and the
times were different on every machine that opened the page.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0007_campaign_advanced_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='auto_schedule_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='campaign',
            name='play_hour',
            field=models.IntegerField(default=8),
        ),
        migrations.AddField(
            model_name='campaign',
            name='play_minute',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='campaign',
            name='pause_hour',
            field=models.IntegerField(default=17),
        ),
        migrations.AddField(
            model_name='campaign',
            name='pause_minute',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='campaign',
            name='auto_schedule_timezone',
            field=models.CharField(default='America/New_York', max_length=64),
        ),
    ]
