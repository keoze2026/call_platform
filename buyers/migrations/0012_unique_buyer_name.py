"""Refuse a second buyer with the same name in a workspace.

Separate from the cleanup in 0011 on purpose. PostgreSQL will not `ALTER TABLE`
on a table with pending trigger events, and deleting a buyer cascades to its
caps and campaign links, which queues exactly those. Doing both in one migration
fails with:

    cannot ALTER TABLE "buyers" because it has pending trigger events

and rolls the cleanup back with it. A separate migration is a separate
transaction, so the triggers have settled before this runs.

0011 has already made the names unique by the time this applies.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0011_buyer_data_integrity'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='buyer',
            constraint=models.UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_buyer_name_per_organization',
            ),
        ),
    ]
