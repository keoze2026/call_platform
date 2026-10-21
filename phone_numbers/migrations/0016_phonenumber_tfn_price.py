"""CH-189: every TFN carries the price it was added at."""
from django.db import migrations, models


def price_existing(apps, schema_editor):
    # Numbers already on the platform take their carrier's current TFN price,
    # so they read exactly what Carrier Costs showed before this change.
    Carrier = apps.get_model('phone_numbers', 'Carrier')
    PhoneNumber = apps.get_model('phone_numbers', 'PhoneNumber')
    for c in Carrier.objects.exclude(number_monthly_cost=0):
        PhoneNumber.objects.filter(carrier=c, tfn_price__isnull=True).update(
            tfn_price=c.number_monthly_cost)


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0015_routingrate'),
    ]

    operations = [
        migrations.AddField(
            model_name='phonenumber',
            name='tfn_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.RunPython(price_existing, migrations.RunPython.noop),
    ]
