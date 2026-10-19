"""A custom role is stored on the member as its id, a 36-character UUID, but
the column held 20. On PostgreSQL, giving a member any custom role (Role Test,
Billing, ...) failed with "value too long for type character varying(20)", so
only the built-in roles could ever be assigned. Widening a varchar is instant
and rewrites no rows.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_role_choices_viewer'),
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
                max_length=40,
            ),
        ),
    ]
