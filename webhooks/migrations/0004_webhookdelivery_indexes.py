"""WebhookDelivery had no indexes at all.

The retry job scans by status and age on every run.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('webhooks', '0003_webhook_headers')]
    operations = [
        migrations.AddIndex(
            model_name='webhookdelivery',
            index=models.Index(fields=['status', 'created_at'], name='whdeliv_status_created_idx'),
        ),
        migrations.AddIndex(
            model_name='webhookdelivery',
            index=models.Index(fields=['webhook', 'created_at'], name='whdeliv_hook_created_idx'),
        ),
    ]
