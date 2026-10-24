"""CH-247: each campaign destination has its own rate and "convert on"."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0018_call_retry_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruledestination',
            name='revenue',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='ruledestination',
            name='convert_on',
            field=models.CharField(blank=True, choices=[('', 'Campaign default'), ('incoming', 'Incoming Call'), ('connected', 'Call Connected'), ('length', 'Call Length')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='ruledestination',
            name='convert_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='calllog',
            name='rule_destination',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='call_logs', to='routing.ruledestination'),
        ),
    ]
