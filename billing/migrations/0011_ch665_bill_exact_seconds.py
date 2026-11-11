from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0010_money_four_decimals'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingaccount',
            name='bill_exact_seconds',
            field=models.BooleanField(default=False),
        ),
    ]
