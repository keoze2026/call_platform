"""Index the two columns the hot queries filter on.

CallLog was indexed on organization, campaign, called_number and status, but
not on destination_number or buyer. format_destination runs several counts per
destination row, and check_buyer_concurrency runs on every incoming call -
both were scanning.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0012_caller_profile_and_dnc'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(
                fields=['destination_number', 'status', 'created_at'],
                name='calllog_dest_status_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='calllog',
            index=models.Index(
                fields=['buyer', 'status', 'created_at'],
                name='calllog_buyer_status_idx',
            ),
        ),
    ]
