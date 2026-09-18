from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_plan_organizationplan'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingaccount',
            name='per_minute_rate',
            field=models.DecimalField(
                decimal_places=4, default=Decimal('0.4500'), max_digits=10,
                help_text='What this client is charged per minute of call time',
            ),
        ),
        migrations.AddField(
            model_name='billingaccount',
            name='markup_percent',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=6,
                help_text='Percentage added on top of the per-minute rate. 0 = no markup',
            ),
        ),
    ]
