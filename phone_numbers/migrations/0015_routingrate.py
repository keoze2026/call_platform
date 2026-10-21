from django.db import migrations, models


class Migration(migrations.Migration):
    """CH-186: one platform-wide routing (termination) rate for Owner > Carrier Costs."""

    dependencies = [
        ('phone_numbers', '0014_phonenumber_retired_reason'),
    ]

    operations = [
        migrations.CreateModel(
            name='RoutingRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='termination', max_length=32, unique=True)),
                ('name', models.CharField(default='XOLO TFN termination', max_length=100)),
                ('per_minute_cost', models.DecimalField(decimal_places=4, default=0, max_digits=8)),
                ('monthly_fee', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
