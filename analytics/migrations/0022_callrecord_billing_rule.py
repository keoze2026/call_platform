"""CH-247: a call billed by its destination's own rule."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0021_autopilotsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='billing_rule',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
    ]
