from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0008_calllog_carrier'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='routing_trace',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
