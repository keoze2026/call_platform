"""The VIEWER role reached the model in CH-105 but never a migration, so
every migrate printed "accounts have changes not yet reflected". Choices
live only in Django's state - the column itself never changes - so this
applies instantly and rewrites nothing.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_workspace_roles'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('admin', 'Admin'),
                    ('publisher', 'Publisher'),
                    ('buyer', 'Buyer'),
                    ('viewer', 'Viewer'),
                    ('agent', 'Agent'),
                    ('reseller', 'Network/Reseller'),
                ],
                default='agent',
                max_length=20,
            ),
        ),
    ]
