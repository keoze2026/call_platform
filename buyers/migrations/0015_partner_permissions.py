"""Give a buyer the same permission columns as a publisher.

A buyer login is the same kind of thing as a publisher login: an outside company
with an account inside this workspace, seeing only its own rows. The settings
page treats them the same way, so the storage behind it is the same too.

Both columns start empty, meaning "nobody has decided" rather than "nothing is
allowed" - the defaults in `accounts/partner_permissions.py` apply until
somebody sets them, so no existing buyer login changes behaviour here.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0014_unique_enabled_destination_tfn'),
    ]

    operations = [
        migrations.AddField(
            model_name='buyer',
            name='permissions',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='buyer',
            name='visible_report_columns',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
