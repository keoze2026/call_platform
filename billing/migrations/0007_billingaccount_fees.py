from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0006_billingaccount_per_minute_rate'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingaccount',
            name='tfn_purchase_fee',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('20.00'), max_digits=10,
                help_text='Charged when this client provisions a tracking number',
            ),
        ),
        migrations.AddField(
            model_name='billingaccount',
            name='monthly_portal_fee',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('49.99'), max_digits=10,
                help_text='Recurring portal access fee. 0 disables it for this client',
            ),
        ),
        migrations.AddField(
            model_name='billingaccount',
            name='portal_fee_charged_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When the portal fee was last taken; drives the 30-day cycle',
            ),
        ),
    ]
