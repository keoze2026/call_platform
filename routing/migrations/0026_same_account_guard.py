# Customer accounts point 6 (CH-632): the database itself refuses a routing
# link between two accounts (routing/same_account.py, PostgreSQL only).
# Triggers only: no table or row is changed.
from django.db import migrations

from routing.same_account import install_trigger, remove_trigger


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0025_ch578_organization_required'),
        ('buyers', '0027_ch578_organization_required'),
        ('publishers', '0008_ch578_organization_required'),
        ('campaigns', '0013_ch578_organization_required'),
        ('phone_numbers', '0023_tfn_pool_guard'),
    ]

    operations = [
        migrations.RunPython(install_trigger, remove_trigger),
    ]
