"""Turn CallRecord.campaign_id into a real ForeignKey — state only, no SQL.

The model already declares:

    campaign = models.ForeignKey(..., db_column='campaign_id', ...)

so the physical column is the same `campaign_id` the old UUIDField used, with
the same contents. Nothing in the database needs to change.

Left to its own devices, makemigrations emits RemoveField('campaign_id') +
AddField('campaign'), which becomes DROP COLUMN then ADD COLUMN — every
CallRecord would lose its campaign, and since revenue and payout resolve through
the campaign, all reporting would read zero.

SeparateDatabaseAndState applies the model-state change and no database
operations. No foreign key constraint is added: the column holds ids written
before the FK existed, and a constraint could fail on any row whose campaign has
since been deleted.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0005_callrecord_carrier_name'),
        ('campaigns', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name='callrecord',
                    name='campaign_id',
                ),
                migrations.AddField(
                    model_name='callrecord',
                    name='campaign',
                    field=models.ForeignKey(
                        blank=True,
                        db_column='campaign_id',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='campaigns.campaign',
                    ),
                ),
            ],
        ),
    ]
