"""Refuse a second publisher with the same name in a workspace.

Separate from 0003 because PostgreSQL will not alter a table that has pending
trigger events, and the deletions there queue them. See
`buyers/migrations/0012_unique_buyer_name.py`.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('publishers', '0003_publisher_name_cleanup'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='publisher',
            constraint=models.UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_publisher_name_per_organization',
            ),
        ),
    ]
