"""Refuse two live destinations on the same number.

Enabled destinations only. The disabled rows are a record of numbers that were
sent to before, and deleting them to satisfy a constraint would destroy that.
What matters is that two destinations cannot be live on one number at the same
time, because every call to it would be credited to both.

0013 has already switched off any duplicates by the time this applies, and it is
a separate migration for the reason given there and in 0012.
"""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0013_disable_duplicate_live_destinations'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='destination',
            constraint=models.UniqueConstraint(
                fields=['organization', 'tfn'],
                condition=Q(enabled=True),
                name='unique_enabled_destination_tfn_per_organization',
            ),
        ),
    ]
