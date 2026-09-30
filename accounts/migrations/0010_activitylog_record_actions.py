"""Add the three record actions to the activity log.

Written by hand rather than generated: a `choices` change produces no SQL on
PostgreSQL, so this exists only to keep the migration state matching the model.
Without it the next `makemigrations` on any app would emit this same AlterField
as an unrelated surprise.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_user_buyer_publisher'),
    ]

    operations = [
        migrations.AlterField(
            model_name='activitylog',
            name='action',
            field=models.CharField(
                choices=[
                    ('login', 'Login'),
                    ('logout', 'Logout'),
                    ('password_change', 'Password Change'),
                    ('mfa_enabled', 'MFA Enabled'),
                    ('mfa_disabled', 'MFA Disabled'),
                    ('api_key_created', 'API Key Created'),
                    ('api_key_revoked', 'API Key Revoked'),
                    ('profile_updated', 'Profile Updated'),
                    ('organization_created', 'Organization Created'),
                    ('record_created', 'Created'),
                    ('record_updated', 'Updated'),
                    ('record_deleted', 'Deleted'),
                ],
                max_length=50,
            ),
        ),
    ]
