"""Per-call and per-minute service fees.

Recording, VoIP Shield and rejected calls were priced on the pricing page and
charged nowhere: platform_cost was only ever ceil(minutes) x rate x markup.
"""
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('billing', '0007_billingaccount_fees')]
    operations = [
        migrations.AddField(
            model_name='billingaccount',
            name='recording_fee_per_minute',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.0025'), max_digits=10,
                                      help_text='Charged per minute when the call was recorded. 0 disables it'),
        ),
        migrations.AddField(
            model_name='billingaccount',
            name='voip_shield_fee_per_call',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.0100'), max_digits=10,
                                      help_text='Charged per call checked by the VoIP Shield. 0 disables it'),
        ),
        migrations.AddField(
            model_name='billingaccount',
            name='rejected_call_fee',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.0150'), max_digits=10,
                                      help_text='Charged per call refused before routing. 0 disables it'),
        ),
    ]
