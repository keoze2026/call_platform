# CH-665: the database rule now knows the stock (sold once, to one customer)
# and trash (never used again). Its own migration, as 0023 (trigger after data).
from django.db import migrations

from phone_numbers.pools import install_trigger


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0024_ch665_tfn_stock'),
    ]

    operations = [
        migrations.RunPython(install_trigger, migrations.RunPython.noop),
    ]
