"""CH-254: how each carrier rounds a call's minutes (1, 6, 30 or 60 seconds)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0018_tfn_test_internal_call'),
    ]

    operations = [
        migrations.AddField(
            model_name='carrier',
            name='billing_increment',
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
