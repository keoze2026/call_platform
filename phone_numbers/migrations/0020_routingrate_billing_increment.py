"""CH-334: how the termination carrier rounds a call's minutes (1, 6, 30 or 60 seconds)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0019_carrier_billing_increment'),
    ]

    operations = [
        migrations.AddField(
            model_name='routingrate',
            name='billing_increment',
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
