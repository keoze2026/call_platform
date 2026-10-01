"""Refuse a second live destination on the same buyer.

Separate from 0016 for the reason given in 0012: PostgreSQL will not alter a
table with pending trigger events, and a data migration in the same transaction
queues them.

0016 has already left each buyer with one by the time this applies.
"""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0016_one_enabled_destination_per_buyer'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='destination',
            constraint=models.UniqueConstraint(
                fields=['buyer'],
                condition=Q(enabled=True, buyer__isnull=False),
                name='one_enabled_destination_per_buyer',
            ),
        ),
    ]
