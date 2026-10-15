from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0009_carrier_terms_and_number_lifecycle'),
    ]

    operations = [
        migrations.AddField(
            model_name='carrier',
            name='max_answered_calls',
            field=models.IntegerField(default=0),
        ),
    ]
