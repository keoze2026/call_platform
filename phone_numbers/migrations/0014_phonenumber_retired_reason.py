from django.db import migrations, models


class Migration(migrations.Migration):
    """CH-179: why the platform retired a number. Additive, empty for every
    existing row; nothing reads it except the "TFNs deleted" notice."""

    dependencies = [
        ('phone_numbers', '0013_tfn_test'),
    ]

    operations = [
        migrations.AddField(
            model_name='phonenumber',
            name='retired_reason',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
