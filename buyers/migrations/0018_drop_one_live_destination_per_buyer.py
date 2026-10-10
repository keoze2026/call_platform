from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0017_unique_live_destination_per_buyer'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='destination',
            name='one_enabled_destination_per_buyer',
        ),
    ]
