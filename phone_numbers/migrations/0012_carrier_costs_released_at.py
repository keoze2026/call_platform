from django.db import migrations, models


def backfill_released_at(apps, schema_editor):
    PhoneNumber = apps.get_model('phone_numbers', 'PhoneNumber')
    PhoneNumber.objects.filter(status='released', released_at__isnull=True).update(
        released_at=models.F('updated_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0011_carrier_risk_override'),
    ]

    operations = [
        migrations.AddField(
            model_name='phonenumber',
            name='released_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='carrier',
            name='number_monthly_cost',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='carrier',
            name='portal_monthly_fee',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='carrier',
            name='per_minute_cost',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=8),
        ),
        migrations.RunPython(backfill_released_at, migrations.RunPython.noop),
    ]
