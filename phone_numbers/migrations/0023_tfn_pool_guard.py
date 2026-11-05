# Customer accounts point 5: the database itself refuses a main-pool TFN in
# another account, a main TFN leaving the main pool, and a TFN changing
# account outside quarantine (phone_numbers/pools.py, PostgreSQL only). Its
# own migration: a trigger after the UPDATE of 0022 in one transaction can
# fail on PostgreSQL ("pending trigger events").
from django.db import migrations

from phone_numbers.pools import install_trigger, remove_trigger


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0022_tfn_pools'),
    ]

    operations = [
        migrations.RunPython(install_trigger, remove_trigger),
    ]
