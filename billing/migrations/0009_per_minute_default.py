"""CH-255: a new workspace starts at $0.045 a minute (Sir, 24 Oct)."""
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_service_fees'),
    ]

    operations = [
        migrations.AlterField(
            model_name='billingaccount',
            name='per_minute_rate',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.0450'), help_text='What this client is charged per minute of call time', max_digits=10),
        ),
    ]
