from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0018_drop_one_live_destination_per_buyer'),
    ]

    operations = [
        migrations.AddField(
            model_name='buyer',
            name='timezone',
            field=models.CharField(default='UTC', max_length=64),
        ),
        migrations.AddField(
            model_name='buyer',
            name='can_block_numbers',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='buyer',
            name='can_download_reports',
            field=models.BooleanField(default=False),
        ),
    ]
