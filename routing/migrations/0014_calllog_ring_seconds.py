from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0013_calllog_destination_buyer_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='ring_seconds',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
