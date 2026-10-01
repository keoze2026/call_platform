"""Give a publisher somewhere to keep its login's permissions.

The settings page offered five permission toggles and eight reporting
checkboxes and saved all of them to the browser. They survived a refresh on that
machine, looked saved, and meant nothing: another admin opening the same
publisher saw different settings, and no toggle gated anything.

Both columns start empty, which means "nobody has decided" rather than "nothing
is allowed" - the defaults in `accounts/partner_permissions.py` apply until
somebody sets them, so no existing publisher login changes behaviour here.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('publishers', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='publisher',
            name='permissions',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='publisher',
            name='visible_report_columns',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
